"""Phase B: render frozen StoryCore — segmented by scene, then length expand/trim."""
from __future__ import annotations

import re
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.acceptance import (
    EXPAND_HARD_RULES,
    TRIM_HARD_RULES,
    format_info_expand_brief,
    probe_enrichment_in_prose,
    probe_info_coverage,
)
from app.detail_level import (
    clamp_detail_level,
    detail_guidance,
    detail_label,
    detail_llm_budget,
    detail_scene_hint,
    length_guidance,
    normalize_story_length,
    product_word_band,
    story_length_label,
    word_target_band,
)
from app.llm import make_chat_openai
from app.models import FinalShortStory, NarrativePerspective, StoryState
from app.narrative_prefs import (
    normalize_perspective,
    normalize_style,
    perspective_guidance,
    perspective_label,
    resolve_focal_character,
    style_guidance,
    style_label,
)
from app.personality import format_character_for_prompt
from app.twist import format_twist_card_for_renderer
from app.voice import dual_draft_score, format_voice_card, pick_style_pack


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _core_data(core) -> dict:
    if core is None:
        return {}
    if hasattr(core, "model_dump"):
        return core.model_dump()
    if isinstance(core, dict):
        return core
    return {}


def _scenes_list(core) -> list[dict]:
    data = _core_data(core)
    return list(data.get("scenes") or [])


def _format_one_scene(s: dict, idx: int) -> str:
    sid = s.get("scene_id") or f"S{s.get('scene_number') or idx}"
    location = s.get("location") or s.get("slugline") or "(location?)"
    backdrop = s.get("setting_backdrop") or s.get("summary") or ""
    goal = s.get("scene_goal") or s.get("dramatic_turn") or ""
    present = s.get("characters_present") or s.get("participants") or []
    lines = [
        f"## {sid} @ {location}",
        f"Backdrop: {backdrop}",
        f"Scene goal: {goal}",
        f"Present: {', '.join(present)}",
        "Beats:",
    ]
    beats = s.get("beats") or []
    if beats:
        for j, b in enumerate(beats, start=1):
            btype = b.get("beat_type") or "action"
            who = b.get("character") or "—"
            content = b.get("content") or ""
            focus = b.get("focus_chain")
            line = f"  {j}. [{btype}] {who}: {content}"
            if focus:
                line += f"  | focus_chain → {' → '.join(focus)}"
            lines.append(line)
    else:
        for b in s.get("action_beats") or []:
            lines.append(f"  • [action] {b}")
        for b in s.get("dialogue_beats") or []:
            lines.append(f"  • [dialogue] {b}")
        for e in s.get("key_events") or []:
            lines.append(f"  ★ [discovery] {e}")
    return "\n".join(lines)


def _format_core_for_prompt(core) -> str:
    data = _core_data(core)
    if not data:
        return "(no story core)"
    lines = [
        f"PREMISE: {data.get('premise_summary', '')}",
        f"CONFLICT: {data.get('overall_conflict', '')}",
        "ENDING FACTS (must keep):",
    ]
    for f in data.get("ending_facts") or []:
        lines.append(f"  - {f}")
    if data.get("forbidden_changes"):
        lines.append("FORBIDDEN CHANGES:")
        for f in data["forbidden_changes"]:
            lines.append(f"  - {f}")
    lines.append("\n=== SCENES ===")
    for idx, s in enumerate(data.get("scenes") or [], start=1):
        lines.append("")
        lines.append(_format_one_scene(s, idx))
    arcs = data.get("character_arcs") or {}
    if arcs:
        lines.append("\nCHARACTER ARCS:")
        for k, v in arcs.items():
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _format_core_header(core) -> str:
    data = _core_data(core)
    lines = [
        f"PREMISE: {data.get('premise_summary', '')}",
        f"CONFLICT: {data.get('overall_conflict', '')}",
        "ENDING FACTS:",
    ]
    for f in data.get("ending_facts") or []:
        lines.append(f"  - {f}")
    return "\n".join(lines)


CAMERA_RULES = """
**CAMERA / LENS LANGUAGE (controlled — NOT every paragraph)**
Use cinematic focus ONLY at key moments: opening establish, danger escalate,
discovery of an important object, scene turn / exit.
If a beat has focus_chain, follow it. Do NOT force exterior→hand→object every paragraph.
HARD LIMIT: at most 1–2 focus_chain moments in the WHOLE story; ordinary beats = no lens grammar.
Dialogue-dense stretches stay clean.
"""

ENRICHMENT_RULES = """
**CHARACTER ENRICHMENT (must obey)**
- Description through attention_bias first.
- Dialogue follows speech_pattern and rhythm_signature.
- Tension shows stress_response.
- Recur private image_system objects (not generic café props unless core requires).
- Respect taboo_register; do not break avoidances unless a core beat explicitly breaks them.
"""

TWIST_REVEAL_RULES = """
**TWIST REVEALS (when twist facts exist — depth + character-bound)**
- Twist content is a hidden plot fact, NOT a line of finished prose.
- Do NOT paste, closely paraphrase, or announce the twist in one explanatory sentence.
- Do NOT have characters state the full twist as thesis dialogue.
- Each twist needs DEPTH (not a single nod):
  (1) layered sensory evidence the focal notices via attention_bias / image_system
  (2) that character's private stress_response in the body (unique, not generic shock)
  (3) one irreversible small behavior change only they would make
- Stage order: evidence → body reaction → incomplete inference → changed action.
- Prefer implication; let the reader assemble the turn.
- Never write "she realized that…" followed by the user's twist wording.
- Second/later twists need their own evidence layer (do not reuse the same shallow object).
"""


class SceneProse(BaseModel):
    """One scene's finished prose (no title)."""

    content: str = Field(..., description="Full prose for this scene only")


class StoryMeta(BaseModel):
    title: str
    chosen_perspective: NarrativePerspective
    literary_techniques_used: List[str] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    suggested_improvements: Optional[List[str]] = None


class ExpandedDraft(BaseModel):
    """Length-only expansion of a full draft (no plot change)."""

    content: str = Field(..., description="Expanded full story prose")
    title: str = Field(..., description="Keep the same title unless clearly wrong")


class TrimmedDraft(BaseModel):
    content: str
    title: str


def _make_llm(detail_level: int, *, temperature: float = 0.65, max_tokens: int | None = None):
    budget = detail_llm_budget(detail_level)
    return make_chat_openai(
        temperature=temperature,
        timeout=budget["timeout"],
        max_retries=2,
        max_tokens=max_tokens or budget["max_tokens"],
    )


def _render_one_scene(
    *,
    scene: dict,
    scene_index: int,
    n_scenes: int,
    scene_word_target: int,
    core_header: str,
    characters_info: str,
    voice_card: str,
    style: str,
    preferred_perspective: str,
    focal: str,
    detail_level: int,
    s_label: str,
    p_label: str,
    style_text: str,
    perspective_text: str,
    detail_text: str,
    d_label: str,
    prior_tail: str,
    draft_hint: str = "",
    temperature: float = 0.65,
) -> str:
    scene_text = _format_one_scene(scene, scene_index)
    extra = f"\n{draft_hint}\n" if draft_hint else ""
    system = f"""You render ONE scene of a frozen StoryCore into literary prose.
You do NOT invent plot outside this scene's beats.
You do NOT write the whole story — only this scene.

{ENRICHMENT_RULES}
{CAMERA_RULES}
{TWIST_REVEAL_RULES}

{voice_card}
{extra}
Style: {style_text}
Perspective: {perspective_text}
Detail grain {detail_level}/5 — {d_label}:
{detail_text}

LENGTH for THIS scene: enough to fully render every beat (roughly
{max(100, scene_word_target - 50)}–{scene_word_target + 80} words as a soft guide only).
Expand beat-by-beat (1–3 sentences per beat; dialogue + reaction).
Information first: each beat must be a lived moment, not a one-line summary.
Discovery/twist beats: dramatize evidence — never paste hidden-fact wording.
No new major plot. No empty camera stacks. Obey the VOICE CARD.
"""
    human = f"""Story context (global — do not contradict):
{core_header}

Characters:
{characters_info}

Render params: style={s_label}; POV={p_label}; focal={focal}
Scene {scene_index}/{n_scenes}:

{scene_text}

Previous scene ending (for continuity only; do not rewrite it):
{prior_tail or "(this is the opening scene)"}

Write ONLY this scene's prose. Continue naturally from the prior ending if any.
Do not add a title. Do not summarize other scenes."""

    llm = _make_llm(
        detail_level,
        temperature=temperature,
        max_tokens=min(4000, detail_llm_budget(detail_level)["max_tokens"]),
    )
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | llm.with_structured_output(SceneProse)
    )
    piece: SceneProse = chain.invoke({})
    return (piece.content or "").strip()


def _render_segmented(
    *,
    core,
    characters_info: str,
    voice_card: str,
    style: str,
    preferred_perspective: str,
    focal: str,
    detail_level: int,
    word_band: dict,
    s_label: str,
    p_label: str,
    style_text: str,
    perspective_text: str,
    detail_text: str,
    d_label: str,
    draft_hint: str = "",
    temperature: float = 0.65,
    label: str = "A",
) -> tuple[str, list[int]]:
    """Render each scene separately; return stitched prose + per-scene word counts."""
    scenes = _scenes_list(core)
    if not scenes:
        raise RuntimeError("StoryCore has no scenes to render")

    n = len(scenes)
    ideal = int(word_band["ideal"])
    # Leave headroom for continuity; per-scene target sums near ideal
    per = max(150, ideal // n)
    core_header = _format_core_header(core)
    parts: list[str] = []
    counts: list[int] = []
    prior_tail = ""

    print(
        f"    segmented render [{label}]: {n} scenes × ~{per} words/scene "
        f"(ideal total {ideal}) temp={temperature}"
    )
    for i, scene in enumerate(scenes, start=1):
        prose = _render_one_scene(
            scene=scene,
            scene_index=i,
            n_scenes=n,
            scene_word_target=per,
            core_header=core_header,
            characters_info=characters_info,
            voice_card=voice_card,
            style=style,
            preferred_perspective=preferred_perspective,
            focal=focal,
            detail_level=detail_level,
            s_label=s_label,
            p_label=p_label,
            style_text=style_text,
            perspective_text=perspective_text,
            detail_text=detail_text,
            d_label=d_label,
            prior_tail=prior_tail,
            draft_hint=draft_hint,
            temperature=temperature,
        )
        wc = _count_words(prose)
        counts.append(wc)
        parts.append(prose)
        # last ~400 chars for continuity
        prior_tail = prose[-400:] if prose else ""
        print(f"      scene {i}/{n} words={wc}")

    stitched = "\n\n".join(p for p in parts if p)
    return stitched, counts


def _attach_meta(
    content: str,
    *,
    core,
    characters_info: str,
    preferred_perspective: str,
    focal: str,
    detail_level: int,
    s_label: str,
    p_label: str,
) -> FinalShortStory:
    system = """Given finished story prose and its frozen core, output title + metadata only.
Do not rewrite the story body. Title should fit the story.
Set chosen_perspective type and focal_character correctly."""
    human = f"""Core premise: {_core_data(core).get('premise_summary', '')}
POV requested: {p_label}; focal={focal}
Style: {s_label}

Story prose:
{content[:6000]}

Return StoryMeta (title, chosen_perspective, techniques, strengths)."""
    llm = _make_llm(detail_level, temperature=0.4, max_tokens=1500)
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | llm.with_structured_output(StoryMeta)
    )
    try:
        meta: StoryMeta = chain.invoke({})
    except Exception as e:
        print(f"    [warn] meta attach failed: {e}")
        meta = StoryMeta(
            title="Untitled",
            chosen_perspective=NarrativePerspective(
                perspective_type=(
                    "first_person"
                    if preferred_perspective == "first_person"
                    else "third_limited"
                ),
                focal_character=focal,
                rationale="default",
            ),
            literary_techniques_used=[],
            strengths=[],
        )

    expected_type = (
        "first_person" if preferred_perspective == "first_person" else "third_limited"
    )
    try:
        cp = meta.chosen_perspective.model_copy(
            update={"perspective_type": expected_type, "focal_character": focal}
        )
    except Exception:
        cp = NarrativePerspective(
            perspective_type=expected_type,
            focal_character=focal,
            rationale="normalized",
        )

    actual = _count_words(content)
    return FinalShortStory(
        title=meta.title or "Untitled",
        content=content,
        word_count=actual,
        chosen_perspective=cp,
        literary_techniques_used=list(meta.literary_techniques_used or []),
        strengths=list(meta.strengths or []),
        suggested_improvements=meta.suggested_improvements,
    )


def _expand_info_units(
    story: FinalShortStory,
    *,
    core,
    characters_info: str,
    voice_card: str,
    info_probe: dict,
    detail_level: int,
    s_label: str,
    p_label: str,
    focal: str,
    style_text: str,
    perspective_text: str,
) -> FinalShortStory:
    """Expand only under-covered information units (not word-floor padding)."""
    if info_probe.get("info_ok", True):
        return story
    actual = _count_words(story.content)
    targets = info_probe.get("expand_targets") or []
    brief = format_info_expand_brief(info_probe)
    print(
        f"    info expand pass: words={actual} "
        f"missing={info_probe.get('n_missing')} thin={info_probe.get('n_thin')} "
        f"targets={len(targets)}"
    )
    core_text = _format_core_for_prompt(core)
    system = f"""You are an information expander for a literary short story.
The StoryCore is FROZEN. You may NOT change plot outcomes, discovery facts, or ending facts.
Your job: develop MISSING or THIN information units into lived moments
(action + reaction, dialogue + reply, concrete sensory detail).
Word count is NOT a target — stop when the listed units are fully present.

{EXPAND_HARD_RULES}
{TWIST_REVEAL_RULES}

For twist gaps: DRAMATIZE evidence — never paste or closely restate the hidden-fact wording.

Also make enrichment visible in prose:
- Filter description through attention_bias of the focal character
- Keep dialogue in speech_pattern / rhythm_signature
- Show stress_response under tension
- Recur private image_system objects

{ENRICHMENT_RULES}
{voice_card}
Style: {style_text}
Perspective: {perspective_text}
"""
    human = f"""FROZEN CORE (facts only):
{core_text}

Characters (enrichment must remain visible after expand):
{characters_info}

INFO COVERAGE GAPS:
{brief}

Current draft ({actual} words) — develop the thin/missing units above.
Title: {story.title}

{story.content}

Return ExpandedDraft with the full prose. Keep the title unless clearly wrong.
POV={p_label}; focal={focal}; style={s_label}.
Do not pad for length. Do not invent plot. Do not quote twist wording. Obey VOICE CARD."""

    llm = _make_llm(detail_level, temperature=0.55, max_tokens=detail_llm_budget(detail_level)["max_tokens"])
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | llm.with_structured_output(ExpandedDraft)
    )
    try:
        expanded: ExpandedDraft = chain.invoke({})
        content = (expanded.content or "").strip()
        title = (expanded.title or story.title).strip()
        if not content:
            return story
        out = story.model_copy(
            update={"content": content, "title": title, "word_count": _count_words(content)}
        )
        print(f"    info expand done words={out.word_count}")
        return out
    except Exception as e:
        print(f"    [warn] info expand failed: {e}")
        return story


def _trim_padding_only(
    story: FinalShortStory,
    *,
    core,
    voice_card: str,
    detail_level: int,
    soft_ceiling: int | None = None,
) -> FinalShortStory:
    """Optional de-padding when draft is very long and likely redundant.

    Not a hard product ceiling — only fires well above a soft guide.
    """
    actual = _count_words(story.content)
    # Soft: only trim if clearly bloated (well past former guide)
    ceiling = int(soft_ceiling or 1600)
    if actual <= ceiling:
        return story
    print(f"    padding trim pass: {actual} → aim leaner (soft ceiling ~{ceiling})")
    core_text = _format_core_for_prompt(core)
    system = f"""Tighten a literary short story that has redundant padding.
Remove repetition and empty atmosphere. Keep all information units.

{TRIM_HARD_RULES}

Do not invent plot. Do not drop discoveries, ending facts, or user twists.
Preserve speech_pattern / stress_response / private image traces already in the draft.
Keep the signature device and voice card laws when cutting.

{voice_card}"""
    human = f"""FROZEN CORE (must keep discoveries + ending facts + twists):
{core_text}

Draft ({actual} words) — TRIM padding only:
Title: {story.title}

{story.content}

Return TrimmedDraft. Prefer remove repetition over cutting plot-bearing lines."""
    llm = _make_llm(detail_level, temperature=0.35, max_tokens=detail_llm_budget(detail_level)["max_tokens"])
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | llm.with_structured_output(TrimmedDraft)
    )
    try:
        trimmed: TrimmedDraft = chain.invoke({})
        content = (trimmed.content or "").strip()
        if not content:
            return story
        out = story.model_copy(
            update={
                "content": content,
                "title": (trimmed.title or story.title).strip(),
                "word_count": _count_words(content),
            }
        )
        print(f"    padding trim done words={out.word_count}")
        return out
    except Exception as e:
        print(f"    [warn] padding trim failed: {e}")
        return story


def _render_whole_with_notes(
    *,
    core_text: str,
    characters_info: str,
    voice_card: str,
    revision_notes: list,
    detail_level: int,
    word_band: dict,
    s_label: str,
    p_label: str,
    len_label: str,
    story_length: str,
    d_label: str,
    style_text: str,
    perspective_text: str,
    detail_text: str,
    scene_hint: dict,
    length_text: str,
    focal: str,
    preferred_perspective: str,
    voice_revise_only: bool = False,
) -> FinalShortStory:
    """Full-story re-render when evaluator left revision notes."""
    voice_mode = ""
    if voice_revise_only:
        voice_mode = """
**VOICE-ONLY REVISION**
Keep plot, discoveries, ending facts, scene order identical.
Rewrite diction, rhythm, private images, and dialogue color to match VOICE CARD.
Strip house-style café/ledger defaults. Do not invent new plot.
"""
    system_prompt = f"""You are a literary renderer. You do NOT invent a new plot.
You RENDER a frozen screenplay-like StoryCore as finished prose.

**HARD CONSTRAINTS — STORY CORE FIDELITY**
1. Cover every scene: location, setting_backdrop, scene_goal.
2. Execute beats in order. Preserve discovery beats and ENDING FACTS.
3. No new major plot.

**BEAT-BY-BEAT EXPANSION**
Each beat → 1–3 sentences; dialogue + reaction. Do not collapse multi-beat scenes.

{ENRICHMENT_RULES}
{CAMERA_RULES}

{voice_card}
{voice_mode}
**LENGTH**
{length_text}

**Detail {detail_level}/5 — {d_label}**
{detail_text}
{scene_hint["scene_focus"]}

**Style**: {style_text}
**Perspective**: {perspective_text}

This is a REVISION. Fix evaluator notes without inventing a different plot.
Quality first: never invent plot only for word count.
If feedback mentions stiff/quoted twists: re-stage as evidence + reaction; keep the same fact.
{TWIST_REVEAL_RULES}
"""
    notes_text = "\n".join(f"- {n}" for n in revision_notes)
    human = f"""FROZEN STORY CORE:
{core_text}

Characters:
{characters_info}

Render params: style={s_label}; POV={p_label}; focal={focal};
soft length guide {word_band['min']}–{word_band['max']} (information first)

Evaluator feedback (address all):
{notes_text}

Write the complete short story as FinalShortStory JSON.
Obey VOICE CARD. Dramatize twists — do not paste hidden-fact wording."""

    llm = _make_llm(detail_level)
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", human),
        ])
        | llm.with_structured_output(FinalShortStory)
    )
    final_story = chain.invoke({})
    actual = _count_words(final_story.content)
    expected_type = (
        "first_person" if preferred_perspective == "first_person" else "third_limited"
    )
    try:
        cp = final_story.chosen_perspective.model_copy(
            update={"perspective_type": expected_type, "focal_character": focal}
        )
        final_story = final_story.model_copy(
            update={"word_count": actual, "chosen_perspective": cp}
        )
    except Exception:
        final_story = final_story.model_copy(update={"word_count": actual})
    return final_story


def _score_draft_candidate(
    prose: str,
    *,
    characters: list,
    focal: str,
    word_min: int,
    word_max: int,
) -> float:
    wc = _count_words(prose)
    probe = probe_enrichment_in_prose(prose, characters, focal_name=focal)
    hits = int(probe.get("focal_hit_count") or 0)
    return dual_draft_score(
        prose,
        word_count=wc,
        word_min=word_min,
        word_max=word_max,
        enrichment_hits=hits,
    )


def literary_renderer_node(state: StoryState) -> dict:
    print(">>> [literary_renderer_node] Phase B (voice card + dual draft + expand/trim)")

    detail_level = clamp_detail_level(state.get("detail_level", 3))
    story_length = normalize_story_length(state.get("story_length"))
    word_band = word_target_band(story_length)  # product 900–1300
    length_text = length_guidance(story_length)
    style = normalize_style(state.get("style"))
    preferred_perspective = normalize_perspective(state.get("preferred_perspective"))
    characters = state.get("characters") or []
    focal = resolve_focal_character(
        state.get("focal_character"),
        characters,
        state.get("user_characters"),
    )
    core = state.get("story_core")
    core_text = _format_core_for_prompt(core)

    detail_text = detail_guidance(detail_level)
    d_label = detail_label(detail_level)
    scene_hint = detail_scene_hint(detail_level)
    style_text = style_guidance(style)
    perspective_text = perspective_guidance(preferred_perspective, focal)
    s_label = style_label(style)
    p_label = perspective_label(preferred_perspective, focal)
    len_label = story_length_label(story_length)
    characters_info = "\n\n".join(format_character_for_prompt(c) for c in characters) or "(none)"

    seed = (
        (state.get("original_user_prompt") or state.get("user_prompt") or state.get("story_seed") or "")
        .strip()
    )
    style_pack = state.get("style_pack_meta") or pick_style_pack(
        seed, style, force_id=state.get("style_pack")
    )
    signature = (state.get("signature_device") or "").strip() or None
    structure = state.get("structure_meta")
    voice_card = format_voice_card(
        characters,
        focal=focal,
        style_pack=style_pack,
        signature_device=signature,
        structure=structure,
    )
    twist_card = format_twist_card_for_renderer(
        state.get("twists"),
        characters=characters,
        focal=focal,
    )
    if twist_card:
        voice_card = f"{voice_card}\n\n{twist_card}"
    dual_draft = state.get("dual_draft") is not False  # default True
    voice_revise_only = bool(state.get("voice_revise_only"))

    iteration_count = (state.get("iteration_count") or 0) + 1
    revision_notes = list(state.get("revision_notes") or [])
    print(
        f"    iter={iteration_count} style={style} pov={preferred_perspective} "
        f"focal={focal} detail={detail_level} band={word_band['min']}-{word_band['max']} "
        f"notes={len(revision_notes)} dual_draft={dual_draft} "
        f"pack={style_pack.get('id')} signature={signature!r} core={'yes' if core else 'no'}"
    )

    # --- Pass A: draft ---
    if revision_notes:
        print(f"    path=whole-story revision voice_only={voice_revise_only}")
        final_story = _render_whole_with_notes(
            core_text=core_text,
            characters_info=characters_info,
            voice_card=voice_card,
            revision_notes=revision_notes,
            detail_level=detail_level,
            word_band=word_band,
            s_label=s_label,
            p_label=p_label,
            len_label=len_label,
            story_length=story_length,
            d_label=d_label,
            style_text=style_text,
            perspective_text=perspective_text,
            detail_text=detail_text,
            scene_hint=scene_hint,
            length_text=length_text,
            focal=focal,
            preferred_perspective=preferred_perspective,
            voice_revise_only=voice_revise_only,
        )
    else:
        print("    path=segmented scenes" + (" + dual_draft" if dual_draft else ""))
        try:
            stitched_a, counts_a = _render_segmented(
                core=core,
                characters_info=characters_info,
                voice_card=voice_card,
                style=style,
                preferred_perspective=preferred_perspective,
                focal=focal,
                detail_level=detail_level,
                word_band=word_band,
                s_label=s_label,
                p_label=p_label,
                style_text=style_text,
                perspective_text=perspective_text,
                detail_text=detail_text,
                d_label=d_label,
                temperature=0.65,
                label="A",
            )
            score_a = _score_draft_candidate(
                stitched_a,
                characters=characters,
                focal=focal,
                word_min=int(word_band["min"]),
                word_max=int(word_band["max"]),
            )
            print(
                f"    draft A words={_count_words(stitched_a)} "
                f"score={score_a} per_scene={counts_a}"
            )
            stitched = stitched_a
            pick_label = "A"

            if dual_draft:
                try:
                    stitched_b, counts_b = _render_segmented(
                        core=core,
                        characters_info=characters_info,
                        voice_card=voice_card,
                        style=style,
                        preferred_perspective=preferred_perspective,
                        focal=focal,
                        detail_level=detail_level,
                        word_band=word_band,
                        s_label=s_label,
                        p_label=p_label,
                        style_text=style_text,
                        perspective_text=perspective_text,
                        detail_text=detail_text,
                        d_label=d_label,
                        draft_hint=(
                            "CANDIDATE B: same frozen plot facts, but lean harder into "
                            "private image_system, rhythm_signature, and signature_device. "
                            "Avoid house-style defaults even more aggressively. "
                            "Different sentence music within the same beats."
                        ),
                        temperature=0.82,
                        label="B",
                    )
                    score_b = _score_draft_candidate(
                        stitched_b,
                        characters=characters,
                        focal=focal,
                        word_min=int(word_band["min"]),
                        word_max=int(word_band["max"]),
                    )
                    print(
                        f"    draft B words={_count_words(stitched_b)} "
                        f"score={score_b} per_scene={counts_b}"
                    )
                    if score_b > score_a:
                        stitched = stitched_b
                        pick_label = "B"
                    print(f"    dual_draft pick={pick_label} (A={score_a} B={score_b})")
                except Exception as e:
                    print(f"    [warn] dual draft B failed ({e}); keep A")

            final_story = _attach_meta(
                stitched,
                core=core,
                characters_info=characters_info,
                preferred_perspective=preferred_perspective,
                focal=focal,
                detail_level=detail_level,
                s_label=s_label,
                p_label=p_label,
            )
        except Exception as e:
            print(f"    [warn] segmented render failed ({e}); falling back to whole-story")
            final_story = _render_whole_with_notes(
                core_text=core_text,
                characters_info=characters_info,
                voice_card=voice_card,
                revision_notes=["Segmented render failed; produce full faithful draft."],
                detail_level=detail_level,
                word_band=word_band,
                s_label=s_label,
                p_label=p_label,
                len_label=len_label,
                story_length=story_length,
                d_label=d_label,
                style_text=style_text,
                perspective_text=perspective_text,
                detail_text=detail_text,
                scene_hint=scene_hint,
                length_text=length_text,
                focal=focal,
                preferred_perspective=preferred_perspective,
            )

    # --- Pass B: information expand (thin/missing units), not word floor ---
    twists = state.get("twists") or []
    info_probe = probe_info_coverage(final_story.content, core, twists=twists)
    print(f"    pre-expand {info_probe.get('summary')}")
    if not info_probe.get("info_ok", True):
        final_story = _expand_info_units(
            final_story,
            core=core,
            characters_info=characters_info,
            voice_card=voice_card,
            info_probe=info_probe,
            detail_level=detail_level,
            s_label=s_label,
            p_label=p_label,
            focal=focal,
            style_text=style_text,
            perspective_text=perspective_text,
        )
        info_probe = probe_info_coverage(final_story.content, core, twists=twists)
        print(f"    post-expand {info_probe.get('summary')}")
        # Second pass if still thin/missing critical units
        if not info_probe.get("info_ok", True):
            print("    info expand second pass")
            final_story = _expand_info_units(
                final_story,
                core=core,
                characters_info=characters_info,
                voice_card=voice_card,
                info_probe=info_probe,
                detail_level=detail_level,
                s_label=s_label,
                p_label=p_label,
                focal=focal,
                style_text=style_text,
                perspective_text=perspective_text,
            )
            info_probe = probe_info_coverage(final_story.content, core, twists=twists)

    # --- Pass C: soft de-padding only if very long ---
    final_story = _trim_padding_only(
        final_story,
        core=core,
        voice_card=voice_card,
        detail_level=detail_level,
        soft_ceiling=max(int(word_band["max"]) + 300, 1600),
    )

    actual = _count_words(final_story.content)
    final_story = final_story.model_copy(update={"word_count": actual})
    band = product_word_band()
    in_band = band["min"] <= actual <= band["max"]
    info_ok = bool(info_probe.get("info_ok", True))
    print(
        f"    render complete words={actual} info_ok={info_ok} "
        f"soft_guide={band['min']}-{band['max']} in_soft_band={in_band}"
    )

    return {
        "final_short_story": final_story,
        "focal_character": focal,
        "iteration_count": iteration_count,
        "style_pack": style_pack.get("id"),
        "style_pack_meta": style_pack,
        "voice_revise_only": False,  # consume one-shot flag after render
        "current_phase": "rendering",
        "last_agent_output": (
            f"Rendered (iter {iteration_count}, voice+info-expand, "
            f"words={actual}, info_ok={info_ok}, pack={style_pack.get('id')})"
        ),
    }


# Backward-compatible name
literary_novelist_node = literary_renderer_node
