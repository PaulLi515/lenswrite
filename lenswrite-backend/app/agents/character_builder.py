"""Internal character enrichment: private, non-interchangeable voices (P0)."""
from __future__ import annotations

from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from functools import lru_cache

from app.llm import make_chat_openai
from app.models import CharacterProfile, StoryState
from app.personality import apply_sliders_to_profile, format_slider_block
from app.voice import recent_pattern_block, record_voice_patterns


@lru_cache(maxsize=1)
def _llm():
    return make_chat_openai(temperature=0.85, timeout=90, max_retries=2)

ENRICHMENT_RULES = """
You create CharacterProfile cards for a short literary story pipeline.

UNIQUENESS (critical):
1. Characters must NOT be interchangeable. Swapping enrichment between two characters
   should feel wrong.
2. FORBIDDEN default factory props unless the user's seed/draft explicitly is about them:
   exact change, paper cups by size, café ledgers, coin trays, endless aligning/stacking,
   generic "short clauses; answers the practical question first" for everyone.
3. attention_bias must be specific to THIS person's history in THIS premise
   (private tells, not generic job checklist).
4. stress_response must be a unique bodily/behavioral tell (not the same tidy-up for all).
5. speech_pattern must be a private rhythm (one character may ramble; another may ask only questions;
   another may quote rules; another may trail into silence). Do NOT clone speech across cast.
6. image_system: 2–3 private recurring images/objects for THIS character in THIS story only.
7. rhythm_signature: how their sentences move on the page.
8. taboo_register: tone they never use.

REQUIRED fields: attention_bias, stress_response, speech_pattern, image_system (2–3),
rhythm_signature, taboo_register; avoidances recommended.

OTHER RULES:
1. ONLY invent characters that belong to the premise.
2. Do NOT invent major biography the user did not supply.
3. personality_traits / motivations specific; voice_style = color; speech_pattern = structure.
4. Sliders if provided are AUTHORITATIVE; weave into speech/stress/attention; fill numeric fields.
5. If no sliders, invent coherent personality through speech/stress/attention (sliders stay null).
6. Relationships when multiple characters create friction.
"""


class CharacterList(BaseModel):
    characters: List[CharacterProfile]


def _normalize_enrichment(char: CharacterProfile) -> CharacterProfile:
    updates = {}
    bias = list(char.attention_bias or [])
    if not bias:
        updates["attention_bias"] = ["one concrete detail only this person would track"]
    if not (char.stress_response or "").strip():
        updates["stress_response"] = "body goes still; one small private habit appears"
    if not (char.speech_pattern or "").strip():
        updates["speech_pattern"] = (char.voice_style or "uneven sentences; private rhythm").strip()
    images = list(char.image_system or [])
    if len(images) < 2:
        base = images[:] if images else []
        while len(base) < 2:
            base.append(f"private image {len(base)+1} from their world")
        updates["image_system"] = base[:3]
    if not (char.rhythm_signature or "").strip():
        updates["rhythm_signature"] = "medium sentences with a hard stop"
    if not (char.taboo_register or "").strip():
        updates["taboo_register"] = "sermonizing; explaining the moral"
    if updates:
        return char.model_copy(update=updates)
    return char


def character_builder_node(state: StoryState) -> dict:
    print(">>> [character_builder_node] uniqueness-first enrichment (P0)")
    user_characters = state.get("user_characters") or []
    premise = (
        (state.get("original_user_prompt") or state.get("user_prompt") or state.get("story_seed") or "")
        .strip()
    )
    recent = recent_pattern_block()
    print(f"    user_characters 数量: {len(user_characters)}")

    if not user_characters:
        structured_llm = _llm().with_structured_output(CharacterList)
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                ENRICHMENT_RULES
                + "\nGenerate 2 characters that fit the premise unless the premise clearly needs more."
                + "\n\n"
                + recent,
            ),
            (
                "human",
                "Story premise / user_prompt:\n{user_prompt}\n\n"
                "Return CharacterList with full uniqueness fields filled.",
            ),
        ])
        chain = prompt | structured_llm
        result = chain.invoke({"user_prompt": premise or "(empty premise)"})
        characters = [_normalize_enrichment(c) for c in result.characters]
    else:
        characters = []
        others = []
        for i, uc in enumerate(user_characters):
            p = (uc.get("prompt") or "").strip()
            if p:
                others.append(f"{i + 1}. {p}")
        others_block = "\n".join(others) if others else "(solo draft)"
        prior_voices = []

        for uc in user_characters:
            user_desc = (uc.get("prompt") or "").strip()
            user_sliders = uc.get("sliders") or {}
            slider_block = format_slider_block(user_sliders, with_behavior=True)
            prior_block = (
                "Already created cast (do NOT clone their speech/stress/images):\n"
                + "\n".join(prior_voices)
                if prior_voices
                else "(first character)"
            )

            expand_prompt = ChatPromptTemplate.from_messages([
                ("system", ENRICHMENT_RULES + "\n\n" + recent),
                (
                    "human",
                    "Story premise:\n{premise}\n\n"
                    "All drafts:\n{others}\n\n"
                    "{prior_block}\n\n"
                    "Expand THIS draft into one CharacterProfile:\n{description}\n\n"
                    "{slider_block}",
                ),
            ])
            structured_llm = _llm().with_structured_output(CharacterProfile)
            chain = expand_prompt | structured_llm
            char = chain.invoke({
                "premise": premise or "(empty premise)",
                "others": others_block,
                "prior_block": prior_block,
                "description": user_desc or "(no description; invent a fitting character)",
                "slider_block": slider_block
                or "(no sliders — invent a private speech/stress/image system; leave numeric sliders null)",
            })
            char = apply_sliders_to_profile(char, user_sliders)
            char = _normalize_enrichment(char)
            prior_voices.append(
                f"- {char.name}: speech={char.speech_pattern!r}; "
                f"stress={char.stress_response!r}; images={char.image_system!r}"
            )
            print(
                f"    enriched {char.name}: bias={char.attention_bias!r} "
                f"images={char.image_system!r} speech={char.speech_pattern[:50]!r}"
            )
            characters.append(char)

    record_voice_patterns(characters, signature=state.get("signature_device"))
    print(f">>> [character_builder_node] done n={len(characters)}")
    return {
        "characters": characters,
        "current_phase": "story_generation",
        "last_agent_output": f"Enriched {len(characters)} unique characters.",
    }
