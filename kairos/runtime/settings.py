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
    """Token verification.

    `issuer` is required in hosted mode and has no default. The reference
    implementation verified signature and audience but never the issuer, which
    left it accepting tokens minted by any project of the same identity
    provider that happened to use the same audience string.
    """

    model_config = SettingsConfigDict(env_prefix="KAIROS_AUTH_")

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

        missing = [
            name
            for name, value in (
                ("auth.jwks_url", self.auth.jwks_url),
                ("auth.issuer", self.auth.issuer),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"hosted deployment requires {', '.join(missing)}; "
                "without them tokens cannot be verified"
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
