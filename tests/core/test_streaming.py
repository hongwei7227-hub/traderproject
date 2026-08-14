"""Stream ordering, and why it is enforced rather than documented."""

from __future__ import annotations

from uuid import uuid4

import pytest

from kairos.core.streaming.events import (
    Event,
    EventKind,
    EventStream,
    Severity,
    StreamViolation,
    Transcript,
)


def stream() -> EventStream:
    return EventStream(uuid4())


def opened() -> EventStream:
    s = stream()
    s.open()
    return s


class TestOpening:
    def test_the_first_event_is_metadata(self) -> None:
        s = stream()
        assert s.open().kind is EventKind.METADATA

    def test_metadata_carries_the_run_id(self) -> None:
        """A reconnecting client resumes by run id, so it must arrive first."""
        s = stream()
        assert s.open().payload["run_id"] == str(s.run_id)

    def test_extra_metadata_is_passed_through(self) -> None:
        event = stream().open(model="big-model", workspace="research")
        assert event.payload["model"] == "big-model"
        assert event.payload["workspace"] == "research"

    def test_content_before_opening_is_refused(self) -> None:
        # Emitting before the identifier strands a client that drops at the
        # wrong moment: it has nothing to resume with.
        with pytest.raises(StreamViolation, match="must open with metadata"):
            stream().text("hello")

    def test_opening_twice_is_refused(self) -> None:
        s = opened()
        with pytest.raises(StreamViolation, match="already open"):
            s.open()


class TestSequencing:
    def test_sequences_start_at_zero_and_are_contiguous(self) -> None:
        """Resumption asks for everything after a sequence, so gaps break it."""
        s = opened()
        s.text("a")
        s.text("b")
        assert [e.sequence for e in s.recorded] == [0, 1, 2]

    def test_since_returns_only_later_events(self) -> None:
        s = opened()
        s.text("first")
        s.text("second")
        assert [e.payload["text"] for e in s.since(1)] == ["second"]

    def test_since_the_last_seen_returns_nothing(self) -> None:
        s = opened()
        s.text("only")
        assert s.since(1) == ()

    def test_a_negative_sequence_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Event(kind=EventKind.TEXT, sequence=-1)


class TestTermination:
    def test_closing_ends_the_stream(self) -> None:
        s = opened()
        assert s.close().kind is EventKind.DONE
        assert s.closed

    def test_failing_ends_the_stream(self) -> None:
        s = opened()
        event = s.fail("upstream refused", retryable=True)
        assert event.kind is EventKind.ERROR
        assert event.payload["retryable"] is True

    def test_content_after_ending_is_refused(self) -> None:
        # A client's completion handler has already run.
        s = opened()
        s.close()
        with pytest.raises(StreamViolation, match="after the stream has ended"):
            s.text("late")

    def test_closing_twice_is_refused(self) -> None:
        s = opened()
        s.close()
        with pytest.raises(StreamViolation, match="already closed"):
            s.close()

    def test_failing_after_closing_is_refused(self) -> None:
        s = opened()
        s.close()
        with pytest.raises(StreamViolation):
            s.fail("too late")

    def test_a_setup_failure_can_be_reported_without_opening(self) -> None:
        """A client that receives only an error knows more than one left waiting."""
        s = stream()
        assert s.fail("could not start").kind is EventKind.ERROR
        assert s.closed


class TestContent:
    def test_reasoning_is_distinct_from_the_answer(self) -> None:
        # Clients render it differently, and some suppress it entirely.
        s = opened()
        assert s.reasoning("thinking").kind is EventKind.REASONING
        assert s.text("answer").kind is EventKind.TEXT

    def test_a_tool_result_carries_a_summary_not_the_payload(self) -> None:
        """Tool output can be enormous; rendering it rarely needs all of it."""
        s = opened()
        event = s.tool_result("call-1", ok=True, summary="searched 40 filings")
        assert "summary" in event.payload
        assert set(event.payload) == {"call_id", "ok", "summary"}

    def test_artifacts_are_referenced_rather_than_inlined(self) -> None:
        s = opened()
        event = s.artifact("chart", "s3://bucket/chart.png", title="Revenue")
        assert event.payload["ref"].startswith("s3://")

    def test_notices_carry_severity(self) -> None:
        s = opened()
        event = s.notice("model fell back", Severity.WARNING)
        assert event.payload["severity"] == "warning"

    def test_usage_names_the_model_that_spent_it(self) -> None:
        # Attribution needs the model, not just the number: a fallback means
        # the same turn spent tokens at two different rates.
        s = opened()
        event = s.usage(input_tokens=100, output_tokens=50, model="big-model")
        assert event.payload["model"] == "big-model"


class TestTranscript:
    def test_text_is_reassembled_in_order(self) -> None:
        s = opened()
        for chunk in ("Hello", ", ", "world"):
            s.text(chunk)
        s.close()
        assert Transcript.of(s).text() == "Hello, world"

    def test_cost_comes_from_the_last_report(self) -> None:
        """Usage is cumulative; summing would count earlier reports twice."""
        s = opened()
        s.usage(input_tokens=100, output_tokens=10, model="m")
        s.usage(input_tokens=100, output_tokens=90, model="m")
        s.close()
        assert Transcript.of(s).cost() == (100, 90)

    def test_a_turn_with_no_usage_reports_nothing_spent(self) -> None:
        s = opened()
        s.close()
        assert Transcript.of(s).cost() == (0, 0)

    def test_completion_and_failure_are_distinguishable(self) -> None:
        done, broken = opened(), opened()
        done.close()
        broken.fail("upstream refused")

        assert Transcript.of(done).complete and not Transcript.of(done).failed
        assert Transcript.of(broken).complete and Transcript.of(broken).failed


class TestTranscriptValidation:
    def test_a_well_formed_transcript_passes(self) -> None:
        s = opened()
        s.text("hi")
        s.close()
        Transcript.of(s).validate()

    def test_an_empty_transcript_is_rejected(self) -> None:
        with pytest.raises(StreamViolation, match="empty"):
            Transcript(run_id=uuid4(), events=()).validate()

    def test_a_transcript_not_opening_with_metadata_is_rejected(self) -> None:
        transcript = Transcript(
            run_id=uuid4(),
            events=(
                Event(kind=EventKind.TEXT, sequence=0),
                Event(kind=EventKind.DONE, sequence=1),
            ),
        )
        with pytest.raises(StreamViolation, match="does not open with metadata"):
            transcript.validate()

    def test_a_gap_in_the_sequence_is_rejected(self) -> None:
        # A replaying client would silently skip whatever is missing.
        transcript = Transcript(
            run_id=uuid4(),
            events=(
                Event(kind=EventKind.METADATA, sequence=0),
                Event(kind=EventKind.DONE, sequence=2),
            ),
        )
        with pytest.raises(StreamViolation, match="not contiguous"):
            transcript.validate()

    def test_a_transcript_that_never_ends_is_rejected(self) -> None:
        transcript = Transcript(
            run_id=uuid4(), events=(Event(kind=EventKind.METADATA, sequence=0),)
        )
        with pytest.raises(StreamViolation, match="never ends"):
            transcript.validate()

    def test_a_transcript_ending_twice_is_rejected(self) -> None:
        """Would make a client's completion handler run twice."""
        transcript = Transcript(
            run_id=uuid4(),
            events=(
                Event(kind=EventKind.METADATA, sequence=0),
                Event(kind=EventKind.DONE, sequence=1),
                Event(kind=EventKind.DONE, sequence=2),
            ),
        )
        with pytest.raises(StreamViolation, match="more than once"):
            transcript.validate()

    def test_content_after_the_end_is_rejected(self) -> None:
        transcript = Transcript(
            run_id=uuid4(),
            events=(
                Event(kind=EventKind.METADATA, sequence=0),
                Event(kind=EventKind.DONE, sequence=1),
                Event(kind=EventKind.TEXT, sequence=2),
            ),
        )
        with pytest.raises(StreamViolation, match="continues after ending"):
            transcript.validate()

    def test_every_stream_this_module_produces_validates(self) -> None:
        """The emitter and the validator must agree.

        If they can disagree, a turn that streamed correctly could still fail
        to replay — which is the hardest kind of bug to notice, because it only
        shows up later.
        """
        s = opened()
        s.reasoning("considering")
        s.tool_call("c1", "search", {"q": "revenue"})
        s.tool_result("c1", summary="12 results")
        s.artifact("chart", "s3://b/c.png")
        s.notice("history condensed")
        s.usage(input_tokens=10, output_tokens=20, model="m")
        s.text("done")
        s.close()

        Transcript.of(s).validate()


class TestWireFormat:
    def test_the_wire_form_carries_kind_and_sequence(self) -> None:
        s = opened()
        wire = s.text("hello").to_wire()
        assert wire["kind"] == "text"
        assert wire["seq"] == 1
        assert wire["text"] == "hello"
