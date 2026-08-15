"""The set of models and providers a deployment can actually reach.

Validated once at construction. A catalogue that names a provider it does not
define, or a model whose provider cannot authenticate, fails here rather than
on a tenant's request — the reference implementation deferred both checks to
call time and surfaced them as upstream 401s and 404s.

Chat models and embedding models live in separate registries. Sharing one
namespace, as the original did, meant asking for an embedding model by name
returned a chat client that would fail on first use.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence

from kairos.core.catalog.descriptors import (
    Access,
    Capability,
    ModelDescriptor,
    ModelId,
    ProviderDescriptor,
    ProviderId,
)


class CatalogError(ValueError):
    """The catalogue is internally inconsistent."""


class UnknownModel(LookupError):
    def __init__(self, model_id: str, available: Iterable[str]) -> None:
        self.model_id = model_id
        options = ", ".join(sorted(available)) or "none"
        super().__init__(f"No model named {model_id!r}. Available: {options}")


class UnknownProvider(LookupError):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"No provider named {provider_id!r}")


class Catalog:
    """An immutable, validated view of what this deployment can call."""

    __slots__ = ("_models", "_providers", "_by_family")

    def __init__(
        self,
        providers: Sequence[ProviderDescriptor],
        models: Sequence[ModelDescriptor],
    ) -> None:
        self._providers = self._index_providers(providers)
        self._models = self._index_models(models, self._providers)
        self._by_family = self._index_families(self._providers)

    # -- construction ------------------------------------------------------

    @staticmethod
    def _index_providers(
        providers: Sequence[ProviderDescriptor],
    ) -> Mapping[ProviderId, ProviderDescriptor]:
        indexed: dict[ProviderId, ProviderDescriptor] = {}
        for provider in providers:
            if provider.id in indexed:
                raise CatalogError(f"duplicate provider id {provider.id!r}")
            indexed[provider.id] = provider

        # A family pointer into nothing would make credential lookup walk off
        # the end of the chain at request time.
        for provider in indexed.values():
            if provider.family is not None and provider.family not in indexed:
                raise CatalogError(
                    f"provider {provider.id!r} names family {provider.family!r}, "
                    "which is not defined"
                )
            if provider.family == provider.id:
                raise CatalogError(f"provider {provider.id!r} is its own family")
        return indexed

    @staticmethod
    def _index_models(
        models: Sequence[ModelDescriptor],
        providers: Mapping[ProviderId, ProviderDescriptor],
    ) -> Mapping[ModelId, ModelDescriptor]:
        indexed: dict[ModelId, ModelDescriptor] = {}
        for model in models:
            if model.id in indexed:
                raise CatalogError(f"duplicate model id {model.id!r}")
            if model.provider not in providers:
                raise CatalogError(
                    f"model {model.id!r} names provider {model.provider!r}, "
                    "which is not defined"
                )
            indexed[model.id] = model
        return indexed

    @staticmethod
    def _index_families(
        providers: Mapping[ProviderId, ProviderDescriptor],
    ) -> Mapping[ProviderId, tuple[ProviderId, ...]]:
        """Group sibling endpoints under their shared root.

        Credential lookup walks these: a key stored against one endpoint of a
        vendor may serve its siblings.
        """
        families: dict[ProviderId, list[ProviderId]] = {}
        for provider in providers.values():
            families.setdefault(provider.root, []).append(provider.id)
        return {root: tuple(sorted(members)) for root, members in families.items()}

    # -- lookup ------------------------------------------------------------

    def model(self, model_id: ModelId | str) -> ModelDescriptor:
        try:
            return self._models[ModelId(str(model_id))]
        except KeyError:
            raise UnknownModel(str(model_id), self._models) from None

    def provider(self, provider_id: ProviderId | str) -> ProviderDescriptor:
        try:
            return self._providers[ProviderId(str(provider_id))]
        except KeyError:
            raise UnknownProvider(str(provider_id)) from None

    def provider_for(self, model_id: ModelId | str) -> ProviderDescriptor:
        return self.provider(self.model(model_id).provider)

    def has_model(self, model_id: ModelId | str) -> bool:
        return ModelId(str(model_id)) in self._models

    # -- queries -----------------------------------------------------------

    def selectable(self) -> tuple[ModelDescriptor, ...]:
        """Models a tenant may choose from."""
        return tuple(m for m in self._models.values() if m.selectable)

    def capable_of(self, required: Capability) -> tuple[ModelDescriptor, ...]:
        """Selectable models meeting a capability requirement.

        Used when assigning a role, so that a role needing vision is never
        handed a text-only model.
        """
        return tuple(m for m in self.selectable() if m.supports(required))

    def family_of(self, provider_id: ProviderId | str) -> tuple[ProviderId, ...]:
        """Sibling endpoints sharing a credential root, nearest first.

        Order is deliberate: the endpoint itself, then its siblings. A key
        stored against a specific endpoint should win over one inherited from
        a sibling, because siblings can differ in wire protocol and host — the
        reference implementation reused a parent's key against a sibling with a
        different credential variable and produced 401s that read as auth
        failures rather than as missing configuration.
        """
        provider = self.provider(provider_id)
        members = self._by_family.get(provider.root, ())
        return (provider.id, *(m for m in members if m != provider.id))

    def byok_providers(self) -> tuple[ProviderId, ...]:
        """Providers where a tenant may supply its own credentials.

        OAuth and local endpoints are excluded regardless of their flag: there
        is no API key to paste for either.
        """
        return tuple(
            p.id
            for p in self._providers.values()
            if p.byok_allowed and p.endpoint.access not in (Access.OAUTH, Access.LOCAL)
        )

    # -- protocol ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._models)

    def __iter__(self) -> Iterator[ModelDescriptor]:
        return iter(self._models.values())

    def __contains__(self, model_id: object) -> bool:
        return isinstance(model_id, str) and self.has_model(model_id)
