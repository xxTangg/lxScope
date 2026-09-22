# -*- coding: utf-8 -*-
"""FastAPI dependencies for Logto identity and scope enforcement."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
import logging
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
from .models import (
    TenantIdentity,
    TenantIdentityStatusError,
    TenantNotProvisionedError,
)
from .permissions import (
    has_permission,
    permissions_from_logto_claims,
    TENANT_MANAGE,
)
from .tenant_binding import TenantBindingRepository


_tenant_identity_context: ContextVar[TenantIdentity | None] = ContextVar(
    "lxscope_tenant_identity",
    default=None,
)

_logger = logging.getLogger(__name__)

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
        principal = await verifier.verify(credentials.credentials)
        return await verifier.enrich_profile(principal, credentials.credentials)
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
                username=principal.username,
                display_name=principal.display_name,
                email=principal.email,
            ),
        )
        for subject, identity_status in (
            ("tenant", identity.tenant_status),
            ("user", identity.user_status),
            ("membership", identity.status),
        ):
            if identity_status != "active":
                raise TenantIdentityStatusError(
                    subject=subject,
                    status=identity_status,
                )
        # Materialize the application permission set at the identity boundary.
        # Logto organization roles can grant tenant permissions only; platform
        # permissions must remain explicit API-resource scopes.
        permissions = permissions_from_logto_claims(
            principal.scopes,
            principal.organization_roles,
            principal.organization_id,
        )
        _logger.warning(
            "lxscope.logto_identity_permissions "
            "organization_id=%s subject=%s roles=%s scopes=%s permissions=%s",
            principal.organization_id,
            principal.subject,
            sorted(principal.organization_roles),
            sorted(principal.scopes),
            sorted(permissions),
        )
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
            scopes=permissions,
            tenant_status=identity.tenant_status,
            user_status=identity.user_status,
        )
        _tenant_identity_context.set(identity)
        return identity
    except TenantNotProvisionedError as exc:
        raise _forbidden(
            exc.code,
            "The Logto organization is not provisioned for this service.",
        ) from exc
    except TenantIdentityStatusError as exc:
        raise _forbidden(
            exc.code,
            f"The {exc.subject} identity is not active.",
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
        principal = await verifier.enrich_profile(principal, token)
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


def application_user_from_tenant_identity(identity: TenantIdentity) -> Any:
    """Adapt a trusted tenant identity to the legacy application user shape."""

    try:
        from auth import AuthUser
    except ModuleNotFoundError:
        from examples.agent_service.auth import AuthUser

    permissions = sorted(identity.scopes)
    return AuthUser(
        id=str(identity.membership_id),
        username=identity.display_name or identity.external_user_id,
        display_name=identity.display_name or identity.external_user_id,
        external_user_id=identity.external_user_id,
        # ``role`` is retained only for response compatibility with older
        # callers. Authorization uses ``permissions`` below.
        role=("admin" if has_permission(permissions, TENANT_MANAGE) else "user"),
        status="active",
        capabilities=permissions,
        permissions=permissions,
        tenant_id=str(identity.tenant_id),
        membership_id=str(identity.membership_id),
        identity_provider=identity.identity_provider,
    )


async def get_current_application_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Any:
    """Return the legacy application user shape at the auth boundary.

    Local mode delegates to the existing JWT service unchanged. Logto mode
    exposes the internal membership UUID as ``AuthUser.id`` and copies the
    verified token permissions into the compatibility response. Permission
    checks must use ``AuthUser.permissions`` or a TenantIdentity dependency;
    ``role`` is not an authorization source.
    """

    if getattr(request.app.state, "auth_provider", "local") != "logto":
        return await request.app.state.auth.get_current_user(authorization)

    identity = await resolve_tenant_identity_from_authorization(
        authorization,
        verifier=get_logto_verifier(request),
        repository=get_tenant_binding_repository(request),
    )
    return application_user_from_tenant_identity(identity)


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


def require_permission(permission: str) -> Callable[..., Any]:
    """Create an application permission dependency for local or Logto auth."""

    normalized_permission = permission.strip()
    if not normalized_permission:
        raise ValueError("permission must be a non-empty string")

    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Any:
        user = await get_current_application_user(request, authorization)
        if not has_permission(user.permissions, normalized_permission):
            raise _forbidden(
                "insufficient_permission",
                f"The current identity is missing permission "
                f"{normalized_permission!r}.",
            )
        return user

    return dependency


def require_tenant_permission(permission: str) -> Callable[..., Any]:
    """Require a tenant-scoped permission such as ``tenant:manage``."""

    normalized = permission.strip()
    if ":" not in normalized:
        normalized = f"tenant:{normalized}"
    if not normalized.startswith("tenant:"):
        raise ValueError("tenant permission must use the tenant: namespace")
    return require_permission(normalized)


def require_platform_permission(permission: str) -> Callable[..., Any]:
    """Require a platform-scoped permission such as ``platform:upgrade``."""

    normalized = permission.strip()
    if ":" not in normalized:
        normalized = f"platform:{normalized}"
    if not normalized.startswith("platform:"):
        raise ValueError("platform permission must use the platform: namespace")
    return require_permission(normalized)


__all__ = [
    "get_bound_membership_id",
    "get_bound_tenant_id",
    "get_bound_tenant_identity",
    "clear_bound_tenant_identity",
    "get_current_tenant_identity",
    "get_current_application_user",
    "application_user_from_tenant_identity",
    "get_logto_principal",
    "get_logto_verifier",
    "get_tenant_binding_repository",
    "get_tenant_identity",
    "require_scope",
    "require_permission",
    "require_platform_permission",
    "require_tenant_permission",
    "reset_bound_tenant_identity",
    "resolve_tenant_identity_from_authorization",
]
