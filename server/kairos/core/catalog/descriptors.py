"""Typed descriptions of the models and providers this platform can reach.

The reference implementation kept this in two hand-maintained JSON files with
no schema. Fields were declared and never read, two parallel catalogues drifted
apart, and an embedding model sat in the same namespace as chat models so that
asking for one by name handed back a chat client.

Here the catalogue is typed, validated at import, and every declared capability
is consumed by something. A field nobody reads is a field that lies.
"""

from __future__ import annotations

from enum import Flag, StrEnum, auto
from typing import Annotated, NewType, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ModelId = NewType("ModelId", str)
ProviderId = NewType("ProviderId", str)


class Wire(StrEnum):
    """The wire protocol a provider speaks.

    Named for the protocol, not the vendor. The reference implementation keyed
    this on vendor names, which produced entries like an OpenRouter provider
    tagged ``deepseek`` and a Qwen coding endpoint that speaks Anthropic —
    true, but unreadable. One vendor can offer several protocols; the protocol
    is what decides how to build the request.
    """

    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GEMINI_GENERATE = "gemini-generate"


class Capability(Flag):
    """What a model can actually do.

    Checked before a model is selected for a role. The reference implementation
    assumed every model supported tool calling and only found out otherwise at
    request time, which turned a configuration mistake into a runtime failure.
    """

    NONE = 0
    TEXT = auto()
    VISION = auto()
    DOCUMENT = auto()
    TOOL_CALLING = auto()
    STREAMING = auto()
    REASONING = auto()

    @classmethod
    def baseline(cls) -> Capability:
        """What an agent-driving model must have to be usable at all."""
        return cls.TEXT | cls.TOOL_CALLING | cls.STREAMING


class Access(StrEnum):
    """How credentials reach the provider."""

    API_KEY = "api_key"
    OAUTH = "oauth"
    SUBSCRIPTION = "subscription"
    LOCAL = "local"


class TokenBudget(BaseModel):
    """Context and output limits, in tokens.

    These take part in runtime decisions. Compaction thresholds are derived
    from `context` rather than hard-coded, so that falling back from a
    million-token model to a two-hundred-thousand-token one moves the
    threshold with it. The reference implementation pinned compaction to an
    absolute number and left it there across fallbacks.
    """

    model_config = ConfigDict(frozen=True)

    context: Annotated[int, Field(gt=0)]
    max_output: Annotated[int, Field(gt=0)]

    def compaction_threshold(self, headroom: float = 0.6) -> int:
        """Token count at which history should be summarized.

        Leaves room for the reply and for the tail of the conversation that
        compaction preserves verbatim.
        """
        return int((self.context - self.max_output) * headroom)


class Endpoint(BaseModel):
    """Where a provider lives and how to authenticate to it."""

    model_config = ConfigDict(frozen=True)

    wire: Wire
    base_url: str | None = None
    credential_env: str | None = None
    access: Access = Access.API_KEY
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _credentials_are_reachable(self) -> Self:
        """Anything but a local endpoint needs a way to authenticate.

        A provider with no credential source and no OAuth flow can only fail,
        and it should fail here at import rather than on a user's request.
        """
        if self.access in (Access.API_KEY, Access.SUBSCRIPTION) and not self.credential_env:
            raise ValueError(
                f"{self.access} endpoint declares no credential_env; "
                "it could never authenticate"
            )
        return self


class ProviderDescriptor(BaseModel):
    """One reachable way to call one vendor.

    Vendors often expose several: a domestic and an international region, a
    pay-as-you-go endpoint and a subscription one. These differ in more than
    their URL — a vendor's subscription endpoint may speak a different wire
    protocol than its metered endpoint — so each is its own descriptor rather
    than a patch on a shared parent. Inheritance by shallow merge is how the
    reference implementation ended up with entries carrying a region they had
    explicitly moved away from.
    """

    model_config = ConfigDict(frozen=True)

    id: ProviderId
    display_name: str
    endpoint: Endpoint
    family: ProviderId | None = Field(
        default=None,
        description="Groups sibling endpoints for credential lookup and display.",
    )
    byok_allowed: bool = Field(
        default=False,
        description=(
            "Whether a tenant may supply its own key. Defaults closed: the "
            "reference implementation defaulted this open and consequently "
            "offered OAuth-only endpoints as places to paste an API key."
        ),
    )

    @property
    def root(self) -> ProviderId:
        return self.family or self.id


class ModelDescriptor(BaseModel):
    """One model, as this platform addresses it.

    `id` is the platform's name for the model and is what tenant preferences,
    role assignments and requests all store. `remote_id` is what goes on the
    wire. They differ whenever the same upstream model is reachable by more
    than one route, which is most of the time.
    """

    model_config = ConfigDict(frozen=True)

    id: ModelId
    remote_id: str
    provider: ProviderId
    budget: TokenBudget
    capabilities: Capability
    selectable: bool = Field(
        default=True,
        description="Whether tenants may choose this model directly.",
    )
    params: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-specific request parameters, passed through verbatim.",
    )

    @model_validator(mode="after")
    def _meets_baseline(self) -> Self:
        """A selectable model must be able to drive an agent turn.

        Non-selectable entries — embeddings, rerankers — are exempt, but they
        live in their own registry rather than this one.
        """
        if self.selectable and Capability.baseline() not in self.capabilities:
            missing = Capability.baseline() & ~self.capabilities
            raise ValueError(f"selectable model {self.id!r} lacks {missing!r}")
        return self

    def supports(self, required: Capability) -> bool:
        return required in self.capabilities
