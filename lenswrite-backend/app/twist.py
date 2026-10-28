"""User-specified plot twists: zero or more, insertable into the premise at any points."""
from __future__ import annotations

import re
from typing import Any, Optional

# Numbered marker the UI inserts at the caret: ⟦TWIST:1⟧, ⟦TWIST:2⟧, ...
TWIST_MARKER_PREFIX = "⟦TWIST:"
TWIST_MARKER_SUFFIX = "⟧"

# Legacy single marker (treated as twist #1)
LEGACY_MARKER = "⟦TWIST⟧"
# Alias kept for older imports / OpenAPI docs
TWIST_MARKER = LEGACY_MARKER

_MARKER_RE = re.compile(r"⟦TWIST(?::(\d+))?⟧")
_ALT_MARKER_RE = re.compile(r"\[\[TWIST(?::(\d+))?\]\]|\[TWIST(?::(\d+))?\]|\{\{TWIST(?::(\d+))?\}\}")


def twist_marker(index: int) -> str:
    """1-based display marker for twist index."""
    n = max(1, int(index))
    return f"{TWIST_MARKER_PREFIX}{n}{TWIST_MARKER_SUFFIX}"


def _normalize_content(content: Optional[str]) -> str:
    return (content or "").strip()


def normalize_twists(
    twists: list | None = None,
    *,
    enabled: bool | None = None,
    content: Optional[str] = None,
) -> list[dict]:
    """Normalize API inputs into a list of {index, content} (1-based index).

    Accepts:
      - twists: list[str] or list[{content|text|twist: str}]
      - legacy: enabled + single content string
    Empty contents are dropped. Indices are reassigned 1..n in order.
    """
    items: list[str] = []
    if twists:
        for t in twists:
            if isinstance(t, str):
                body = _normalize_content(t)
            elif isinstance(t, dict):
                body = _normalize_content(
                    t.get("content") or t.get("text") or t.get("twist") or ""
                )
            else:
                body = _normalize_content(getattr(t, "content", None) or str(t))
            if body:
                items.append(body)
    elif enabled and _normalize_content(content):
        items.append(_normalize_content(content))

    return [{"index": i, "content": c, "marker": twist_marker(i)} for i, c in enumerate(items, start=1)]


# Shared craft law for every agent that sees twist facts
TWIST_CRAFT_RULES = """
TWIST REVEAL CRAFT (mandatory when user twists exist):
- Twists are HIDDEN FACTS for plotting — not sentences to paste into prose.
- NEVER quote, paraphrase closely, or announce the twist in one explanatory line.
- NEVER have a character say the full twist out loud as a thesis.
- DEPTH (each twist needs all three, not just a nod):
  1) EVIDENCE: 1–2 concrete sensory/object details that only this fact explains
     (handwriting, scent, wrong name, wrong date, worn crease — not a single vague "envelope").
  2) PRIVATE REACTION: the FOCAL character's stress_response must fire in-body
     (and speech_pattern may break or go silent) — reaction is unique to THEM, not generic shock.
  3) BEHAVIOR CHANGE: after the turn, they do one irreversible small act that only makes
     sense after knowing (abandon a ritual, hide an object, refuse a room, re-order a pile).
- Filter the evidence through the focal character's attention_bias and private image_system
  (they notice the twist via THEIR tells, not a narrator lecture).
- Prefer implication over summary; keep the insertion-point narrative moment as lived scene,
  not "and then it was revealed that…".
- Multiple twists: each gets its own evidence+reaction beat; do not dump two facts in one sentence.
""".strip()


def format_twist_binding(content: str, *, index: int | None = None) -> str:
    """Inline structural marker for a twist at its narrative moment.

    Encodes the fact for plotting agents without inviting prose quotation.
    """
    body = _normalize_content(content)
    label = f"#{index}" if index is not None else ""
    return (
        f"[TWIST {label} — HIDDEN FACT at this narrative moment "
        f"(plot delivers the fact; prose DRAMATIZES with depth — never quote this wording)] "
        f"FACT: {body} "
        f"| STAGE: (a) sensory evidence (b) focal stress_response (c) one private behavior change; "
        f"filter via attention_bias / image_system; no exposition dump."
    )


def compose_seed_with_twists(
    seed: str,
    twists: list[dict] | None,
    *,
    append_if_no_marker: bool = True,
) -> tuple[str, list[dict]]:
    """Replace numbered markers with binding blocks; append unplaced twists.

    Markers supported: ⟦TWIST:1⟧ … ⟦TWIST:n⟧, legacy ⟦TWIST⟧ (= #1),
    and [[TWIST:n]] / [TWIST:n] / {{TWIST:n}} variants.
    """
    raw = (seed or "").strip()
    rows = list(twists or [])
    if not rows:
        # strip stray markers if any
        cleaned = _MARKER_RE.sub("", raw)
        cleaned = _ALT_MARKER_RE.sub("", cleaned)
        return cleaned.strip(), []

    by_index = {int(r["index"]): r for r in rows if r.get("content")}
    used: set[int] = set()
    composed = raw

    # Legacy bare marker → first twist
    if LEGACY_MARKER in composed and 1 in by_index:
        composed = composed.replace(
            LEGACY_MARKER,
            f"\n{format_twist_binding(by_index[1]['content'], index=1)}\n",
            1,
        )
        used.add(1)

    def _repl_numbered(match: re.Match) -> str:
        # group 1 from primary pattern; alt patterns use other groups
        g = match.group(1)
        if g is None:
            # try other groups from alt pattern
            for i in range(1, match.lastindex + 1 if match.lastindex else 1):
                if match.group(i):
                    g = match.group(i)
                    break
        if not g:
            return match.group(0)
        idx = int(g)
        if idx not in by_index or idx in used:
            return ""  # drop unknown / duplicate markers
        used.add(idx)
        return f"\n{format_twist_binding(by_index[idx]['content'], index=idx)}\n"

    composed = _MARKER_RE.sub(_repl_numbered, composed)
    composed = _ALT_MARKER_RE.sub(_repl_numbered, composed)

    # Append twists that never got a marker
    unplaced = [by_index[i] for i in sorted(by_index) if i not in used]
    if unplaced and append_if_no_marker:
        blocks = [
            format_twist_binding(r["content"], index=int(r["index"]))
            for r in unplaced
        ]
        sep = "\n\n" if composed else ""
        composed = f"{composed}{sep}" + "\n\n".join(blocks)
        used.update(int(r["index"]) for r in unplaced)

    meta = []
    for r in rows:
        idx = int(r["index"])
        meta.append({
            "index": idx,
            "content": r["content"],
            "marker": r.get("marker") or twist_marker(idx),
            "placed": idx in used,
            "marker_used": idx in used,
        })
    return composed.strip(), meta


def compose_seed_with_twist(
    seed: str,
    *,
    enabled: bool,
    content: Optional[str] = None,
    append_if_no_marker: bool = True,
) -> tuple[str, Optional[dict]]:
    """Backward-compatible single-twist compose. Returns first meta dict or None."""
    rows = normalize_twists(enabled=enabled, content=content)
    composed, meta = compose_seed_with_twists(
        seed, rows, append_if_no_marker=append_if_no_marker
    )
    if not meta:
        return composed, None
    m0 = meta[0]
    return composed, {
        "enabled": True,
        "content": m0["content"],
        "placed": m0.get("placed", True),
        "marker_used": m0.get("marker_used", False),
        "binding": format_twist_binding(m0["content"], index=1),
        "twists": meta,
    }


def format_twist_for_prompt(state: dict | None) -> str:
    """Block for expander / plot weaver / renderer (multi-twist)."""
    if not state:
        return "(no user twists)"

    rows = state.get("twists") if isinstance(state.get("twists"), list) else None
    lines: list[str] = []
    if rows:
        for r in rows:
            if isinstance(r, dict):
                idx = r.get("index") or ""
                body = _normalize_content(r.get("content"))
            else:
                idx = ""
                body = _normalize_content(str(r))
            if body:
                label = f"{idx}" if idx != "" else "-"
                lines.append(
                    f"  {label}. HIDDEN FACT (do not paste into prose): {body}\n"
                    f"     → beats must include: discovery evidence + focal reaction beat "
                    f"+ later behavior change; never a spoken summary of this sentence."
                )
    else:
        enabled = bool(state.get("twist_enabled"))
        content = _normalize_content(state.get("twist_content"))
        if enabled and content:
            lines.append(
                f"  1. HIDDEN FACT (do not paste into prose): {content}\n"
                f"     → evidence + focal stress reaction + behavior change."
            )

    if not lines:
        return "(no user twists)"

    return (
        "USER TWISTS — plot-binding hidden facts (NOT prose to quote):\n"
        + "\n".join(lines)
        + "\n\n"
        + TWIST_CRAFT_RULES
    )


def twist_must_include_lines(twists: list | None) -> list[str]:
    """Outline must_include lines: outcome-oriented, discourage quotation."""
    out = []
    for r in twists or []:
        if isinstance(r, dict):
            body = _normalize_content(r.get("content"))
            idx = r.get("index")
        else:
            body = _normalize_content(str(r))
            idx = None
        if body:
            label = f"#{idx}" if idx is not None else ""
            out.append(
                f"Twist {label} must land as dramatized evidence "
                f"(not quoted wording): {body}"
            )
    return out


def twist_must_include_line(content: Optional[str]) -> str:
    body = _normalize_content(content)
    if not body:
        return ""
    return f"Twist must land as dramatized evidence (not quoted wording): {body}"


def _focal_enrichment_brief(characters: list | None, focal: str | None) -> str:
    """Pull stress / attention / images for the focal character (for twist binding)."""
    if not characters:
        return ""
    focal_l = (focal or "").strip().lower()
    chosen = None
    for c in characters:
        if hasattr(c, "model_dump"):
            d = c.model_dump()
        elif isinstance(c, dict):
            d = c
        else:
            d = {
                "name": getattr(c, "name", ""),
                "stress_response": getattr(c, "stress_response", ""),
                "speech_pattern": getattr(c, "speech_pattern", ""),
                "attention_bias": getattr(c, "attention_bias", None),
                "image_system": getattr(c, "image_system", None),
            }
        name = str(d.get("name") or "")
        if focal_l and (
            focal_l in name.lower()
            or name.lower() in focal_l
            or (name.split() and name.split()[0].lower() in focal_l)
        ):
            chosen = d
            break
    if chosen is None:
        c0 = characters[0]
        if hasattr(c0, "model_dump"):
            chosen = c0.model_dump()
        elif isinstance(c0, dict):
            chosen = c0
        else:
            return ""
    att = chosen.get("attention_bias") or []
    if isinstance(att, str):
        att = [att]
    imgs = chosen.get("image_system") or []
    if isinstance(imgs, str):
        imgs = [imgs]
    return (
        f"FOCAL for twist reaction: {chosen.get('name') or '?'}\n"
        f"  MUST use stress_response on each twist: {chosen.get('stress_response') or 'n/a'}\n"
        f"  speech_pattern under pressure: {chosen.get('speech_pattern') or 'n/a'}\n"
        f"  notice evidence via attention_bias: {', '.join(str(x) for x in att) or 'n/a'}\n"
        f"  let private image_system touch the evidence: {', '.join(str(x) for x in imgs) or 'n/a'}"
    )


def format_twist_card_for_renderer(
    twists: list | None,
    *,
    characters: list | None = None,
    focal: str | None = None,
) -> str:
    """Compact iron-law block for literary_renderer system prompts."""
    rows = []
    for r in twists or []:
        if isinstance(r, dict):
            body = _normalize_content(r.get("content"))
            idx = r.get("index")
        else:
            body = _normalize_content(str(r))
            idx = None
        if body:
            label = f"#{idx}" if idx is not None else "•"
            rows.append(
                f"- {label} hidden fact (dramatize only): {body}\n"
                f"  require: layered evidence + focal stress_response + one private act after"
            )
    if not rows:
        return ""
    focal_block = _focal_enrichment_brief(characters, focal)
    parts = [
        "=== TWIST REVEALS (binding facts; anti-quotation; character-bound depth) ===",
        *rows,
    ]
    if focal_block:
        parts.append(focal_block)
    parts.append(TWIST_CRAFT_RULES)
    return "\n".join(parts)


def extract_twist_from_state(state: Any) -> Optional[dict]:
    if not state:
        return None
    get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    rows = get("twists")
    if isinstance(rows, list) and rows:
        contents = []
        for r in rows:
            if isinstance(r, dict):
                c = _normalize_content(r.get("content"))
            else:
                c = _normalize_content(str(r))
            if c:
                contents.append(c)
        if contents:
            return {
                "enabled": True,
                "twists": contents,
                "content": contents[0],
                "marker": twist_marker(1),
            }
    if not get("twist_enabled"):
        return None
    content = _normalize_content(get("twist_content"))
    if not content:
        return None
    return {
        "enabled": True,
        "content": content,
        "twists": [content],
        "marker": LEGACY_MARKER,
    }
