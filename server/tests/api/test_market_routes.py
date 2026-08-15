"""Analyst coverage and billing over HTTP.

These routes stand between the interface and a service that can be down. What
they owe is a response the interface can act on: a gap where a card would be,
not a 500 that says nothing about whether the rest of the platform is fine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from kairos.adapters.services.analyst import AnalystRating, Grade
from kairos.adapters.services.billing import Membership, OrderState, Plan, RechargeOrder
from kairos.adapters.services.http import ServiceUnavailable
from kairos.runtime.app import create_app, dependency_overrides_for
from kairos.runtime.settings import AuthSettings, Deployment, Settings


class FakeAnalyst:
    def __init__(self, rating: AnalystRating | None = None, fails: bool = False) -> None:
        self._rating = rating
        self._fails = fails

    async def rating(self, symbol: str) -> AnalystRating | None:
        if self._fails:
            raise ServiceUnavailable("analyst", "circuit open")
        return self._rating

    async def cache_stats(self) -> dict[str, int]:
        if self._fails:
            raise ServiceUnavailable("analyst", "circuit open")
        return {"l1_hits": 83, "l2_hits": 16}


class FakeBilling:
    def __init__(
        self,
        plans: list[Plan] | None = None,
        membership: Membership | None = None,
        order: RechargeOrder | None = None,
        fails: bool = False,
    ) -> None:
        self._plans = plans or []
        self._membership = membership or Membership(level=0)
        self._order = order
        self._fails = fails
        self.requests: list[tuple[str, int, str]] = []

    def _guard(self) -> None:
        if self._fails:
            raise ServiceUnavailable("recharge", "connection refused")

    async def plans(self) -> list[Plan]:
        self._guard()
        return self._plans

    async def membership(self, user_id: str) -> Membership:
        self._guard()
        return self._membership

    async def start_recharge(self, user_id: str, plan_id: int, *, request_id: str) -> RechargeOrder:
        self._guard()
        self.requests.append((user_id, plan_id, request_id))
        return self._order or RechargeOrder(
            id=5, plan_id=plan_id, amount=Decimal("199"), state=OrderState.UNPAID
        )

    async def order(self, user_id: str, order_id: int) -> RechargeOrder | None:
        self._guard()
        return self._order


def build(
    *, analyst: FakeAnalyst | None = None, billing: FakeBilling | None = None
) -> tuple[TestClient, FakeBilling]:
    app = create_app(settings=Settings(deployment=Deployment.SOLO, auth=AuthSettings()))
    billing = billing or FakeBilling()
    dependency_overrides_for(app, analyst=analyst or FakeAnalyst(), billing=billing)
    return TestClient(app), billing


def rating(**overrides: object) -> AnalystRating:
    base: dict[str, object] = {
        "symbol": "NVDA",
        "company_name": "NVIDIA",
        "consensus": "Buy",
        "target_price": 210.0,
        "analyst_count": 42,
    }
    return AnalystRating(**(base | overrides))  # type: ignore[arg-type]


class TestAnalystRoute:
    def test_a_rating_is_returned(self) -> None:
        client, _ = build(analyst=FakeAnalyst(rating()))
        body = client.get("/api/v1/stocks/NVDA/analyst").json()
        assert body["consensus"] == "Buy"
        assert body["analyst_count"] == 42

    def test_an_uncovered_symbol_is_404(self) -> None:
        """An empty rating rendered in a card looks like coverage that says
        nothing."""
        client, _ = build(analyst=FakeAnalyst(None))
        assert client.get("/api/v1/stocks/OBSCURE/analyst").status_code == 404

    def test_upside_is_computed_against_a_supplied_price(self) -> None:
        """Taken as a parameter rather than fetched, because the caller is
        already rendering a live price and two would disagree on screen."""
        client, _ = build(analyst=FakeAnalyst(rating(target_price=120.0)))
        body = client.get("/api/v1/stocks/NVDA/analyst?price=100").json()
        assert body["upside"] == 0.2

    def test_upside_is_absent_without_a_price(self) -> None:
        client, _ = build(analyst=FakeAnalyst(rating()))
        assert client.get("/api/v1/stocks/NVDA/analyst").json()["upside"] is None

    def test_grades_are_included(self) -> None:
        client, _ = build(
            analyst=FakeAnalyst(
                rating(
                    recent_grades=(
                        Grade(firm="Acme", from_grade="Hold", to_grade="Buy", action="upgrade"),
                    )
                )
            )
        )
        grades = client.get("/api/v1/stocks/NVDA/analyst").json()["recent_grades"]
        assert grades[0]["firm"] == "Acme"

    def test_an_updated_timestamp_is_rendered(self) -> None:
        client, _ = build(
            analyst=FakeAnalyst(rating(updated_at=datetime(2026, 8, 16, tzinfo=UTC)))
        )
        assert client.get("/api/v1/stocks/NVDA/analyst").json()["updated_at"].startswith("2026")


class TestDegradation:
    def test_an_unavailable_service_is_503_not_500(self) -> None:
        """500 would suggest this platform is broken. 503 says a dependency is,
        which is both true and something a caller can act on."""
        client, _ = build(analyst=FakeAnalyst(fails=True))
        response = client.get("/api/v1/stocks/NVDA/analyst")

        assert response.status_code == 503

    def test_it_says_when_to_come_back(self) -> None:
        client, _ = build(analyst=FakeAnalyst(fails=True))
        response = client.get("/api/v1/stocks/NVDA/analyst")
        assert response.headers["retry-after"] == "5"

    def test_the_failing_service_is_named(self) -> None:
        client, _ = build(billing=FakeBilling(fails=True))
        assert "recharge" in client.get("/api/v1/billing/plans").json()["detail"]

    def test_one_service_being_down_does_not_affect_another(self) -> None:
        """The whole point of separate services reached separately."""
        client, _ = build(analyst=FakeAnalyst(fails=True), billing=FakeBilling())
        assert client.get("/api/v1/stocks/NVDA/analyst").status_code == 503
        assert client.get("/api/v1/billing/plans").status_code == 200


class TestBillingRoutes:
    def test_plans_carry_a_comparable_monthly_price(self) -> None:
        """A yearly plan and a monthly one are not otherwise comparable by the
        headline price, which is the number a reader sees first."""
        client, _ = build(
            billing=FakeBilling(
                plans=[Plan(id=1, name="Yearly", price=Decimal("365"), duration_days=365)]
            )
        )
        plan = client.get("/api/v1/billing/plans").json()["plans"][0]
        assert plan["monthly_price"] == "30.00"

    def test_membership_reports_whether_it_is_active(self) -> None:
        """Level alone is not enough — an expired membership still has one."""
        client, _ = build(
            billing=FakeBilling(
                membership=Membership(level=2, expires_at=datetime(2020, 1, 1, tzinfo=UTC))
            )
        )
        body = client.get("/api/v1/billing/membership").json()
        assert body["level"] == 2
        assert body["active"] is False

    def test_a_user_with_nothing_reads_as_inactive(self) -> None:
        client, _ = build()
        assert client.get("/api/v1/billing/membership").json()["active"] is False

    def test_the_acting_user_comes_from_the_scope(self) -> None:
        """A user id a client could supply is a user id a client could change."""
        client, billing = build()
        client.post(
            "/api/v1/billing/recharge", json={"plan_id": 1, "request_id": "req-1"}
        )
        assert billing.requests[0][0] == "operator"  # the solo-mode identity

    def test_the_idempotency_key_is_passed_through(self) -> None:
        client, billing = build()
        client.post(
            "/api/v1/billing/recharge", json={"plan_id": 1, "request_id": "req-1"}
        )
        assert billing.requests[0][2] == "req-1"

    def test_a_recharge_returns_201_because_the_order_exists(self) -> None:
        """Unlike a trade proposal: the recharge service creates the row
        synchronously and hands back its id."""
        client, _ = build()
        response = client.post(
            "/api/v1/billing/recharge", json={"plan_id": 1, "request_id": "req-1"}
        )
        assert response.status_code == 201
        assert response.json()["awaiting_payment"] is True

    def test_a_missing_request_id_is_refused(self) -> None:
        client, _ = build()
        assert client.post("/api/v1/billing/recharge", json={"plan_id": 1}).status_code == 422

    def test_an_unknown_recharge_order_is_404(self) -> None:
        client, _ = build(billing=FakeBilling(order=None))
        assert client.get("/api/v1/billing/recharge/9").status_code == 404

    def test_a_paid_order_reads_as_settled(self) -> None:
        client, _ = build(
            billing=FakeBilling(
                order=RechargeOrder(
                    id=9, plan_id=1, amount=Decimal("199"), state=OrderState.PAID
                )
            )
        )
        body = client.get("/api/v1/billing/recharge/9").json()
        assert body["state"] == "paid"
        assert body["awaiting_payment"] is False


class TestCacheStats:
    def test_the_services_own_counters_are_exposed(self) -> None:
        """A two-level cache whose hit rate nobody can see is one nobody can
        tune."""
        client, _ = build(analyst=FakeAnalyst(rating()))
        assert client.get("/api/v1/stocks/cache-stats").json()["l1_hits"] == 83
