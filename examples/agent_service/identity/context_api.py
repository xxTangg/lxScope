"""Authentication context endpoint for the application layer."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

try:
    from auth import AuthUser
except ModuleNotFoundError:
    from examples.agent_service.auth import AuthUser
from .dependencies import (
    application_user_from_tenant_identity,
    get_logto_verifier,
    get_tenant_binding_repository,
    resolve_tenant_identity_from_authorization,
)


class AuthContextResponse(BaseModel):
    """The backend-authoritative identity and permission context."""

    provider: Literal["local", "logto"]
    user: AuthUser
    permissions: list[str]
    tenant_id: str | None = None
    membership_id: str | None = None
    external_org_id: str | None = None
    membership_role: str | None = None
    tenant_status: str | None = None
    user_status: str | None = None
    membership_status: str | None = None


auth_context_router = APIRouter(tags=["auth"])


@auth_context_router.get("/auth/context", response_model=AuthContextResponse)
async def get_auth_context(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthContextResponse:
    """Return the current application identity without trusting the UI role."""

    provider = getattr(request.app.state, "auth_provider", "local")
    if provider != "logto":
        user = await request.app.state.auth.get_current_user(authorization)
        return AuthContextResponse(
            provider="local",
            user=user,
            permissions=sorted(user.permissions),
        )

    identity = await resolve_tenant_identity_from_authorization(
        authorization,
        verifier=get_logto_verifier(request),
        repository=get_tenant_binding_repository(request),
    )
    user = application_user_from_tenant_identity(identity)
    return AuthContextResponse(
        provider="logto",
        user=user,
        permissions=sorted(identity.scopes),
        tenant_id=str(identity.tenant_id),
        membership_id=str(identity.membership_id),
        external_org_id=identity.external_org_id,
        membership_role=identity.role,
        tenant_status=identity.tenant_status,
        user_status=identity.user_status,
        membership_status=identity.status,
    )


__all__ = ["AuthContextResponse", "auth_context_router", "get_auth_context"]
