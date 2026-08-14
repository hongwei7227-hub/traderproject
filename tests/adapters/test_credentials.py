"""Which key pays, and when a sibling's key may not be borrowed."""

from __future__ import annotations

import pytest

from kairos.adapters.llm.credentials import (
    CredentialResolver,
    EnvironmentKeys,
    InMemoryKeyStore,
    Payer,
)
from kairos.core.catalog import (
    Access,
    Catalog,
    Endpoint,
    ProviderDescriptor,
    ProviderId,
    Wire,
)


def provider(
    pid: str,
    *,
    family: str | None = None,
    wire: Wire = Wire.OPENAI_CHAT,
    access: Access = Access.API_KEY,
    env: str | None = "VENDOR_KEY",
    url: str | None = "https://vendor.example/v1",
) -> ProviderDescriptor:
    return ProviderDescriptor(
        id=ProviderId(pid),
        display_name=pid,
        endpoint=Endpoint(wire=wire, credential_env=env, access=access, base_url=url),
        family=ProviderId(family) if family else None,
    )


VENDOR_FAMILY = [
    provider("vendor"),
    # Same brand, same key variable, different region: interchangeable.
    provider("vendor-intl", family="vendor", url="https://intl.vendor.example/v1"),
    # Same brand, different key variable and protocol: not interchangeable.
    provider(
        "vendor-sub",
        family="vendor",
        wire=Wire.ANTHROPIC_MESSAGES,
        access=Access.SUBSCRIPTION,
        env="VENDOR_SUB_KEY",
        url="https://sub.vendor.example/anthropic",
    ),
]


def resolver(
    tenant_keys: dict[str, tuple[str, str | None]] | None = None,
    platform: dict[str, str] | None = None,
    providers: list[ProviderDescriptor] | None = None,
    **kwargs: bool,
) -> CredentialResolver:
    return CredentialResolver(
        Catalog(providers or VENDOR_FAMILY, []),
        InMemoryKeyStore(tenant_keys),
        EnvironmentKeys(platform or {}),
        **kwargs,  # type: ignore[arg-type]
    )


class TestPrecedence:
    async def test_a_tenant_key_is_preferred(self) -> None:
        found = await resolver(
            tenant_keys={"vendor": ("tenant-secret", None)},
            platform={"VENDOR_KEY": "platform-secret"},
        ).resolve(ProviderId("vendor"))

        assert found.credential is not None
        assert found.credential.secret == "tenant-secret"
        assert found.credential.payer is Payer.TENANT

    async def test_the_platform_key_is_the_fallback(self) -> None:
        found = await resolver(platform={"VENDOR_KEY": "platform-secret"}).resolve(
            ProviderId("vendor")
        )
        assert found.credential is not None
        assert found.credential.payer is Payer.PLATFORM

    async def test_the_platform_key_can_be_refused(self) -> None:
        # Secondary roles must not quietly fall back to the platform's key:
        # that shifts cost onto the platform for work a tenant configured.
        found = await resolver(platform={"VENDOR_KEY": "x"}).resolve(
            ProviderId("vendor"), allow_platform=False
        )
        assert not found.found

    async def test_nothing_anywhere_resolves_to_nothing(self) -> None:
        assert not (await resolver().resolve(ProviderId("vendor"))).found

    async def test_who_pays_is_recorded(self) -> None:
        # Billing cannot be reconstructed from usage patterns afterwards.
        found = await resolver(tenant_keys={"vendor": ("s", None)}).resolve(
            ProviderId("vendor")
        )
        assert found.credential is not None
        assert found.credential.billable_to_tenant


class TestSiblingReuse:
    async def test_an_interchangeable_sibling_key_is_borrowed(self) -> None:
        """Same brand, same key variable, same access: one key serves both."""
        found = await resolver(tenant_keys={"vendor": ("shared", None)}).resolve(
            ProviderId("vendor-intl")
        )
        assert found.found
        assert found.holder == "vendor"

    async def test_a_borrowed_key_keeps_the_callee_endpoint(self) -> None:
        """The URL belongs to the provider being called, not to the key holder.

        A protocol variant reached through its sibling's URL speaks the wrong
        protocol, which fails in a way that looks nothing like a routing bug.
        """
        found = await resolver(tenant_keys={"vendor": ("shared", None)}).resolve(
            ProviderId("vendor-intl")
        )
        assert found.credential is not None
        assert found.credential.base_url == "https://intl.vendor.example/v1"

    async def test_a_differently_authenticated_sibling_is_not_borrowed(self) -> None:
        """The mistake worth not repeating.

        The subscription endpoint takes its own key. Borrowing the metered
        one produces a 401 that reads as an authentication failure when the
        real answer is 'this endpoint needs its own key'.
        """
        found = await resolver(tenant_keys={"vendor": ("metered-key", None)}).resolve(
            ProviderId("vendor-sub"), allow_platform=False
        )
        assert not found.found

    async def test_its_own_key_is_used_when_present(self) -> None:
        found = await resolver(
            tenant_keys={"vendor": ("metered", None), "vendor-sub": ("subscription", None)}
        ).resolve(ProviderId("vendor-sub"))

        assert found.credential is not None
        assert found.credential.secret == "subscription"
        assert found.holder == "vendor-sub"

    async def test_the_nearest_key_wins(self) -> None:
        found = await resolver(
            tenant_keys={"vendor": ("parent", None), "vendor-intl": ("own", None)}
        ).resolve(ProviderId("vendor-intl"))
        assert found.holder == "vendor-intl"

    async def test_reuse_can_be_disabled_entirely(self) -> None:
        found = await resolver(
            tenant_keys={"vendor": ("shared", None)}, allow_sibling_reuse=False
        ).resolve(ProviderId("vendor-intl"), allow_platform=False)
        assert not found.found


class TestTenantEndpoints:
    async def test_a_tenant_supplied_url_overrides_the_catalogue(self) -> None:
        # A tenant bringing its own key often brings its own endpoint with it.
        found = await resolver(
            tenant_keys={"vendor": ("k", "https://private.example/v1")}
        ).resolve(ProviderId("vendor"))
        assert found.credential is not None
        assert found.credential.base_url == "https://private.example/v1"


class TestAccessModes:
    async def test_a_local_endpoint_needs_no_secret(self) -> None:
        found = await resolver(
            providers=[provider("rig", access=Access.LOCAL, env=None, url="http://x:8000/v1")]
        ).resolve(ProviderId("rig"))
        assert found.credential is not None
        assert found.credential.payer is Payer.PLATFORM

    async def test_an_oauth_endpoint_yields_nothing_from_stored_keys(self) -> None:
        """Its token comes from a flow; returning nothing routes the caller there."""
        found = await resolver(
            providers=[provider("vendor-oauth", access=Access.OAUTH, env=None)],
            platform={"VENDOR_KEY": "irrelevant"},
        ).resolve(ProviderId("vendor-oauth"))
        assert not found.found

    async def test_an_empty_environment_variable_is_not_a_key(self) -> None:
        assert not (
            await resolver(platform={"VENDOR_KEY": ""}).resolve(ProviderId("vendor"))
        ).found


class TestBatchResolution:
    async def test_a_batch_resolves_every_provider(self) -> None:
        """One turn needs the primary, each secondary role and every fallback.

        Resolving those one at a time puts several round trips on the
        latency-critical path.
        """
        found = await resolver(
            tenant_keys={"vendor": ("shared", None), "vendor-sub": ("own", None)}
        ).resolve_many(
            [ProviderId("vendor"), ProviderId("vendor-intl"), ProviderId("vendor-sub")]
        )

        assert found[ProviderId("vendor")].holder == "vendor"
        assert found[ProviderId("vendor-intl")].holder == "vendor"
        assert found[ProviderId("vendor-sub")].holder == "vendor-sub"

    async def test_a_batch_fetches_once(self) -> None:
        calls: list[int] = []

        class CountingStore(InMemoryKeyStore):
            async def get_many(self, providers):  # type: ignore[no-untyped-def]
                calls.append(len(providers))
                return await super().get_many(providers)

        subject = CredentialResolver(
            Catalog(VENDOR_FAMILY, []),
            CountingStore({"vendor": ("k", None)}),
            EnvironmentKeys({}),
        )
        await subject.resolve_many([ProviderId("vendor"), ProviderId("vendor-intl")])

        assert len(calls) == 1

    async def test_a_batch_agrees_with_resolving_singly(self) -> None:
        keys = {"vendor": ("shared", None)}
        batch = await resolver(tenant_keys=keys).resolve_many([ProviderId("vendor-intl")])
        single = await resolver(tenant_keys=keys).resolve(ProviderId("vendor-intl"))

        assert batch[ProviderId("vendor-intl")].holder == single.holder
        assert batch[ProviderId("vendor-intl")].credential == single.credential


class TestUnknownProviders:
    async def test_an_unknown_provider_raises(self) -> None:
        from kairos.core.catalog import UnknownProvider

        with pytest.raises(UnknownProvider):
            await resolver().resolve(ProviderId("ghost"))
