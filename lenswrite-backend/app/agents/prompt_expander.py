"""Prompt standardizer + outline: any user seed → short high-density brief + plot outline.

Product finding (prompt quality tests): short + high information density works best.
This node ALWAYS rewrites the user seed into that standard before plot_weaver.

Internal only — no mid-page user edit.
"""
from __future__ import annotations

import re
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.detail_level import normalize_story_length, story_length_label, word_target_band
from functools import lru_cache

from app.llm import make_chat_openai
from app.models import StoryState
from app.personality import format_character_for_prompt
from app.twist import format_twist_for_prompt, twist_must_include_lines
from app.voice import pick_structure, pick_style_pack


@lru_cache(maxsize=1)
def _llm():
    return make_chat_openai(temperature=0.45, timeout=120, max_retries=2)

# Soft scene budget (info-driven length; still useful for structure)
SCENE_BUDGET = {
    "short": {"min": 5, "max": 6, "ideal": 5},
    "medium": {"min": 5, "max": 6, "ideal": 5},
    "long": {"min": 5, "max": 6, "ideal": 6},
}

# Product standard for the canonical seed (from short×rich experiments)
STANDARDIZED_SEED_MIN_WORDS = 28
STANDARDIZED_SEED_MAX_WORDS = 75
STANDARDIZED_SEED_IDEAL = "40–60 English words"


class OutlinePhase(BaseModel):
    """One temporal / dramatic phase (planning language only)."""

    name: str = Field(..., description="Short phase label, e.g. Day 1 inventory")
    goal: str = Field(..., description="What this phase must accomplish (factual)")
    key_beats: List[str] = Field(
        default_factory=list,
        description="2–5 executable beat ideas (not literary prose)",
    )


class StoryOutline(BaseModel):
    """Standardized seed + structured outline for Phase A plotting."""

    standardized_seed: str = Field(
        ...,
        description=(
            "CANONICAL product seed: SHORT + HIGH DENSITY only. "
            f"Target {STANDARDIZED_SEED_IDEAL} (hard max ~{STANDARDIZED_SEED_MAX_WORDS}). "
            "Pack: named/role who, concrete place, objects, time pressure, decision, "
            "constraints. No mood essays, no 'write a literary story' meta-instructions. "
            "If user seed is sparse: add minimal scaffolding (role/place/object/decision) "
            "consistent with it. If user seed is long/vague: compress to hard facts only."
        ),
    )
    expanded_premise: str = Field(
        ...,
        description=(
            "Optional 2–4 extra planning sentences beyond standardized_seed "
            "(stakes/decision only). Keep dense; not literary prose. "
            "May repeat key facts from standardized_seed if needed."
        ),
    )
    overall_conflict: str = Field(..., description="Central conflict in plain terms")
    phases: List[OutlinePhase] = Field(
        ...,
        description="Ordered phases covering beginning → middle → decision/end",
    )
    must_include: List[str] = Field(
        default_factory=list,
        description="Objects, reveals, decisions, or facts that must appear",
    )
    must_not_invent: List[str] = Field(
        default_factory=list,
        description="Forbidden inventions (major biography, crime, new main characters, etc.)",
    )
    ending_direction: str = Field(
        ...,
        description="How the story should resolve directionally (no literary ending prose)",
    )
    scene_count_target: int = Field(
        ...,
        description="How many scenes plot_weaver should aim for",
    )
    signature_device: str = Field(
        ...,
        description=(
            "One private narrative device unique to THIS story "
            "(object rule, information asymmetry, broken ritual, structural trick). "
            "Not a generic 'note in a book' unless the seed already is that."
        ),
    )
    structure_id: str = Field(
        default="",
        description="Id from structure pool if known, else short free label",
    )


def scene_budget(story_length: str | None) -> dict:
    key = normalize_story_length(story_length)
    return dict(SCENE_BUDGET[key])


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def format_outline_for_plot(outline: StoryOutline | dict | None) -> str:
    """Serialize outline into the plot_weaver human prompt block."""
    if outline is None:
        return "(no expanded outline)"
    if hasattr(outline, "model_dump"):
        d = outline.model_dump()
    elif isinstance(outline, dict):
        d = outline
    else:
        return str(outline)

    std = (d.get("standardized_seed") or d.get("expanded_premise") or "").strip()
    lines = [
        "STANDARDIZED SEED (authoritative short high-density brief — honor this):",
        std,
        f"OVERALL CONFLICT: {d.get('overall_conflict') or ''}",
        f"ENDING DIRECTION: {d.get('ending_direction') or ''}",
        f"SIGNATURE DEVICE: {d.get('signature_device') or ''}",
        f"STRUCTURE: {d.get('structure_id') or ''}",
        f"SCENE COUNT TARGET: {d.get('scene_count_target')}",
        "PHASES:",
    ]
    extra = (d.get("expanded_premise") or "").strip()
    if extra and extra != std:
        lines.insert(3, f"PLANNING NOTES:\n{extra}")
    for i, p in enumerate(d.get("phases") or [], start=1):
        lines.append(f"  {i}. {p.get('name')}: {p.get('goal')}")
        for b in p.get("key_beats") or []:
            lines.append(f"     - {b}")
    if d.get("must_include"):
        lines.append("MUST INCLUDE:")
        for x in d["must_include"]:
            lines.append(f"  ★ {x}")
    if d.get("must_not_invent"):
        lines.append("MUST NOT INVENT:")
        for x in d["must_not_invent"]:
            lines.append(f"  ✗ {x}")
    return "\n".join(lines)


def _format_world_brief(world) -> str:
    if world is None:
        return "(no world yet)"
    if hasattr(world, "model_dump"):
        d = world.model_dump()
    elif isinstance(world, dict):
        d = world
    else:
        return str(world)
    parts = []
    for k in ("location", "time_period", "atmosphere", "rules_or_constraints"):
        if d.get(k):
            parts.append(f"{k}: {d[k]}")
    places = d.get("key_places") or []
    if places:
        parts.append("key_places: " + "; ".join(str(p) for p in places))
    return "\n".join(parts) or "(empty world)"


def _normalize_standardized_seed(text: str, *, fallback: str = "") -> str:
    """Clamp standardized seed toward short-dense product band."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        s = re.sub(r"\s+", " ", (fallback or "").strip())
    # Strip common meta instructions if model leaked them
    for bad in (
        "write a literary",
        "please write",
        "in third person",
        "short story about",
        "the story should",
    ):
        if s.lower().startswith(bad):
            # leave as-is but prefer later rewrite; just clean duplicates spaces
            break
    wc = _word_count(s)
    if wc > STANDARDIZED_SEED_MAX_WORDS + 15:
        # Soft hard-cut at sentence boundary near max
        words = s.split()
        cut = " ".join(words[:STANDARDIZED_SEED_MAX_WORDS])
        # try end at punctuation in cut
        m = re.search(r"^(.+[.!?])\s", cut + " ")
        s = (m.group(1) if m else cut).strip()
    return s


def expand_story_prompt(
    *,
    seed: str,
    theme: str | None = None,
    story_length: str | None = None,
    characters=None,
    world=None,
    structure: dict | None = None,
    style_pack: dict | None = None,
    twist_content: str | None = None,
    twist_enabled: bool = False,
    twists: list | None = None,
) -> StoryOutline:
    """Standardize seed (short+dense) + build StoryOutline. Raises on total failure."""
    length = normalize_story_length(story_length)
    budget = scene_budget(length)
    band = word_target_band(length)
    structure = structure or pick_structure(seed)
    style_pack = style_pack or pick_style_pack(seed)
    chars_info = (
        "\n\n".join(format_character_for_prompt(c) for c in (characters or []))
        or "(characters not yet fully built — infer only from seed)"
    )
    world_info = _format_world_brief(world)
    twist_rows = list(twists or [])
    if not twist_rows and twist_enabled and (twist_content or "").strip():
        twist_rows = [{"index": 1, "content": (twist_content or "").strip()}]
    twist_lines = twist_must_include_lines(twist_rows)
    twist_prompt = format_twist_for_prompt({
        "twists": twist_rows,
        "twist_enabled": bool(twist_rows),
        "twist_content": twist_content,
    })

    system = f"""You are the PROMPT STANDARDIZER + outline builder for a literary short-story pipeline.

Product quality tests showed the best user inputs are SHORT + HIGH INFORMATION DENSITY
(about {STANDARDIZED_SEED_IDEAL}). Your first job is to rewrite ANY user seed into that standard.
Your second job is a structured StoryOutline for plotting.

Output StoryOutline JSON only (via schema). Planning language — NOT finished literary prose.

=== STANDARDIZED SEED (field: standardized_seed) — MANDATORY FORMAT ===
- Length: ideally {STANDARDIZED_SEED_IDEAL}; never fluff past ~{STANDARDIZED_SEED_MAX_WORDS} words.
- Must pack concrete facts (as many as the seed allows; invent only minimal glue if sparse):
  WHO (name or role), WHERE (concrete place), OBJECTS, TIME PRESSURE / deadline,
  DECISION the focal person must face, CONSTRAINTS / stakes.
- Style: telegraphic but grammatical; one tight paragraph or 2–3 short sentences.
- FORBIDDEN in standardized_seed:
  - Mood essays ("something feels different in a way they cannot explain…")
  - Meta instructions ("write a literary short story", "third person", "please")
  - Padding synonyms, moral summary, genre slogans
- If seed is SHORT + SPARSE (e.g. "A woman finds a key."): densify with minimal scaffolding
  consistent with it (role, place, what the key might open, a near decision) — do not invent
  crime empires or new main plots the seed forbids.
- If seed is LONG + VAGUE: DELETE atmosphere and keep only hard facts; compress hard.
- If seed is already SHORT + RICH: preserve facts; trim only redundancy; do not dilute.
- User twists (if any) are HIDDEN FACTS for later dramatization — put outcomes in must_include,
  do NOT paste twist wording into standardized_seed as exposition.

STRUCTURE PACK:
id={structure.get('id')} — {structure.get('guidance')}

VOICE COLOR (plot still factual):
id={style_pack.get('id')} — {style_pack.get('guidance')}

OUTLINE RULES:
1. HONOR the seed core situation (after standardization).
2. phases: 3–4; key_beats executable, not lyrical.
3. signature_device: one private device unique to THIS story.
4. must_include: objects, decisions, deadlines from standardized_seed (+ twists as dramatize outcomes).
5. must_not_invent: explicit.
6. scene_count_target in [{budget['min']}, {budget['max']}] prefer {budget['ideal']}.
7. structure_id = "{structure.get('id')}".
8. Soft product word guide ~{band['min']}–{band['max']} (acceptance is information-based).
9. Avoid factory café/ledger/cup-stack beats unless seed requires them.
10. Twists: binding hidden facts → must_include as dramatize outcomes; evidence+reaction+behavior change.
"""

    human = """Original user seed / prompt (may be short, long, sparse, or dense):
{seed}

Theme (optional, usually none):
{theme}

{twist_block}

Story length pref: {length_label} ({length}) | scene target {scene_min}–{scene_max}
Structure: {structure_id} — {structure_guidance}
Style pack: {style_pack_id}

World (if any):
{world_info}

Characters (if any — use names/roles in standardized_seed when present):
{characters_info}

Produce StoryOutline:
1) standardized_seed = SHORT HIGH-DENSITY canonical brief (product standard)
2) full outline fields for plotting."""

    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | _llm().with_structured_output(StoryOutline)
    )
    outline: StoryOutline = chain.invoke({
        "seed": seed or "(empty seed)",
        "theme": (theme or "").strip() or "(none)",
        "twist_block": twist_prompt,
        "length": length,
        "length_label": story_length_label(length),
        "scene_min": budget["min"],
        "scene_max": budget["max"],
        "structure_id": structure.get("id"),
        "structure_guidance": structure.get("guidance"),
        "style_pack_id": style_pack.get("id"),
        "world_info": world_info,
        "characters_info": chars_info,
    })

    n = int(outline.scene_count_target or budget["ideal"])
    n = max(budget["min"], min(budget["max"], n))
    updates: dict = {}
    if n != outline.scene_count_target:
        updates["scene_count_target"] = n
    if not (outline.structure_id or "").strip():
        updates["structure_id"] = structure.get("id") or ""
    if not (outline.signature_device or "").strip():
        updates["signature_device"] = (
            f"a private constraint implied by the seed ({(seed or '')[:80]})"
        )

    std = _normalize_standardized_seed(
        outline.standardized_seed,
        fallback=outline.expanded_premise or seed,
    )
    if not std:
        # Last resort: compress seed naively
        std = _normalize_standardized_seed(seed)
    if std != (outline.standardized_seed or "").strip():
        updates["standardized_seed"] = std
    # Keep expanded_premise non-empty
    if not (outline.expanded_premise or "").strip():
        updates["expanded_premise"] = std

    if twist_lines:
        mi = list(outline.must_include or [])
        for line in twist_lines:
            snippet = line.split(":", 1)[-1].strip()[:40].lower()
            if not any(snippet and snippet in (x or "").lower() for x in mi):
                mi.append(line)
        updates["must_include"] = mi

    if updates:
        outline = outline.model_copy(update=updates)
    return outline


def prompt_expander_node(state: StoryState) -> dict:
    """Graph node: standardize seed (short+dense) + outline for plot_weaver."""
    print(">>> [prompt_expander_node] standardize → short high-density seed + outline")
    seed = (
        (state.get("original_user_prompt") or "").strip()
        or (state.get("story_seed") or "").strip()
        or (state.get("user_prompt") or "").strip()
    )
    length = normalize_story_length(state.get("story_length"))
    budget = scene_budget(length)

    original = (state.get("original_user_prompt") or seed or "").strip() or seed

    if not seed:
        print("    [warn] empty seed — skip expand")
        return {
            "original_user_prompt": original,
            "expanded_prompt": None,
            "story_outline": None,
            "current_phase": "plot_weaving",
            "last_agent_output": "Prompt expand skipped (empty seed).",
        }

    structure = state.get("structure_meta") or pick_structure(seed)
    style_pack = state.get("style_pack_meta") or pick_style_pack(
        seed, state.get("style"), force_id=state.get("style_pack")
    )

    try:
        outline = expand_story_prompt(
            seed=seed,
            theme=state.get("theme"),
            story_length=length,
            characters=state.get("characters"),
            world=state.get("world_setting"),
            structure=structure,
            style_pack=style_pack,
            twist_enabled=bool(state.get("twist_enabled")),
            twist_content=state.get("twist_content"),
            twists=state.get("twists"),
        )
        std = (outline.standardized_seed or outline.expanded_premise or "").strip()
        std = _normalize_standardized_seed(std, fallback=seed)
        wc = _word_count(std)
        composite = (
            f"{std}\n\n"
            f"[STRUCTURE FOR PLOTTING — honor this]\n"
            f"{format_outline_for_plot(outline)}"
        )
        print(
            f"    standardized_seed words={wc} "
            f"(target {STANDARDIZED_SEED_MIN_WORDS}–{STANDARDIZED_SEED_MAX_WORDS}) "
            f"preview={std[:100]!r}"
        )
        print(
            f"    outline scenes_target={outline.scene_count_target} "
            f"phases={len(outline.phases or [])} signature={outline.signature_device!r} "
            f"structure={outline.structure_id} pack={style_pack.get('id')}"
        )
        dump = outline.model_dump()
        dump["standardized_seed"] = std
        return {
            "original_user_prompt": original,
            "user_prompt": composite,
            "expanded_prompt": std,  # canonical short-dense seed
            "story_outline": dump,
            "scene_count_target": outline.scene_count_target,
            "signature_device": outline.signature_device,
            "structure_meta": structure,
            "style_pack": style_pack.get("id"),
            "style_pack_meta": style_pack,
            "current_phase": "plot_weaving",
            "last_agent_output": (
                f"Prompt standardized ({wc}w short-dense; "
                f"signature={outline.signature_device[:40]!r}, "
                f"structure={structure.get('id')}, pack={style_pack.get('id')})."
            ),
        }
    except Exception as e:
        print(f"    [warn] prompt standardize failed, using original seed: {e}")
        return {
            "original_user_prompt": original,
            "user_prompt": seed,
            "expanded_prompt": None,
            "story_outline": None,
            "scene_count_target": budget["ideal"],
            "signature_device": None,
            "structure_meta": structure,
            "style_pack": style_pack.get("id"),
            "style_pack_meta": style_pack,
            "current_phase": "plot_weaving",
            "last_agent_output": f"Prompt standardize failed; using seed. ({e})",
        }
