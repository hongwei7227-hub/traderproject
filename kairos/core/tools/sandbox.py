"""Where agent-authored code runs.

The agent writes Python and something has to run it. That something is hostile
territory by construction: the code was written by a model, from instructions
that may have come from a web page the model read, in a session belonging to a
tenant who should not reach anything outside their own workspace.

This module defines the boundary rather than any particular implementation of
it. A deployment might back it with a container, a microVM, or a remote
service; what must not vary is which paths are reachable, which are refused,
and what a refusal looks like.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Refusal(StrEnum):
    """Why a path or command was refused.

    Distinguished so that the agent gets a message it can act on. "Access
    denied" tells it to try something else; "that lives in the store, read it
    with the file tool" tells it what to try.
    """

    OUTSIDE_WORKSPACE = "outside_workspace"
    RESERVED_PATH = "reserved_path"
    NOT_ON_DISK = "not_on_disk"
    SECRET_MATERIAL = "secret_material"


class PathRefused(PermissionError):
    def __init__(self, path: str, reason: Refusal, advice: str = "") -> None:
        self.path = path
        self.reason = reason
        super().__init__(advice or f"{path!r} refused: {reason}")


# Paths the agent may not reach, and why. Ordered longest-first at use so a
# more specific rule wins over a general one.
_RESERVED: Mapping[str, tuple[Refusal, str]] = {
    "_internal": (
        Refusal.SECRET_MATERIAL,
        "Internal runtime files are not readable.",
    ),
    ".credentials": (
        Refusal.SECRET_MATERIAL,
        "Credentials are not readable from code. Use the vault helper.",
    ),
    ".agents/memory": (
        Refusal.NOT_ON_DISK,
        "Memory lives in the store, not on disk. Read it with the file tool "
        "and pass the text into your script.",
    ),
    ".agents/memo": (
        Refusal.NOT_ON_DISK,
        "Documents live in the store, not on disk. Read one with the file "
        "tool and pass the text into your script.",
    ),
}


@dataclass(frozen=True, slots=True)
class Workspace:
    """The one directory a session may reach."""

    root: str = "/workspace"

    def resolve(self, path: str) -> str:
        """Normalise a path and confirm it stays inside the workspace.

        Normalisation happens before the check, not after. A check that runs
        first sees `data/../../etc/passwd` as starting with `data/` and lets it
        through — which is the shape of the reference implementation's public
        file endpoint, where the traversal guard ran before a normaliser that
        did not resolve `..` at all.
        """
        candidate = path if posixpath.isabs(path) else posixpath.join(self.root, path)
        resolved = posixpath.normpath(candidate)

        if resolved != self.root and not resolved.startswith(f"{self.root}/"):
            raise PathRefused(
                path,
                Refusal.OUTSIDE_WORKSPACE,
                "Paths must stay inside the workspace.",
            )

        relative = posixpath.relpath(resolved, self.root)
        for reserved, (reason, advice) in sorted(
            _RESERVED.items(), key=lambda kv: -len(kv[0])
        ):
            if relative == reserved or relative.startswith(f"{reserved}/"):
                raise PathRefused(path, reason, advice)

        return resolved

    def allows(self, path: str) -> bool:
        try:
            self.resolve(path)
        except PathRefused:
            return False
        return True


@dataclass(frozen=True, slots=True)
class Limits:
    """What one execution may consume.

    Wall time and output size are both bounded because they fail differently:
    a script that loops forever is caught by the first, and one that prints a
    hundred megabytes in two seconds by the second. The reference
    implementation bounded only the first, so a chatty loop could return a
    result too large for the context window it was meant to protect.
    """

    seconds: float = 120.0
    max_output_bytes: int = 256 * 1024
    max_memory_mb: int = 1024

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("seconds must be positive")
        if self.max_output_bytes < 1024:
            raise ValueError("an output cap below 1 KiB cannot carry a useful result")


@dataclass(slots=True)
class Execution:
    """What running a script produced."""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    truncated: bool = False
    files_written: tuple[str, ...] = ()
    timed_out: bool = False
    # Calls the script made through generated wrappers. Kept out of the
    # conversation and used for provenance, because a script that read forty
    # filings should be citable without putting forty filings in the context.
    invocations: tuple[str, ...] = ()

    def summarise(self, limit: int = 2000) -> str:
        """What goes back to the model.

        Failure puts stderr first: a model shown a truncated success followed
        by an error tends to keep going as if it had worked.
        """
        if not self.ok:
            detail = self.stderr or self.stdout or "no output"
            head = "timed out" if self.timed_out else "failed"
            return f"{head}: {detail[:limit]}"

        body = self.stdout[:limit] if self.stdout else "(no output)"
        notes = []
        if self.truncated:
            notes.append("output truncated")
        if self.files_written:
            notes.append(f"wrote {len(self.files_written)} file(s)")
        suffix = f"\n[{'; '.join(notes)}]" if notes else ""
        return f"{body}{suffix}"


class SandboxSession(Protocol):
    """A running environment bound to one workspace."""

    async def execute(self, code: str, limits: Limits) -> Execution: ...

    async def write_file(self, path: str, content: str) -> None: ...

    async def read_file(self, path: str) -> str: ...

    async def install_modules(self, modules: Mapping[str, str]) -> None: ...


@dataclass(slots=True)
class SessionSpec:
    """What a session needs before agent code can run in it.

    Assembled by the composition root, so that neither the tool layer nor the
    reasoning layer needs to know how a sandbox is provisioned.
    """

    workspace: Workspace = field(default_factory=Workspace)
    limits: Limits = field(default_factory=Limits)
    modules: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    def with_module(self, namespace: str, source: str) -> None:
        """Stage a generated wrapper module for installation."""
        self.modules[f"tools/{namespace}.py"] = source

    def redactable_values(self) -> tuple[str, ...]:
        """Secrets that must never appear in output.

        Returned so the caller can scrub results before they reach the model or
        the client. Anything long enough to be a credential is included; short
        values are excluded because scrubbing a three-character value would
        redact ordinary prose.
        """
        return tuple(v for v in self.environment.values() if len(v) >= 8)


def scrub(text: str, secrets: Sequence[str]) -> str:
    """Replace secret material with a marker.

    Longest first, so that a secret which contains another as a substring is
    replaced whole rather than leaving a fragment behind.
    """
    scrubbed = text
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            scrubbed = scrubbed.replace(secret, "[redacted]")
    return scrubbed
