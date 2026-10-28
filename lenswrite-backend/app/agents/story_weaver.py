from langchain_core.prompts import ChatPromptTemplate
from app.models import StoryState, RawInteractionStory
from functools import lru_cache

from app.llm import make_chat_openai
from app.detail_level import clamp_detail_level, detail_label, detail_guidance, detail_scene_hint
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


@lru_cache(maxsize=1)
def _llm():
    return make_chat_openai(temperature=0.75, timeout=120, max_retries=2)


def _structured_llm():
    return _llm().with_structured_output(RawInteractionStory)


def story_weaver_node(state: StoryState) -> dict:
    print(">>> [story_weaver_node] 开始执行")
    characters = state.get("characters", [])
    detail_level = clamp_detail_level(state.get("detail_level", 3))
    style = normalize_style(state.get("style"))
    preferred_perspective = normalize_perspective(state.get("preferred_perspective"))
    focal = resolve_focal_character(
        state.get("focal_character"),
        characters,
        state.get("user_characters"),
    )
    d_label = detail_label(detail_level)
    d_guide = detail_guidance(detail_level)
    scene_hint = detail_scene_hint(detail_level)
    print(
        f"    characters={len(characters)} detail={detail_level} "
        f"style={style} pov={preferred_perspective} focal={focal}"
    )

    characters_info = "\n\n".join(format_character_for_prompt(c) for c in characters)

    system_prompt = f"""You are a strong dramatic storyteller focused on character-driven narrative.
Write the raw story in scene form. Emphasize conflicts, interactions, and emotional stakes.

**Characters**: Treat personality dimensions as binding constraints on dialogue and power dynamics.

**Narrative detail level {detail_level}/5 ({d_label})**:
{d_guide}
Prefer {scene_hint["scene_focus"]}.

**Style (affect how beats feel)**:
{style_guidance(style)}

**Perspective (shape information access; final prose will use this lens)**:
{perspective_guidance(preferred_perspective, focal)}
Build scenes so they can be told through this focal/narrator without requiring omniscience.
Note what each character knows when it matters.

Do not optimize for a word count."""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", """Story premise: {user_prompt}

Characters (with personality dimensions):
{characters_info}

Style: {style_label}
Perspective: {perspective_label}
Focal / narrator: {focal_character}
Detail level: {detail_level}/5 ({detail_label})

Generate the raw interaction story as structured scenes that already respect these constraints."""),
    ])

    chain = prompt | _structured_llm()
    raw_story = chain.invoke({
        "user_prompt": state["user_prompt"],
        "characters_info": characters_info,
        "detail_level": detail_level,
        "detail_label": d_label,
        "style_label": style_label(style),
        "perspective_label": perspective_label(preferred_perspective, focal),
        "focal_character": focal,
    })

    return {
        "raw_interaction_story": raw_story,
        "focal_character": focal,
        "current_phase": "novelization",
        "last_agent_output": "Raw character interaction story generated.",
    }
