# -*- coding: utf-8 -*-
"""FastAPI dependencies for Logto identity and scope enforcement."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .logto_verifier import (
    LogtoOrganizationRequiredError,
    LogtoPrincipal,
    LogtoTokenVerificationError,
    LogtoVerifier,
)
from .models import TenantIdentity, TenantNotProvisionedError
from .tenant_binding import TenantBindingRepository


_tenant_identity_context: ContextVar[TenantIdentity | None] = ContextVar(
    "lxscope_tenant_identity",
    default=None,
)


def get_bound_tenant_identity() -> TenantIdentity | None:
    """Return the tenant identity bound to the current request/task context."""

    return _tenant_identity_context.get()


def get_bound_tenant_id() -> UUID | None:
    """Return the trusted internal tenant UUID for the current context."""

    identity = get_bound_tenant_identity()
    return identity.tenant_id if identity is not None else None


def get_bound_membership_id() -> UUID | None:
    """Return the trusted membership UUID for the current context."""

    identity = get_bound_tenant_identity()
    return identity.membership_id if identity is not None else None


def clear_bound_tenant_identity() -> Any:
    """Clear request context and return a token suitable for restoration."""

    return _tenant_identity_context.set(None)


def reset_bound_tenant_identity(token: Any) -> None:
    """Restore the tenant context that existed before a request began."""

    _tenant_identity_context.reset(token)

_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_token", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


def get_logto_verifier(request: Request) -> LogtoVerifier:
    """Return the configured verifier, creating it from env lazily."""

    verifier = getattr(request.app.state, "logto_verifier", None)
    if verifier is not None:
        return verifier
    try:
        verifier = LogtoVerifier.from_env()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "logto_not_configured",
                "message": "Logto token verification is not configured.",
            },
        ) from exc
    request.app.state.logto_verifier = verifier
    return verifier


def get_tenant_binding_repository(request: Request) -> TenantBindingRepository:
    """Return the repository backed by the existing ApplicationDatabase."""

    repository = getattr(request.app.state, "tenant_binding_repository", None)
    if repository is not None:
        return repository

    application_database = getattr(
        request.app.state,
        "application_database",
        None,
    )
    if application_database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "application_database_unavailable",
                "message": "The application database is not ready.",
            },
        )
    repository = TenantBindingRepository(application_database)
    request.app.state.tenant_binding_repository = repository
    return repository


async def get_logto_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> LogtoPrincipal:
    """Extract and verify the Bearer token from the request."""

    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials.strip()
    ):
        raise _unauthorized("A Bearer access token is required.")

    verifier = get_logto_verifier(request)
    try:
        return await verifier.verify(credentials.credentials)
    except LogtoOrganizationRequiredError as exc:
        raise _forbidden(
            "organization_required",
            "The access token must contain organization_id.",
        ) from exc
    except LogtoTokenVerificationError as exc:
        raise _unauthorized(
            "The Logto access token is invalid or expired.",
        ) from exc


async def _resolve_tenant_identity(
    principal: LogtoPrincipal,
    repository: TenantBindingRepository,
) -> TenantIdentity:
    """Resolve verified Logto claims to internal tenant-scoped IDs."""
    from .models import IdentityPrincipal

    try:
        identity = await repository.resolve(
            IdentityPrincipal(
                external_org_id=principal.organization_id,
                external_user_id=principal.subject,
                identity_provider="logto",
            ),
        )
        # Preserve the verifier's exact scope claim at the application
        # boundary. Authorization remains a direct scope check; this is not a
        # role-to-permission mapping.
        identity = TenantIdentity(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            membership_id=identity.membership_id,
            identity_provider=identity.identity_provider,
            external_org_id=identity.external_org_id,
            external_user_id=identity.external_user_id,
            role=identity.role,
            status=identity.status,
            display_name=identity.display_name,
            scopes=principal.scopes,
        )
        _tenant_identity_context.set(identity)
        return identity
    except TenantNotProvisionedError as exc:
        raise _forbidden(
            exc.code,
            "The Logto organization is not provisioned for this service.",
        ) from exc


async def resolve_tenant_identity_from_authorization(
    authorization: str | None,
    *,
    verifier: LogtoVerifier,
    repository: TenantBindingRepository,
) -> TenantIdentity:
    """Resolve a raw request Authorization header at the app boundary."""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("A Bearer access token is required.")
    token = token.strip()
    try:
        principal = await verifier.verify(token)
    except LogtoOrganizationRequiredError as exc:
        raise _forbidden(
            "organization_required",
            "The access token must contain organization_id.",
        ) from exc
    except LogtoTokenVerificationError as exc:
        raise _unauthorized(
            "The Logto access token is invalid or expired.",
        ) from exc
    return await _resolve_tenant_identity(principal, repository)


async def get_current_application_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Any:
    """Return the legacy application user shape at the auth boundary.

    Local mode delegates to the existing JWT service unchanged. Logto mode
    exposes the internal membership UUID as ``AuthUser.id`` and treats the
    explicit ``tenant:manage`` scope as administrator access. The backend is
    the authority for this decision; the frontend role is only a navigation
    hint.
    """

    from auth import AuthUser

    if getattr(request.app.state, "auth_provider", "local") != "logto":
        return await request.app.state.auth.get_current_user(authorization)

    identity = await resolve_tenant_identity_from_authorization(
        authorization,
        verifier=get_logto_verifier(request),
        repository=get_tenant_binding_repository(request),
    )
    role = "admin" if "tenant:manage" in identity.scopes else "user"
    status_value = (
        identity.status
        if identity.status in {"active", "locked", "banned", "deleted"}
        else "active"
    )
    return AuthUser(
        id=str(identity.membership_id),
        username=identity.display_name or identity.external_user_id,
        role=role,
        status=status_value,
    )


async def get_tenant_identity(
    principal: LogtoPrincipal = Depends(get_logto_principal),
    repository: TenantBindingRepository = Depends(
        get_tenant_binding_repository,
    ),
) -> TenantIdentity:
    """Resolve the verified Logto organization and subject to internal IDs."""

    return await _resolve_tenant_identity(principal, repository)


get_current_tenant_identity = get_tenant_identity


def require_scope(scope: str) -> Callable[..., Any]:
    """Create a dependency that requires one exact token scope."""

    normalized_scope = scope.strip()
    if not normalized_scope:
        raise ValueError("scope must be a non-empty string")

    async def dependency(
        request: Request,
        principal: LogtoPrincipal = Depends(get_logto_principal),
    ) -> TenantIdentity:
        if normalized_scope not in principal.scopes:
            raise _forbidden(
                "insufficient_scope",
                f"The access token is missing scope {normalized_scope!r}.",
            )
        return await _resolve_tenant_identity(
            principal,
            get_tenant_binding_repository(request),
        )

    return dependency


__all__ = [
    "get_bound_membership_id",
    "get_bound_tenant_id",
    "get_bound_tenant_identity",
    "clear_bound_tenant_identity",
    "get_current_tenant_identity",
    "get_current_application_user",
    "get_logto_principal",
    "get_logto_verifier",
    "get_tenant_binding_repository",
    "get_tenant_identity",
    "require_scope",
    "reset_bound_tenant_identity",
    "resolve_tenant_identity_from_authorization",
]
