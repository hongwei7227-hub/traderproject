"""Calling a model, and surviving the ways that fails.

Four things have to happen around every call, and the reference implementation
spread them across three layers that each retried independently — middleware
retried three times over an SDK that retried five, so one upstream wobble
became twenty-four requests against a provider that was already struggling.

Here retry lives in exactly one place, and the layers around it do different
jobs rather than the same job twice:

  the breaker decides whether a provider is worth trying at all
  retry handles the same provider failing transiently
  fallback moves to a different provider when it does not recover
  usage is recorded whichever of those happens

Transport is injected. The orchestration is the part with the interesting
decisions in it, and it should be testable without a network.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from kairos.adapters.llm.credentials import Credential, CredentialResolver
from kairos.adapters.llm.wire import (
    Attempt,
    Request,
    Turn,
    Usage,
    encoder_for,
    is_retryable,
    read_usage,
)
from kairos.core.catalog.descriptors import ModelDescriptor, ProviderId
from kairos.core.catalog.registry import Catalog
from kairos.core.resilience.breaker import BreakerKey, CircuitBreaker
from kairos.core.tenancy.context import TenantId


@dataclass(frozen=True, slots=True)
class Response:
    """What a provider returned."""

    status: int
    payload: dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport(Protocol):
    """Sends a prepared request.

    Injected so that the orchestration below — which provider to try, whether
    to retry, when to give up — can be exercised without a network.
    """

    async def send(self, request: Request) -> Response: ...


class TransportFailure(Exception):
    """The request never reached a provider.

    Distinct from an error response: there is no status, which is why an
    unknown status counts as retryable. A reset connection is the cheapest
    kind of failure to recover from.
    """


@dataclass(slots=True)
class Outcome:
    """The result of a call, and everything it took to get there."""

    response: Response | None
    model: ModelDescriptor | None
    attempts: list[Attempt] = field(default_factory=list)
    exhausted: bool = False

    @property
    def ok(self) -> bool:
        return self.response is not None and self.response.ok

    @property
    def usage(self) -> Usage:
        """Everything spent, including on attempts that failed.

        A failed attempt can still have consumed tokens upstream. Counting only
        the successful one would understate what the tenant actually cost.
        """
        return Usage(
            input_tokens=sum(a.usage.input_tokens for a in self.attempts),
            output_tokens=sum(a.usage.output_tokens for a in self.attempts),
        )

    @property
    def served_by(self) -> str | None:
        for attempt in reversed(self.attempts):
            if attempt.succeeded:
                return attempt.model_id
        return None

    @property
    def degraded(self) -> bool:
        """Whether the answer came from something other than the first choice.

        Worth surfacing to the reader: a fallback answer may be noticeably
        different, and a silent substitution reads as the model getting worse.
        """
        return self.ok and len(self.attempts) > 1


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How hard to try the same provider before moving on.

    Two attempts by default, not the six the reference implementation ended up
    with. Retrying is only worth it for genuinely transient failures, and for
    anything else a different provider is both faster and more likely to work.
    """

    max_attempts: int = 2
    initial_backoff: float = 1.0
    max_backoff: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.25

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    def backoff(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Delay before attempt `attempt` (1-based, so the first is immediate)."""
        if attempt <= 1:
            return 0.0
        delay = min(
            self.initial_backoff * (self.multiplier ** (attempt - 2)), self.max_backoff
        )
        if self.jitter:
            # Spread retries so that a fleet recovering from the same outage
            # does not synchronise into a second one.
            spread = delay * self.jitter
            delay += (rng or random).uniform(-spread, spread)
        return max(0.0, delay)


class ModelInvoker:
    """Runs a turn against a model, falling back as needed."""

    __slots__ = ("_catalog", "_credentials", "_breaker", "_transport", "_retry", "_sleep")

    def __init__(
        self,
        catalog: Catalog,
        credentials: CredentialResolver,
        breaker: CircuitBreaker,
        transport: Transport,
        retry: RetryPolicy | None = None,
        sleep=asyncio.sleep,  # type: ignore[no-untyped-def]
    ) -> None:
        self._catalog = catalog
        self._credentials = credentials
        self._breaker = breaker
        self._transport = transport
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    async def invoke(
        self, turn: Turn, tenant: TenantId, candidates: Sequence[str]
    ) -> Outcome:
        """Try each candidate in order until one answers.

        `candidates` is the fallback chain, most preferred first. Chains cross
        providers deliberately: a chain within one vendor does not survive that
        vendor having a bad afternoon.
        """
        if not candidates:
            return Outcome(response=None, model=None, exhausted=True)

        outcome = Outcome(response=None, model=None)

        for model_id in candidates:
            model = self._catalog.model(model_id)
            key = BreakerKey(tenant, model.provider)

            if not self._breaker.allows(key):
                # Known broken. Skipping costs nothing; trying costs the full
                # retry budget before arriving at the same answer.
                outcome.attempts.append(
                    Attempt(
                        model_id=str(model.id),
                        provider_id=str(model.provider),
                        error="skipped: provider circuit is open",
                    )
                )
                continue

            served = await self._try_model(turn, model, key, outcome)
            if served:
                outcome.response = served
                outcome.model = model
                return outcome

        outcome.exhausted = True
        return outcome

    async def _try_model(
        self,
        turn: Turn,
        model: ModelDescriptor,
        key: BreakerKey,
        outcome: Outcome,
    ) -> Response | None:
        resolved = await self._credentials.resolve(model.provider)
        if resolved.credential is None:
            # Not a provider failure, so it must not trip the breaker: the
            # provider is fine, this deployment simply has no way in.
            outcome.attempts.append(
                Attempt(
                    model_id=str(model.id),
                    provider_id=str(model.provider),
                    error="no credential available",
                )
            )
            return None

        request = self._encode(turn, model, resolved.credential)

        for attempt_number in range(1, self._retry.max_attempts + 1):
            if delay := self._retry.backoff(attempt_number):
                await self._sleep(delay)

            attempt, response = await self._send(request, model)
            outcome.attempts.append(attempt)

            if attempt.succeeded and response is not None:
                self._breaker.record_success(key)
                return response

            self._breaker.record_failure(key)

            if not is_retryable(attempt.status):
                # The provider rejected the request itself. Sending it again
                # sends the same rejection; only a different model can help.
                return None

        return None

    def _encode(
        self, turn: Turn, model: ModelDescriptor, credential: Credential
    ) -> Request:
        provider = self._catalog.provider(model.provider)
        return encoder_for(provider.endpoint.wire).encode(turn, model, credential)

    async def _send(
        self, request: Request, model: ModelDescriptor
    ) -> tuple[Attempt, Response | None]:
        attempt = Attempt(model_id=str(model.id), provider_id=str(model.provider))
        provider = self._catalog.provider(model.provider)

        try:
            response = await self._transport.send(request)
        except TransportFailure as failure:
            # No status: the request never arrived. Left as None so the retry
            # classifier treats it as transient.
            attempt.error = str(failure)
            return attempt, None

        attempt.status = response.status
        attempt.usage = read_usage(provider.endpoint.wire, response.payload)

        if not response.ok:
            attempt.error = self._describe(response)
            return attempt, None

        return attempt, response

    @staticmethod
    def _describe(response: Response) -> str:
        """A short, structured description of a failure.

        Prefers the provider's own error field over the raw body: truncating
        a body tends to cut off exactly the part that says what went wrong.
        """
        error = response.payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or ""
            if message:
                return f"{response.status}: {message}"[:300]
        if isinstance(error, str) and error:
            return f"{response.status}: {error}"[:300]
        return f"{response.status}"


def fallback_chain(catalog: Catalog, primary: str, *alternates: str) -> tuple[str, ...]:
    """Build a chain, dropping anything the catalogue does not have.

    A chain naming a model that was removed would fail at call time in a way
    that looks like an outage. Dropping it here, at assembly, makes the loss
    visible while the process is starting instead.
    """
    return tuple(
        model_id for model_id in (primary, *alternates) if catalog.has_model(model_id)
    )


def crosses_providers(catalog: Catalog, chain: Sequence[str]) -> bool:
    """Whether a chain would survive one provider being down.

    A chain within a single vendor does not: every entry fails together. Worth
    checking at startup rather than discovering during an incident.
    """
    providers: set[ProviderId] = {
        catalog.model(model_id).provider for model_id in chain if catalog.has_model(model_id)
    }
    return len(providers) > 1
