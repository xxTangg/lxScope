"""Application-layer permissions for tenant and platform boundaries."""

from __future__ import annotations

from collections.abc import Iterable

TENANT_MANAGE = "tenant:manage"
PLATFORM_MANAGE = "platform:manage"
PLATFORM_UPGRADE = "platform:upgrade"
PLATFORM_INTEGRATION = "platform:integration"
PLATFORM_OBSERVE = "platform:observe"

# These are Logto organization roles, not application authorization checks.
# They are translated once at the identity boundary into the explicit
# tenant:* permission namespace. Platform permissions are never inferred from
# an organization role.
TENANT_ADMIN_ORGANIZATION_ROLES = frozenset({"admin", "owner"})

LOCAL_USER_PERMISSIONS = frozenset({"chat:read", "chat:write"})
LOCAL_ADMIN_PERMISSIONS = frozenset(
    {
        *LOCAL_USER_PERMISSIONS,
        TENANT_MANAGE,
        PLATFORM_MANAGE,
        PLATFORM_UPGRADE,
        PLATFORM_INTEGRATION,
        PLATFORM_OBSERVE,
    },
)


def normalize_permissions(permissions: Iterable[str]) -> frozenset[str]:
    """Return a stable set of non-empty permission names."""

    return frozenset(
        permission.strip()
        for permission in permissions
        if isinstance(permission, str) and permission.strip()
    )


def has_permission(permissions: Iterable[str], required: str) -> bool:
    """Check an exact permission, including its manage parent scope."""

    normalized = normalize_permissions(permissions)
    required = required.strip()
    if not required:
        return False
    if required in normalized:
        return True
    if required.startswith("tenant:") and TENANT_MANAGE in normalized:
        return True
    if required.startswith("platform:") and PLATFORM_MANAGE in normalized:
        return True
    return False


def permissions_from_logto_claims(
    scopes: Iterable[str],
    organization_roles: Iterable[str],
    organization_id: str | None = None,
) -> frozenset[str]:
    """Materialize application permissions from verified Logto claims.

    Logto organization roles describe authority inside the selected tenant;
    they are therefore allowed to produce tenant permissions only. Platform
    permissions must be issued explicitly as API-resource scopes/claims.
    """

    permissions = set(normalize_permissions(scopes))
    roles: set[str] = set()
    normalized_organization_id = (
        organization_id.strip().lower()
        if isinstance(organization_id, str) and organization_id.strip()
        else None
    )
    for role in organization_roles:
        if not isinstance(role, str) or not role.strip():
            continue
        normalized_role = role.strip().lower()
        if normalized_role in TENANT_ADMIN_ORGANIZATION_ROLES:
            roles.add(normalized_role)
            continue

        # Logto exposes organization roles as ``<organization_id>:<role>``
        # in user claims. Only accept a qualified role for the organization
        # represented by the verified access token.
        if normalized_organization_id is None:
            continue
        prefix = f"{normalized_organization_id}:"
        if normalized_role.startswith(prefix):
            role_name = normalized_role[len(prefix) :].strip()
            if role_name in TENANT_ADMIN_ORGANIZATION_ROLES:
                roles.add(role_name)
    if roles & TENANT_ADMIN_ORGANIZATION_ROLES:
        permissions.add(TENANT_MANAGE)
    return frozenset(permissions)


__all__ = [
    "LOCAL_ADMIN_PERMISSIONS",
    "LOCAL_USER_PERMISSIONS",
    "PLATFORM_INTEGRATION",
    "PLATFORM_MANAGE",
    "PLATFORM_OBSERVE",
    "PLATFORM_UPGRADE",
    "TENANT_ADMIN_ORGANIZATION_ROLES",
    "TENANT_MANAGE",
    "has_permission",
    "normalize_permissions",
    "permissions_from_logto_claims",
]
