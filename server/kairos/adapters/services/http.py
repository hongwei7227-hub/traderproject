"""The shared half of every service call.

Each client below speaks a different vocabulary, but they all have the same
problem: another process may be slow, down, or answering with something other
than what it promised. Solving that once here keeps the clients about their own
service rather than about failure.

The circuit breaker is the platform's own, keyed by `(tenant, service)`.
Keying it globally would let one tenant's traffic — an account hammering the
analyst service, say — open the circuit for everyone. Keying it on the service
alone would lose the fact that different tenants have different credentials and
different quotas with the same upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from kairos.core.catalog.descriptors import ProviderId
from kairos.core.resilience.breaker import BreakerKey, CircuitBreaker
from kairos.core.tenancy.context import current_scope


class ServiceUnavailable(RuntimeError):
    """The service could not be reached, or refused to answer usefully.

    One exception for every failure mode on purpose. A caller rendering a page
    can do exactly one thing about a timeout, a 503 and an open circuit —
    show that the data is not available — and giving it three exceptions to
    distinguish would only invite catching two of them.
    """

    def __init__(self, service: str, reason: str) -> None:
        self.service = service
        self.reason = reason
        super().__init__(f"{service} unavailable: {reason}")


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport(Protocol):
    """Whatever actually performs the request.

    A protocol so every client can be tested without a server. The real
    implementation wraps an HTTP client; a test passes a dictionary of routes.
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response: ...


class ServiceCall:
    """One service, wrapped in the platform's failure memory.

    Not a base class. The clients hold one of these rather than inherit from
    it, because inheritance would let a client reach past it to the transport
    and quietly skip the breaker — which is exactly the thing that must not be
    skippable.
    """

    __slots__ = ("_name", "_base_url", "_transport", "_breaker", "_timeout")

    def __init__(
        self,
        name: str,
        base_url: str,
        transport: Transport,
        *,
        breaker: CircuitBreaker | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._breaker = breaker
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._name

    def _key(self) -> BreakerKey:
        return BreakerKey(
            tenant=current_scope().tenant_id, provider=ProviderId(self._name)
        )

    async def get(self, path: str, *, headers: dict[str, str] | None = None) -> Any:
        return await self._call("GET", path, headers=headers)

    async def post(
        self, path: str, body: dict | None = None, *, headers: dict[str, str] | None = None
    ) -> Any:
        return await self._call("POST", path, json=body, headers=headers)

    async def get_optional(self, path: str, *, headers: dict[str, str] | None = None) -> Any:
        """As `get`, but a 404 is an answer rather than a failure.

        Asking about a symbol nobody has rated is a normal thing to do. Raising
        would make "no data" indistinguishable from "the service is down", and
        the interface has to say something different for each.
        """
        try:
            return await self._call("GET", path, headers=headers, absent_is_none=True)
        except ServiceUnavailable:
            raise

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
        absent_is_none: bool = False,
    ) -> Any:
        key = self._key()
        if self._breaker is not None and not self._breaker.allows(key):
            # Refused without trying. The point of remembering an outage is not
            # to spare the upstream — it is to fail fast enough that a page
            # renders a gap instead of hanging on a timeout that is already
            # known to be coming.
            raise ServiceUnavailable(self._name, "circuit open")

        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            response = await self._transport.request(
                method, url, json=json, headers=headers, timeout=self._timeout
            )
        except Exception as error:  # noqa: BLE001 - every transport failure is one thing
            self._record_failure(key)
            raise ServiceUnavailable(self._name, f"{type(error).__name__}: {error}") from error

        if response.status == 404 and absent_is_none:
            # Not a failure, so the circuit does not hear about it. Counting
            # 404s toward an outage would open the breaker for a tenant that
            # merely asked about unfamiliar symbols.
            self._record_success(key)
            return None

        if not response.ok:
            # A 5xx is the upstream failing; a 4xx is this platform asking
            # wrongly. Only the first says anything about the upstream's
            # health, so only the first counts toward the circuit.
            if response.status >= 500:
                self._record_failure(key)
            else:
                self._record_success(key)
            raise ServiceUnavailable(self._name, f"HTTP {response.status}")

        self._record_success(key)
        return response.body

    def _record_failure(self, key: BreakerKey) -> None:
        if self._breaker is not None:
            self._breaker.record_failure(key)

    def _record_success(self, key: BreakerKey) -> None:
        if self._breaker is not None:
            self._breaker.record_success(key)
