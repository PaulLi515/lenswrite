"""Phase A: build a screenplay-like structured StoryCore (no literary style)."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from app.agents.prompt_expander import format_outline_for_plot, scene_budget
from app.detail_level import normalize_story_length
from functools import lru_cache

from app.llm import make_chat_openai
from app.models import (
    RawInteractionStory,
    RawStoryScene,
    Scene,
    StoryCore,
    StoryState,
    WorldSetting,
)
from app.personality import format_character_for_prompt
from app.twist import format_twist_for_prompt
from app.voice import pick_structure


@lru_cache(maxsize=1)
def _llm():
    return make_chat_openai(temperature=0.55, timeout=180, max_retries=2)

# Hard cap: cinematic focus_chain on at most this many beats in the whole core
FOCUS_CHAIN_MAX = 2


def _core_to_raw(core: StoryCore) -> RawInteractionStory:
    """Legacy mirror for any code still reading raw_interaction_story."""
    scenes: list[RawStoryScene] = []
    for i, s in enumerate(core.scenes or [], start=1):
        actions = [
            b.content
            for b in (s.beats or [])
            if b.beat_type in ("action", "discovery", "transition")
        ]
        dialogues = [
            f"{b.character or '?'}: {b.content}"
            for b in (s.beats or [])
            if b.beat_type == "dialogue"
        ]
        discoveries = [
            b.content for b in (s.beats or []) if b.beat_type == "discovery"
        ]
        scenes.append(
            RawStoryScene(
                scene_number=i,
                summary=s.scene_goal or s.location,
                key_interactions=discoveries or actions[:4],
                emotional_tone="neutral",
                important_dialogue_or_actions="; ".join(dialogues[:4] or actions[:3]),
            )
        )
    return RawInteractionStory(
        premise=core.premise_summary,
        scenes=scenes,
        overall_conflict=core.overall_conflict or "",
        character_arcs=dict(core.character_arcs or {}),
    )


def _format_world(world) -> str:
    if world is None:
        return "(no world card)"
    if hasattr(world, "model_dump"):
        d = world.model_dump()
    elif isinstance(world, dict):
        d = world
    else:
        return str(world)
    lines = []
    for key, label in (
        ("title", "Title"),
        ("time_period", "Time"),
        ("location", "Location"),
        ("atmosphere", "Atmosphere"),
        ("social_context", "Social context"),
        ("rules_or_constraints", "Constraints"),
        ("conflicts_latent", "Latent conflicts"),
        ("additional_notes", "Notes"),
    ):
        val = d.get(key)
        if val:
            lines.append(f"{label}: {val}")
    places = d.get("key_places") or []
    if places:
        lines.append("Key places: " + "; ".join(str(p) for p in places))
    return "\n".join(lines) or "(empty world card)"


def _normalize_core(core: StoryCore) -> StoryCore:
    """Ensure scene_ids, beat counts, and focus_chain budget are usable after LLM output."""
    scenes: list[Scene] = []
    focus_kept = 0
    for i, s in enumerate(core.scenes or [], start=1):
        sid = (s.scene_id or "").strip() or f"S{i}"
        beats = list(s.beats or [])
        # Soft clamp: keep structure even if model overshoots
        if len(beats) > 12:
            beats = beats[:12]
        # Cap total focus_chain beats across the whole core
        new_beats = []
        for b in beats:
            fc = list(b.focus_chain or []) if b.focus_chain else []
            if fc:
                if focus_kept >= FOCUS_CHAIN_MAX:
                    b = b.model_copy(update={"focus_chain": None})
                else:
                    focus_kept += 1
            new_beats.append(b)
        beats = new_beats
        scenes.append(
            s.model_copy(
                update={
                    "scene_id": sid,
                    "location": (s.location or "unspecified location").strip(),
                    "setting_backdrop": (s.setting_backdrop or s.location or "").strip(),
                    "scene_goal": (s.scene_goal or "advance the conflict").strip(),
                    "beats": beats,
                }
            )
        )
    return core.model_copy(update={"scenes": scenes})


def _signature_from_state(state: StoryState) -> str:
    sig = (state.get("signature_device") or "").strip()
    if sig:
        return sig
    outline = state.get("story_outline")
    if outline is None:
        return ""
    if hasattr(outline, "model_dump"):
        d = outline.model_dump()
    elif isinstance(outline, dict):
        d = outline
    else:
        return ""
    return (d.get("signature_device") or "").strip()


def plot_weaver_node(state: StoryState) -> dict:
    print(">>> [plot_weaver_node] 开始执行 (Phase A screenplay StoryCore)")
    characters = state.get("characters") or []
    characters_info = "\n\n".join(format_character_for_prompt(c) for c in characters) or "(none)"
    world_info = _format_world(state.get("world_setting"))
    length = normalize_story_length(state.get("story_length"))
    budget = scene_budget(length)
    scene_target = state.get("scene_count_target") or budget["ideal"]
    try:
        scene_target = int(scene_target)
    except (TypeError, ValueError):
        scene_target = budget["ideal"]
    scene_target = max(budget["min"], min(budget["max"], scene_target))

    outline_block = format_outline_for_plot(state.get("story_outline"))
    original = (state.get("original_user_prompt") or "").strip()
    expanded = (state.get("expanded_prompt") or "").strip()
    # Prefer explicit expanded + outline; fall back to user_prompt (may already be composite)
    plot_prompt = expanded or (state.get("user_prompt") or "")
    signature = _signature_from_state(state)
    structure = state.get("structure_meta") or pick_structure(
        original or plot_prompt or ""
    )
    print(
        f"    characters={len(characters)} length={length} "
        f"scene_target={scene_target} outline={'yes' if state.get('story_outline') else 'no'} "
        f"signature={signature[:50]!r} structure={structure.get('id')}"
    )

    system = f"""You are a professional story editor writing a SCREENPLAY-LIKE plot skeleton.
Output a structured StoryCore — NOT literary prose.

SCHEMA (required):
- premise_summary: 1–3 factual sentences
- scenes: ordered list of Scene
  - scene_id: "S1", "S2", ...
  - location: concrete place
  - setting_backdrop: environment / time / atmosphere (factual, not lyrical)
  - scene_goal: narrative function of this scene
  - characters_present: names present
  - beats: list of Beat (about 4–8 per scene)
    - beat_type: "action" | "dialogue" | "discovery" | "transition"
    - character: speaker/actor or null
    - content: concise executable line (action or dialogue gist) — LITTLE rhetoric
    - focus_chain: OPTIONAL list of focus steps ONLY on key cinematic moments
      (opening establish, danger escalate, discovery of object, scene turn).
      Example: ["street outside window", "private object rim", "unread note"].
      OMIT focus_chain on ordinary dialogue / routine action.
      HARD CAP: at most {FOCUS_CHAIN_MAX} beats in the WHOLE core may have focus_chain.

Also fill:
- overall_conflict
- ending_facts (irreversible outcomes)
- character_arcs (factual)
- forbidden_changes
- setting (from world card)

HARD RULES:
1. NO literary style instructions or purple description in beats.
2. Every scene MUST have location + setting_backdrop + scene_goal.
3. Beats MUST include both character actions and dialogue (not only summaries).
4. Granularity: ~4–8 beats per scene — NOT every glance; NOT one-sentence whole scene.
5. discovery beats mark hard reveals that any rendering must keep.
6. Respect world card places/constraints.
7. Use character speech habits only as constraints on WHAT they would say/do in gist form
   (full prose voice is for the later renderer).
8. Aim for about {scene_target} scenes (allowed range {budget['min']}–{budget['max']} for story_length={length}).
9. If an EXPANDED OUTLINE is provided: honor its phases, must_include, ending_direction,
   and must_not_invent. Do not invent forbidden content. Cover every phase with at least one scene.
10. Prefer information density in beats (objects, decisions, who acts) over summary.
11. SIGNATURE DEVICE (BINDING when provided): make it concrete in beats/objects/rules —
    it must appear in at least one discovery or action beat and affect the ending_facts.
12. STRUCTURE GUIDANCE: honor the structure pack (scene shape / timeline / dialogue density).
13. Do NOT default to café counter / coin tray / ledger / cup-size stacking choreography
    unless the seed explicitly requires it. Prefer private objects from character image systems.
14. If USER TWISTS are provided: each is a BINDING HIDDEN FACT with DEPTH.
    For EVERY twist, encode at least THREE beats near the insertion moment:
      (a) discovery: layered sensory/object evidence (not one vague noun)
      (b) reaction: focal character's stress_response / broken speech_pattern (private, unique)
      (c) consequence action: one small irreversible behavior change only this person would make
    Filter evidence through that character's attention_bias and image_system.
    NEVER a dialogue line that recites the full fact; NEVER restate the user's twist wording.
    ending_facts: plain factual form for fidelity (may name outcomes) but beat content stays stageable.
    Multiple twists → separate beat triples; do not collapse two facts into one beat.
"""

    twist_block = format_twist_for_prompt(state)

    human = """Original user seed (do not contradict):
{original_seed}

Expanded premise / plotting brief:
{plot_prompt}

Structured outline (if any — BINDING when present):
{outline_block}

SIGNATURE DEVICE (must appear as concrete beats):
{signature_device}

STRUCTURE PACK:
id={structure_id} — {structure_guidance}

{twist_block}

World card (honor places & constraints):
{world_info}

Characters (enriched profiles — use for conflict, speech gist, stress beats, private images):
{characters_info}

Scene count target: {scene_target}
Produce a complete structured StoryCore. focus_chain on at most {focus_max} beats total."""

    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | _llm().with_structured_output(StoryCore)
    )

    core: StoryCore = chain.invoke({
        "original_seed": original or plot_prompt or "(none)",
        "plot_prompt": plot_prompt or original or "",
        "outline_block": outline_block,
        "signature_device": signature or "(none — invent one private device and use it)",
        "structure_id": structure.get("id") or "",
        "structure_guidance": structure.get("guidance") or "",
        "twist_block": twist_block,
        "world_info": world_info,
        "characters_info": characters_info,
        "scene_target": scene_target,
        "focus_max": FOCUS_CHAIN_MAX,
    })
    core = _normalize_core(core)

    # Prefer user world when LLM setting empty
    world = state.get("world_setting")
    if world is not None and isinstance(world, WorldSetting):
        if not (core.setting and (core.setting.location or core.setting.time_period)):
            core = core.model_copy(update={"setting": world})

    n = len(core.scenes or [])
    n_beats = sum(len(s.beats or []) for s in core.scenes or [])
    n_focus = sum(
        1
        for s in core.scenes or []
        for b in (s.beats or [])
        if b.focus_chain
    )
    print(
        f"    story_core scenes={n} beats={n_beats} focus_chains={n_focus} "
        f"ending_facts={len(core.ending_facts or [])}"
    )
    if n < 3:
        print("    [warn] core has fewer than 3 scenes")

    raw = _core_to_raw(core)
    return {
        "story_core": core,
        "raw_interaction_story": raw,
        "core_locked": False,
        "structure_meta": structure,
        "signature_device": signature or state.get("signature_device"),
        "current_phase": "plot_weaving",
        "last_agent_output": (
            f"Plot core woven ({n} scenes, {n_beats} beats, "
            f"focus_chains={n_focus}, structure={structure.get('id')})."
        ),
    }


def lock_core_node(state: StoryState) -> dict:
    """Freeze Phase A StoryCore — scene goals and plot facts must not reopen."""
    print(">>> [lock_core_node] 冻结 StoryCore (structured scenes/beats)")
    core = state.get("story_core")
    if not core:
        print("    [warn] no story_core to lock")
        return {
            "core_locked": True,
            "current_phase": "core_locked",
            "last_agent_output": "Core lock (empty).",
        }
    n = len(core.scenes) if hasattr(core, "scenes") else 0
    forbidden = list(getattr(core, "forbidden_changes", None) or [])
    # Ensure lock semantics are explicit in forbidden_changes for the renderer
    lock_rules = [
        "Do not change scene_goal of any scene",
        "Do not drop discovery beats or ending_facts",
        "Do not reorder major outcomes",
    ]
    merged = list(dict.fromkeys(forbidden + lock_rules))
    if hasattr(core, "model_copy"):
        core = core.model_copy(update={"forbidden_changes": merged})
    print(f"    locked scenes={n} forbidden={len(merged)}")
    return {
        "story_core": core,
        "core_locked": True,
        "current_phase": "core_locked",
        "last_agent_output": f"StoryCore locked ({n} scenes).",
    }
