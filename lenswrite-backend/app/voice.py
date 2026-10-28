"""Voice uniqueness: anti-house-style bans, style packs, structure pool, recent deweight."""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

# Persist recent patterns across runs (repo-local; best-effort)
_RECENT_PATH = Path(__file__).resolve().parent.parent.parent / ".lenswrite_recent_voice.json"
_RECENT_MAX = 24

# --- P0: house-style ban (unless StoryCore explicitly requires) ---
HOUSE_STYLE_BAN_PHRASES = [
    "exact change",
    "paper cup",
    "re-stack",
    "restack",
    "aligned the",
    "lined up",
    "largest to smallest",
    "checked the clock",
    "wall clock",
    "ledger",
    "coin tray",
    "short clauses",
    "without looking up",
    "the usual",
    "same minute",
    "four words",
]

HOUSE_STYLE_NEGATIVE_PROMPT = """
ANTI-HOUSE-STYLE (do NOT default to these unless the frozen core explicitly requires them):
- Transactional coffee-counter choreography (cups by size, coin rows, ledger columns)
- Endless aligning / re-stacking / wiping as emotional substitute
- Clock-checking every scene
- Completely affectless closing that only restates routine
- Generic "short clauses; answers the practical question first" for every character
- Mechanical focus_chain on every beat (exterior → hand → object)

Write THIS story's private sensory world and speech rhythms instead.
"""

# --- P1: structure pool ---
STRUCTURE_POOL = [
    {
        "id": "single_pressure",
        "label": "Single-location pressure cooker",
        "guidance": "Most action in one primary place; time pressure; few exits.",
    },
    {
        "id": "dual_timeline",
        "label": "Dual timeline braid",
        "guidance": "Alternate present beats with brief past flashes that change meaning of present objects.",
    },
    {
        "id": "dialogue_heavy",
        "label": "Dialogue-forward",
        "guidance": "Story advances mainly through talk and subtext; description stays lean but specific.",
    },
    {
        "id": "almost_silent",
        "label": "Almost silent",
        "guidance": "Minimal dialogue; meaning through action, objects, and withheld speech.",
    },
    {
        "id": "aftermath_first",
        "label": "Aftermath first",
        "guidance": "Open after a decision already happened; reconstruct what forced it.",
    },
    {
        "id": "ritual_break",
        "label": "Ritual broken",
        "guidance": "Establish a private ritual, break it once, and end on the new unstable pattern.",
    },
]

# --- P2: style packs (voice color, not plot) ---
STYLE_PACKS = {
    "cold_procedural": {
        "label": "Cold procedural",
        "guidance": (
            "Clinical precision, inventory of actions, emotion only as interruption. "
            "Prefer exact nouns over mood adjectives."
        ),
    },
    "warm_domestic": {
        "label": "Warm domestic",
        "guidance": (
            "Heat, fabric, food, small caretaking gestures; tenderness without speechifying. "
            "Allow slightly longer interior breaths."
        ),
    },
    "uncanny_quiet": {
        "label": "Uncanny quiet",
        "guidance": (
            "Slight wrongness in ordinary rooms; pauses last too long; objects feel watched. "
            "Never explain the uncanny."
        ),
    },
    "comic_deadpan": {
        "label": "Comic deadpan",
        "guidance": (
            "Dry understatement, awkward timing, serious treatment of small absurdities. "
            "No laugh-track; no slapstick."
        ),
    },
}


def _load_recent() -> list[dict]:
    try:
        if _RECENT_PATH.exists():
            data = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-_RECENT_MAX:]
    except Exception:
        pass
    return []


def _save_recent(rows: list[dict]) -> None:
    try:
        _RECENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_PATH.write_text(
            json.dumps(rows[-_RECENT_MAX:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def pattern_key(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def recent_pattern_block() -> str:
    """De-weight recently used speech/stress/image patterns."""
    rows = _load_recent()
    if not rows:
        return "(no recent voice patterns on file)"
    lines = ["RECENTLY USED (avoid repeating these exact patterns):"]
    for r in rows[-12:]:
        lines.append(
            f"- speech≈{r.get('speech', '')[:60]!r}; "
            f"stress≈{r.get('stress', '')[:60]!r}; "
            f"images≈{r.get('images', '')[:60]!r}"
        )
    return "\n".join(lines)


def record_voice_patterns(characters: list | None, signature: str | None = None) -> None:
    rows = _load_recent()
    for c in characters or []:
        if hasattr(c, "model_dump"):
            d = c.model_dump()
        elif isinstance(c, dict):
            d = c
        else:
            d = {
                "speech_pattern": getattr(c, "speech_pattern", ""),
                "stress_response": getattr(c, "stress_response", ""),
                "image_system": getattr(c, "image_system", None),
            }
        images = d.get("image_system") or []
        if isinstance(images, str):
            images = [images]
        rows.append({
            "speech": (d.get("speech_pattern") or "")[:120],
            "stress": (d.get("stress_response") or "")[:120],
            "images": ", ".join(str(x) for x in images)[:120],
            "signature": (signature or "")[:80],
            "key": pattern_key(
                f"{d.get('speech_pattern')}|{d.get('stress_response')}|{images}"
            ),
        })
    _save_recent(rows)


def pick_structure(seed: str, *, rng: random.Random | None = None) -> dict:
    r = rng or random.Random(hash(seed) & 0xFFFFFFFF)
    # Light keyword bias, still random among pool
    s = (seed or "").lower()
    preferred = None
    if any(k in s for k in ("secret", "letter", "note", "hidden")):
        preferred = "ritual_break"
    elif any(k in s for k in ("night", "empty", "bell", "alone")):
        preferred = "almost_silent"
    elif any(k in s for k in ("argue", "tell", "say", "talk", "phone")):
        preferred = "dialogue_heavy"
    elif any(k in s for k in ("after", "funeral", "left", "gone")):
        preferred = "aftermath_first"

    pool = list(STRUCTURE_POOL)
    if preferred:
        for item in pool:
            if item["id"] == preferred:
                if r.random() < 0.55:
                    return dict(item)
    return dict(r.choice(pool))


def pick_style_pack(
    seed: str,
    product_style: str | None = None,
    *,
    rng: random.Random | None = None,
    force_id: str | None = None,
) -> dict:
    if force_id and force_id in STYLE_PACKS:
        pack = STYLE_PACKS[force_id]
        return {"id": force_id, **pack}
    r = rng or random.Random((hash(seed) ^ hash(product_style or "")) & 0xFFFFFFFF)
    s = (seed or "").lower()
    weights = {
        "cold_procedural": 1.0,
        "warm_domestic": 1.0,
        "uncanny_quiet": 1.0,
        "comic_deadpan": 0.85,
    }
    if product_style == "minimalism":
        weights["cold_procedural"] += 1.2
    if product_style == "psychological":
        weights["uncanny_quiet"] += 1.0
    if product_style == "poetic":
        weights["warm_domestic"] += 0.6
        weights["uncanny_quiet"] += 0.6
    if any(k in s for k in ("family", "mother", "kitchen", "home", "child")):
        weights["warm_domestic"] += 1.0
    if any(k in s for k in ("empty", "night", "bell", "storm", "alone", "ghost")):
        weights["uncanny_quiet"] += 1.2
    if any(k in s for k in ("archive", "catalog", "protocol", "shift", "nurse")):
        weights["cold_procedural"] += 1.0
    if any(k in s for k in ("awkward", "bus", "stranger", "same book")):
        weights["comic_deadpan"] += 0.8

    ids = list(weights.keys())
    total = sum(weights[i] for i in ids)
    x = r.random() * total
    acc = 0.0
    chosen = ids[0]
    for i in ids:
        acc += weights[i]
        if x <= acc:
            chosen = i
            break
    pack = STYLE_PACKS[chosen]
    return {"id": chosen, **pack}


def house_fingerprint_score(prose: str) -> float:
    """Higher = more generic house style (worse for uniqueness). 0–1+."""
    text = (prose or "").lower()
    if not text:
        return 1.0
    hits = 0
    for p in HOUSE_STYLE_BAN_PHRASES:
        hits += text.count(p.lower())
    # normalize roughly by 800 words
    words = max(1, len(re.findall(r"\b\w+\b", text)))
    return round(hits / max(1.0, words / 200.0), 3)


def format_voice_card(
    characters: list | None,
    *,
    focal: str | None = None,
    style_pack: dict | None = None,
    signature_device: str | None = None,
    structure: dict | None = None,
) -> str:
    """Compact voice iron-laws for renderer."""
    lines = ["=== VOICE CARD (binding for THIS story) ==="]
    if signature_device:
        lines.append(f"Signature device: {signature_device}")
    if structure:
        lines.append(
            f"Structure: {structure.get('id')} — {structure.get('guidance', '')}"
        )
    if style_pack:
        lines.append(
            f"Style pack: {style_pack.get('id')} — {style_pack.get('guidance', '')}"
        )
    lines.append(HOUSE_STYLE_NEGATIVE_PROMPT.strip())
    lines.append("Character voice laws:")
    for c in characters or []:
        if hasattr(c, "model_dump"):
            d = c.model_dump()
        elif isinstance(c, dict):
            d = c
        else:
            d = {
                "name": getattr(c, "name", "?"),
                "attention_bias": getattr(c, "attention_bias", []),
                "stress_response": getattr(c, "stress_response", ""),
                "speech_pattern": getattr(c, "speech_pattern", ""),
                "avoidances": getattr(c, "avoidances", []),
                "image_system": getattr(c, "image_system", []),
                "rhythm_signature": getattr(c, "rhythm_signature", ""),
                "taboo_register": getattr(c, "taboo_register", ""),
                "voice_style": getattr(c, "voice_style", ""),
            }
        name = d.get("name") or "?"
        mark = " [FOCAL]" if focal and str(focal).lower() in str(name).lower() else ""
        images = d.get("image_system") or []
        if isinstance(images, str):
            images = [images]
        lines.append(f"- {name}{mark}:")
        lines.append(f"  speech: {d.get('speech_pattern') or d.get('voice_style') or 'n/a'}")
        lines.append(f"  stress: {d.get('stress_response') or 'n/a'}")
        lines.append(f"  notices first: {', '.join(_as_list(d.get('attention_bias'))) or 'n/a'}")
        if images:
            lines.append(f"  private images: {', '.join(str(x) for x in images)}")
        if d.get("rhythm_signature"):
            lines.append(f"  rhythm: {d.get('rhythm_signature')}")
        if d.get("taboo_register"):
            lines.append(f"  never: {d.get('taboo_register')}")
    lines.append(
        "FOCUS_CHAIN LIMIT: use cinematic focus_chain on at most 1–2 discovery beats "
        "in the whole story; ordinary beats = no lens grammar."
    )
    return "\n".join(lines)


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    return [str(v).strip()] if str(v).strip() else []


def dual_draft_score(
    prose: str,
    *,
    word_count: int,
    word_min: int = 900,
    word_max: int = 1300,
    enrichment_hits: int = 0,
) -> float:
    """Higher is better. Used to pick between dual drafts."""
    score = 0.0
    if word_min <= word_count <= word_max:
        score += 3.0
    elif word_count < word_min:
        score += max(0.0, 2.0 * (word_count / word_min))
    else:
        score += max(0.0, 2.0 * (word_max / max(word_count, 1)))
    score += min(2.0, enrichment_hits * 0.25)
    # lower house fingerprint is better
    fp = house_fingerprint_score(prose)
    score += max(0.0, 3.0 - fp)
    return round(score, 3)
