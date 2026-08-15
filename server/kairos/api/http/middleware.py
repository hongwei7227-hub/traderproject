"""The middleware that puts a tenant scope around every request.

This is the only place scope is established. Having one means the guarantee the
persistence layer relies on — that a scope is present — is structural rather
than something each route remembers.

Public routes are named explicitly. The reference implementation had the
opposite default: authentication was a dependency each route opted into, and
four routers never did. One of them served the identifiers of every active
workspace to anyone who asked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Final

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from kairos.api.http.identity import AuthenticationError, IdentityResolver
from kairos.core.tenancy import establish, release

Handler = Callable[[Request], Awaitable[Response]]

SERVICE_TOKEN_HEADER: Final = "X-Service-Token"
ACTING_TENANT_HEADER: Final = "X-Acting-Tenant"
ACTING_USER_HEADER: Final = "X-Acting-User"


class TenantScopeMiddleware:
    """Establishes tenant scope, and tears it down again.

    Written against the ASGI interface rather than as a decorator so that it
    cannot be applied to some routes and not others.
    """

    def __init__(
        self,
        app: ASGIApp,
        resolver: IdentityResolver,
        public_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self._resolver = resolver
        # Prefix match, so a public prefix covers everything beneath it. Kept
        # as an explicit allowlist: adding a route should not be able to make
        # it public by accident.
        self._public = tuple(public_paths)

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if self._is_public(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        try:
            tenant_scope = await self._resolver.resolve(
                authorization=request.headers.get("authorization"),
                service_token=request.headers.get(SERVICE_TOKEN_HEADER),
                acting_tenant=request.headers.get(ACTING_TENANT_HEADER),
                acting_user=request.headers.get(ACTING_USER_HEADER),
            )
        except AuthenticationError:
            response = JSONResponse({"detail": "Not authenticated"}, status_code=401)
            await response(scope, receive, send)
            return

        token = establish(tenant_scope)
        # Carried on the ASGI scope too, so that logging and metrics can read
        # it without reaching into the context variable.
        scope["tenant_scope"] = tenant_scope
        try:
            await self.app(scope, receive, send)
        finally:
            # Without this the scope outlives the request and whatever runs
            # next on this task inherits it.
            release(token)

    def _is_public(self, path: str) -> bool:
        return any(path == p or path.startswith(f"{p}/") for p in self._public)


DEFAULT_PUBLIC_PATHS: Final = (
    "/health",
    "/docs",
    "/openapi.json",
)
