"""The sandbox boundary: what agent code may reach, and what a refusal says."""

from __future__ import annotations

import pytest

from kairos.core.tools.sandbox import (
    Execution,
    Limits,
    PathRefused,
    Refusal,
    SessionSpec,
    Workspace,
    scrub,
)

WS = Workspace()


class TestPathResolution:
    def test_a_relative_path_lands_inside(self) -> None:
        assert WS.resolve("data/prices.csv") == "/workspace/data/prices.csv"

    def test_an_absolute_path_inside_is_kept(self) -> None:
        assert WS.resolve("/workspace/notes.md") == "/workspace/notes.md"

    def test_the_root_itself_is_allowed(self) -> None:
        assert WS.resolve(".") == "/workspace"

    def test_redundant_segments_are_normalised(self) -> None:
        assert WS.resolve("./data/../notes.md") == "/workspace/notes.md"


class TestTraversal:
    @pytest.mark.parametrize(
        "path",
        [
            "../etc/passwd",
            "/etc/passwd",
            "data/../../etc/passwd",
            "../../../../root/.ssh/id_rsa",
            "/workspace/../etc/passwd",
        ],
    )
    def test_escapes_are_refused(self, path: str) -> None:
        with pytest.raises(PathRefused) as caught:
            WS.resolve(path)
        assert caught.value.reason is Refusal.OUTSIDE_WORKSPACE

    def test_normalisation_happens_before_the_check(self) -> None:
        """The ordering the reference implementation got wrong.

        Its public file endpoint checked for traversal first and normalised
        after — and the normaliser it used did not resolve `..` at all, so
        `data/../../etc/passwd` passed a prefix check on `data/`.
        """
        with pytest.raises(PathRefused):
            WS.resolve("data/../../../etc/passwd")

    def test_a_prefix_that_merely_looks_similar_is_refused(self) -> None:
        # /workspace-other must not pass a startswith check on /workspace.
        with pytest.raises(PathRefused):
            WS.resolve("/workspace-other/secrets")


class TestReservedPaths:
    @pytest.mark.parametrize(
        ("path", "reason"),
        [
            ("_internal/runtime.py", Refusal.SECRET_MATERIAL),
            (".credentials/key", Refusal.SECRET_MATERIAL),
            (".agents/memory/memory.md", Refusal.NOT_ON_DISK),
            (".agents/memo/report.md", Refusal.NOT_ON_DISK),
        ],
    )
    def test_reserved_areas_are_refused(self, path: str, reason: Refusal) -> None:
        with pytest.raises(PathRefused) as caught:
            WS.resolve(path)
        assert caught.value.reason is reason

    def test_the_refusal_says_what_to_do_instead(self) -> None:
        """"Access denied" tells the agent to give up; this tells it what to try.

        Memory is not on disk at all, so a retry with a different path would
        fail the same way forever.
        """
        with pytest.raises(PathRefused, match="Read it with the file tool"):
            WS.resolve(".agents/memory/memory.md")

    def test_a_similarly_named_sibling_is_allowed(self) -> None:
        # `.agents/memoranda` is not `.agents/memo`.
        assert WS.allows(".agents/memoranda/notes.md")

    def test_ordinary_paths_are_unaffected(self) -> None:
        assert WS.allows("results/summary.md")


class TestLimits:
    def test_defaults_are_usable(self) -> None:
        limits = Limits()
        assert limits.seconds > 0
        assert limits.max_output_bytes > 1024

    def test_output_is_bounded_as_well_as_time(self) -> None:
        """They fail differently.

        A loop that never ends is caught by the clock; one that prints a
        hundred megabytes in two seconds is not. The reference implementation
        bounded only the clock, so a chatty script could return a result too
        large for the context window the sandbox exists to protect.
        """
        assert Limits().max_output_bytes < 1024 * 1024

    @pytest.mark.parametrize(
        "kwargs", [{"seconds": 0}, {"seconds": -1}, {"max_output_bytes": 10}]
    )
    def test_degenerate_limits_are_rejected(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            Limits(**kwargs)  # type: ignore[arg-type]


class TestExecutionSummary:
    def test_success_returns_the_output(self) -> None:
        assert "42" in Execution(ok=True, stdout="42").summarise()

    def test_empty_output_says_so(self) -> None:
        # Better than an empty string, which reads as a broken tool.
        assert "no output" in Execution(ok=True, stdout="").summarise()

    def test_failure_leads_with_the_error(self) -> None:
        """A model shown truncated success then an error keeps going as if it worked."""
        summary = Execution(ok=False, stdout="partial", stderr="NameError: x").summarise()
        assert summary.startswith("failed")
        assert "NameError" in summary

    def test_a_timeout_says_so_specifically(self) -> None:
        assert Execution(ok=False, timed_out=True, stderr="").summarise().startswith(
            "timed out"
        )

    def test_truncation_is_disclosed(self) -> None:
        summary = Execution(ok=True, stdout="x" * 10, truncated=True).summarise()
        assert "truncated" in summary

    def test_written_files_are_mentioned(self) -> None:
        summary = Execution(
            ok=True, stdout="done", files_written=("a.csv", "b.csv")
        ).summarise()
        assert "2 file(s)" in summary

    def test_long_output_is_capped(self) -> None:
        assert len(Execution(ok=True, stdout="x" * 10_000).summarise(limit=100)) <= 120


class TestSecretHandling:
    def test_only_credential_length_values_are_redactable(self) -> None:
        """Scrubbing a three-character value would redact ordinary prose."""
        spec = SessionSpec(environment={"SHORT": "abc", "TOKEN": "s3cret-value-here"})
        assert spec.redactable_values() == ("s3cret-value-here",)

    def test_secrets_are_replaced_in_output(self) -> None:
        assert "s3cret" not in scrub("key is s3cret-value", ["s3cret-value"])

    def test_longest_secrets_are_replaced_first(self) -> None:
        """Otherwise a shorter secret inside a longer one leaves a fragment."""
        text = "value: abc123456789"
        result = scrub(text, ["abc123", "abc123456789"])
        assert "abc123456789" not in result
        assert "456789" not in result

    def test_scrubbing_without_secrets_is_a_no_op(self) -> None:
        assert scrub("plain text", []) == "plain text"


class TestSessionSpec:
    def test_modules_are_staged_under_tools(self) -> None:
        spec = SessionSpec()
        spec.with_module("sec", "def filings(): ...")
        assert "tools/sec.py" in spec.modules

    def test_a_spec_carries_its_own_workspace_and_limits(self) -> None:
        spec = SessionSpec(
            workspace=Workspace(root="/scratch"), limits=Limits(seconds=30.0)
        )
        assert spec.workspace.resolve("a.txt") == "/scratch/a.txt"
        assert spec.limits.seconds == 30.0
