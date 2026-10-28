import re
from langchain_core.prompts import ChatPromptTemplate
from app.models import StoryState, FinalShortStory
from app.llm import make_chat_openai
from app.detail_level import (
    clamp_detail_level,
    detail_guidance,
    detail_label,
    detail_scene_hint,
    detail_llm_budget,
)
from app.narrative_prefs import (
    style_guidance,
    perspective_guidance,
    style_label,
    perspective_label,
    normalize_style,
    normalize_perspective,
    resolve_focal_character,
)
from app.personality import format_character_for_prompt


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _build_llm(detail_level: int):
    budget = detail_llm_budget(detail_level)
    print(
        f"    LLM max_tokens={budget['max_tokens']}, "
        f"timeout={budget['timeout']}s, detail_level={detail_level}"
    )
    return make_chat_openai(
        temperature=0.65,
        timeout=budget["timeout"],
        max_retries=2,
        max_tokens=budget["max_tokens"],
    )


def literary_novelist_node(state: StoryState) -> dict:
    print(">>> [literary_novelist_node] 开始执行")

    detail_level = clamp_detail_level(state.get("detail_level", 3))
    style = normalize_style(state.get("style"))
    preferred_perspective = normalize_perspective(state.get("preferred_perspective"))
    characters = state.get("characters") or []
    focal = resolve_focal_character(
        state.get("focal_character"),
        characters,
        state.get("user_characters"),
    )
    detail_text = detail_guidance(detail_level)
    label = detail_label(detail_level)
    scene_hint = detail_scene_hint(detail_level)
    style_text = style_guidance(style)
    perspective_text = perspective_guidance(preferred_perspective, focal)
    s_label = style_label(style)
    p_label = perspective_label(preferred_perspective, focal)

    raw_story_text = ""
    if state.get("raw_interaction_story"):
        raw_story_text = state["raw_interaction_story"].model_dump_json(indent=2)

    characters_info = "\n\n".join(format_character_for_prompt(c) for c in characters) or "(none)"

    iteration_count = state.get("iteration_count", 0) + 1
    print(f"    当前迭代次数: {iteration_count}")
    print(f"    style={style}, pov={preferred_perspective}, focal={focal}")
    print(f"    detail_level={detail_level} ({label}) characters={len(characters)}")
    revision_notes = state.get("revision_notes", [])

    system_prompt = f"""You are a highly skilled literary fiction writer.
Write a complete, high-quality short story based on the given premise, characters, and raw material.

**Narrative detail / complexity (description grain — not a word-count quota)**:
Level {detail_level}/5 — {label}
{detail_text}
Structural preference: {scene_hint["scene_focus"]} (about {scene_hint["beats"]} story beats).
Do NOT aim for a specific word count. Match how fine-grained the description is.

**Style Requirement (must be audible in prose)**:
{style_text}

**Narrative Perspective Requirement (must control access and voice)**:
{perspective_text}

**Characters & personality**:
Honor each character's voice_style and any numeric personality dimensions.
Sliders constrain speech rhythm, power dynamics, empathy, and compromise under pressure.

Key Requirements:
1. Match detail level, style, and perspective simultaneously.
2. Keep character psychology consistent with the provided dimensions.
3. End with emotional or thematic resonance.
4. Set word_count to the true English word count of content (metadata only).

Write with literary ambition and precision.
"""

    if iteration_count > 1:
        system_prompt += """

This is a revision round. Address the evaluator feedback while preserving strengths.
Keep the same core plot and characters unless feedback says otherwise.
Revisions must still obey style, perspective, detail level, and personality constraints.
"""

    human_template = """Story brief (standardized):
{user_prompt}

Characters (binding for voice and behavior):
{characters_info}

Style: {style_label}
{style_guidance}

Perspective: {perspective_label}
Focal / narrator character: {focal_character}
{perspective_guidance}

Detail / complexity level: {detail_level}/5 ({detail_label})
{detail_guidance}

Raw story material:
{raw_story_text}

Write a complete literary short story that satisfies style, perspective (with the correct focal/narrator),
detail level, and character personalities. Do not optimize for a fixed word count."""

    if revision_notes:
        notes_text = "\n".join([f"- {note}" for note in revision_notes])
        human_template += f"""

Previous evaluation feedback to address:
{notes_text}

Produce a full new draft that satisfies the feedback while keeping detail level {detail_level}/5,
style={style_label}, perspective={perspective_label}, focal={focal_character}."""

    print("    准备调用 LLM...")
    llm = _build_llm(detail_level)
    structured_llm = llm.with_structured_output(FinalShortStory)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", human_template),
    ])
    chain = prompt | structured_llm
    invoke_payload = {
        "user_prompt": state["user_prompt"],
        "characters_info": characters_info,
        "raw_story_text": raw_story_text,
        "style_label": s_label,
        "style_guidance": style_text,
        "perspective_label": p_label,
        "perspective_guidance": perspective_text,
        "focal_character": focal,
        "detail_level": detail_level,
        "detail_label": label,
        "detail_guidance": detail_text,
    }

    print("    开始调用 chain.invoke...")
    final_story = None
    last_err: Exception | None = None
    for attempt in range(1, 3):
        try:
            final_story = chain.invoke(invoke_payload)
            break
        except Exception as e:
            last_err = e
            print(f"    novelist invoke 失败 attempt={attempt}: {e}")
            if attempt == 1:
                continue
            raise

    if final_story is None:
        raise last_err or RuntimeError("literary_novelist produced no story")

    actual = _count_words(final_story.content)
    print(f"    实际字数(仅元数据): {actual} | detail_level={detail_level}")

    final_story = final_story.model_copy(update={"word_count": actual})
    print("    LLM 调用成功返回！")

    # Ensure structured POV fields match request when model drifts
    try:
        cp = final_story.chosen_perspective
        expected_type = "first_person" if preferred_perspective == "first_person" else "third_limited"
        final_story = final_story.model_copy(
            update={
                "chosen_perspective": cp.model_copy(
                    update={"perspective_type": expected_type, "focal_character": focal}
                )
            }
        )
    except Exception:
        pass

    return {
        "final_short_story": final_story,
        "focal_character": focal,
        "iteration_count": iteration_count,
        "current_phase": "done",
        "last_agent_output": (
            f"Final literary short story completed. "
            f"(Iteration {iteration_count}, detail={detail_level}/5, "
            f"style={style}, pov={preferred_perspective}/{focal}, words={actual})"
        ),
    }
