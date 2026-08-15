"""Ordering constraints, checked by assembly rather than by review."""

from __future__ import annotations

import random

import pytest

from kairos.core.reasoning.pipeline import (
    AssemblyError,
    CyclicOrdering,
    MissingStage,
    Phase,
    Pipeline,
    Provides,
    Stage,
    StageName,
    UnmetRequirement,
)
from kairos.core.reasoning.stages import STANDARD_STAGES, standard_pipeline


def stage(
    name: str,
    *,
    phase: Phase = Phase.CONTEXT,
    inside_of: set[str] | None = None,
    outside_of: set[str] | None = None,
    provides: set[Provides] | None = None,
    requires: set[Provides] | None = None,
) -> Stage:
    return Stage(
        name=StageName(name),
        phase=phase,
        inside_of=frozenset(StageName(n) for n in (inside_of or ())),
        outside_of=frozenset(StageName(n) for n in (outside_of or ())),
        provides=frozenset(provides or ()),
        requires=frozenset(requires or ()),
    )


class TestDeclaration:
    def test_a_stage_cannot_be_both_inside_and_outside(self) -> None:
        with pytest.raises(ValueError, match="both inside and outside"):
            stage("a", inside_of={"b"}, outside_of={"b"})

    def test_a_stage_cannot_be_positioned_against_itself(self) -> None:
        with pytest.raises(ValueError, match="relative to itself"):
            stage("a", inside_of={"a"})


class TestOrdering:
    def test_outside_of_places_a_stage_earlier(self) -> None:
        pipeline = Pipeline.assemble([stage("outer", outside_of={"inner"}), stage("inner")])
        assert pipeline.runs_inside("inner", "outer")

    def test_inside_of_is_the_same_constraint_from_the_other_side(self) -> None:
        """A stage can express its own position without editing its neighbour."""
        pipeline = Pipeline.assemble([stage("outer"), stage("inner", inside_of={"outer"})])
        assert pipeline.runs_inside("inner", "outer")

    def test_constraints_are_transitive(self) -> None:
        pipeline = Pipeline.assemble(
            [
                stage("a", outside_of={"b"}),
                stage("b", outside_of={"c"}),
                stage("c"),
            ]
        )
        assert pipeline.runs_inside("c", "a")

    def test_the_declaration_order_does_not_matter(self) -> None:
        """The property the old middleware list lacked.

        Reordering the literal changes nothing; only the constraints do.
        """
        shuffled = list(STANDARD_STAGES)
        random.Random(1).shuffle(shuffled)

        assert [s.name for s in Pipeline.assemble(shuffled)] == [
            s.name for s in standard_pipeline()
        ]

    def test_assembly_is_deterministic(self) -> None:
        # An assembly that varied between runs would turn an ordering bug into
        # one that reproduces intermittently.
        first = [s.name for s in standard_pipeline()]
        for _ in range(5):
            assert [s.name for s in standard_pipeline()] == first


class TestRejection:
    def test_a_cycle_is_rejected(self) -> None:
        with pytest.raises(CyclicOrdering) as caught:
            Pipeline.assemble(
                [stage("a", outside_of={"b"}), stage("b", outside_of={"a"})]
            )
        assert set(caught.value.involved) == {"a", "b"}

    def test_a_longer_cycle_is_rejected(self) -> None:
        with pytest.raises(CyclicOrdering):
            Pipeline.assemble(
                [
                    stage("a", outside_of={"b"}),
                    stage("b", outside_of={"c"}),
                    stage("c", outside_of={"a"}),
                ]
            )

    def test_a_constraint_against_an_absent_stage_is_rejected(self) -> None:
        """Otherwise the constraint silently becomes a no-op.

        That is how an ordering guarantee stops holding when some unrelated
        change removes the stage it referred to.
        """
        with pytest.raises(MissingStage):
            Pipeline.assemble([stage("a", inside_of={"gone"})])

    def test_an_unprovided_requirement_is_rejected(self) -> None:
        with pytest.raises(UnmetRequirement):
            Pipeline.assemble([stage("a", requires={Provides.RETRY})])

    def test_duplicate_names_are_rejected(self) -> None:
        with pytest.raises(AssemblyError, match="duplicate"):
            Pipeline.assemble([stage("a"), stage("a")])


class TestStandardPipeline:
    def test_it_assembles(self) -> None:
        assert len(standard_pipeline()) == len(STANDARD_STAGES)

    def test_modality_fit_runs_inside_retry(self) -> None:
        """The constraint whose violation caused the original bug.

        Outside the retry boundary, falling back from a vision model to a
        text-only one replays the images that caused the failure, and the
        fallback fails for the same reason as the attempt it was meant to
        rescue.
        """
        assert standard_pipeline().runs_inside("modality-fit", "retry")

    def test_compaction_runs_outside_retry(self) -> None:
        # Summarising is expensive and valid for every attempt; inside, a
        # flapping provider would pay for it repeatedly.
        assert not standard_pipeline().runs_inside("compaction", "retry")

    def test_media_capture_runs_outside_retry(self) -> None:
        assert not standard_pipeline().runs_inside("media-capture", "retry")

    def test_provider_markers_run_inside_retry(self) -> None:
        # Vendor-specific cache markers must not travel to another vendor's
        # request after a fallback.
        assert standard_pipeline().runs_inside("provider-markers", "retry")

    def test_live_context_runs_inside_the_cache_breakpoint(self) -> None:
        # Per-turn facts before the breakpoint would invalidate the cached
        # prefix on every turn.
        assert standard_pipeline().runs_inside("live-context", "cache-breakpoint")

    def test_reasoning_compatibility_is_innermost(self) -> None:
        pipeline = standard_pipeline()
        assert pipeline.position_of("reasoning-compat") == len(pipeline) - 1

    def test_oversized_results_is_outermost(self) -> None:
        assert standard_pipeline().position_of("oversized-results") == 0

    def test_every_stage_records_why_it_sits_where_it_does(self) -> None:
        # The reasons were learned expensively; losing one loses the reason a
        # constraint exists, and the next person removes it.
        assert all(s.why for s in STANDARD_STAGES)


class TestInspection:
    def test_explain_names_every_stage(self) -> None:
        explanation = standard_pipeline().explain()
        for stage_ in STANDARD_STAGES:
            assert stage_.name in explanation

    def test_position_of_an_unknown_stage_raises(self) -> None:
        with pytest.raises(KeyError):
            standard_pipeline().position_of("imaginary")


class TestRegression:
    def test_a_new_stage_cannot_silently_land_in_the_wrong_place(self) -> None:
        """Adding a stage that violates a boundary fails assembly.

        In the original, inserting a line in the wrong position compiled,
        ran, and produced a bug that only appeared once a fallback happened.
        """
        offender = stage(
            "naive-modality-strip",
            phase=Phase.CONTEXT,
            outside_of={"retry"},
            inside_of={"modality-fit"},
        )
        with pytest.raises(AssemblyError):
            Pipeline.assemble([*STANDARD_STAGES, offender])
