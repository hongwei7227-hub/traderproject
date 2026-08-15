"""Configuration, in one tree.

The reference implementation spread this across two YAML files and the
environment, with overlapping keys and no schema. Worse, one of those layers
was read once at startup and cached for the life of the process, so half the
settings were live and half needed a restart — with nothing in the file saying
which was which.

Here everything is one validated tree loaded once. What genuinely needs to
change without a restart — a tenant's model choice, its quota — is tenant data
in the database, not configuration. That line is the point: configuration
describes the deployment, and deployments do not change between requests.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Deployment(StrEnum):
    """How this instance is being run.

    `SOLO` is a single operator on their own machine: authentication is off and
    every request belongs to one built-in tenant. `HOSTED` serves many tenants
    and authenticates everything.

    Keeping this as an enum rather than a `debug` boolean is deliberate — the
    difference determines whether authentication happens at all, and that
    decision should be a named mode rather than a side effect of a flag.
    """

    SOLO = "solo"
    HOSTED = "hosted"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIROS_DB_")

    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/kairos"
    pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    max_overflow: Annotated[int, Field(ge=0, le=100)] = 20
    pool_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    echo_sql: bool = False


class CacheSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIROS_CACHE_")

    url: str = "redis://localhost:6379/0"
    default_ttl_seconds: Annotated[int, Field(gt=0)] = 300
    stream_ttl_seconds: Annotated[int, Field(gt=0)] = 3600


class AuthSettings(BaseSettings):
    """How a caller is identified.

    Two providers, and the choice is a deployment decision:

    `session` — the login service owns identity. It mints an opaque token and
    keeps the user behind it in Redis, refreshing the expiry on each use. This
    platform reads the same key rather than calling the service on every
    request, because an identity check on the hot path should not add a network
    hop to a service that is already one lookup away from the same Redis.

    `jwt` — an external identity provider signs a token that carries its own
    claims. Kept for deployments that already have one; `issuer` is then
    required, because verifying signature and audience alone accepts tokens
    minted by any project of that provider using the same audience string.
    """

    model_config = SettingsConfigDict(env_prefix="KAIROS_AUTH_")

    provider: Literal["session", "jwt"] = "session"

    # -- session provider (the login service) ------------------------------
    #
    # The key format is the login service's, not ours. It is written here
    # rather than derived so that a change on either side shows up as a diff
    # in one obvious place instead of as everyone being logged out.
    session_key_prefix: str = "login:token:"
    session_ttl_minutes: Annotated[int, Field(gt=0)] = 30
    session_refresh_on_read: bool = Field(
        default=True,
        description=(
            "Slide the expiry when a token is used, matching the login "
            "service's own interceptor. Disable only if some other component "
            "owns the sliding window, or the two will fight over it."
        ),
    )

    # -- jwt provider ------------------------------------------------------
    jwks_url: str | None = None
    issuer: str | None = None
    audience: str = "authenticated"
    algorithms: tuple[str, ...] = ("RS256", "ES256")
    jwks_cache_seconds: Annotated[int, Field(gt=0)] = 300

    solo_tenant_id: str = "solo"
    solo_user_id: str = "operator"

    service_token: SecretStr | None = Field(
        default=None,
        description=(
            "Shared secret for service-to-service calls. When set, a caller "
            "presenting it may act for any tenant, so it is a credential of "
            "last resort rather than a convenience."
        ),
    )


class ServiceSettings(BaseSettings):
    """Where the Java services live.

    Four of them, each owning a capability this platform does not implement
    itself: identity, analyst data, order execution and billing. They are
    addressed by URL rather than linked in, so one can be restarted, scaled or
    replaced without this process knowing.

    Ports match what each service binds by default. They are listed rather
    than discovered because a wrong port should fail as a connection refused at
    startup, not as a mysterious 404 halfway through a request.
    """

    model_config = SettingsConfigDict(env_prefix="KAIROS_SERVICE_")

    login_url: str = "http://localhost:8081"
    recharge_url: str = "http://localhost:8082"
    execution_url: str = "http://localhost:8090"
    analyst_url: str = "http://localhost:8091"

    request_timeout_seconds: Annotated[float, Field(gt=0)] = 5.0

    # The schema the execution worker's migrations own. This platform reads
    # those tables and never writes them: the worker is the only writer, and
    # two writers to an order row is how a fill gets overwritten by a stale
    # status.
    execution_schema: str = "execution"

    # Orders leave through a table rather than a broker connection. The worker
    # consumes RocketMQ; this process does not speak it, and adding a second
    # client to the request path would make placing an order depend on the
    # broker being reachable at that instant. The relay drains the table.
    order_outbox_enabled: bool = True


class TradingSettings(BaseSettings):
    """The risk envelope, and the account it is measured against.

    Every limit is a fraction of equity rather than an absolute sum, so the
    envelope scales with the account instead of needing to be re-tuned whenever
    it grows. Defaults are deliberately tight: an envelope that has to be
    loosened on purpose leaves a trace of someone deciding to.
    """

    model_config = SettingsConfigDict(env_prefix="KAIROS_TRADING_")

    enabled: bool = False
    default_account_id: str = "paper"

    max_order_fraction: Annotated[float, Field(gt=0, le=1)] = 0.02
    max_position_fraction: Annotated[float, Field(gt=0, le=1)] = 0.30
    max_orders_per_day: Annotated[int, Field(ge=0)] = 3
    universe: tuple[str, ...] = Field(
        default=(),
        description=(
            "Tradable symbols. Empty means unrestricted rather than nothing "
            "tradable — otherwise a deployment that had not configured one "
            "would refuse every order."
        ),
    )

    # Used only when the account service cannot be reached. Sizing against a
    # guess is worse than refusing, so this is deliberately small: it lets a
    # demo place tiny orders and stops a real deployment placing large ones on
    # a number nobody supplied.
    fallback_equity: Annotated[float, Field(ge=0)] = 0.0


class QuotaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIROS_QUOTA_")

    enabled: bool = True
    default_period_tokens: Annotated[int, Field(gt=0)] = 10_000_000

    # Reservation is pessimistic because a request's cost is unknown until it
    # finishes. Reserve against the worst case, settle against the actual, and
    # a tenant cannot overrun by starting many requests at once.
    reservation_multiplier: Annotated[float, Field(ge=1.0)] = 1.0
    on_exhaustion: Literal["reject", "degrade", "allow"] = "reject"


class ResilienceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIROS_RESILIENCE_")

    failure_threshold: Annotated[int, Field(ge=1)] = 5
    recovery_after_seconds: Annotated[float, Field(gt=0)] = 60.0
    success_threshold: Annotated[int, Field(ge=1)] = 2

    # One attempt plus this many retries, per candidate. Kept low because the
    # provider SDK retries underneath: the reference implementation had three
    # middleware retries over five SDK retries, so a single upstream wobble
    # became twenty-four requests per candidate.
    max_retries: Annotated[int, Field(ge=0, le=5)] = 2
    initial_backoff_seconds: Annotated[float, Field(gt=0)] = 1.0
    max_backoff_seconds: Annotated[float, Field(gt=0)] = 30.0


class RateLimitSettings(BaseSettings):
    """Three tiers, because one is never enough.

    Global protects the platform from everyone, per-tenant enforces fairness
    between them, per-user stops one person inside a tenant consuming its whole
    allowance.
    """

    model_config = SettingsConfigDict(env_prefix="KAIROS_RATELIMIT_")

    enabled: bool = True
    global_requests_per_minute: Annotated[int, Field(gt=0)] = 6000
    tenant_requests_per_minute: Annotated[int, Field(gt=0)] = 600
    user_concurrent_requests: Annotated[int, Field(gt=0)] = 5


class Settings(BaseSettings):
    """The whole configuration of a deployment."""

    model_config = SettingsConfigDict(
        env_prefix="KAIROS_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="forbid",
    )

    deployment: Deployment = Deployment.SOLO
    service_name: str = "kairos"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    services: ServiceSettings = Field(default_factory=ServiceSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)
    quota: QuotaSettings = Field(default_factory=QuotaSettings)
    resilience: ResilienceSettings = Field(default_factory=ResilienceSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)

    @model_validator(mode="after")
    def _hosted_mode_is_fully_configured(self) -> Self:
        """A multi-tenant deployment must be able to verify who is calling.

        Failing at startup is the point. A hosted instance that silently falls
        back to solo behaviour would serve every request as the same tenant,
        and nothing downstream would look wrong.
        """
        if self.deployment is not Deployment.HOSTED:
            return self

        # What must be present depends on who issues the tokens. Demanding the
        # JWT settings from a deployment that uses the login service would make
        # it configure an identity provider it does not have.
        if self.auth.provider == "session":
            required: tuple[tuple[str, object], ...] = (
                ("cache.url", self.cache.url),
                ("services.login_url", self.services.login_url),
            )
        else:
            required = (
                ("auth.jwks_url", self.auth.jwks_url),
                ("auth.issuer", self.auth.issuer),
            )

        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(
                f"hosted deployment with the {self.auth.provider!r} auth "
                f"provider requires {', '.join(missing)}; without them tokens "
                "cannot be verified"
            )
        return self

    @property
    def authenticates_requests(self) -> bool:
        return self.deployment is Deployment.HOSTED


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process's settings.

    Cached because configuration describes the deployment, and the deployment
    does not change while it is running. Anything that must vary per request
    belongs in the database.
    """
    return Settings()
