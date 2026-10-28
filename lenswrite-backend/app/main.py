import asyncio
import json
import os
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Proxy + dotenv first; keep heavy graph import after /health is defined so
# Railway can pass healthchecks even if later imports are slow.
from app.llm import apply_proxy_env, get_proxy_url

apply_proxy_env()
_proxy = get_proxy_url()
print(f"[LensWrite] outbound proxy: {_proxy or '(direct — no proxy)'}")
print(f"[LensWrite] XAI_API_KEY set: {bool(os.getenv('XAI_API_KEY'))}")

app = FastAPI(title="LensWrite API", version="1.0.0")


def _cors_origins() -> list[str]:
    """Comma-separated CORS_ORIGINS env, plus local dev defaults.

    Examples:
      CORS_ORIGINS=https://lenswrite.vercel.app
      CORS_ORIGINS=https://a.vercel.app,https://b.vercel.app
    No trailing slash. Scheme required (https://).
    """
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    # Always allow local Next dev unless explicitly disabled
    if (os.getenv("CORS_ALLOW_LOCAL") or "1").strip() not in ("0", "false", "no"):
        for o in ("http://localhost:3000", "http://127.0.0.1:3000"):
            if o not in origins:
                origins.append(o)
    if not origins:
        origins = ["http://localhost:3000"]
    return origins


def _cors_origin_regex() -> str | None:
    """Regex for Vercel hosts.

    Default ON in production (when CORS_ORIGIN_REGEX is unset) so preview
    deploys work without listing every *.vercel.app URL.

    Disable: CORS_ORIGIN_REGEX=0
    Explicit all vercel: CORS_ORIGIN_REGEX=1
    Custom: set a full regex string.
    """
    raw = (os.getenv("CORS_ORIGIN_REGEX") or "").strip()
    vercel_re = r"https://[\w.-]+\.vercel\.app"
    if raw in ("0", "false", "no", "off"):
        return None
    if not raw:
        # Auto-enable vercel hosts unless user only wants exact CORS_ORIGINS list
        # and explicitly disabled above. Safe for LensWrite + Vercel split deploy.
        return vercel_re
    if raw in ("1", "true", "yes", "vercel"):
        return vercel_re
    return raw


_origins = _cors_origins()
_origin_regex = _cors_origin_regex()
print(f"[LensWrite] CORS origins: {_origins}")
print(f"[LensWrite] CORS origin regex: {_origin_regex or '(none)'}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Liveness for Railway/Render/load balancers."""
    return {
        "status": "ok",
        "service": "lenswrite-api",
        "proxy": bool(get_proxy_url()),
        "has_xai_key": bool(os.getenv("XAI_API_KEY")),
    }


@app.get("/")
async def root():
    return {
        "service": "LensWrite API",
        "docs": "/docs",
        "health": "/health",
    }


# Heavy deps after health routes exist (import still runs at startup before listen).
from app.agents.setup_builder import generate_setup  # noqa: E402
from app.detail_level import resolve_detail_level, normalize_story_length  # noqa: E402
from app.events import event_queues  # noqa: E402
from app.graph import lenswrite_graph  # noqa: E402
from app.models import CharacterProfile, WorldSetting  # noqa: E402
from app.narrative_prefs import normalize_perspective, normalize_style  # noqa: E402
from app.twist import (  # noqa: E402
    TWIST_MARKER,
    compose_seed_with_twists,
    normalize_twists,
    twist_marker,
)

# thread_id -> last fatal error message (so status can finish instead of hanging)
thread_errors: dict[str, str] = {}

# setup_id -> draft session (seed, cards, suggested prompt) before plot generation
setup_sessions: dict[str, dict] = {}


async def run_graph_in_background(initial_state, config, thread_id: str):
    try:
        thread_errors.pop(thread_id, None)
        await lenswrite_graph.ainvoke(initial_state, config)
        await event_queues[thread_id].put({"event": "complete"})
    except Exception as e:
        print(f"[LensWrite] graph error: {e}")
        thread_errors[thread_id] = str(e)
        await event_queues[thread_id].put({"event": "error", "message": str(e)})


# ===================== Pydantic 模型 =====================


class CharacterDraft(BaseModel):
    """Optional rough notes before cards are generated."""

    prompt: str = ""
    use_slider: bool = False
    sliders: Optional[dict] = None


class SetupRequest(BaseModel):
    """Step 1: seed / theme / optional character drafts → system builds cards."""

    seed: str = Field(..., description="Story seed or premise spark")
    theme: Optional[str] = Field(None, description="Optional theme or emotional register")
    character_drafts: List[CharacterDraft] = Field(default_factory=list)
    # Used only when no drafts: how many characters to invent (2–5)
    target_character_count: int = 2


class SetupUpdateRequest(BaseModel):
    """Step 2: user-edited cards (optional persist)."""

    characters: List[CharacterProfile]
    world: WorldSetting
    suggested_story_prompt: Optional[str] = None


class CreateStoryRequest(BaseModel):
    """One-shot story: premise (+ optional char prompts) → internal setup → plot + render.

    Prefer story_prompt / story_seed. Full character cards are optional — if omitted,
    pass character_drafts (or nothing) and ensure_setup enriches inside the graph.
    """

    story_prompt: str = Field(
        default="",
        description="Story premise / prompt (falls back to story_seed or theme)",
    )
    characters: List[CharacterProfile] = Field(
        default_factory=list,
        description="Optional pre-built character cards; if empty, graph builds from drafts/seed",
    )
    character_drafts: List[CharacterDraft] = Field(
        default_factory=list,
        description="Rough character prompts; expanded internally when characters is empty",
    )
    world: Optional[WorldSetting] = None
    setup_id: Optional[str] = None
    story_seed: Optional[str] = None
    theme: Optional[str] = None
    # Used only when inventing characters from seed (no drafts / cards)
    target_character_count: int = 2
    # short | medium | long — drives auto detail grain with style
    story_length: str = "medium"
    style: str = "literary_realism"
    preferred_perspective: str = "third_limited"
    # Narrator (1st) or focal mind (3rd). Index "1" or character name.
    focal_character: Optional[str] = None
    # Optional override (batch tests). If omitted, derived from style + story_length.
    detail_level: Optional[int] = None
    # AI expand thin seed → structured outline before plot (default on; keep on for quality)
    expand_prompt: bool = True
    # Dual draft: render two prose candidates, pick more distinctive (default on)
    dual_draft: bool = True
    # Optional voice color pack: cold_procedural | warm_domestic | uncanny_quiet | comic_deadpan
    style_pack: Optional[str] = None
    # Optional user plot twists (multiple). Place with ⟦TWIST:1⟧, ⟦TWIST:2⟧, …
    # in story_prompt; unplaced twists are appended as binding late-reveals.
    twists: List[str] = Field(
        default_factory=list,
        description=(
            "User-written twist texts in order. Markers: "
            f"{twist_marker(1)}, {twist_marker(2)}, … (legacy {TWIST_MARKER} = #1)."
        ),
    )
    # Legacy single-twist fields (merged into twists when twists is empty)
    twist_enabled: bool = False
    twist_content: Optional[str] = Field(
        default=None,
        description=f"Legacy single twist. Prefer `twists`. Marker {TWIST_MARKER} or {twist_marker(1)}.",
    )


# ===================== Setup APIs =====================


@app.post("/setup")
async def create_setup(request: SetupRequest):
    seed = (request.seed or "").strip()
    if not seed and not (request.theme or "").strip():
        raise HTTPException(400, "Please provide a story seed or theme")

    drafts = [d.model_dump() for d in request.character_drafts if (d.prompt or "").strip()]
    try:
        bundle = await asyncio.to_thread(
            generate_setup,
            seed=seed or (request.theme or ""),
            theme=request.theme,
            character_drafts=drafts,
            target_character_count=request.target_character_count,
        )
    except Exception as e:
        print(f"[LensWrite] setup failed: {e}")
        raise HTTPException(500, f"Failed to generate character/world cards: {e}") from e

    setup_id = str(uuid.uuid4())
    payload = {
        "setup_id": setup_id,
        "seed": seed,
        "theme": (request.theme or "").strip() or None,
        "characters": [c.model_dump() for c in bundle.characters],
        "world": bundle.world.model_dump(),
        "suggested_story_prompt": bundle.suggested_story_prompt,
    }
    setup_sessions[setup_id] = payload
    return payload


@app.get("/setup/{setup_id}")
async def get_setup(setup_id: str):
    data = setup_sessions.get(setup_id)
    if not data:
        raise HTTPException(404, "Setup not found or expired")
    return data


@app.put("/setup/{setup_id}")
async def update_setup(setup_id: str, request: SetupUpdateRequest):
    data = setup_sessions.get(setup_id)
    if not data:
        raise HTTPException(404, "Setup not found or expired")
    data["characters"] = [c.model_dump() for c in request.characters]
    data["world"] = request.world.model_dump()
    if request.suggested_story_prompt is not None:
        data["suggested_story_prompt"] = request.suggested_story_prompt
    setup_sessions[setup_id] = data
    return data


# ===================== Story generation =====================


@app.post("/stories")
async def create_story(request: CreateStoryRequest):
    raw_prompt = (
        (request.story_prompt or "").strip()
        or (request.story_seed or "").strip()
    )
    if not raw_prompt:
        raise HTTPException(400, "Please provide a story premise")

    twist_rows = normalize_twists(
        list(request.twists or []),
        enabled=bool(request.twist_enabled),
        content=request.twist_content,
    )
    # If client sent twist_enabled with empty twists/content
    if bool(request.twist_enabled) and not twist_rows and not (request.twists or []):
        raise HTTPException(
            400,
            "Twist is enabled but no twist content was provided. "
            f"Send `twists: [\"...\"]` and place markers like {twist_marker(1)} in the premise.",
        )
    # Non-empty twists list with only blanks
    if (request.twists or []) and not twist_rows:
        raise HTTPException(400, "All twist entries are empty. Remove them or write content.")

    prompt, twist_meta_list = compose_seed_with_twists(
        raw_prompt,
        twist_rows,
        append_if_no_marker=True,
    )
    if twist_meta_list:
        print(
            f"[LensWrite] twists={len(twist_meta_list)} "
            f"contents={[t.get('content', '')[:40] for t in twist_meta_list]!r}"
        )
    first_twist = twist_meta_list[0]["content"] if twist_meta_list else None

    characters = list(request.characters or [])
    drafts = [
        d.model_dump()
        for d in (request.character_drafts or [])
        if (d.prompt or "").strip()
    ]

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    style = normalize_style(request.style)
    perspective = normalize_perspective(request.preferred_perspective)
    story_length = normalize_story_length(request.story_length)
    detail_level = resolve_detail_level(
        style,
        story_length,
        explicit=request.detail_level,
    )
    focal = (request.focal_character or "").strip() or "1"

    world = request.world
    if world is None and request.setup_id and request.setup_id in setup_sessions:
        w = setup_sessions[request.setup_id].get("world")
        world = WorldSetting(**w) if w else None

    # Cards pre-confirmed → plot immediately. Otherwise ensure_setup enriches
    # drafts / invents from seed inside the graph (no mid UI step).
    if characters:
        user_characters: list = []
        current_phase = "plot_weaving"
    else:
        user_characters = drafts
        current_phase = "character_building"

    n_target = max(2, min(5, int(request.target_character_count or 2)))

    initial_state = {
        "user_prompt": prompt,
        "original_user_prompt": prompt,
        "story_seed": request.story_seed or prompt,
        "theme": request.theme,
        "detail_level": detail_level,
        "story_length": story_length,
        "style": style,
        "preferred_perspective": perspective,
        "focal_character": focal,
        "characters": characters,
        "user_characters": user_characters,
        "world_setting": world,
        "target_character_count": n_target,
        "expand_prompt": bool(request.expand_prompt),
        "dual_draft": bool(request.dual_draft),
        "style_pack": (request.style_pack or "").strip() or None,
        "style_pack_meta": None,
        "structure_meta": None,
        "signature_device": None,
        "voice_revise_only": False,
        "twist_enabled": bool(twist_meta_list),
        "twist_content": first_twist,
        "twists": twist_meta_list or [],
        "expanded_prompt": None,
        "story_outline": None,
        "scene_count_target": None,
        "current_phase": current_phase,
        "iteration_count": 0,
        "max_iterations": 6,  # hard-gate revisions only; no score threshold
        "revision_threshold": None,  # disabled — do not revise on total_score alone
        "core_locked": False,
        "story_core": None,
        "thread_id": thread_id,
        "best_score": 0.0,
        "revision_notes": [],
    }

    asyncio.create_task(run_graph_in_background(initial_state, config, thread_id))

    return {"thread_id": thread_id, "message": "Story generation in progress..."}


@app.get("/stories/{thread_id}/status")
async def get_story_status(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    state = lenswrite_graph.get_state(config)
    values = state.values or {}

    final_story = values.get("final_short_story")
    best_story = values.get("best_final_story")
    best_score = values.get("best_score", 0.0) or 0.0
    evaluation = values.get("evaluation")
    iteration_count = values.get("iteration_count") or 0
    max_iterations = values.get("max_iterations") or 6

    if iteration_count >= max_iterations and best_story:
        chosen_story = best_story
    else:
        chosen_story = final_story

    eval_dump = evaluation.model_dump() if evaluation else None
    fatal = thread_errors.get(thread_id)
    is_interrupted = bool(state.next) and not fatal

    world = values.get("world_setting")
    chars = values.get("characters") or []

    return {
        "thread_id": thread_id,
        "current_phase": values.get("current_phase"),
        "has_final_story": bool(chosen_story),
        "final_story": chosen_story.model_dump() if chosen_story else None,
        "evaluation": eval_dump,
        "is_interrupted": is_interrupted,
        "has_raw_story": bool(values.get("raw_interaction_story")),
        "iteration_count": iteration_count,
        "max_iterations": max_iterations,
        "best_score": best_score,
        "actual_word_count": (eval_dump or {}).get("actual_word_count"),
        "detail_level": values.get("detail_level"),
        "story_length": values.get("story_length"),
        "style": values.get("style"),
        "preferred_perspective": values.get("preferred_perspective"),
        "focal_character": values.get("focal_character"),
        "style_match": (eval_dump or {}).get("style_match"),
        "perspective_match": (eval_dump or {}).get("perspective_match"),
        "style_ok": (eval_dump or {}).get("style_ok"),
        "perspective_ok": (eval_dump or {}).get("perspective_ok"),
        "fidelity_score": (eval_dump or {}).get("fidelity_score"),
        "fidelity_ok": (eval_dump or {}).get("fidelity_ok"),
        "acceptance_ok": (eval_dump or {}).get("acceptance_ok"),
        "in_word_band": (eval_dump or {}).get("in_word_band"),
        "info_ok": (eval_dump or {}).get("info_ok"),
        "info_coverage": (eval_dump or {}).get("info_coverage"),
        "enrichment_in_prose_ok": (eval_dump or {}).get("enrichment_in_prose_ok"),
        "acceptance_summary": (eval_dump or {}).get("acceptance_summary"),
        "failure_reasons": (eval_dump or {}).get("failure_reasons") or [],
        "pass_reasons": (eval_dump or {}).get("pass_reasons") or [],
        "reasons": (eval_dump or {}).get("reasons") or [],
        "enrichment_probe": (eval_dump or {}).get("enrichment_probe"),
        "core_locked": values.get("core_locked"),
        "has_story_core": bool(values.get("story_core")),
        "original_user_prompt": values.get("original_user_prompt"),
        "expanded_prompt": values.get("expanded_prompt"),  # standardized short-dense seed
        "story_outline": values.get("story_outline"),
        "scene_count_target": values.get("scene_count_target"),
        "expand_prompt": values.get("expand_prompt"),
        "standardized_seed": (
            (values.get("story_outline") or {}).get("standardized_seed")
            if isinstance(values.get("story_outline"), dict)
            else None
        ) or values.get("expanded_prompt"),
        "dual_draft": values.get("dual_draft"),
        "style_pack": values.get("style_pack"),
        "style_pack_meta": values.get("style_pack_meta"),
        "structure_meta": values.get("structure_meta"),
        "signature_device": values.get("signature_device"),
        "voice_distinctiveness": (eval_dump or {}).get("voice_distinctiveness"),
        "twist_enabled": values.get("twist_enabled"),
        "twist_content": values.get("twist_content"),
        "twists": values.get("twists") or [],
        "characters": [c.model_dump() if hasattr(c, "model_dump") else c for c in chars],
        "world": world.model_dump() if world and hasattr(world, "model_dump") else world,
        "error": fatal,
    }


@app.get("/stories/{thread_id}/stream")
async def stream_story(thread_id: str):
    async def event_generator():
        queue = event_queues[thread_id]
        while True:
            event = await queue.get()
            if event["event"] == "complete":
                yield f"event: complete\ndata: {{}}\n\n"
                break
            yield f"event: {event['event']}\ndata: {json.dumps(event)}\n\n"

    return EventSourceResponse(event_generator())


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
