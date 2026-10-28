from __future__ import annotations

from typing import TypedDict, Annotated, List, Optional, Literal, Any
from pydantic import BaseModel, Field, field_validator
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# =====================
# Structured Data Models
# =====================

def _coerce_str_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, (list, tuple)):
        out: List[str] = []
        for item in v:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    s = str(v).strip()
    return [s] if s else []


class CharacterProfile(BaseModel):
    name: str = Field(..., description="Character's full name")
    background: str = Field(
        ...,
        description="Personal history and formative experiences (do not invent major biography the user did not supply)",
    )
    personality_traits: List[str] = Field(..., description="List of core personality traits")
    motivations: List[str] = Field(..., description="What drives this character?")
    relationships: dict[str, str] = Field(
        default_factory=dict,
        description="Map of other character names to relationship descriptions",
    )
    voice_style: str = Field(..., description="How this character speaks and thinks")
    role_in_story: Optional[str] = Field(
        None, description="Protagonist, antagonist, supporting character, etc."
    )
    # --- Internal enrichment (behavioral, observable) ---
    attention_bias: List[str] = Field(
        default_factory=list,
        description=(
            "REQUIRED in generation. Observable things this character notices first "
            "(occupational / habit filters). Concrete, not abstract adjectives. "
            "Accepts str or list[str]; normalized to list."
        ),
    )
    stress_response: str = Field(
        default="",
        description=(
            "REQUIRED in generation. Observable body or behavior under pressure "
            "(e.g. freezes mid-count, talks faster, leaves the room)."
        ),
    )
    speech_pattern: str = Field(
        default="",
        description=(
            "REQUIRED in generation. Concrete speech rhythm: short sentences / circles before the point / "
            "data-then-conclusion / interrupts / trails off / etc."
        ),
    )
    avoidances: List[str] = Field(
        default_factory=list,
        description=(
            "Optional but preferred. Topics, emotions, or behaviors this character dodges."
        ),
    )
    # Voice uniqueness (P0)
    image_system: List[str] = Field(
        default_factory=list,
        description=(
            "2–3 private recurring images/objects unique to this character in THIS story "
            "(not generic coffee-counter props unless the seed requires them)."
        ),
    )
    rhythm_signature: str = Field(
        default="",
        description="Sentence rhythm: e.g. long then cut; questions only; fragments; lists.",
    )
    taboo_register: str = Field(
        default="",
        description="Tone this character must never use (sermon, banter, melodrama, etc.).",
    )
    introversion_extraversion: Optional[int] = Field(
        None, description="0=highly introverted, 100=highly extraverted"
    )
    dominance: Optional[int] = Field(
        None, description="0=submissive/yielding, 100=highly dominant"
    )
    empathy: Optional[int] = Field(
        None, description="0=low empathy, 100=highly empathetic"
    )
    principled_flexible: Optional[int] = Field(
        None, description="0=strictly principled, 100=highly flexible/pragmatic"
    )

    @field_validator("attention_bias", "avoidances", "image_system", mode="before")
    @classmethod
    def _list_fields(cls, v: Any) -> List[str]:
        return _coerce_str_list(v)

    @field_validator("stress_response", "speech_pattern", "rhythm_signature", "taboo_register", mode="before")
    @classmethod
    def _nonempty_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()


class StoryEvaluation(BaseModel):
    scores: dict[str, float] = Field(default_factory=dict)
    total_score: float = 0.0
    revision_notes: List[str] = Field(default_factory=list)
    actual_word_count: int = 0
    detail_level: int = 3
    # Style / perspective
    style_match: float = 0.0
    perspective_match: float = 0.0
    style_ok: bool = True
    perspective_ok: bool = True
    requested_style: str = ""
    requested_perspective: str = ""
    observed_perspective: Optional[str] = None
    # Core fidelity (Phase B render)
    fidelity_score: float = 10.0
    fidelity_ok: bool = True
    missing_core_facts: List[str] = Field(default_factory=list)
    invented_major_facts: List[str] = Field(default_factory=list)
    # Character enrichment / camera discipline
    character_behavior_match: float = 0.0
    camera_discipline: float = 0.0
    voice_distinctiveness: float = 0.0
    # Product acceptance (information coverage hard; word band soft)
    acceptance_ok: bool = False
    in_word_band: bool = False
    enrichment_in_prose_ok: bool = False
    info_ok: bool = False
    info_coverage: Optional[dict] = None
    # Human-readable reasons for frontend / logs
    acceptance_summary: str = ""
    failure_reasons: List[str] = Field(default_factory=list)
    pass_reasons: List[str] = Field(default_factory=list)
    reasons: List[dict] = Field(
        default_factory=list,
        description="Structured reason rows: {code, ok, message}",
    )
    enrichment_probe: Optional[dict] = None


class WorldSetting(BaseModel):
    """World / setting card (generated internally before plot when needed)."""

    title: Optional[str] = Field(None, description="Short world card title")
    time_period: Optional[str] = Field(None, description="When the story takes place")
    location: Optional[str] = Field(None, description="Primary place / geography")
    atmosphere: Optional[str] = Field(None, description="Mood / sensory atmosphere")
    social_context: Optional[str] = Field(
        None, description="Class, community, institutions, norms that matter"
    )
    rules_or_constraints: Optional[str] = Field(
        None, description="Hard limits: laws, secrets, resources, deadlines"
    )
    key_places: List[str] = Field(
        default_factory=list,
        description="Named locations the story may use",
    )
    conflicts_latent: Optional[str] = Field(
        None, description="Tensions already present in the world"
    )
    additional_notes: Optional[str] = None


# ---- Phase A: screenplay-like StoryCore (structured beats, no literary prose) ----

BeatType = Literal["action", "dialogue", "discovery", "transition"]


class Beat(BaseModel):
    """One executable screenplay beat (sparse rhetoric)."""

    beat_type: BeatType = Field(
        ...,
        description="action | dialogue | discovery | transition",
    )
    character: Optional[str] = Field(
        None,
        description="Speaker or primary actor; null for pure environment/transition",
    )
    content: str = Field(
        ...,
        description="Action line or dialogue gist — concise, executable, little rhetoric",
    )
    focus_chain: Optional[List[str]] = Field(
        None,
        description=(
            "Optional camera focus chain for KEY moments only "
            "(e.g. ['cliff', 'hands', 'tide chart']). Omit on ordinary dialogue beats."
        ),
    )


class Scene(BaseModel):
    """One scene unit — location + goal + ordered beats."""

    scene_id: str = Field(..., description="Stable id, e.g. S1, S2")
    location: str = Field(..., description="Concrete place")
    setting_backdrop: str = Field(
        ...,
        description="Environment / atmosphere / time-of-day backdrop (factual, not lyrical)",
    )
    scene_goal: str = Field(
        ...,
        description="Narrative function of this scene (what it must accomplish)",
    )
    characters_present: List[str] = Field(
        default_factory=list,
        description="Character names present",
    )
    beats: List[Beat] = Field(
        ...,
        description="About 4–8 beats; mix of action/dialogue/discovery/transition",
    )


class StoryCore(BaseModel):
    """Frozen plot bible: multi-scene screenplay skeleton. No literary style."""

    premise_summary: str = Field(..., description="1–3 sentences, factual")
    scenes: List[Scene] = Field(
        ...,
        description="Ordered scenes; each with location, backdrop, goal, and beats",
    )
    # Optional fidelity anchors (kept for evaluator / render lock)
    overall_conflict: str = Field(
        default="",
        description="Central conflict in plain terms",
    )
    ending_facts: List[str] = Field(
        default_factory=list,
        description="Outcome facts every rendering must preserve",
    )
    character_arcs: dict[str, str] = Field(
        default_factory=dict,
        description="Name -> factual arc",
    )
    forbidden_changes: List[str] = Field(
        default_factory=list,
        description="What renderers must not alter",
    )
    setting: WorldSetting = Field(
        default_factory=WorldSetting,
        description="World snapshot used by the core",
    )


# Backward-compat alias (old import name CoreScene → Scene)
CoreScene = Scene


# Legacy raw story (filled from StoryCore when useful)
class RawStoryScene(BaseModel):
    scene_number: int
    summary: str
    key_interactions: List[str]
    emotional_tone: str
    important_dialogue_or_actions: str


class RawInteractionStory(BaseModel):
    premise: str
    scenes: List[RawStoryScene]
    overall_conflict: str
    character_arcs: dict[str, str]


class NarrativePerspective(BaseModel):
    perspective_type: Literal[
        "first_person",
        "third_limited",
        "third_omniscient",
        "second_person",
        "multiple_povs",
    ]
    focal_character: Optional[str] = None
    rationale: str = Field(...)
    time_distance: Literal["immediate", "retrospective", "non_linear"] = "immediate"


class FinalShortStory(BaseModel):
    title: str
    content: str
    word_count: int
    chosen_perspective: NarrativePerspective
    literary_techniques_used: List[str]
    strengths: List[str]
    suggested_improvements: Optional[List[str]] = None


# =====================
# LangGraph State
# =====================

class StoryState(TypedDict):
    thread_id: str
    user_prompt: str  # may become expanded composite after prompt_expander
    original_user_prompt: Optional[str]  # raw user seed (immutable after expand)
    expanded_prompt: Optional[str]  # AI expanded premise (planning text)
    story_outline: Optional[dict]  # structured StoryOutline dump
    scene_count_target: Optional[int]  # from outline / length budget
    current_phase: Literal[
        "setup",
        "awaiting_prompt",
        "prompt_expanding",
        "plot_weaving",
        "core_locked",
        "rendering",
        "evaluating",
        "done",
    ]

    # Loop control (render phase only)
    iteration_count: int
    max_iterations: int
    revision_threshold: float

    # Characters
    characters: List[CharacterProfile]
    user_characters: List[dict]  # optional early drafts

    # Seed → setup
    story_seed: Optional[str]
    theme: Optional[str]

    # Writing preferences (render layer)
    detail_level: int
    story_length: Optional[str]  # short | medium | long — word-band target
    style: str
    preferred_perspective: str
    focal_character: Optional[str]
    world_setting: Optional[WorldSetting]
    expand_prompt: Optional[bool]  # default True; False skips AI expand
    dual_draft: Optional[bool]  # default True; render two candidates, pick more distinctive
    style_pack: Optional[str]  # cold_procedural | warm_domestic | uncanny_quiet | comic_deadpan
    style_pack_meta: Optional[dict]
    structure_meta: Optional[dict]
    signature_device: Optional[str]
    voice_revise_only: Optional[bool]  # evaluator asked for voice-only rewrite
    # User plot twists (optional; zero or more; inserted into premise at chosen points)
    twist_enabled: Optional[bool]
    twist_content: Optional[str]  # first twist / joined legacy
    twists: Optional[List]  # list of {index, content, marker}

    # Phase A output
    story_core: Optional[StoryCore]
    core_locked: bool
    raw_interaction_story: Optional[RawInteractionStory]
    raw_story_feedback: Optional[str]

    chosen_perspective: Optional[NarrativePerspective]
    final_short_story: Optional[FinalShortStory]
    revision_notes: List[str]
    evaluation: Optional[StoryEvaluation]

    messages: Annotated[List[BaseMessage], add_messages]
    last_agent_output: Optional[str]
    error: Optional[str]

    best_final_story: Optional[FinalShortStory]
    best_score: float
