"""Phase 6 platform, credential, and cross-tenant security tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import uuid4

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from identity.dependencies import _tenant_identity_context  # noqa: E402
from identity.models import TenantIdentity  # noqa: E402
from identity.permissions import (  # noqa: E402
    PLATFORM_ADMIN_PERMISSIONS,
    PLATFORM_UPGRADE,
    TENANT_MANAGE,
    is_platform_admin,
)
from longxin_admin.credential_policy import AdminManagedCredentialPolicy  # noqa: E402
from longxin_admin.upgrade.router import _require_admin  # noqa: E402
from agentscope.app.access import ResourceKind  # noqa: E402
from agentscope.app.storage import CredentialRecord  # noqa: E402


class _Auth:
    async def is_admin_user(self, user_id: str) -> bool:
        return user_id == "local-admin"

    async def list_accounts(self):
        return []


class _TenantDirectory:
    def __init__(self) -> None:
        self.rows = {
            "tenant-a": [
                {
                    "membership_id": "admin-a",
                    "permissions": [TENANT_MANAGE],
                    "membership_status": "active",
                    "user_status": "active",
                },
                {
                    "membership_id": "member-a",
                    "permissions": [],
                    "membership_status": "active",
                    "user_status": "active",
                },
            ],
            "tenant-b": [
                {
                    "membership_id": "admin-b",
                    "permissions": [TENANT_MANAGE],
                    "membership_status": "active",
                    "user_status": "active",
                },
            ],
        }

    async def list_members(self, tenant_id):
        return self.rows[str(tenant_id)]


class _Storage:
    def __init__(self) -> None:
        self.records = {
            "admin-a": [
                CredentialRecord(
                    id="cred-a",
                    user_id="admin-a",
                    data={"type": "test", "name": "A"},
                ),
            ],
            "admin-b": [
                CredentialRecord(
                    id="cred-b",
                    user_id="admin-b",
                    data={"type": "test", "name": "B"},
                ),
            ],
        }

    async def list_credentials(self, owner_id: str):
        return self.records.get(owner_id, [])


def _identity(tenant_id: str, permissions=()):
    value = uuid4()
    return TenantIdentity(
        tenant_id=tenant_id,
        user_id=value,
        membership_id=value,
        identity_provider="logto",
        external_org_id=tenant_id,
        external_user_id="viewer",
        role="member",
        status="active",
        permissions=frozenset(permissions),
        scopes=frozenset(permissions),
    )


class Phase6PermissionTest(TestCase):
    def test_tenant_admin_is_not_platform_admin(self) -> None:
        self.assertFalse(is_platform_admin([TENANT_MANAGE]))
        self.assertFalse(is_platform_admin([PLATFORM_UPGRADE]))
        self.assertTrue(is_platform_admin(PLATFORM_ADMIN_PERMISSIONS))


class Phase6CredentialIsolationTest(IsolatedAsyncioTestCase):
    async def test_tenant_catalog_never_returns_another_tenant_credential(self) -> None:
        policy = AdminManagedCredentialPolicy(
            _Auth(),
            tenant_member_provider=lambda: _TenantDirectory(),
            scope="tenant",
        )
        storage = _Storage()
        token = _tenant_identity_context.set(_identity("tenant-a"))
        try:
            refs = await policy.list_accessible("member-a", ResourceKind.CREDENTIAL, storage)
        finally:
            _tenant_identity_context.reset(token)
        self.assertEqual([(ref.owner_id, ref.resource_id) for ref in refs], [("admin-a", "cred-a")])


class Phase6UpgradeBoundaryTest(IsolatedAsyncioTestCase):
    async def test_tenant_admin_can_upgrade(self) -> None:
        from auth import AuthUser

        user = await _require_admin(
            AuthUser(
                id="tenant-admin",
                username="tenant-admin",
                permissions=[TENANT_MANAGE],
            ),
        )
        self.assertEqual(user.id, "tenant-admin")

    async def test_platform_admin_can_upgrade(self) -> None:
        from auth import AuthUser

        user = await _require_admin(
            AuthUser(
                id="platform-admin",
                username="platform-admin",
                permissions=sorted(PLATFORM_ADMIN_PERMISSIONS),
            ),
        )
        self.assertEqual(user.id, "platform-admin")


if __name__ == "__main__":
    import unittest

    unittest.main()
