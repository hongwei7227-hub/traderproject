"""Deciding which key pays for a call.

Two sources: the tenant's own key, or the platform's. Which one is used decides
who gets billed, so the answer is recorded on every call rather than inferred
later from usage patterns.

The subtlety is that vendors expose several endpoints — regions, subscription
tiers, protocol variants — and a tenant that pastes a key usually pastes it
against one of them. Whether that key serves the others is a judgement call, and
the reference implementation got it wrong in a way worth not repeating: it
reused a parent endpoint's key against a sibling with a different credential
variable and a different host, producing 401s that read as authentication
failures when the real answer was "this endpoint needs its own key".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from kairos.core.catalog.descriptors import Access, ProviderDescriptor, ProviderId
from kairos.core.catalog.registry import Catalog


class Payer(StrEnum):
    """Whose credential is being spent."""

    TENANT = "tenant"
    PLATFORM = "platform"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Credential:
    """A resolved credential, and the endpoint it authenticates against."""

    secret: str
    payer: Payer
    provider: ProviderId
    base_url: str | None = None

    @property
    def billable_to_tenant(self) -> bool:
        return self.payer is Payer.TENANT


class TenantKeyStore(Protocol):
    """Where a tenant's own keys live.

    A protocol so that resolution can be exercised without a database, and so
    that a deployment can keep secrets somewhere other than its own tables.
    """

    async def get(self, provider: ProviderId) -> tuple[str, str | None] | None:
        """Return `(secret, base_url)` for a provider, or None."""

    async def get_many(
        self, providers: Sequence[ProviderId]
    ) -> Mapping[ProviderId, tuple[str, str | None]]:
        """Fetch several at once."""


class PlatformKeys(Protocol):
    """The deployment's own keys, typically from the environment."""

    def get(self, variable: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Resolution:
    """A credential, plus which endpoint actually supplied it.

    `holder` differs from `provider` when a sibling's key was used. Recording
    both is what lets a support question — "why is this failing?" — be answered
    without reproducing it.
    """

    credential: Credential | None
    holder: ProviderId | None = None

    @property
    def found(self) -> bool:
        return self.credential is not None


class CredentialResolver:
    """Finds the credential for a provider, tenant keys first."""

    __slots__ = ("_catalog", "_tenant_keys", "_platform_keys", "_allow_sibling_reuse")

    def __init__(
        self,
        catalog: Catalog,
        tenant_keys: TenantKeyStore,
        platform_keys: PlatformKeys,
        *,
        allow_sibling_reuse: bool = True,
    ) -> None:
        self._catalog = catalog
        self._tenant_keys = tenant_keys
        self._platform_keys = platform_keys
        self._allow_sibling_reuse = allow_sibling_reuse

    async def resolve(
        self, provider_id: ProviderId, *, allow_platform: bool = True
    ) -> Resolution:
        provider = self._catalog.provider(provider_id)

        if (found := await self._from_tenant(provider)).found:
            return found

        if allow_platform and (platform := self._from_platform(provider)) is not None:
            return Resolution(credential=platform, holder=provider.id)

        return Resolution(credential=None)

    async def resolve_many(
        self, provider_ids: Sequence[ProviderId], *, allow_platform: bool = True
    ) -> dict[ProviderId, Resolution]:
        """Resolve a batch in as few round trips as possible.

        A single turn needs credentials for the primary model, every secondary
        role and each fallback. Resolving those one at a time puts several
        database round trips on the latency-critical path, so the candidate
        endpoints are collected first and fetched together.
        """
        candidates: set[ProviderId] = set()
        for provider_id in provider_ids:
            candidates.update(self._candidates(self._catalog.provider(provider_id)))

        prefetched = await self._tenant_keys.get_many(sorted(candidates))

        return {
            provider_id: await self._resolve_with(
                self._catalog.provider(provider_id),
                prefetched,
                allow_platform=allow_platform,
            )
            for provider_id in provider_ids
        }

    # -- tenant keys -------------------------------------------------------

    async def _from_tenant(self, provider: ProviderDescriptor) -> Resolution:
        for candidate in self._candidates(provider):
            stored = await self._tenant_keys.get(candidate)
            if stored is None:
                continue
            return self._build(provider, candidate, stored)
        return Resolution(credential=None)

    async def _resolve_with(
        self,
        provider: ProviderDescriptor,
        prefetched: Mapping[ProviderId, tuple[str, str | None]],
        *,
        allow_platform: bool,
    ) -> Resolution:
        for candidate in self._candidates(provider):
            if (stored := prefetched.get(candidate)) is not None:
                return self._build(provider, candidate, stored)

        if allow_platform and (platform := self._from_platform(provider)) is not None:
            return Resolution(credential=platform, holder=provider.id)
        return Resolution(credential=None)

    def _candidates(self, provider: ProviderDescriptor) -> tuple[ProviderId, ...]:
        """Endpoints whose key might serve this one, nearest first.

        Sibling reuse is refused when the sibling authenticates differently —
        a subscription endpoint and a metered one under the same brand take
        different keys, and borrowing one for the other yields a 401 that
        misdescribes the problem.
        """
        if not self._allow_sibling_reuse:
            return (provider.id,)

        family = self._catalog.family_of(provider.id)
        usable = [provider.id]
        for sibling_id in family[1:]:
            sibling = self._catalog.provider(sibling_id)
            if self._interchangeable(provider, sibling):
                usable.append(sibling_id)
        return tuple(usable)

    @staticmethod
    def _interchangeable(
        provider: ProviderDescriptor, sibling: ProviderDescriptor
    ) -> bool:
        if sibling.endpoint.access is not provider.endpoint.access:
            return False
        # Distinct credential variables are the deployment saying these take
        # different keys. Honour that rather than discovering it upstream.
        return sibling.endpoint.credential_env == provider.endpoint.credential_env

    def _build(
        self,
        provider: ProviderDescriptor,
        holder: ProviderId,
        stored: tuple[str, str | None],
    ) -> Resolution:
        secret, tenant_base_url = stored
        return Resolution(
            credential=Credential(
                secret=secret,
                payer=Payer.TENANT,
                provider=provider.id,
                # The endpoint belongs to the provider being called, not to
                # whichever sibling held the key. A protocol variant reached
                # through its sibling's URL speaks the wrong protocol.
                base_url=tenant_base_url or provider.endpoint.base_url,
            ),
            holder=holder,
        )

    # -- platform keys -----------------------------------------------------

    def _from_platform(self, provider: ProviderDescriptor) -> Credential | None:
        endpoint = provider.endpoint

        if endpoint.access is Access.LOCAL:
            # A local runtime needs no secret, but clients still expect a
            # non-empty string.
            return Credential(
                secret="local",
                payer=Payer.PLATFORM,
                provider=provider.id,
                base_url=endpoint.base_url,
            )

        if endpoint.access is Access.OAUTH:
            # An OAuth endpoint's token comes from a flow, not from a stored
            # key. Returning nothing sends the caller down the right path.
            return None

        if not endpoint.credential_env:
            return None

        secret = self._platform_keys.get(endpoint.credential_env)
        if not secret:
            return None

        return Credential(
            secret=secret,
            payer=Payer.PLATFORM,
            provider=provider.id,
            base_url=endpoint.base_url,
        )


class EnvironmentKeys:
    """Platform keys read from the process environment."""

    __slots__ = ("_source",)

    def __init__(self, source: Mapping[str, str] | None = None) -> None:
        if source is None:
            import os

            source = os.environ
        self._source = source

    def get(self, variable: str) -> str | None:
        value = self._source.get(variable)
        return value or None


class InMemoryKeyStore:
    """A tenant key store backed by a dict. For tests and single-user runs."""

    __slots__ = ("_keys",)

    def __init__(self, keys: Mapping[str, tuple[str, str | None]] | None = None) -> None:
        self._keys = {ProviderId(k): v for k, v in (keys or {}).items()}

    async def get(self, provider: ProviderId) -> tuple[str, str | None] | None:
        return self._keys.get(provider)

    async def get_many(
        self, providers: Sequence[ProviderId]
    ) -> Mapping[ProviderId, tuple[str, str | None]]:
        return {p: self._keys[p] for p in providers if p in self._keys}
