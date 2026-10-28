"""Narrative detail grain (1–5) + soft word guide (900–1300).

Product policy:
- Acceptance is information-coverage based (StoryCore / twist units developed).
- 900–1300 is a SOFT diagnostic guide only (not a hard acceptance gate).
- Prompt thickness must not force padding; thin seeds are expanded upstream for structure.
- detail_level only controls description grain (style-driven), not page count.
"""
from __future__ import annotations

DETAIL_MIN = 1
DETAIL_MAX = 5
DETAIL_DEFAULT = 3

# ---- Soft word guide (diagnostic only; acceptance uses info coverage) ----
PRODUCT_WORD_MIN = 900
PRODUCT_WORD_MAX = 1300
PRODUCT_WORD_IDEAL = 1100

# Legacy length keys still accepted by API; all map to the same soft guide.
STORY_LENGTHS = ("short", "medium", "long")
STORY_LENGTH_DEFAULT = "medium"
STORY_LENGTH_LABELS = {
    "short": "Soft guide (info-driven length)",
    "medium": "Soft guide (info-driven length)",
    "long": "Soft guide (info-driven length)",
}

STORY_LENGTH_WORD_TARGETS = {
    "short": {
        "min": PRODUCT_WORD_MIN,
        "max": PRODUCT_WORD_MAX,
        "ideal": PRODUCT_WORD_IDEAL,
    },
    "medium": {
        "min": PRODUCT_WORD_MIN,
        "max": PRODUCT_WORD_MAX,
        "ideal": PRODUCT_WORD_IDEAL,
    },
    "long": {
        "min": PRODUCT_WORD_MIN,
        "max": PRODUCT_WORD_MAX,
        "ideal": PRODUCT_WORD_IDEAL,
    },
}

# Style baseline density (1–5). Length no longer shifts detail — fixed product band.
_STYLE_DETAIL_BASE = {
    "minimalism": 2,
    "literary_realism": 3,
    "psychological": 4,
    "poetic": 3,
}


def normalize_story_length(value: str | None) -> str:
    key = (value or STORY_LENGTH_DEFAULT).strip().lower()
    aliases = {
        "s": "short",
        "m": "medium",
        "l": "long",
        "短": "short",
        "中": "medium",
        "长": "long",
        "compact": "short",
        "standard": "medium",
        "extended": "long",
        "product": "medium",
    }
    key = aliases.get(key, key)
    if key not in STORY_LENGTHS:
        return STORY_LENGTH_DEFAULT
    return key


def product_word_band() -> dict:
    """Authoritative product band: always 900–1300."""
    return {
        "min": PRODUCT_WORD_MIN,
        "max": PRODUCT_WORD_MAX,
        "ideal": PRODUCT_WORD_IDEAL,
    }


def word_target_band(story_length: str | None = None) -> dict:
    """Return product word band (length key kept for API compat; does not change band)."""
    _ = normalize_story_length(story_length)
    return product_word_band()


def length_guidance(story_length: str | None = None) -> str:
    """Prompt block: fixed product band + how to hit it without filler."""
    band = product_word_band()
    return (
        f"PRODUCT LENGTH (fixed for all stories): {band['min']}–{band['max']} English words "
        f"(aim near {band['ideal']}).\n"
        f"Hard floor: do not finish below {band['min']} words.\n"
        f"Soft ceiling: prefer not to exceed {band['max']} words; if over, tighten "
        f"repetition and summary, do not cut ending facts or discovery beats.\n"
        "How to reach length (allowed): fully render each StoryCore beat; "
        "add gesture, setting, subtext, and brief interiority; give dialogue a reaction beat.\n"
        "How NOT to pad: no repeated sentences, no new major plot, no essay digressions, "
        "no stacking empty camera inserts.\n"
        "Quality over padding: never invent plot only to hit the floor."
    )


def length_ok(actual_words: int, story_length: str | None = None) -> bool:
    """Soft guide: True if word count ≥ former floor (≥900). Not a hard gate."""
    band = product_word_band()
    try:
        n = int(actual_words)
    except (TypeError, ValueError):
        return False
    return n >= int(band["min"])


def length_over_max(actual_words: int, story_length: str | None = None) -> bool:
    """Soft guide: True if above former ceiling (>1300). Not a hard gate."""
    band = product_word_band()
    try:
        n = int(actual_words)
    except (TypeError, ValueError):
        return False
    return n > int(band["max"])


def resolve_detail_level(
    style: str | None = None,
    story_length: str | None = None,
    *,
    explicit: int | None = None,
) -> int:
    """Detail grain from style only (product length is fixed, not used here)."""
    if explicit is not None:
        return clamp_detail_level(explicit)
    from app.narrative_prefs import normalize_style

    style_key = normalize_style(style)
    _ = story_length  # unused — fixed product band
    base = _STYLE_DETAIL_BASE.get(style_key, DETAIL_DEFAULT)
    return clamp_detail_level(base)


def story_length_label(value: str | None) -> str:
    return STORY_LENGTH_LABELS.get(
        normalize_story_length(value),
        STORY_LENGTH_LABELS[STORY_LENGTH_DEFAULT],
    )


# UI / API labels
DETAIL_LABELS = {
    1: "精简 · 粗略轮廓",
    2: "偏简 · 要点清晰",
    3: "适中 · 张弛有度",
    4: "偏详 · 场景充实",
    5: "复杂 · 细致入微",
}

DETAIL_GUIDANCE = {
    1: (
        "Detail level 1 — SPARE grain (not short page-count).\n"
        "Lean diction and implication over ornament. Still fully stage every StoryCore beat; "
        "do not collapse scenes into summary. Prefer sharp strokes and white space, "
        "but meet the 900–1300 word product band by covering all beats with clear action and speech."
    ),
    2: (
        "Detail level 2 — LEAN / ESSENTIAL grain.\n"
        "Economical prose that still lands every beat. Include the sensory and psychological "
        "notes needed for clarity. Reach 900–1300 words by rendering beats fully, not side plots."
    ),
    3: (
        "Detail level 3 — BALANCED grain.\n"
        "Alternate concrete moments and cleaner transitions. Enough dialogue, gesture, and "
        "setting to feel inhabited. Develop each scene fully into the 900–1300 word band."
    ),
    4: (
        "Detail level 4 — FULL / SCENE-RICH grain.\n"
        "Develop scenes with sustained action, dialogue, and atmosphere. "
        "Show psychology through behavior and subtext. Prefer showing; stay within 900–1300 words."
    ),
    5: (
        "Detail level 5 — INTRICATE / FINE-GRAINED.\n"
        "Rich sensory texture and layered interiority without losing coherence. "
        "Linger on gesture and subtext, but prefer not to exceed 1300 words."
    ),
}

# Fixed scene structure for product band (~900–1300 words)
DETAIL_SCENE_HINTS = {
    1: {
        "beats": 6,
        "scene_focus": "5–6 fully rendered StoryCore scenes; lean sentences, full beat coverage",
    },
    2: {
        "beats": 6,
        "scene_focus": "5–6 scenes; economical but complete beat-by-beat prose",
    },
    3: {
        "beats": 6,
        "scene_focus": "5–6 StoryCore scenes fully developed for a 900–1300 word story",
    },
    4: {
        "beats": 7,
        "scene_focus": "5–6 scenes with sustained moments; stay near 1100 words",
    },
    5: {
        "beats": 7,
        "scene_focus": "5–6 scenes with layered texture; soft ceiling 1300 words",
    },
}

# Token budgets sized for ~900–1300 word outputs
DETAIL_LLM_BUDGET = {
    1: {"max_tokens": 8000, "timeout": 360},
    2: {"max_tokens": 9000, "timeout": 360},
    3: {"max_tokens": 11000, "timeout": 420},
    4: {"max_tokens": 12000, "timeout": 480},
    5: {"max_tokens": 13000, "timeout": 480},
}


def clamp_detail_level(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DETAIL_DEFAULT
    return max(DETAIL_MIN, min(DETAIL_MAX, n))


def detail_guidance(level: int) -> str:
    level = clamp_detail_level(level)
    return DETAIL_GUIDANCE[level]


def detail_label(level: int) -> str:
    level = clamp_detail_level(level)
    return DETAIL_LABELS[level]


def detail_scene_hint(level: int) -> dict:
    level = clamp_detail_level(level)
    return DETAIL_SCENE_HINTS[level]


def detail_llm_budget(level: int) -> dict:
    level = clamp_detail_level(level)
    return DETAIL_LLM_BUDGET[level]
