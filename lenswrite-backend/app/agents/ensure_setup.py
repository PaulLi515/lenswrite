"""Graph entry: use user-confirmed cards, or fall back to drafts/seed (tests/legacy)."""
from __future__ import annotations

from app.agents.character_builder import character_builder_node
from app.agents.setup_builder import generate_setup
from app.models import CharacterProfile, StoryState, WorldSetting


def _enrichment_defaults(data: dict) -> dict:
    """Fill required enrichment fields for older / partial payloads."""
    d = dict(data)
    bias = d.get("attention_bias")
    if not bias:
        d["attention_bias"] = ["immediate practical details of the situation"]
    elif isinstance(bias, str):
        d["attention_bias"] = [bias] if bias.strip() else ["immediate practical details of the situation"]
    if not (d.get("stress_response") or "").strip():
        d["stress_response"] = "goes quiet and attends to a small controllable task"
    if not (d.get("speech_pattern") or "").strip():
        d["speech_pattern"] = (d.get("voice_style") or "brief functional sentences").strip()
    if d.get("avoidances") is None:
        d["avoidances"] = []
    return d


def _coerce_characters(raw) -> list:
    out = []
    for c in raw or []:
        if isinstance(c, CharacterProfile):
            # Re-validate / fill if model somehow empty
            try:
                dump = c.model_dump()
                out.append(CharacterProfile(**_enrichment_defaults(dump)))
            except Exception:
                out.append(c)
        elif isinstance(c, dict):
            try:
                out.append(CharacterProfile(**_enrichment_defaults(c)))
            except Exception:
                continue
        else:
            out.append(c)
    return out


def ensure_setup_node(state: StoryState) -> dict:
    """Prefer pre-confirmed characters+world; otherwise generate from seed/drafts."""
    print(">>> [ensure_setup_node] 检查角色卡 / 世界卡")
    characters = _coerce_characters(state.get("characters"))
    world = state.get("world_setting")

    if characters:
        print(f"    使用已确认角色卡 x{len(characters)}")
        if world is None:
            world = WorldSetting(
                additional_notes=(state.get("user_prompt") or "")[:400] or None
            )
        return {
            "characters": characters,
            "world_setting": world,
            "current_phase": "plot_weaving",
            "last_agent_output": f"Setup ready ({len(characters)} characters).",
        }

    # Legacy / batch tests: expand drafts via character_builder
    user_chars = state.get("user_characters") or []
    if user_chars:
        print(f"    从草稿扩展角色卡 x{len(user_chars)}")
        built = character_builder_node(state)
        characters = _coerce_characters(built.get("characters"))
        if world is None:
            try:
                bundle = generate_setup(
                    seed=state.get("user_prompt") or state.get("story_seed") or "",
                    theme=state.get("theme"),
                    character_drafts=user_chars,
                )
                # Prefer world from setup; keep character_builder cards (slider-aware)
                world = bundle.world
            except Exception as e:
                print(f"    [warn] world setup fallback: {e}")
                world = WorldSetting()
        return {
            "characters": characters,
            "world_setting": world,
            "current_phase": "plot_weaving",
            "last_agent_output": f"Setup from drafts ({len(characters)} characters).",
        }

    # Fully auto from prompt seed
    seed = (state.get("story_seed") or state.get("user_prompt") or "").strip()
    n_target = int(state.get("target_character_count") or 2)
    n_target = max(2, min(5, n_target))
    print(f"    从种子自动生成角色卡 + 世界卡 (n={n_target})")
    bundle = generate_setup(
        seed=seed or "A short literary encounter between two people.",
        theme=state.get("theme"),
        character_drafts=[],
        target_character_count=n_target,
    )
    return {
        "characters": bundle.characters,
        "world_setting": bundle.world,
        "current_phase": "plot_weaving",
        "last_agent_output": f"Auto setup ({len(bundle.characters)} characters).",
    }
