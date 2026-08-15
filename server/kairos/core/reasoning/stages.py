"""The standard pipeline.

Each stage carries the reason for its position. Those reasons were learned the
expensive way in the system this replaces — every one of them corresponds to a
bug that ordering caused — so they are recorded next to the constraint that
prevents them rather than in a comment beside a list.
"""

from __future__ import annotations

from kairos.core.reasoning.pipeline import (
    Phase,
    Pipeline,
    Provides,
    Stage,
    StageName,
)


def name(value: str) -> StageName:
    return StageName(value)


# --- outer: runs once per turn ---------------------------------------------

OVERSIZED_RESULTS = Stage(
    name=name("oversized-results"),
    phase=Phase.INTAKE,
    outside_of=frozenset({name("identity"), name("history")}),
    why=(
        "Outermost, and declared so rather than merely described so. Swaps a "
        "huge tool result for a reference before anything else has to carry "
        "it — redaction, provenance and compaction all become cheaper for "
        "having never seen the full text."
    ),
)

IDENTITY = Stage(
    name=name("identity"),
    phase=Phase.INTAKE,
    provides=frozenset({Provides.IDENTITY}),
    why="Establishes who the turn belongs to; everything downstream scopes on it.",
)

HISTORY = Stage(
    name=name("history"),
    phase=Phase.CONTEXT,
    provides=frozenset({Provides.HISTORY}),
    requires=frozenset({Provides.IDENTITY}),
    why="Loads the conversation. Needs identity to know whose.",
)

MEDIA_CAPTURE = Stage(
    name=name("media-capture"),
    phase=Phase.CONTEXT,
    outside_of=frozenset({name("retry")}),
    why=(
        "Outside retry: it uploads images found in the final answer. Inside, "
        "every retry would re-upload them."
    ),
)

COMPACTION = Stage(
    name=name("compaction"),
    phase=Phase.CONTEXT,
    provides=frozenset({Provides.COMPACTION}),
    requires=frozenset({Provides.HISTORY}),
    outside_of=frozenset({name("retry")}),
    why=(
        "Outside retry: summarising is expensive and its result is valid for "
        "every attempt. Inside, a flapping provider would pay for it repeatedly."
    ),
)

# --- the cache boundary ----------------------------------------------------

CACHE_BREAKPOINT = Stage(
    name=name("cache-breakpoint"),
    phase=Phase.CACHE_BOUNDARY,
    provides=frozenset({Provides.CACHE_BREAKPOINT}),
    why=(
        "Marks where the reusable prefix ends. Everything before it must be "
        "byte-stable across turns; everything after is free to change."
    ),
)

# --- the retry boundary ----------------------------------------------------

RETRY = Stage(
    name=name("retry"),
    phase=Phase.DISPATCH,
    provides=frozenset({Provides.RETRY, Provides.MODEL_SELECTION}),
    why=(
        "The dividing line. Stages outside run once per turn; stages inside "
        "run again on every attempt and see the model actually chosen."
    ),
)

# --- inner: runs per attempt, sees the post-fallback model -----------------

MODALITY_FIT = Stage(
    name=name("modality-fit"),
    phase=Phase.ADAPT,
    provides=frozenset({Provides.MODALITY_FIT}),
    requires=frozenset({Provides.MODEL_SELECTION}),
    inside_of=frozenset({name("retry")}),
    why=(
        "Inside retry, and the reason the boundary matters. It strips content "
        "the chosen model cannot accept. Outside, a fallback from a vision "
        "model to a text-only one would replay the images that caused the "
        "failure and fail again for the same reason."
    ),
)

PROVIDER_MARKERS = Stage(
    name=name("provider-markers"),
    phase=Phase.ADAPT,
    requires=frozenset({Provides.MODEL_SELECTION, Provides.CACHE_BREAKPOINT}),
    inside_of=frozenset({name("retry")}),
    why=(
        "Inside retry: cache markers are vendor-specific, so one vendor's must "
        "never travel to another's request after a fallback."
    ),
)

LIVE_CONTEXT = Stage(
    name=name("live-context"),
    phase=Phase.ADAPT,
    inside_of=frozenset({name("cache-breakpoint")}),
    why=(
        "Inside the breakpoint: the current time and other per-turn facts "
        "would invalidate the cached prefix on every single turn."
    ),
)

REASONING_COMPAT = Stage(
    name=name("reasoning-compat"),
    phase=Phase.ADAPT,
    requires=frozenset({Provides.MODEL_SELECTION}),
    inside_of=frozenset({name("retry")}),
    why=(
        "Innermost. Reasoning blocks are opaque and vendor-private; replaying "
        "one vendor's to another is rejected outright. Only here is the target "
        "model finally known."
    ),
)


STANDARD_STAGES: tuple[Stage, ...] = (
    OVERSIZED_RESULTS,
    IDENTITY,
    HISTORY,
    MEDIA_CAPTURE,
    COMPACTION,
    CACHE_BREAKPOINT,
    RETRY,
    MODALITY_FIT,
    PROVIDER_MARKERS,
    LIVE_CONTEXT,
    REASONING_COMPAT,
)


def standard_pipeline() -> Pipeline:
    """The default assembly.

    Declared unordered on purpose: the tuple above is a set of stages, not a
    sequence, and the order comes from the constraints. Reordering the literal
    changes nothing, which is the property the old middleware list lacked.
    """
    return Pipeline.assemble(STANDARD_STAGES)
