"""Phase B evaluator: StoryCore fidelity + enrichment + camera discipline + style/POV."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.acceptance import (
    build_acceptance,
    format_info_expand_brief,
    probe_enrichment_in_prose,
    probe_info_coverage,
)
from app.detail_level import (
    clamp_detail_level,
    detail_guidance,
    detail_label,
    length_ok,
    length_over_max,
    normalize_story_length,
    story_length_label,
    word_target_band,
)
from app.events import event_queues
from functools import lru_cache

from app.llm import make_chat_openai
from app.models import FinalShortStory, StoryEvaluation, StoryState
from app.narrative_prefs import (
    expected_perspective_type,
    normalize_perspective,
    normalize_style,
    perspective_guidance,
    perspective_label,
    resolve_focal_character,
    style_guidance,
    style_label,
)
from app.personality import format_character_for_prompt
from app.voice import house_fingerprint_score


@lru_cache(maxsize=1)
def _llm():
    return make_chat_openai(temperature=0.4, timeout=90, max_retries=1)

MATCH_THRESHOLD = 7.0
FIDELITY_THRESHOLD = 8.0
VOICE_SOFT_THRESHOLD = 6.0


class RenderCritique(BaseModel):
    scores: Dict[str, float] = Field(
        default_factory=dict,
        description=(
            "prose, character, structure, emotion, detail_match, "
            "style_match, perspective_match, fidelity, "
            "character_behavior_match, camera_discipline, "
            "voice_distinctiveness (each 1-10)"
        ),
    )
    total_score: float = Field(..., description="Average of main dimension scores")
    fidelity_score: float = Field(
        ...,
        description=(
            "1-10: scene goals, discovery beats, ending_facts covered; "
            "no invented major plot"
        ),
    )
    style_match: float = Field(..., description="1-10 style fidelity")
    perspective_match: float = Field(..., description="1-10 POV fidelity")
    character_behavior_match: float = Field(
        ...,
        description=(
            "1-10: dialogue follows speech_pattern; tension uses stress_response; "
            "description filters through attention_bias; avoidances respected"
        ),
    )
    camera_discipline: float = Field(
        ...,
        description=(
            "1-10: focus_chain / lens language only at key moments; "
            "not forced exterior→hand→object every paragraph; clean dialogue stretches"
        ),
    )
    voice_distinctiveness: float = Field(
        default=7.0,
        description=(
            "1-10: private character voice & images; NOT generic café/ledger/align house style; "
            "speech rhythms feel non-interchangeable; signature device present in texture"
        ),
    )
    style_feedback: str = ""
    perspective_feedback: str = ""
    fidelity_feedback: str = Field(..., description="What core facts were kept or missed")
    character_feedback: str = ""
    camera_feedback: str = ""
    voice_feedback: str = ""
    missing_core_facts: List[str] = Field(default_factory=list)
    invented_major_facts: List[str] = Field(default_factory=list)
    revision_notes: List[str] = Field(default_factory=list)


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _score_val(scores: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in scores and scores[k] is not None:
            try:
                return float(scores[k])
            except (TypeError, ValueError):
                continue
    return default


def _observed_perspective(final_story: FinalShortStory) -> Optional[str]:
    cp = getattr(final_story, "chosen_perspective", None)
    if not cp:
        return None
    return getattr(cp, "perspective_type", None)


def _observed_focal(final_story: FinalShortStory) -> Optional[str]:
    cp = getattr(final_story, "chosen_perspective", None)
    if not cp:
        return None
    fc = getattr(cp, "focal_character", None)
    return str(fc).strip() if fc else None


def _perspective_structurally_ok(
    requested: str,
    observed: Optional[str],
    *,
    requested_focal: str,
    observed_focal: Optional[str],
) -> bool:
    if observed and observed != expected_perspective_type(requested):
        return False
    if observed_focal and requested_focal:
        if observed_focal.lower() != requested_focal.lower():
            return False
    return True


def _format_core_checklist(core) -> str:
    if not core:
        return "(no core)"
    data = core.model_dump() if hasattr(core, "model_dump") else (
        core if isinstance(core, dict) else {}
    )
    lines = ["ENDING FACTS:"]
    for f in data.get("ending_facts") or []:
        lines.append(f"  - {f}")
    lines.append("SCENES / GOALS / DISCOVERIES:")
    for idx, s in enumerate(data.get("scenes") or [], start=1):
        sid = s.get("scene_id") or f"S{s.get('scene_number') or idx}"
        loc = s.get("location") or s.get("slugline") or "?"
        goal = s.get("scene_goal") or s.get("dramatic_turn") or "?"
        lines.append(f"  {sid} @ {loc} | goal: {goal}")
        beats = s.get("beats") or []
        if beats:
            for b in beats:
                if b.get("beat_type") == "discovery":
                    lines.append(f"    ★ discovery: {b.get('content')}")
                if b.get("focus_chain"):
                    lines.append(
                        f"    ◎ focus_chain: {' → '.join(b.get('focus_chain') or [])}"
                    )
        else:
            for e in s.get("key_events") or []:
                lines.append(f"    ★ key_event: {e}")
    return "\n".join(lines)


def _format_characters_brief(characters) -> str:
    if not characters:
        return "(no characters)"
    return "\n\n".join(format_character_for_prompt(c) for c in characters)


def _is_better_candidate(*, new_score: float, old_score: float, has_old: bool) -> bool:
    if not has_old:
        return True
    return new_score > old_score


async def story_evaluator_node(state: StoryState) -> dict:
    print("\n" + "=" * 70)
    print(">>> [story_evaluator_node] Phase B render evaluation")

    final_story = state.get("final_short_story")
    if not final_story:
        return {"evaluation": None, "revision_notes": []}

    iteration_count = state.get("iteration_count") or 0
    max_iterations = state.get("max_iterations") or 6
    # revision_threshold removed: do not revise solely because total_score is "low"
    thread_id = state.get("thread_id")
    detail_level = clamp_detail_level(state.get("detail_level", 3))
    d_label = detail_label(detail_level)
    d_guide = detail_guidance(detail_level)
    story_length = normalize_story_length(state.get("story_length"))
    word_band = word_target_band(story_length)
    len_label = story_length_label(story_length)
    style = normalize_style(state.get("style"))
    preferred_perspective = normalize_perspective(state.get("preferred_perspective"))
    characters = state.get("characters") or []
    focal = resolve_focal_character(
        state.get("focal_character"),
        characters,
        state.get("user_characters"),
    )
    core = state.get("story_core")
    core_checklist = _format_core_checklist(core)
    characters_brief = _format_characters_brief(characters)

    s_label = style_label(style)
    p_label = perspective_label(preferred_perspective, focal)
    observed_pov = _observed_perspective(final_story)
    observed_focal = _observed_focal(final_story)
    actual = _count_words(final_story.content)
    words_ok = length_ok(actual, story_length)
    words_too_long = length_over_max(actual, story_length)
    enrichment_probe = probe_enrichment_in_prose(
        final_story.content,
        characters,
        focal_name=focal,
    )
    info_probe = probe_info_coverage(
        final_story.content,
        core,
        twists=state.get("twists"),
    )
    house_fp = house_fingerprint_score(final_story.content)
    signature = (state.get("signature_device") or "").strip() or "(none)"
    style_pack_id = (
        (state.get("style_pack") or "")
        or ((state.get("style_pack_meta") or {}).get("id") if isinstance(state.get("style_pack_meta"), dict) else "")
        or "(none)"
    )

    print(
        f"[DEBUG] max_iter={max_iterations} style={style} pov={preferred_perspective} "
        f"focal={focal} words={actual} band={word_band['min']}-{word_band['max']} "
        f"(soft) floor_ok={words_ok} over_max={words_too_long} "
        f"info={info_probe.get('summary')} "
        f"enrich={enrichment_probe.get('summary')} house_fp={house_fp}"
    )

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You evaluate a RENDERED short story against a FROZEN screenplay-like StoryCore "
            "and enriched character profiles.\n"
            "Score 1-10 on: prose, character, structure, emotion, detail_match, "
            "style_match, perspective_match, fidelity, character_behavior_match, "
            "camera_discipline, voice_distinctiveness.\n\n"
            "fidelity = scene goals honored; discovery beats & ending_facts present; "
            "no invented major plot changes.\n"
            "character_behavior_match = dialogue follows speech_pattern; "
            "tension shows stress_response; description filters via attention_bias; "
            "avoidances not broken by unearned confessions.\n"
            "camera_discipline = lens/focus_chain used only at key moments; "
            "NOT exterior→hand→object on every paragraph; dialogue stretches stay clean.\n"
            "voice_distinctiveness = private images & speech rhythms feel unique to THIS cast; "
            "NOT default café counter / coin tray / ledger / endless aligning / affectless clock-check house style "
            "unless the frozen core explicitly requires those; signature device should texture the prose.\n"
            "style_match / perspective_match = prose manner and POV correctness.\n"
            "INFORMATION (primary): every scene goal, discovery, ending fact, and user twist must "
            "appear as a lived moment (not a one-line summary). Word count is diagnostic only — "
            "do NOT demand padding for a numeric floor. If units are thin/missing, say so in revision_notes "
            "(develop under-rendered beats — no new plot, no empty padding).\n"
            "TWIST craft: if a twist is delivered by quoting or closely restating the user's twist "
            "wording / thesis dialogue, mark it as a craft failure in revision_notes "
            "(rewrite as dramatized evidence + reaction; keep the same fact).\n"
            "Return JSON with all scores, feedbacks, missing_core_facts, invented_major_facts, "
            "revision_notes (3-6 items), total_score (average of main scores). Only valid JSON.",
        ),
        (
            "human",
            "STORY CORE CHECKLIST:\n{core_checklist}\n\n"
            "CHARACTERS (binding enrichment — must leave traces in prose):\n{characters_brief}\n\n"
            "SIGNATURE DEVICE: {signature_device}\n"
            "STYLE PACK: {style_pack}\n\n"
            "INFO COVERAGE PROBE (deterministic):\n{info_probe_summary}\n"
            "ENRICHMENT PROBE (deterministic token hits):\n{enrichment_probe_summary}\n"
            "HOUSE-STYLE FINGERPRINT (higher=more generic factory voice): {house_fp}\n\n"
            "Detail level {detail_level}/5 ({detail_label}):\n{detail_guidance}\n\n"
            "actual_words={actual_words} (soft guide only {word_min}–{word_max})\n\n"
            "Style: {style_label}\n{style_guidance}\n\n"
            "Perspective: {perspective_label}\nFocal/narrator: {focal_character}\n"
            "{perspective_guidance}\n\n"
            "Story reports perspective_type={observed_perspective}, "
            "focal_character={observed_focal}\n\n"
            "Title: {title}\n\nStory:\n{content}",
        ),
    ])

    try:
        critique: RenderCritique = (
            prompt | _llm().with_structured_output(RenderCritique)
        ).invoke({
            "core_checklist": core_checklist,
            "characters_brief": characters_brief,
            "signature_device": signature,
            "style_pack": style_pack_id,
            "detail_level": detail_level,
            "detail_label": d_label,
            "detail_guidance": d_guide,
            "story_length": story_length,
            "length_label": len_label,
            "word_min": word_band["min"],
            "word_max": word_band["max"],
            "word_ideal": word_band["ideal"],
            "actual_words": actual,
            "enrichment_probe_summary": enrichment_probe.get("summary")
            or "(no probe)",
            "info_probe_summary": format_info_expand_brief(info_probe),
            "house_fp": house_fp,
            "style_label": s_label,
            "style_guidance": style_guidance(style),
            "perspective_label": p_label,
            "perspective_guidance": perspective_guidance(preferred_perspective, focal),
            "focal_character": focal,
            "observed_perspective": observed_pov or "(not set)",
            "observed_focal": observed_focal or "(not set)",
            "title": final_story.title,
            "content": final_story.content,
        })

        scores = dict(critique.scores or {})
        fidelity_score = float(
            critique.fidelity_score
            if critique.fidelity_score is not None
            else _score_val(scores, "fidelity", "core_fidelity", default=8.0)
        )
        style_match = float(
            critique.style_match
            if critique.style_match is not None
            else _score_val(scores, "style_match", "style")
        )
        perspective_match = float(
            critique.perspective_match
            if critique.perspective_match is not None
            else _score_val(scores, "perspective_match", "perspective")
        )
        behavior_match = float(
            critique.character_behavior_match
            if critique.character_behavior_match is not None
            else _score_val(scores, "character_behavior_match", "character", default=7.0)
        )
        camera_disc = float(
            critique.camera_discipline
            if critique.camera_discipline is not None
            else _score_val(scores, "camera_discipline", default=7.0)
        )
        llm_voice = float(
            critique.voice_distinctiveness
            if critique.voice_distinctiveness is not None
            else _score_val(scores, "voice_distinctiveness", default=7.0)
        )
        # Blend LLM voice score with inverse house fingerprint (0–1+ → penalty)
        det_voice = max(1.0, min(10.0, 10.0 - (house_fp * 2.5)))
        voice_distinctiveness = round((llm_voice * 0.65) + (det_voice * 0.35), 2)
        scores.setdefault("fidelity", fidelity_score)
        scores.setdefault("style_match", style_match)
        scores.setdefault("perspective_match", perspective_match)
        scores.setdefault("character_behavior_match", behavior_match)
        scores.setdefault("camera_discipline", camera_disc)
        scores["voice_distinctiveness"] = voice_distinctiveness
        scores["house_fingerprint"] = house_fp

        fidelity_ok = fidelity_score >= FIDELITY_THRESHOLD
        style_ok = style_match >= MATCH_THRESHOLD
        pov_struct_ok = _perspective_structurally_ok(
            preferred_perspective,
            observed_pov,
            requested_focal=focal,
            observed_focal=observed_focal,
        )
        perspective_ok = (perspective_match >= MATCH_THRESHOLD) and pov_struct_ok
        behavior_ok = behavior_match >= MATCH_THRESHOLD
        camera_ok = camera_disc >= MATCH_THRESHOLD
        voice_ok = voice_distinctiveness >= VOICE_SOFT_THRESHOLD
        literary_score = float(critique.total_score or 0.0)

        can_iterate = iteration_count < max_iterations
        info_ok = bool(info_probe.get("info_ok", True))
        # Revise only on hard gates (no total_score / word-floor threshold).
        # Max rounds: max_iterations (default 6). Word count is soft only.
        hard_gate_fail = (
            (not fidelity_ok)
            or (not style_ok)
            or (not perspective_ok)
            or (not behavior_ok)
            or (not camera_ok)
            or (not info_ok)
            or (not enrichment_probe.get("enrichment_ok", True))
        )
        should_revise = can_iterate and hard_gate_fail
        voice_revise_only = False

        notes: List[str] = []
        if should_revise:
            # Priority: fidelity → info coverage → POV/style/behavior/camera
            if not fidelity_ok:
                miss = "; ".join(critique.missing_core_facts or []) or critique.fidelity_feedback
                inv = "; ".join(critique.invented_major_facts or [])
                notes.append(
                    f"FIDELITY ({fidelity_score:.1f}/10, need ≥{FIDELITY_THRESHOLD}): "
                    f"{miss}"
                    + (f" Remove/alter invented: {inv}." if inv else "")
                    + " Preserve scene goals, discovery beats, and ENDING FACTS. "
                    + "Do NOT invent plot only to add words."
                )
            if not info_ok:
                notes.append(
                    "INFORMATION COVERAGE WEAK: develop missing/thin units into lived moments "
                    "(dialogue + reaction, concrete action, sensory detail). "
                    "Do not pad for word count. "
                    "For twists: dramatize evidence — do NOT quote user twist wording. "
                    f"{format_info_expand_brief(info_probe)}"
                )
            if not style_ok:
                notes.append(
                    f"STYLE MISMATCH ({style_match:.1f}/10): requested {s_label}. "
                    f"{critique.style_feedback}"
                )
            if not perspective_ok:
                expected = expected_perspective_type(preferred_perspective)
                notes.append(
                    f"PERSPECTIVE MISMATCH ({perspective_match:.1f}/10): "
                    f"requested {p_label} (type={expected}, focal={focal}); "
                    f"observed type={observed_pov}, focal={observed_focal}. "
                    f"{critique.perspective_feedback}"
                )
            if not behavior_ok:
                notes.append(
                    f"CHARACTER BEHAVIOR ({behavior_match:.1f}/10): "
                    f"honor speech_pattern, stress_response, attention_bias, avoidances. "
                    f"{critique.character_feedback}"
                )
            if not enrichment_probe.get("enrichment_ok", True):
                notes.append(
                    "ENRICHMENT IN PROSE WEAK: make attention_bias, stress_response, and "
                    "speech_pattern observable in the writing (not only implied by traits). "
                    f"{enrichment_probe.get('summary', '')}"
                )
            if not camera_ok:
                notes.append(
                    f"CAMERA DISCIPLINE ({camera_disc:.1f}/10): "
                    f"use focus_chain only at key moments; do not stack inserts every paragraph; "
                    f"keep dialogue stretches clean. {critique.camera_feedback}"
                )
            for n in critique.revision_notes or []:
                if n and n not in notes:
                    notes.append(n)
            notes = notes[:8]

        # Soft voice-only revise: hard gates pass but voice is generic
        if (
            can_iterate
            and not should_revise
            and not voice_ok
            and not state.get("voice_revise_only")  # one soft voice pass max
        ):
            should_revise = True
            voice_revise_only = True
            notes = [
                f"VOICE DISTINCTIVENESS ({voice_distinctiveness:.1f}/10, soft need ≥{VOICE_SOFT_THRESHOLD}): "
                f"strip house-style defaults (café ledger / cup stacks / endless aligning) unless core requires them; "
                f"lean into private image_system, speech_pattern, rhythm_signature, signature_device={signature!r}. "
                f"house_fingerprint={house_fp}. "
                f"{getattr(critique, 'voice_feedback', '') or ''}"
            ]

        acceptance = build_acceptance(
            actual_words=actual,
            fidelity_score=fidelity_score,
            fidelity_ok=fidelity_ok,
            style_match=style_match,
            style_ok=style_ok,
            perspective_match=perspective_match,
            perspective_ok=perspective_ok,
            behavior_match=behavior_match,
            behavior_ok=behavior_ok,
            camera_discipline=camera_disc,
            camera_ok=camera_ok,
            enrichment_probe=enrichment_probe,
            info_probe=info_probe,
            missing_core_facts=list(critique.missing_core_facts or []),
            invented_major_facts=list(critique.invented_major_facts or []),
            revision_notes=notes if should_revise else (critique.revision_notes or []),
            voice_distinctiveness=voice_distinctiveness,
            house_fingerprint=house_fp,
        )
        acceptance_ok = bool(acceptance["acceptance_ok"])
        scores["acceptance_ok"] = 1.0 if acceptance_ok else 0.0
        scores["in_word_band"] = 1.0 if acceptance["in_word_band"] else 0.0
        scores["info_ok"] = 1.0 if acceptance.get("info_ok") else 0.0
        scores["enrichment_in_prose_ok"] = (
            1.0 if acceptance["enrichment_in_prose_ok"] else 0.0
        )
        if info_probe:
            scores["info_coverage_ratio"] = float(info_probe.get("coverage_ratio") or 0)
            scores["info_develop_ratio"] = float(info_probe.get("develop_ratio") or 0)

        evaluation = StoryEvaluation(
            scores=scores,
            total_score=literary_score,
            revision_notes=notes if should_revise else (critique.revision_notes or []),
            actual_word_count=actual,
            detail_level=detail_level,
            style_match=style_match,
            perspective_match=perspective_match,
            style_ok=style_ok,
            perspective_ok=perspective_ok,
            requested_style=style,
            requested_perspective=preferred_perspective,
            observed_perspective=observed_pov,
            fidelity_score=fidelity_score,
            fidelity_ok=fidelity_ok,
            missing_core_facts=list(critique.missing_core_facts or []),
            invented_major_facts=list(critique.invented_major_facts or []),
            character_behavior_match=behavior_match,
            camera_discipline=camera_disc,
            voice_distinctiveness=voice_distinctiveness,
            acceptance_ok=acceptance_ok,
            in_word_band=bool(acceptance["in_word_band"]),
            enrichment_in_prose_ok=bool(acceptance["enrichment_in_prose_ok"]),
            info_ok=bool(acceptance.get("info_ok")),
            info_coverage=info_probe,
            acceptance_summary=acceptance["summary"],
            failure_reasons=list(acceptance["failure_reasons"]),
            pass_reasons=list(acceptance["pass_reasons"]),
            reasons=list(acceptance["reasons"]),
            enrichment_probe=enrichment_probe,
        )

        if thread_id:
            await event_queues[thread_id].put({
                "event": "iteration_update",
                "iteration_count": iteration_count,
                "max_iterations": max_iterations,
                "total_score": literary_score,
                "fidelity_score": fidelity_score,
                "style_match": style_match,
                "perspective_match": perspective_match,
                "character_behavior_match": behavior_match,
                "camera_discipline": camera_disc,
                "voice_distinctiveness": voice_distinctiveness,
                "fidelity_ok": fidelity_ok,
                "style_ok": style_ok,
                "perspective_ok": perspective_ok,
                "acceptance_ok": acceptance_ok,
                "info_ok": acceptance.get("info_ok"),
                "in_word_band": acceptance["in_word_band"],
                "acceptance_summary": acceptance["summary"],
            })

        print("-" * 50)
        print(f"总分={literary_score} fidelity={fidelity_score} ok={fidelity_ok}")
        print(f"style_m={style_match} ok={style_ok} | pov_m={perspective_match} ok={perspective_ok}")
        print(f"behavior={behavior_match} ok={behavior_ok} | camera={camera_disc} ok={camera_ok}")
        print(
            f"voice={voice_distinctiveness} ok={voice_ok} house_fp={house_fp} "
            f"voice_revise_only={voice_revise_only}"
        )
        print(
            f"info_ok={info_ok} cov={info_probe.get('coverage_ratio')} "
            f"dev={info_probe.get('develop_ratio')} "
            f"thin={info_probe.get('n_thin')} missing={info_probe.get('n_missing')}"
        )
        print(
            f"words={actual} soft_guide={word_band['min']}-{word_band['max']} "
            f"(not hard) floor_ok={words_ok} over_max={words_too_long}"
        )
        print(f"ACCEPTANCE: {acceptance['summary']}")
        for r in acceptance["reasons"]:
            mark = "✓" if r["ok"] else "✗"
            print(f"  {mark} [{r['code']}] {r['message']}")
        print(f"should_revise={should_revise}")
        for n in notes:
            print(f"  - {n}")
        print("-" * 50)

        updates = {}
        best_story = state.get("best_final_story")
        best_score = state.get("best_score", 0.0) or 0.0
        # Prefer accepted drafts; among them higher literary + voice
        ranking_score = literary_score + (voice_distinctiveness * 0.15)
        if acceptance_ok:
            ranking_score += 2.0
        if fidelity_ok and _is_better_candidate(
            new_score=ranking_score,
            old_score=best_score,
            has_old=bool(best_story),
        ):
            updates["best_final_story"] = final_story
            updates["best_score"] = ranking_score
        elif not best_story:
            updates["best_final_story"] = final_story
            updates["best_score"] = ranking_score

        return {
            "evaluation": evaluation,
            "revision_notes": notes if should_revise else [],
            "voice_revise_only": voice_revise_only if should_revise else False,
            "iteration_count": iteration_count,
            "current_phase": "evaluating",
            **updates,
        }

    except Exception as e:
        print(f"    evaluator error: {e}")
        return {
            "evaluation": StoryEvaluation(
                actual_word_count=_count_words(final_story.content),
                detail_level=detail_level,
                requested_style=style,
                requested_perspective=preferred_perspective,
            ),
            "revision_notes": [],
        }
