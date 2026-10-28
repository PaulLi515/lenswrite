"""Personality slider helpers: labels + behavioral interpretation for prompts."""
from __future__ import annotations

from typing import Any

SLIDER_KEYS = (
    "introversion_extraversion",
    "dominance",
    "empathy",
    "principled_flexible",
)

SLIDER_LABELS = {
    "introversion_extraversion": "Introversion (0) ↔ Extraversion (100)",
    "dominance": "Yielding (0) ↔ Dominance (100)",
    "empathy": "Low empathy (0) ↔ High empathy (100)",
    "principled_flexible": "Principled (0) ↔ Flexible/pragmatic (100)",
}


def _band(value: int) -> str:
    if value <= 33:
        return "low"
    if value <= 66:
        return "mid"
    return "high"


def interpret_slider(key: str, value: int) -> str:
    """Map a 0–100 slider to concrete behavior for writers."""
    band = _band(int(value))
    table = {
        "introversion_extraversion": {
            "low": "reserved, draws energy inward; sparse speech, observes before acting",
            "mid": "situationally social; can open up or withdraw depending on stakes",
            "high": "outward, talkative or socially forward; seeks contact and reaction",
        },
        "dominance": {
            "low": "defers, accommodates, avoids open power struggle; may seethe quietly",
            "mid": "negotiates status; asserts when cornered but not always controlling",
            "high": "steers conversations, interrupts, claims authority, resists being led",
        },
        "empathy": {
            "low": "blunt or self-protective; misses or dismisses others' feelings",
            "mid": "can read others but balances self-interest with care",
            "high": "highly attuned to others' pain; may over-absorb or soft-pedal conflict",
        },
        "principled_flexible": {
            "low": "rule-bound, duty-first, reluctant to compromise core codes",
            "mid": "has principles but can bargain under pressure",
            "high": "pragmatic, adapts rules to outcome; comfortable with ambiguity",
        },
    }
    return table.get(key, {}).get(band, "")


def format_slider_block(sliders: dict | None, *, with_behavior: bool = True) -> str:
    if not sliders:
        return ""
    lines = []
    for key in SLIDER_KEYS:
        if key not in sliders or sliders[key] is None:
            continue
        try:
            value = int(sliders[key])
        except (TypeError, ValueError):
            continue
        label = SLIDER_LABELS[key]
        if with_behavior:
            behavior = interpret_slider(key, value)
            lines.append(f"- {label}: {value}/100 → {behavior}")
        else:
            lines.append(f"- {label}: {value}")
    if not lines:
        return ""
    return "User-set personality dimensions (honor these in voice and choices):\n" + "\n".join(lines)


def apply_sliders_to_profile(char: Any, sliders: dict | None) -> Any:
    """Persist numeric sliders onto a CharacterProfile-like object."""
    if not sliders:
        return char
    updates = {}
    for key in SLIDER_KEYS:
        if key in sliders and sliders[key] is not None:
            try:
                updates[key] = int(sliders[key])
            except (TypeError, ValueError):
                continue
    if updates and hasattr(char, "model_copy"):
        return char.model_copy(update=updates)
    return char


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    s = str(value).strip()
    return [s] if s else []


def format_character_for_prompt(c: Any) -> str:
    """Full character block for weaver/novelist, including behavioral filters."""
    if isinstance(c, dict):
        get = c.get
        name = get("name") or "Unknown"
        background = get("background") or ""
        traits = get("personality_traits") or []
        motivations = get("motivations") or []
        voice = get("voice_style") or ""
        role = get("role_in_story")
        rel = get("relationships") or {}
        attention = _as_list(get("attention_bias"))
        stress = (get("stress_response") or "").strip()
        speech = (get("speech_pattern") or "").strip()
        avoidances = _as_list(get("avoidances"))
        images = _as_list(get("image_system"))
        rhythm = (get("rhythm_signature") or "").strip()
        taboo = (get("taboo_register") or "").strip()
        slider_dict = {k: get(k) for k in SLIDER_KEYS if get(k) is not None}
    else:
        name = getattr(c, "name", None) or "Unknown"
        background = getattr(c, "background", None) or ""
        traits = getattr(c, "personality_traits", None) or []
        motivations = getattr(c, "motivations", None) or []
        voice = getattr(c, "voice_style", None) or ""
        role = getattr(c, "role_in_story", None)
        rel = getattr(c, "relationships", None) or {}
        attention = _as_list(getattr(c, "attention_bias", None))
        stress = (getattr(c, "stress_response", None) or "").strip()
        speech = (getattr(c, "speech_pattern", None) or "").strip()
        avoidances = _as_list(getattr(c, "avoidances", None))
        images = _as_list(getattr(c, "image_system", None))
        rhythm = (getattr(c, "rhythm_signature", None) or "").strip()
        taboo = (getattr(c, "taboo_register", None) or "").strip()
        slider_dict = {}
        for key in SLIDER_KEYS:
            val = getattr(c, key, None)
            if val is not None:
                slider_dict[key] = val

    parts = [
        f"Name: {name}",
        f"Background: {background}",
        f"Personality Traits: {', '.join(traits)}",
        f"Motivations: {', '.join(motivations)}",
        f"Voice Style: {voice}",
    ]
    if speech:
        parts.append(f"Speech pattern: {speech}")
    if rhythm:
        parts.append(f"Rhythm signature: {rhythm}")
    if attention:
        parts.append(f"Attention bias (notices first): {', '.join(attention)}")
    if stress:
        parts.append(f"Stress response: {stress}")
    if images:
        parts.append(f"Private image system: {', '.join(images)}")
    if avoidances:
        parts.append(f"Avoidances: {', '.join(avoidances)}")
    if taboo:
        parts.append(f"Taboo register (never): {taboo}")
    if role:
        parts.append(f"Role: {role}")
    block = format_slider_block(slider_dict, with_behavior=True)
    if block:
        parts.append(block)
    if rel:
        parts.append("Relationships: " + "; ".join(f"{k}: {v}" for k, v in rel.items()))
    return "\n".join(parts)
