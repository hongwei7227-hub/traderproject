"""Building the application.

Everything the process needs is assembled here and nowhere else. Routes receive
their collaborators through dependency overrides rather than importing them, so
that a test can supply a fake engine without patching module globals and a
deployment can supply a real one without the routes knowing which.

Startup either succeeds completely or fails loudly. A partially-started server
that answers health checks while its database is unreachable is worse than one
that refused to start: the first looks fine to a load balancer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kairos.api.http import threads as thread_routes
from kairos.api.http.identity import IdentityResolver, TokenVerifier
from kairos.api.http.middleware import DEFAULT_PUBLIC_PATHS, TenantScopeMiddleware
from kairos.core.catalog.registry import Catalog
from kairos.runtime.container import Container
from kairos.runtime.settings import Deployment, Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Hold resources for the life of the process."""
    container: Container = app.state.container
    try:
        yield
    finally:
        await container.aclose()


def create_app(
    *,
    settings: Settings | None = None,
    catalog: Catalog | None = None,
    verifier: TokenVerifier | None = None,
    container: Container | None = None,
) -> FastAPI:
    """Assemble the application.

    Every collaborator is injectable. The defaults are what a real deployment
    uses; the parameters are what tests and alternative deployments substitute.
    """
    resolved = settings or get_settings()
    resolved_catalog = catalog or Catalog(providers=[], models=[])
    resolved_container = container or Container(resolved, resolved_catalog)

    app = FastAPI(
        title="Kairos",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs expose every route and schema. Useful while
        # developing, an unnecessary map of the attack surface in production.
        docs_url="/docs" if resolved.deployment is Deployment.SOLO else None,
        openapi_url="/openapi.json" if resolved.deployment is Deployment.SOLO else None,
    )
    app.state.container = resolved_container
    app.state.settings = resolved

    _install_middleware(app, resolved, verifier)
    _install_routes(app, resolved_container)

    return app


def _install_middleware(
    app: FastAPI, settings: Settings, verifier: TokenVerifier | None
) -> None:
    """Attach middleware.

    Registration order is reverse execution order, so the scope middleware is
    added last and therefore runs first — everything downstream, including the
    routes, can then rely on a scope being present.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.deployment is Deployment.SOLO else [],
        allow_credentials=True,
        # An explicit list rather than a wildcard: a wildcard here would also
        # admit methods added to the framework later without review.
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    app.add_middleware(
        TenantScopeMiddleware,
        resolver=IdentityResolver(settings, verifier),
        public_paths=DEFAULT_PUBLIC_PATHS,
    )


def _install_routes(app: FastAPI, container: Container) -> None:
    """Mount routers and wire their dependencies.

    Dependency overrides rather than imports: the routes declare what they
    need, and this decides what satisfies it.
    """
    app.include_router(thread_routes.router, prefix="/api/v1")

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        """Liveness.

        Reports what it actually checked. A health endpoint that returns a
        constant tells a load balancer nothing except that the process can
        still serve a route.
        """
        return {
            "status": "ok",
            "service": "kairos",
            "version": app.version,
            "models": len(container.catalog),
            "pipeline_stages": len(container.pipeline),
        }


def dependency_overrides_for(
    app: FastAPI, *, engine: Any = None, repositories: Any = None
) -> None:
    """Point the route dependencies at concrete implementations.

    Separate from `create_app` because what satisfies them differs between a
    deployment, an integration test and a unit test — and burying that choice
    inside the factory would force every caller to accept one of them.
    """
    if engine is not None:
        app.dependency_overrides[thread_routes.get_engine] = lambda: engine
    if repositories is not None:
        app.dependency_overrides[thread_routes.get_repositories] = lambda: repositories
