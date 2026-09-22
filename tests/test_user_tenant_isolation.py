# -*- coding: utf-8 -*-
"""Phase 2 tenant-member isolation and role resolution tests."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from examples.agent_service.identity.models import (
    IdentityPrincipal,
    TenantIdentityStatusError,
)
from examples.agent_service.identity.tenant_binding import TenantBindingRepository


async def _tenant(
    engine: AsyncEngine,
    repository: TenantBindingRepository,
    external_org_id: str,
) -> object:
    tenants = repository._table_definitions()["tenants"]
    tenant_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            insert(tenants).values(
                id=tenant_id,
                code=f"tenant-{external_org_id}",
                name=external_org_id,
                status="active",
                identity_provider="logto",
                external_org_id=external_org_id,
            ),
        )
    return tenant_id


class UserTenantIsolationTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.repository = TenantBindingRepository(self.engine)
        tables = self.repository._table_definitions()
        metadata = next(iter(tables.values())).metadata
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            await connection.execute(
                insert(tables["role_permissions"]),
                [
                    {"role": "tenant_admin", "permission": "tenant:manage"},
                    {"role": "tenant_admin", "permission": "task:use"},
                    {"role": "member", "permission": "task:use"},
                ],
            )
        self.tenant_a = await _tenant(self.engine, self.repository, "org-a")
        self.tenant_b = await _tenant(self.engine, self.repository, "org-b")

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_a_admin_lists_only_a_members(self) -> None:
        await self.repository.resolve(
            IdentityPrincipal(
                external_org_id="org-a",
                external_user_id="admin-a",
                organization_roles=frozenset({"org-a:admin"}),
            ),
        )
        await self.repository.add_member(
            self.tenant_a,
            external_user_id="member-a",
        )
        await self.repository.add_member(
            self.tenant_b,
            external_user_id="member-b",
        )

        members = await self.repository.list_members(self.tenant_a)

        self.assertEqual(
            {row["external_user_id"] for row in members},
            {"admin-a", "member-a"},
        )

    async def test_a_admin_cannot_address_a_b_membership_id(self) -> None:
        identity_b = await self.repository.resolve(
            IdentityPrincipal(
                external_org_id="org-b",
                external_user_id="member-b",
            ),
        )

        self.assertIsNone(
            await self.repository.get_member(self.tenant_a, identity_b.membership_id),
        )
        self.assertIsNone(
            await self.repository.update_member(
                self.tenant_a,
                identity_b.membership_id,
                status="disabled",
            ),
        )

    async def test_same_logto_user_has_independent_org_roles_and_status(self) -> None:
        identity_a = await self.repository.resolve(
            IdentityPrincipal(
                external_org_id="org-a",
                external_user_id="shared-user",
                organization_roles=frozenset({"org-a:admin"}),
            ),
        )
        identity_b = await self.repository.resolve(
            IdentityPrincipal(
                external_org_id="org-b",
                external_user_id="shared-user",
            ),
        )

        self.assertEqual(identity_a.role, "tenant_admin")
        self.assertIn("tenant:manage", identity_a.scopes)
        self.assertEqual(identity_b.role, "member")
        self.assertNotIn("tenant:manage", identity_b.scopes)
        self.assertEqual(identity_a.user_id, identity_b.user_id)
        self.assertNotEqual(identity_a.membership_id, identity_b.membership_id)

        await self.repository.update_member(
            self.tenant_b,
            identity_b.membership_id,
            status="disabled",
        )
        with self.assertRaises(TenantIdentityStatusError) as error:
            await self.repository.resolve(
                IdentityPrincipal(
                    external_org_id="org-b",
                    external_user_id="shared-user",
                ),
            )
        self.assertEqual(error.exception.subject, "membership")
