"""Product acceptance: information coverage (hard) + soft word diagnostics.

Used by evaluator, status API, regression harness, and info-driven expand.
"""
from __future__ import annotations

import re
from typing import Any

from app.detail_level import (
    PRODUCT_WORD_IDEAL,
    PRODUCT_WORD_MAX,
    PRODUCT_WORD_MIN,
    product_word_band,
)

# ---- Product policy (single source of truth) ----
ACCEPTANCE_POLICY = {
    # Soft diagnostic only — not required for acceptance_ok
    "word_min": PRODUCT_WORD_MIN,
    "word_max": PRODUCT_WORD_MAX,
    "word_ideal": PRODUCT_WORD_IDEAL,
    "word_is_hard": False,
    "fidelity_min": 8.0,
    "style_min": 7.0,
    "perspective_min": 7.0,
    "behavior_min": 7.0,
    "camera_min": 7.0,
    # Soft: enrichment should leave observable traces in prose
    "require_enrichment_in_prose": True,
    "enrichment_min_hits_per_focal": 1,
    # Hard: StoryCore / twist information coverage
    "info_coverage_min": 0.85,
    "info_develop_min": 0.70,
    "info_critical_thin_max": 0,  # ending/discovery/twist may not be thin
    "info_min_token_hits": 1,
    "info_develop_min_hits": 2,  # ≥2 content tokens ≈ more than a one-word nod
    "info_develop_min_span_chars": 80,  # local window around first hit
}

# ---- Expand / trim: information first (word count is diagnostic only) ----
EXPAND_HARD_RULES = """
HARD EXPAND RULES (information density — not padding for word count):
ALLOW:
  - Fully render under-covered beats: action + reaction, dialogue + reply
  - Gesture, sensory detail, setting texture that carries a beat
  - Scene openings/transitions that make scene_goal readable
  - Interiority consistent with focal character and attention_bias
  - stress_response under tension; speech_pattern in dialogue
  - Develop thin discoveries / ending facts / user twists into a lived moment
FORBID:
  - New major plot, new characters, new locations not in StoryCore
  - New discoveries or changed ending_facts
  - Copy-paste repeated paragraphs or synonym-loop filler
  - Empty camera stacks (exterior→hand→object every paragraph)
  - Essay digressions or moral summary monologues
  - Inventing plot only to increase word count
STOP when every required information unit is present and developed (not thin).
Word count is secondary — do not pad after information is complete.
"""

TRIM_HARD_RULES = """
HARD TRIM RULES (anti-mistaken cuts / anti-padding):
MUST KEEP:
  - All ending_facts and discovery beats from StoryCore
  - User-specified twists
  - Scene goals and who is present at major turns
  - Character names and focal POV
  - At least one clear stress_response under pressure if present in draft
  - Distinct dialogue that advances the scene
MAY REMOVE:
  - Repeated sentences / synonym loops
  - Empty atmospheric paragraphs with no beat progress
  - Redundant camera inserts
  - Padding that restates the same fact twice
NEVER remove a paragraph that is the only carrier of a discovery, ending fact, or twist.
Trim for redundancy, not to hit an arbitrary word ceiling.
"""


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    return [str(v).strip()] if str(v).strip() else []


def _tokens_from_text(text: str, *, min_len: int = 4) -> list[str]:
    """Extract content tokens for loose prose matching."""
    words = re.findall(r"[A-Za-z][A-Za-z']+", text or "")
    stop = {
        "that", "this", "with", "from", "have", "will", "when", "what", "they",
        "them", "their", "there", "then", "than", "into", "about", "would",
        "could", "should", "under", "over", "after", "before", "while", "where",
        "which", "whose", "being", "been", "were", "was", "are", "and", "the",
        "her", "his", "she", "him", "you", "your", "not", "but", "for", "all",
        "any", "can", "may", "does", "did", "done", "just", "only", "also",
        "very", "more", "most", "some", "such", "like", "into", "onto",
        "highly", "always", "never", "often", "still", "even", "much", "many",
        "each", "other", "between", "through", "during", "without", "because",
        "character", "person", "people", "something", "someone", "anything",
    }
    out = []
    for w in words:
        lw = w.lower()
        if len(lw) < min_len or lw in stop:
            continue
        out.append(lw)
    # unique preserve order
    seen = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def enrichment_cues_for_character(char: Any) -> dict:
    """Collect enrichment strings + match tokens for one character."""
    if hasattr(char, "model_dump"):
        d = char.model_dump()
    elif isinstance(char, dict):
        d = char
    else:
        d = {
            "name": getattr(char, "name", None),
            "attention_bias": getattr(char, "attention_bias", None),
            "stress_response": getattr(char, "stress_response", None),
            "speech_pattern": getattr(char, "speech_pattern", None),
            "avoidances": getattr(char, "avoidances", None),
            "image_system": getattr(char, "image_system", None),
            "rhythm_signature": getattr(char, "rhythm_signature", None),
        }
    attention = _as_list(d.get("attention_bias"))
    stress = (d.get("stress_response") or "").strip()
    speech = (d.get("speech_pattern") or "").strip()
    avoid = _as_list(d.get("avoidances"))
    images = _as_list(d.get("image_system"))
    rhythm = (d.get("rhythm_signature") or "").strip()
    cue_texts = (
        attention
        + ([stress] if stress else [])
        + ([speech] if speech else [])
        + images
        + ([rhythm] if rhythm else [])
    )
    tokens: list[str] = []
    for t in cue_texts:
        tokens.extend(_tokens_from_text(t, min_len=4))
    # de-dupe
    seen = set()
    tok_u = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            tok_u.append(t)
    return {
        "name": d.get("name") or "Unknown",
        "attention_bias": attention,
        "stress_response": stress,
        "speech_pattern": speech,
        "avoidances": avoid,
        "image_system": images,
        "tokens": tok_u[:28],
    }


def probe_enrichment_in_prose(
    prose: str,
    characters: list | None,
    *,
    focal_name: str | None = None,
) -> dict:
    """Deterministic check: do enrichment cues leave traces in prose?

    Returns hits per character and overall ok flag for acceptance.
    """
    text = (prose or "").lower()
    chars = characters or []
    per_char = []
    focal = (focal_name or "").strip().lower()
    focal_hits = 0
    any_named = False

    for c in chars:
        cues = enrichment_cues_for_character(c)
        name = cues["name"]
        name_l = name.lower()
        name_in = bool(name_l) and (name_l in text or name_l.split()[0] in text)
        if name_in:
            any_named = True
        matched = [t for t in cues["tokens"] if t in text]
        hit_count = len(matched)
        row = {
            "name": name,
            "name_in_prose": name_in,
            "token_hits": matched[:12],
            "hit_count": hit_count,
            "has_attention": bool(cues["attention_bias"]),
            "has_stress": bool(cues["stress_response"]),
            "has_speech": bool(cues["speech_pattern"]),
        }
        per_char.append(row)
        if focal and (focal in name_l or name_l in focal or name_l.split()[0] in focal):
            focal_hits = hit_count

    # If no focal match, use max hits among characters present by name
    if not focal or focal_hits == 0:
        named = [r for r in per_char if r["name_in_prose"]]
        focal_hits = max((r["hit_count"] for r in named), default=0)
        if not named and per_char:
            focal_hits = max(r["hit_count"] for r in per_char)

    min_hits = int(ACCEPTANCE_POLICY["enrichment_min_hits_per_focal"])
    enrichment_ok = (not ACCEPTANCE_POLICY["require_enrichment_in_prose"]) or (
        any_named and focal_hits >= min_hits
    ) or (
        # no characters → skip
        len(chars) == 0
    )

    return {
        "enrichment_ok": enrichment_ok,
        "focal_hit_count": focal_hits,
        "min_hits_required": min_hits,
        "characters": per_char,
        "summary": (
            f"enrichment_in_prose={'OK' if enrichment_ok else 'WEAK'}: "
            f"focal_token_hits={focal_hits} (need ≥{min_hits}); "
            f"named_chars={sum(1 for r in per_char if r['name_in_prose'])}/{len(per_char)}"
        ),
    }


# ---------------------------------------------------------------------------
# Information coverage (primary hard gate instead of word count)
# ---------------------------------------------------------------------------


def _core_as_dict(core: Any) -> dict:
    if core is None:
        return {}
    if hasattr(core, "model_dump"):
        return core.model_dump()
    if isinstance(core, dict):
        return core
    return {}


def _significant_tokens(text: str, *, min_len: int = 4, limit: int = 10) -> list[str]:
    """Content tokens for matching a required info unit in prose."""
    toks = _tokens_from_text(text, min_len=min_len)
    # Prefer rarer / longer tokens first
    toks = sorted(toks, key=lambda t: (-len(t), t))
    return toks[:limit]


def _window_around(text: str, needle: str, *, radius: int = 120) -> str:
    """Local prose window around first occurrence of needle (case-insensitive)."""
    if not text or not needle:
        return ""
    low = text.lower()
    n = needle.lower()
    i = low.find(n)
    if i < 0:
        return ""
    a = max(0, i - radius)
    b = min(len(text), i + len(needle) + radius)
    return text[a:b]


def _score_unit_in_prose(prose: str, unit_text: str) -> dict:
    """Match one required unit against prose; classify present / thin / missing."""
    prose_l = (prose or "").lower()
    tokens = _significant_tokens(unit_text)
    hits = [t for t in tokens if t in prose_l]
    hit_count = len(hits)
    min_hits = int(ACCEPTANCE_POLICY["info_min_token_hits"])
    develop_hits = int(ACCEPTANCE_POLICY["info_develop_min_hits"])
    min_span = int(ACCEPTANCE_POLICY["info_develop_min_span_chars"])

    present = hit_count >= min_hits
    # Depth: enough distinct tokens OR a non-trivial local window around first hit
    span = ""
    if hits:
        span = _window_around(prose, hits[0], radius=max(40, min_span // 2))
    span_words = len(re.findall(r"\b\w+\b", span)) if span else 0
    developed = present and (
        hit_count >= develop_hits or span_words >= 12 or len(span) >= min_span
    )
    if not present:
        status = "missing"
    elif not developed:
        status = "thin"
    else:
        status = "developed"

    return {
        "status": status,
        "present": present,
        "developed": developed,
        "thin": present and not developed,
        "hit_count": hit_count,
        "token_hits": hits[:8],
        "span_words": span_words,
    }


def collect_required_info_units(
    core: Any,
    *,
    twists: list | None = None,
) -> list[dict]:
    """Build checklist of required information units from StoryCore + user twists."""
    data = _core_as_dict(core)
    units: list[dict] = []

    for f in data.get("ending_facts") or []:
        text = str(f).strip()
        if text:
            units.append({
                "kind": "ending_fact",
                "critical": True,
                "id": f"ending:{len(units)}",
                "text": text,
            })

    for si, s in enumerate(data.get("scenes") or [], start=1):
        sid = (s.get("scene_id") or f"S{si}").strip()
        goal = (s.get("scene_goal") or "").strip()
        if goal:
            units.append({
                "kind": "scene_goal",
                "critical": False,
                "id": f"{sid}:goal",
                "text": goal,
                "scene_id": sid,
            })
        # Prefer discovery beats; also keep a few strong action beats if few discoveries
        discoveries = []
        other_beats = []
        for bi, b in enumerate(s.get("beats") or [], start=1):
            content = (b.get("content") or "").strip()
            if not content:
                continue
            btype = (b.get("beat_type") or "action").lower()
            row = {
                "kind": "discovery" if btype == "discovery" else "beat",
                "critical": btype == "discovery",
                "id": f"{sid}:b{bi}",
                "text": content,
                "scene_id": sid,
                "beat_type": btype,
            }
            if btype == "discovery":
                discoveries.append(row)
            else:
                other_beats.append(row)
        units.extend(discoveries)
        # Sample up to 2 non-discovery beats per scene for density (not every glance)
        for row in other_beats[:2]:
            units.append(row)

    # User twists
    for i, t in enumerate(twists or [], start=1):
        if isinstance(t, dict):
            text = (t.get("content") or t.get("text") or "").strip()
            idx = t.get("index") or i
        else:
            text = str(t).strip()
            idx = i
        if text:
            units.append({
                "kind": "twist",
                "critical": True,
                "id": f"twist:{idx}",
                "text": text,
            })

    return units


def probe_info_coverage(
    prose: str,
    core: Any,
    *,
    twists: list | None = None,
) -> dict:
    """Deterministic information coverage: present + developed vs thin/missing.

    Hard pass when coverage_ratio and develop_ratio meet thresholds and no
    critical unit is missing or thin.
    """
    units = collect_required_info_units(core, twists=twists)
    scored = []
    for u in units:
        m = _score_unit_in_prose(prose, u["text"])
        scored.append({**u, **m})

    n = len(scored)
    if n == 0:
        # No core → cannot fail on info; treat as ok
        return {
            "info_ok": True,
            "coverage_ratio": 1.0,
            "develop_ratio": 1.0,
            "n_units": 0,
            "n_present": 0,
            "n_developed": 0,
            "n_thin": 0,
            "n_missing": 0,
            "n_critical_thin": 0,
            "n_critical_missing": 0,
            "missing": [],
            "thin": [],
            "units": [],
            "summary": "info_coverage=OK (no StoryCore units to check)",
            "expand_targets": [],
        }

    n_present = sum(1 for u in scored if u["present"])
    n_developed = sum(1 for u in scored if u["developed"])
    n_thin = sum(1 for u in scored if u["thin"])
    n_missing = sum(1 for u in scored if u["status"] == "missing")
    critical_thin = [u for u in scored if u.get("critical") and u["thin"]]
    critical_missing = [u for u in scored if u.get("critical") and u["status"] == "missing"]

    coverage = n_present / n
    develop = n_developed / n
    cov_min = float(ACCEPTANCE_POLICY["info_coverage_min"])
    dev_min = float(ACCEPTANCE_POLICY["info_develop_min"])
    crit_thin_max = int(ACCEPTANCE_POLICY["info_critical_thin_max"])

    info_ok = (
        coverage >= cov_min
        and develop >= dev_min
        and len(critical_missing) == 0
        and len(critical_thin) <= crit_thin_max
    )

    missing = [
        {"id": u["id"], "kind": u["kind"], "text": u["text"][:120]}
        for u in scored
        if u["status"] == "missing"
    ]
    thin = [
        {"id": u["id"], "kind": u["kind"], "text": u["text"][:120]}
        for u in scored
        if u["thin"]
    ]
    expand_targets = []
    for u in scored:
        if u["status"] not in ("missing", "thin"):
            continue
        if u.get("kind") == "twist":
            expand_targets.append(
                f"[twist] DEEPEN (do not quote): {u['text'][:120]} "
                f"— layered evidence + focal stress_response + one private behavior change"
            )
        else:
            expand_targets.append(f"[{u['kind']}] {u['text'][:160]}")
    expand_targets = expand_targets[:12]

    return {
        "info_ok": info_ok,
        "coverage_ratio": round(coverage, 3),
        "develop_ratio": round(develop, 3),
        "n_units": n,
        "n_present": n_present,
        "n_developed": n_developed,
        "n_thin": n_thin,
        "n_missing": n_missing,
        "n_critical_thin": len(critical_thin),
        "n_critical_missing": len(critical_missing),
        "missing": missing[:12],
        "thin": thin[:12],
        "units": [
            {
                "id": u["id"],
                "kind": u["kind"],
                "critical": bool(u.get("critical")),
                "status": u["status"],
                "text": u["text"][:100],
                "hit_count": u["hit_count"],
            }
            for u in scored
        ],
        "expand_targets": expand_targets,
        "thresholds": {
            "coverage_min": cov_min,
            "develop_min": dev_min,
            "critical_thin_max": crit_thin_max,
        },
        "summary": (
            f"info_coverage={'OK' if info_ok else 'WEAK'}: "
            f"present={n_present}/{n} ({coverage:.0%}), "
            f"developed={n_developed}/{n} ({develop:.0%}), "
            f"thin={n_thin}, missing={n_missing}, "
            f"critical_missing={len(critical_missing)}, critical_thin={len(critical_thin)}"
        ),
    }


def format_info_expand_brief(info_probe: dict | None) -> str:
    """Human brief for expand / revision prompts."""
    if not info_probe:
        return "(no info probe)"
    lines = [info_probe.get("summary") or ""]
    targets = info_probe.get("expand_targets") or []
    if targets:
        lines.append("Under-covered units to develop (no new plot):")
        for t in targets:
            lines.append(f"  - {t}")
    return "\n".join(lines)


def build_acceptance(
    *,
    actual_words: int,
    fidelity_score: float,
    fidelity_ok: bool,
    style_match: float,
    style_ok: bool,
    perspective_match: float,
    perspective_ok: bool,
    behavior_match: float,
    behavior_ok: bool,
    camera_discipline: float,
    camera_ok: bool,
    enrichment_probe: dict | None = None,
    info_probe: dict | None = None,
    missing_core_facts: list | None = None,
    invented_major_facts: list | None = None,
    revision_notes: list | None = None,
    voice_distinctiveness: float | None = None,
    house_fingerprint: float | None = None,
) -> dict:
    """Compute acceptance_ok + human-readable reasons for logs/frontend.

    Hard: fidelity, style, POV, behavior, enrichment, information coverage.
    Soft: word count (diagnostic), voice distinctiveness, camera (camera stays hard).
    """
    band = product_word_band()
    in_band = band["min"] <= int(actual_words or 0) <= band["max"]
    under = int(actual_words or 0) < band["min"]
    over = int(actual_words or 0) > band["max"]
    enrich = enrichment_probe or {}
    enrichment_ok = bool(enrich.get("enrichment_ok", True))
    info = info_probe or {}
    # If probe not run, do not block on info
    info_ok = bool(info.get("info_ok", True)) if info else True

    reasons: list[dict] = []

    def add(code: str, ok: bool, message: str):
        reasons.append({"code": code, "ok": ok, "message": message})

    # Soft word diagnostics (never alone reject)
    if under:
        add(
            "WORD_SOFT",
            True,  # soft: ok=True so it doesn't look like hard fail; message explains
            f"Word count {actual_words} is under former guide {band['min']} "
            f"(soft only — acceptance uses information coverage, not word floor).",
        )
    elif over:
        add(
            "WORD_SOFT",
            True,
            f"Word count {actual_words} is over former guide {band['max']} "
            f"(soft only — trim if padding; not a hard reject).",
        )
    else:
        add(
            "WORD_SOFT",
            True,
            f"Word count {actual_words} (soft guide {band['min']}–{band['max']}).",
        )

    add(
        "INFO_COVERAGE",
        info_ok,
        info.get("summary")
        or (
            "Information coverage: OK"
            if info_ok
            else "Information coverage: weak — missing/thin StoryCore or twist units"
        ),
    )

    add(
        "FIDELITY",
        fidelity_ok,
        (
            f"Fidelity {fidelity_score:.1f}/10 "
            f"({'pass' if fidelity_ok else f'fail, need ≥{ACCEPTANCE_POLICY['fidelity_min']}'})"
            + (
                f"; missing: {', '.join(missing_core_facts[:4])}"
                if missing_core_facts and not fidelity_ok
                else ""
            )
            + (
                f"; invented: {', '.join(invented_major_facts[:3])}"
                if invented_major_facts and not fidelity_ok
                else ""
            )
        ),
    )
    add(
        "STYLE",
        style_ok,
        f"Style match {style_match:.1f}/10 ({'pass' if style_ok else 'fail'}).",
    )
    add(
        "PERSPECTIVE",
        perspective_ok,
        f"Perspective match {perspective_match:.1f}/10 ({'pass' if perspective_ok else 'fail'}).",
    )
    add(
        "BEHAVIOR",
        behavior_ok,
        f"Character behavior {behavior_match:.1f}/10 "
        f"({'pass' if behavior_ok else 'fail — speech/stress/attention may not show in prose'}).",
    )
    add(
        "CAMERA",
        camera_ok,
        f"Camera discipline {camera_discipline:.1f}/10 ({'pass' if camera_ok else 'fail'}).",
    )
    add(
        "ENRICHMENT_IN_PROSE",
        enrichment_ok,
        enrich.get("summary")
        or (
            "Enrichment probe: OK"
            if enrichment_ok
            else "Enrichment probe: weak traces of attention_bias/stress/speech in prose"
        ),
    )

    # Soft signal only — does not flip acceptance_ok by itself
    if voice_distinctiveness is not None:
        voice_soft_ok = float(voice_distinctiveness) >= 6.0
        fp_bit = (
            f"; house_fingerprint={house_fingerprint}"
            if house_fingerprint is not None
            else ""
        )
        add(
            "VOICE",
            voice_soft_ok,
            f"Voice distinctiveness {float(voice_distinctiveness):.1f}/10 "
            f"({'ok' if voice_soft_ok else 'soft-warn — may trigger voice-only revise'})"
            f"{fp_bit}.",
        )

    # Hard acceptance: information + quality gates (NOT word band)
    acceptance_ok = bool(
        info_ok
        and fidelity_ok
        and perspective_ok
        and behavior_ok
        and style_ok
        and enrichment_ok
    )

    soft_codes = {"VOICE", "WORD_SOFT", "WORD_FLOOR", "WORD_CEILING", "WORD_BAND"}
    fail_msgs = [
        r["message"] for r in reasons if not r["ok"] and r["code"] not in soft_codes
    ]
    pass_msgs = [r["message"] for r in reasons if r["ok"]]

    readable = {
        "acceptance_ok": acceptance_ok,
        "in_word_band": in_band,
        "enrichment_in_prose_ok": enrichment_ok,
        "info_ok": info_ok,
        "info_coverage": info or None,
        "word_count": actual_words,
        "word_band": band,
        "word_is_hard": False,
        "policy": dict(ACCEPTANCE_POLICY),
        "reasons": reasons,
        "failure_reasons": fail_msgs,
        "pass_reasons": pass_msgs,
        "summary": (
            "ACCEPT"
            if acceptance_ok
            else "REJECT — " + ("; ".join(fail_msgs[:4]) if fail_msgs else "quality gates failed")
        ),
        "revision_notes": list(revision_notes or []),
    }
    return readable

