"""Shared style + perspective guidance (product option set).

Styles (product): literary_realism | minimalism | psychological | poetic
Perspectives (product): third_limited | first_person  (+ focal_character)
"""
from __future__ import annotations

from typing import Optional

# ---- Product-allowed IDs ----
PRODUCT_STYLES = (
    "literary_realism",
    "minimalism",
    "psychological",
    "poetic",
)

PRODUCT_PERSPECTIVES = (
    "third_limited",
    "first_person",
)

STYLE_GUIDANCE = {
    "literary_realism": (
        "Style: literary realism.\n"
        "- Precise observation of ordinary life; earned emotion, not melodrama.\n"
        "- Psychological depth through gesture, subtext, and concrete detail.\n"
        "- Restrained lyricism; avoid purple prose and genre spectacle."
    ),
    "minimalism": (
        "Style: minimalism.\n"
        "- Spare diction, short sentences, understatement, white space.\n"
        "- Prefer implication and omission over explanation.\n"
        "- Still deliver a complete arc; do not confuse spareness with incompleteness.\n"
        "- Omission must NOT become head-hopping: stay with the focal character."
    ),
    "psychological": (
        "Style: psychological realism.\n"
        "- Prioritize interiority, perception under pressure, and layered motive.\n"
        "- Thought and feeling may distort the outer scene; keep external stakes clear.\n"
        "- Avoid pure essay-like analysis; root psyche in action and sensation."
    ),
    "poetic": (
        "Style: poetic / lyrical.\n"
        "- Musical cadence, vivid imagery, selective metaphor.\n"
        "- Emotion and atmosphere can lead, but keep narrative intelligibility.\n"
        "- Avoid empty ornament; every image should carry story weight.\n"
        "- Imagery must stay inside the focal character's senses (no omniscient flourish)."
    ),
}

STYLE_LABELS = {
    "literary_realism": "写实文学",
    "minimalism": "极简",
    "psychological": "心理写实",
    "poetic": "诗性 / 抒情",
}

PERSPECTIVE_LABELS = {
    "third_limited": "第三人称限知",
    "first_person": "第一人称",
}


def normalize_style(style: str | None) -> str:
    key = (style or "literary_realism").strip()
    if key not in PRODUCT_STYLES:
        return "literary_realism"
    return key


def normalize_perspective(perspective: str | None) -> str:
    key = (perspective or "third_limited").strip()
    # Map legacy values to product set
    legacy = {
        "multiple": "third_limited",
        "second_person": "first_person",
        "third_omniscient": "third_limited",
        "multiple_povs": "third_limited",
    }
    key = legacy.get(key, key)
    if key not in PRODUCT_PERSPECTIVES:
        return "third_limited"
    return key


def style_guidance(style: str | None) -> str:
    return STYLE_GUIDANCE[normalize_style(style)]


def style_label(style: str | None) -> str:
    key = normalize_style(style)
    return STYLE_LABELS.get(key, key)


def perspective_label(perspective: str | None, focal: str | None = None) -> str:
    key = normalize_perspective(perspective)
    base = PERSPECTIVE_LABELS.get(key, key)
    if focal and str(focal).strip():
        if key == "first_person":
            return f"{base}（叙述者：{focal.strip()}）"
        return f"{base}（焦点：{focal.strip()}）"
    return base


def perspective_guidance(
    perspective: str | None,
    focal_character: str | None = None,
) -> str:
    """Build POV instructions including who the focal/narrator character is."""
    key = normalize_perspective(perspective)
    focal = (focal_character or "").strip() or "the designated focal character"

    if key == "first_person":
        return (
            f"Perspective: FIRST PERSON as {focal}.\n"
            f"- The narrator is {focal}; use \"I\" / \"me\" throughout.\n"
            f"- Voice must match {focal}'s personality, diction, and biases.\n"
            f"- Knowledge is limited to what {focal} knows, notices, remembers, or invents.\n"
            f"- Do NOT enter other characters' private thoughts except as {focal} infers them.\n"
            f"- Set chosen_perspective.perspective_type = first_person and "
            f"focal_character = \"{focal}\"."
        )

    # third_limited (default)
    return (
        f"Perspective: THIRD-PERSON LIMITED, focal character = {focal}.\n"
        f"- Stay inside {focal}'s perceptions each scene; filter the world through that mind.\n"
        f"- Prefer free indirect style for {focal}'s thoughts; no omniscient summary of others.\n"
        f"- Do NOT freely head-hop to other consciousnesses.\n"
        f"- Set chosen_perspective.perspective_type = third_limited and "
        f"focal_character = \"{focal}\"."
    )


def resolve_focal_character(
    requested: Optional[str],
    characters: list | None,
    user_characters: list | None = None,
) -> str:
    """
    Pick a concrete focal/narrator name.
    Prefer exact match on CharacterProfile.name; else index \"1\"/\"2\"; else first character.
    """
    req = (requested or "").strip()
    names: list[str] = []
    for c in characters or []:
        name = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else None)
        if name:
            names.append(str(name).strip())

    if req:
        for n in names:
            if n.lower() == req.lower():
                return n
        # Allow "1" / "Character 1" style indices from frontend before names exist
        if req.isdigit():
            idx = int(req) - 1
            if 0 <= idx < len(names):
                return names[idx]
        if req.lower().startswith("character"):
            parts = req.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                idx = int(parts[-1]) - 1
                if 0 <= idx < len(names):
                    return names[idx]
        # Keep requested string if profiles not ready yet
        if not names:
            return req

    if names:
        return names[0]
    if user_characters:
        return "Character 1"
    return "the protagonist"


def expected_perspective_type(perspective: str | None) -> str:
    key = normalize_perspective(perspective)
    return "first_person" if key == "first_person" else "third_limited"
