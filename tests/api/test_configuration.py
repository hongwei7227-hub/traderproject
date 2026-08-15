"""The endpoints a tenant uses to see and change its own configuration.

The interesting behaviour is refusal. Setting a preference is a form
submission, and the whole point of validating it here is that a model which
cannot fill a role gets rejected while someone is looking at the form, rather
than several minutes later inside a turn that fails on a capability nobody
knew was missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from kairos.core.catalog import (
    Capability,
    Catalog,
    Endpoint,
    ModelDescriptor,
    ModelId,
    ProviderDescriptor,
    ProviderId,
    TokenBudget,
    Wire,
)
from kairos.runtime.app import create_app, dependency_overrides_for
from kairos.runtime.settings import AuthSettings, Deployment, Settings

# Three models chosen to make the distinctions visible: one that can fill every
# role, one that lacks vision and so cannot be primary, and one the platform
# uses internally and does not offer.
CATALOG = Catalog(
    providers=[
        ProviderDescriptor(
            id=ProviderId("vendor"),
            display_name="Vendor Inc.",
            endpoint=Endpoint(
                wire=Wire.ANTHROPIC_MESSAGES,
                base_url="https://internal.example",
                credential_env="VENDOR_KEY",
            ),
        )
    ],
    models=[
        ModelDescriptor(
            id=ModelId("flagship"),
            remote_id="vendor-flagship-2026",
            provider=ProviderId("vendor"),
            budget=TokenBudget(context=200_000, max_output=8_000),
            capabilities=Capability.baseline() | Capability.VISION,
        ),
        ModelDescriptor(
            id=ModelId("cheap"),
            remote_id="vendor-cheap-2026",
            provider=ProviderId("vendor"),
            budget=TokenBudget(context=32_000, max_output=2_000),
            capabilities=Capability.baseline(),
        ),
        ModelDescriptor(
            id=ModelId("internal"),
            remote_id="vendor-internal",
            provider=ProviderId("vendor"),
            budget=TokenBudget(context=8_000, max_output=1_000),
            capabilities=Capability.TEXT,
            selectable=False,
        ),
    ],
)


class FakePreferenceRepo:
    def __init__(self, stored: dict[str, str] | None = None) -> None:
        self.stored = dict(stored or {})

    async def as_mapping(self) -> dict[str, str]:
        return dict(self.stored)

    async def set_role(self, role: str, model_id: str) -> None:
        self.stored[role] = model_id

    async def clear_role(self, role: str) -> None:
        self.stored.pop(role, None)


class FakeTurnRepo:
    def __init__(self, usage: tuple[int, int] = (0, 0)) -> None:
        self.usage = usage

    async def token_usage(self) -> tuple[int, int]:
        return self.usage


@dataclass
class FakeRepositories:
    preferences: FakePreferenceRepo = field(default_factory=FakePreferenceRepo)
    turns: FakeTurnRepo = field(default_factory=FakeTurnRepo)


def build(
    *,
    preferences: dict[str, str] | None = None,
    usage: tuple[int, int] = (0, 0),
) -> tuple[TestClient, FakeRepositories]:
    app = create_app(
        settings=Settings(deployment=Deployment.SOLO, auth=AuthSettings()),
        catalog=CATALOG,
    )
    repositories = FakeRepositories(
        preferences=FakePreferenceRepo(preferences),
        turns=FakeTurnRepo(usage),
    )
    dependency_overrides_for(app, repositories=repositories)
    return TestClient(app), repositories


class TestModelList:
    def test_lists_selectable_models(self) -> None:
        client, _ = build()
        models = client.get("/api/v1/models").json()["models"]
        assert [m["id"] for m in models] == ["cheap", "flagship"]

    def test_omits_models_the_platform_keeps_to_itself(self) -> None:
        """Offering a non-selectable model invites picking one that cannot serve."""
        client, _ = build()
        models = client.get("/api/v1/models").json()["models"]
        assert "internal" not in {m["id"] for m in models}

    def test_does_not_leak_how_the_platform_reaches_the_provider(self) -> None:
        """remote_id, base_url and the credential variable are deployment detail."""
        body = build()[0].get("/api/v1/models").text
        assert "vendor-flagship-2026" not in body
        assert "internal.example" not in body
        assert "VENDOR_KEY" not in body

    def test_capabilities_are_named_not_numbered(self) -> None:
        """A flag's numeric value shifts whenever a member is inserted."""
        client, _ = build()
        cheap = _by_id(client.get("/api/v1/models").json()["models"], "cheap")
        assert set(cheap["capabilities"]) == {"text", "tool_calling", "streaming"}

    def test_a_model_without_vision_is_not_eligible_to_be_primary(self) -> None:
        client, _ = build()
        models = client.get("/api/v1/models").json()["models"]
        assert "primary" not in _by_id(models, "cheap")["eligible_roles"]
        assert "primary" in _by_id(models, "flagship")["eligible_roles"]

    def test_reports_the_wire_protocol_rather_than_the_vendor(self) -> None:
        client, _ = build()
        models = client.get("/api/v1/models").json()["models"]
        assert _by_id(models, "cheap")["wire"] == "anthropic-messages"


class TestPreferences:
    def test_every_role_resolves_to_something(self) -> None:
        """Returning only what is stored would leave unset roles blank.

        They are not blank — each resolves to a baseline — and it is the
        resolved answer a tenant needs in order to know where requests go.
        """
        client, _ = build()
        roles = client.get("/api/v1/preferences").json()["roles"]
        assert {r["role"] for r in roles} == {"primary", "swift", "condense", "extract"}
        assert all(r["model_id"] for r in roles)

    def test_an_unset_role_reports_the_baseline_as_its_decider(self) -> None:
        client, _ = build()
        roles = client.get("/api/v1/preferences").json()["roles"]
        assert _by_role(roles, "condense")["decided_by"] == "system-baseline"
        assert _by_role(roles, "condense")["overridden"] is False

    def test_a_set_role_reports_the_tenant_as_its_decider(self) -> None:
        client, _ = build(preferences={"condense": "flagship"})
        role = _by_role(client.get("/api/v1/preferences").json()["roles"], "condense")
        assert role["model_id"] == "flagship"
        assert role["decided_by"] == "tenant-preference"
        assert role["overridden"] is True

    def test_reports_what_a_role_requires(self) -> None:
        client, _ = build()
        roles = client.get("/api/v1/preferences").json()["roles"]
        assert "vision" in _by_role(roles, "primary")["requires"]
        assert "vision" not in _by_role(roles, "swift")["requires"]


class TestSettingAPreference:
    def test_stores_the_choice(self) -> None:
        client, repositories = build()
        response = client.put(
            "/api/v1/preferences/swift", json={"model_id": "cheap"}
        )
        assert response.status_code == 200
        assert repositories.preferences.stored == {"swift": "cheap"}

    def test_takes_effect_on_the_next_read(self) -> None:
        """The chain reads preferences per request; nothing is cached at start."""
        client, _ = build()
        client.put("/api/v1/preferences/swift", json={"model_id": "flagship"})
        roles = client.get("/api/v1/preferences").json()["roles"]
        assert _by_role(roles, "swift")["model_id"] == "flagship"

    def test_refuses_an_unknown_role(self) -> None:
        client, repositories = build()
        response = client.put(
            "/api/v1/preferences/architect", json={"model_id": "cheap"}
        )
        assert response.status_code == 404
        assert repositories.preferences.stored == {}

    def test_refuses_an_unknown_model(self) -> None:
        client, _ = build()
        response = client.put(
            "/api/v1/preferences/swift", json={"model_id": "does-not-exist"}
        )
        assert response.status_code == 422

    def test_refuses_a_model_the_platform_does_not_offer(self) -> None:
        client, _ = build()
        response = client.put(
            "/api/v1/preferences/swift", json={"model_id": "internal"}
        )
        assert response.status_code == 422
        assert "not selectable" in response.json()["detail"]

    def test_refuses_a_model_that_cannot_fill_the_role(self) -> None:
        """The refusal worth making here: otherwise it surfaces mid-turn."""
        client, repositories = build()
        response = client.put(
            "/api/v1/preferences/primary", json={"model_id": "cheap"}
        )
        assert response.status_code == 422
        assert "vision" in response.json()["detail"]
        assert repositories.preferences.stored == {}

    def test_rejects_an_empty_model_id_before_reaching_the_catalogue(self) -> None:
        client, _ = build()
        assert (
            client.put("/api/v1/preferences/swift", json={"model_id": ""}).status_code
            == 422
        )


class TestClearingAPreference:
    def test_drops_the_override(self) -> None:
        client, repositories = build(preferences={"swift": "flagship"})
        assert client.delete("/api/v1/preferences/swift").status_code == 204
        assert repositories.preferences.stored == {}

    def test_the_role_falls_back_down_the_chain(self) -> None:
        client, _ = build(preferences={"swift": "flagship"})
        client.delete("/api/v1/preferences/swift")
        roles = client.get("/api/v1/preferences").json()["roles"]
        assert _by_role(roles, "swift")["decided_by"] == "system-baseline"

    def test_clearing_an_unset_role_is_not_an_error(self) -> None:
        """A client that has just cleared it cannot tell the difference."""
        client, _ = build()
        assert client.delete("/api/v1/preferences/swift").status_code == 204

    def test_refuses_an_unknown_role(self) -> None:
        client, _ = build()
        assert client.delete("/api/v1/preferences/architect").status_code == 404


class TestUsage:
    def test_reports_input_and_output_separately(self) -> None:
        """They are priced separately; one total hides which way a tenant leans."""
        client, _ = build(usage=(12_000, 3_000))
        body = client.get("/api/v1/usage").json()
        assert body == {
            "input_tokens": 12_000,
            "output_tokens": 3_000,
            "total_tokens": 15_000,
        }


def _by_id(models: list[dict], model_id: str) -> dict:
    return next(m for m in models if m["id"] == model_id)


def _by_role(roles: list[dict], role: str) -> dict:
    return next(r for r in roles if r["role"] == role)
