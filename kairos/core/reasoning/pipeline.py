"""Composing a turn out of stages that declare what they need.

The reference implementation composed this as a list of about thirty
middlewares where position was the contract. Retry had to sit inside image
capture or every retry re-fetched the images; multimodal stripping had to sit
inside retry or a fallback to a text-only model replayed the pictures that
caused the failure; the prompt-cache breakpoint had to sit between the static
prefix and everything that changes per turn. All of that was true, all of it
was load-bearing, and all of it was recorded in comments beside the list.

Here a stage names what it must run inside and outside of, and assembly sorts
them. The same constraints, checked by the machine at startup instead of by a
reviewer noticing an insertion in the wrong place.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Final, NewType, Self

StageName = NewType("StageName", str)


class Phase(StrEnum):
    """Coarse ordering bands.

    A stage usually only cares about a handful of neighbours, but everything
    has to end up somewhere relative to everything else. Phases give a default
    ordering so that stages only declare the constraints that actually matter
    to them.
    """

    INTAKE = "intake"
    CONTEXT = "context"
    CACHE_BOUNDARY = "cache_boundary"
    DISPATCH = "dispatch"
    ADAPT = "adapt"


_PHASE_ORDER: Final[tuple[Phase, ...]] = (
    Phase.INTAKE,
    Phase.CONTEXT,
    Phase.CACHE_BOUNDARY,
    Phase.DISPATCH,
    Phase.ADAPT,
)


class Provides(StrEnum):
    """Capabilities a stage contributes, so others can depend on them by name.

    Depending on a capability rather than on a class means a stage can be
    replaced by a different implementation of the same job without editing
    everything that needed it.
    """

    IDENTITY = auto()
    HISTORY = auto()
    COMPACTION = auto()
    CACHE_BREAKPOINT = auto()
    MODEL_SELECTION = auto()
    RETRY = auto()
    MODALITY_FIT = auto()


@dataclass(frozen=True, slots=True)
class Stage:
    """One step, and where it must sit relative to the others.

    `inside_of` and `outside_of` are the interesting fields. "Inside" means
    nearer the model: a stage inside the retry boundary runs again on every
    attempt, and one outside it runs once per turn. Getting that backwards is
    the class of bug this design exists to make impossible.
    """

    name: StageName
    phase: Phase = Phase.CONTEXT
    provides: frozenset[Provides] = frozenset()
    inside_of: frozenset[StageName] = frozenset()
    outside_of: frozenset[StageName] = frozenset()
    requires: frozenset[Provides] = frozenset()
    why: str = ""

    def __post_init__(self) -> None:
        if overlap := self.inside_of & self.outside_of:
            raise ValueError(
                f"stage {self.name!r} claims to be both inside and outside "
                f"{', '.join(sorted(overlap))}"
            )
        if self.name in self.inside_of | self.outside_of:
            raise ValueError(f"stage {self.name!r} is positioned relative to itself")


class AssemblyError(Exception):
    """The declared constraints cannot all be satisfied.

    Raised at startup. A pipeline that cannot be ordered is a programming
    error, and discovering it on the first request would mean discovering it
    in production.
    """


class CyclicOrdering(AssemblyError):
    def __init__(self, involved: Iterable[StageName]) -> None:
        self.involved = tuple(sorted(involved))
        super().__init__(
            "stage ordering constraints form a cycle among: "
            + ", ".join(self.involved)
        )


class MissingStage(AssemblyError):
    def __init__(self, referrer: StageName, missing: StageName) -> None:
        super().__init__(
            f"stage {referrer!r} is positioned relative to {missing!r}, "
            "which is not in the pipeline"
        )


class UnmetRequirement(AssemblyError):
    def __init__(self, stage: StageName, capability: Provides) -> None:
        super().__init__(
            f"stage {stage!r} requires {capability!r}, which no stage provides"
        )


class Pipeline:
    """An ordered, validated sequence of stages.

    Outermost first, matching the order they wrap execution: the first entry
    sees the request first and the response last.
    """

    __slots__ = ("_stages",)

    def __init__(self, stages: Sequence[Stage]) -> None:
        self._stages = tuple(stages)

    @classmethod
    def assemble(cls, stages: Iterable[Stage]) -> Self:
        """Order stages by their declared constraints.

        Every failure here is a failure to start, which is the point: the
        alternative is a pipeline that runs in an order nobody intended.
        """
        catalogue = cls._index(stages)
        cls._check_references(catalogue)
        cls._check_requirements(catalogue)
        return cls(cls._topological_order(catalogue))

    # -- validation --------------------------------------------------------

    @staticmethod
    def _index(stages: Iterable[Stage]) -> dict[StageName, Stage]:
        catalogue: dict[StageName, Stage] = {}
        for stage in stages:
            if stage.name in catalogue:
                raise AssemblyError(f"duplicate stage {stage.name!r}")
            catalogue[stage.name] = stage
        return catalogue

    @staticmethod
    def _check_references(catalogue: dict[StageName, Stage]) -> None:
        for stage in catalogue.values():
            for referenced in stage.inside_of | stage.outside_of:
                if referenced not in catalogue:
                    # A constraint against an absent stage is silently no-op
                    # otherwise, which is how an ordering guarantee quietly
                    # stops holding when a stage is removed elsewhere.
                    raise MissingStage(stage.name, referenced)

    @staticmethod
    def _check_requirements(catalogue: dict[StageName, Stage]) -> None:
        available = frozenset().union(*(s.provides for s in catalogue.values())) or frozenset()
        for stage in catalogue.values():
            for capability in stage.requires:
                if capability not in available:
                    raise UnmetRequirement(stage.name, capability)

    # -- ordering ----------------------------------------------------------

    @staticmethod
    def _edges(catalogue: dict[StageName, Stage]) -> dict[StageName, set[StageName]]:
        """Build "must come before" edges from every kind of constraint.

        Three sources, normalised into one edge set:

        `a.outside_of = {b}` and `b.inside_of = {a}` say the same thing.
        Accepting both spellings means a stage can express its own position
        without editing its neighbour.

        A requirement is also an ordering constraint: a stage that needs a
        capability must run after whatever provides it. Treating `requires` as
        documentation and checking only that some stage provides it would let
        the provider be ordered second, which satisfies the check and fails at
        runtime.
        """
        before: dict[StageName, set[StageName]] = {name: set() for name in catalogue}

        for stage in catalogue.values():
            for inner in stage.outside_of:
                before[stage.name].add(inner)
            for outer in stage.inside_of:
                before[outer].add(stage.name)

        providers: dict[Provides, set[StageName]] = {}
        for stage in catalogue.values():
            for capability in stage.provides:
                providers.setdefault(capability, set()).add(stage.name)

        for stage in catalogue.values():
            for capability in stage.requires:
                for provider in providers.get(capability, ()):
                    if provider != stage.name:
                        before[provider].add(stage.name)

        return before

    @classmethod
    def _topological_order(cls, catalogue: dict[StageName, Stage]) -> list[Stage]:
        before = cls._edges(catalogue)
        indegree = {name: 0 for name in catalogue}
        for successors in before.values():
            for successor in successors:
                indegree[successor] += 1

        # Ties break on phase, then name. Deterministic ordering matters more
        # than it looks: an assembly that varies between runs turns an ordering
        # bug into one that reproduces intermittently.
        def sort_key(name: StageName) -> tuple[int, str]:
            stage = catalogue[name]
            return (_PHASE_ORDER.index(stage.phase), stage.name)

        ready = sorted((n for n, d in indegree.items() if d == 0), key=sort_key)
        ordered: list[Stage] = []

        while ready:
            name = ready.pop(0)
            ordered.append(catalogue[name])
            newly_ready = []
            for successor in before[name]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    newly_ready.append(successor)
            if newly_ready:
                ready = sorted(ready + newly_ready, key=sort_key)

        if len(ordered) != len(catalogue):
            stuck = {name for name, degree in indegree.items() if degree > 0}
            raise CyclicOrdering(stuck)
        return ordered

    # -- inspection --------------------------------------------------------

    @property
    def stages(self) -> tuple[Stage, ...]:
        return self._stages

    def position_of(self, name: StageName | str) -> int:
        target = StageName(str(name))
        for index, stage in enumerate(self._stages):
            if stage.name == target:
                return index
        raise KeyError(f"no stage named {name!r}")

    def runs_inside(self, inner: StageName | str, outer: StageName | str) -> bool:
        """Whether `inner` is nearer the model than `outer`."""
        return self.position_of(inner) > self.position_of(outer)

    def explain(self) -> str:
        """A readable rendering of the assembled order.

        Exists so that "why is this stage here?" is answerable from the running
        system rather than from reading the constraint declarations and doing
        the sort by hand.
        """
        lines = []
        for depth, stage in enumerate(self._stages):
            indent = "  " * depth
            note = f"  — {stage.why}" if stage.why else ""
            lines.append(f"{indent}{stage.name} [{stage.phase}]{note}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._stages)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._stages)
