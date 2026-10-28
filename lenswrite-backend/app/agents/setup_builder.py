"""Generate character cards + world card from story seed / theme / drafts."""
from __future__ import annotations

from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from functools import lru_cache

from app.llm import make_chat_openai
from app.models import CharacterProfile, WorldSetting
from app.personality import apply_sliders_to_profile, format_slider_block


@lru_cache(maxsize=1)
def _llm():
    # Lazy: missing XAI_API_KEY must not crash process import / healthcheck.
    return make_chat_openai(temperature=0.65, timeout=120, max_retries=2)


class SetupBundle(BaseModel):
    """LLM output: full cards ready for user editing."""

    characters: List[CharacterProfile] = Field(
        ...,
        description="2-5 characters unless drafts specify a different count",
    )
    world: WorldSetting = Field(..., description="World / setting card for the story")
    suggested_story_prompt: str = Field(
        ...,
        description=(
            "A clear 2-6 sentence story prompt the user can accept or edit: "
            "situation, stakes, and what must be decided or discovered. Factual, not literary prose."
        ),
    )


def generate_setup(
    *,
    seed: str,
    theme: str | None = None,
    character_drafts: list[dict] | None = None,
    target_character_count: int | None = None,
) -> SetupBundle:
    """Build character cards + world card from seed/theme and optional drafts."""
    seed = (seed or "").strip()
    theme = (theme or "").strip() or None
    drafts = character_drafts or []

    draft_lines: list[str] = []
    for i, d in enumerate(drafts):
        prompt = (d.get("prompt") or "").strip()
        if not prompt:
            continue
        slider_txt = format_slider_block(d.get("sliders") or {}, with_behavior=True)
        line = f"{i + 1}. {prompt}"
        if slider_txt:
            line += f"\n   Personality overrides:\n   {slider_txt.replace(chr(10), chr(10) + '   ')}"
        draft_lines.append(line)

    if draft_lines:
        n_chars = len(draft_lines)
        draft_block = "\n".join(draft_lines)
    else:
        n_chars = max(2, min(int(target_character_count or 2), 5))
        draft_block = "(no drafts — invent characters that fit the seed)"

    system = """You design editable story SETUP cards for a short literary story product.

Output:
1) characters: rich CharacterProfile cards (same count as drafts if drafts exist; else the requested count).
2) world: a WorldSetting card (time, place, atmosphere, social context, constraints, key places).
3) suggested_story_prompt: a neutral plot prompt (2-6 sentences) the user can confirm or edit.

Rules:
- Honor the seed and theme. Do not invent a completely different premise.
- If drafts exist, expand them in order (same count); keep recognizable identity.
- Do NOT invent major biography the user did not supply.
- Characters need distinct motivations and potential friction.
- REQUIRED per character (observable, concrete — no vague adjectives):
  attention_bias (list, min 1: what they notice first — job/habit filters),
  stress_response (body/behavior under pressure),
  speech_pattern (concrete speech rhythm: short clauses / data-then-conclusion / etc.),
  avoidances (list, may be empty: topics/emotions/behaviors they dodge).
- World must support the seed; keep realistic unless seed implies otherwise.
- suggested_story_prompt is planning language, NOT finished literary prose.
- Fill relationship maps between characters when relevant.
"""

    human = """Story seed:
{seed}

Theme (optional):
{theme}

Character drafts:
{draft_block}

Target character count if no drafts: {n_chars}

Return SetupBundle JSON."""

    chain = (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        | _llm().with_structured_output(SetupBundle)
    )

    bundle: SetupBundle = chain.invoke({
        "seed": seed or "(empty seed)",
        "theme": theme or "(none)",
        "draft_block": draft_block,
        "n_chars": n_chars,
    })

    # Re-apply user slider overrides on matching draft indices
    fixed: list[CharacterProfile] = []
    for i, char in enumerate(bundle.characters):
        if i < len(drafts):
            sliders = drafts[i].get("sliders") or {}
            if sliders:
                char = apply_sliders_to_profile(char, sliders)
        fixed.append(char)
    bundle.characters = fixed

    print(
        f">>> [setup_builder] characters={len(bundle.characters)} "
        f"world={bundle.world.location or bundle.world.time_period or 'ok'}"
    )
    return bundle
