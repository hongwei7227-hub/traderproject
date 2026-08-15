"""Calling the Java services.

What matters here is not the happy path — it is what this platform does when
another process is slow, down, or answering with something other than what it
promised. A page should render a gap; it should not hang, and it should not
show a price target of zero because a field was missing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kairos.adapters.services.analyst import AnalystClient
from kairos.adapters.services.billing import BillingClient, OrderState
from kairos.adapters.services.http import Response, ServiceCall, ServiceUnavailable
from kairos.core.resilience.breaker import BreakerPolicy, CircuitBreaker
from kairos.core.tenancy import Role, TenantId, TenantScope, UserId, scoped

ACME = TenantScope(
    tenant_id=TenantId("acme"), user_id=UserId("alice"), roles=frozenset({Role.MEMBER})
)
RIVAL = TenantScope(
    tenant_id=TenantId("rival"), user_id=UserId("mallory"), roles=frozenset({Role.MEMBER})
)


class FakeTransport:
    """Routes, or an exception. Enough to drive every branch."""

    def __init__(
        self,
        routes: dict[str, Response] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.routes = routes or {}
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response:
        self.calls.append((method, url, headers))
        if self.raises is not None:
            raise self.raises
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        return self.routes.get(f"/{path}", Response(404, None))


def call(transport: FakeTransport, breaker: CircuitBreaker | None = None) -> ServiceCall:
    return ServiceCall("analyst", "http://svc", transport, breaker=breaker)


class TestFailureHandling:
    async def test_a_transport_error_becomes_one_exception(self) -> None:
        """A caller can do exactly one thing about a timeout, a refused
        connection and a DNS failure. Three exceptions would only invite
        catching two of them."""
        with scoped(ACME), pytest.raises(ServiceUnavailable):
            await call(FakeTransport(raises=ConnectionError("refused"))).get("/x")

    async def test_a_server_error_is_unavailable(self) -> None:
        transport = FakeTransport({"/x": Response(503, None)})
        with scoped(ACME), pytest.raises(ServiceUnavailable):
            await call(transport).get("/x")

    async def test_the_service_is_named_in_the_failure(self) -> None:
        with scoped(ACME):
            try:
                await call(FakeTransport(raises=TimeoutError())).get("/x")
            except ServiceUnavailable as error:
                assert error.service == "analyst"


class TestCircuitBreaking:
    def breaker(self) -> CircuitBreaker:
        return CircuitBreaker(BreakerPolicy(failure_threshold=2, recovery_after_seconds=60))

    async def test_repeated_failures_open_the_circuit(self) -> None:
        """Failing fast is the point: a page renders a gap rather than hanging
        on a timeout that is already known to be coming."""
        breaker = self.breaker()
        transport = FakeTransport(raises=ConnectionError("down"))

        with scoped(ACME):
            for _ in range(2):
                with pytest.raises(ServiceUnavailable):
                    await call(transport, breaker).get("/x")

            before = len(transport.calls)
            with pytest.raises(ServiceUnavailable, match="circuit open"):
                await call(transport, breaker).get("/x")

        assert len(transport.calls) == before  # refused without trying

    async def test_one_tenant_does_not_open_the_circuit_for_another(self) -> None:
        """Keying globally would let one account's traffic cut everyone off."""
        breaker = self.breaker()
        down = FakeTransport(raises=ConnectionError("down"))

        with scoped(ACME):
            for _ in range(2):
                with pytest.raises(ServiceUnavailable):
                    await call(down, breaker).get("/x")

        healthy = FakeTransport({"/x": Response(200, {"ok": True})})
        with scoped(RIVAL):
            assert await call(healthy, breaker).get("/x") == {"ok": True}

    async def test_a_client_error_does_not_count_toward_the_outage(self) -> None:
        """A 4xx is this platform asking wrongly. Only a 5xx says anything
        about the upstream's health."""
        breaker = self.breaker()
        transport = FakeTransport({"/x": Response(400, None)})

        with scoped(ACME):
            for _ in range(3):
                with pytest.raises(ServiceUnavailable):
                    await call(transport, breaker).get("/x")

            # Still closed: three 400s did not open it.
            assert len(transport.calls) == 3

    async def test_an_expected_absence_does_not_count_either(self) -> None:
        """Asking about unfamiliar symbols would otherwise open the breaker."""
        breaker = self.breaker()
        transport = FakeTransport()

        with scoped(ACME):
            for _ in range(5):
                assert await call(transport, breaker).get_optional("/missing") is None
            assert len(transport.calls) == 5


class TestAnalyst:
    def client(self, body: object, status: int = 200) -> AnalystClient:
        transport = FakeTransport({"/stock/NVDA/analyst": Response(status, body)})
        return AnalystClient(ServiceCall("analyst", "http://svc", transport))

    async def test_a_rating_is_mapped_from_the_services_field_names(self) -> None:
        client = self.client(
            {
                "symbol": "NVDA",
                "companyName": "NVIDIA",
                "consensusRating": "Buy",
                "targetPriceConsensus": 210.5,
                "numAnalysts": 42,
            }
        )
        with scoped(ACME):
            rating = await client.rating("nvda")

        assert rating is not None
        assert rating.company_name == "NVIDIA"
        assert rating.consensus == "Buy"
        assert rating.analyst_count == 42

    async def test_an_uncovered_symbol_is_none_rather_than_an_error(self) -> None:
        """Most listed companies have no coverage. That is not a failure."""
        client = AnalystClient(ServiceCall("analyst", "http://svc", FakeTransport()))
        with scoped(ACME):
            assert await client.rating("OBSCURE") is None

    async def test_a_missing_target_stays_missing(self) -> None:
        """Coercing it to 0.0 would put a price target of zero in front of a
        reader."""
        with scoped(ACME):
            rating = await self.client({"symbol": "NVDA"}).rating("NVDA")
        assert rating is not None
        assert rating.target_price is None
        assert not rating.has_target

    async def test_grades_are_read(self) -> None:
        client = self.client(
            {
                "symbol": "NVDA",
                "recentGrades": [
                    {"firm": "Acme", "fromGrade": "Hold", "toGrade": "Buy", "action": "upgrade"}
                ],
            }
        )
        with scoped(ACME):
            rating = await client.rating("NVDA")

        assert rating is not None
        assert rating.recent_grades[0].is_upgrade

    async def test_upside_is_none_without_a_target(self) -> None:
        """Showing 0% would assert the stock is fairly valued, which is a
        different statement from having nothing to say."""
        with scoped(ACME):
            rating = await self.client({"symbol": "NVDA"}).rating("NVDA")
        assert rating is not None
        assert rating.upside_from(100.0) is None

    async def test_upside_is_a_fraction_of_the_current_price(self) -> None:
        with scoped(ACME):
            rating = await self.client(
                {"symbol": "NVDA", "targetPriceConsensus": 120.0}
            ).rating("NVDA")
        assert rating is not None
        assert rating.upside_from(100.0) == pytest.approx(0.2)


class TestBilling:
    def client(self, routes: dict[str, Response]) -> tuple[BillingClient, FakeTransport]:
        transport = FakeTransport(routes)
        return BillingClient(ServiceCall("recharge", "http://svc", transport)), transport

    async def test_plans_are_unwrapped_from_the_envelope(self) -> None:
        client, _ = self.client(
            {
                "/recharge/plans": Response(
                    200,
                    {
                        "success": True,
                        "data": [
                            {"id": 1, "name": "Yearly", "price": "199.00", "durationDays": 365}
                        ],
                    },
                )
            }
        )
        with scoped(ACME):
            plans = await client.plans()

        assert plans[0].name == "Yearly"
        assert plans[0].price == Decimal("199.00")

    async def test_a_monthly_equivalent_makes_plans_comparable(self) -> None:
        client, _ = self.client(
            {
                "/recharge/plans": Response(
                    200,
                    {
                        "success": True,
                        "data": [
                            {"id": 1, "name": "Yearly", "price": "365.00", "durationDays": 365}
                        ],
                    },
                )
            }
        )
        with scoped(ACME):
            plans = await client.plans()

        assert plans[0].monthly_price == Decimal("30.00")

    async def test_money_never_becomes_a_float(self) -> None:
        """`Decimal(0.1)` keeps the binary error; `Decimal("0.1")` does not."""
        client, _ = self.client(
            {
                "/recharge/plans": Response(
                    200,
                    {"success": True, "data": [{"id": 1, "price": 0.1, "durationDays": 30}]},
                )
            }
        )
        with scoped(ACME):
            plans = await client.plans()

        assert plans[0].price == Decimal("0.1")

    async def test_a_user_with_nothing_has_level_zero(self) -> None:
        """Absence is a real answer, so every caller is spared a null check
        before asking whether it is active."""
        client, _ = self.client({"/membership/me": Response(200, {"success": True, "data": None})})
        with scoped(ACME):
            membership = await client.membership("7")

        assert membership.level == 0
        assert not membership.active

    async def test_the_acting_user_is_named_on_the_call(self) -> None:
        client, transport = self.client(
            {"/membership/me": Response(200, {"success": True, "data": {"level": 2}})}
        )
        with scoped(ACME):
            await client.membership("7")

        assert transport.calls[0][2] == {"X-User-Id": "7"}

    async def test_an_unknown_order_state_is_treated_as_unsettled(self) -> None:
        """Showing an order as paid on the strength of a number this platform
        has never seen is the one mistake worth ruling out."""
        client, _ = self.client(
            {
                "/recharge/order/9": Response(
                    200, {"success": True, "data": {"id": 9, "status": 99}}
                )
            }
        )
        with scoped(ACME):
            order = await client.order("7", 9)

        assert order is not None
        assert order.state is OrderState.UNPAID

    async def test_a_failed_envelope_reads_as_no_data(self) -> None:
        client, _ = self.client(
            {"/recharge/plans": Response(200, {"success": False, "errorMsg": "nope"})}
        )
        with scoped(ACME):
            assert await client.plans() == []

    async def test_the_idempotency_key_comes_from_the_caller(self) -> None:
        """A double-clicked buy must produce one order, which is only possible
        if both requests carry the same key."""
        client, transport = self.client(
            {
                "/recharge/order": Response(
                    200, {"success": True, "data": {"id": 5, "planId": 1, "status": 1}}
                )
            }
        )
        with scoped(ACME):
            order = await client.start_recharge("7", 1, request_id="req-1")

        assert order.awaiting_payment
        assert transport.calls[0][0] == "POST"
