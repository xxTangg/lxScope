# -*- coding: utf-8 -*-
"""Unit tests for the Logto-to-lxScope tenant binding repository."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from examples.agent_service.identity.models import (
    IdentityPrincipal,
    TenantIdentityStatusError,
    TenantNotProvisionedError,
)
from examples.agent_service.identity.tenant_binding import (
    TenantBindingRepository,
)


async def _provision_tenant(
    engine: AsyncEngine,
    repository: TenantBindingRepository,
    *,
    external_org_id: str,
    status: str = "active",
) -> None:
    tenants = repository._table_definitions()["tenants"]
    async with engine.begin() as connection:
        await connection.execute(
            insert(tenants).values(
                id=uuid4(),
                code=f"tenant-{external_org_id}",
                name=f"Tenant {external_org_id}",
                status=status,
                identity_provider="logto",
                external_org_id=external_org_id,
            ),
        )


class TenantBindingTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.repository = TenantBindingRepository(self.engine)
        tables = self.repository._table_definitions()
        metadata = next(iter(tables.values())).metadata
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_resolve_is_idempotent_for_same_org_and_user(self) -> None:
        await _provision_tenant(
            self.engine,
            self.repository,
            external_org_id="org-acme",
        )
        principal = IdentityPrincipal(
            external_org_id="org-acme",
            external_user_id="user-42",
            display_name="Ada",
            email="ada@example.test",
        )

        first = await self.repository.resolve(principal)
        second = await self.repository.resolve(principal)

        self.assertEqual(second, first)
        tables = self.repository._table_definitions()
        async with self.engine.connect() as connection:
            user_count = await connection.scalar(
                select(func.count()).select_from(tables["users"]),
            )
            membership_count = await connection.scalar(
                select(func.count()).select_from(tables["memberships"]),
            )
        self.assertEqual(user_count, 1)
        self.assertEqual(membership_count, 1)

    async def test_same_user_in_different_orgs_gets_different_memberships(
        self,
    ) -> None:
        await _provision_tenant(
            self.engine,
            self.repository,
            external_org_id="org-acme",
        )
        await _provision_tenant(
            self.engine,
            self.repository,
            external_org_id="org-beta",
        )

        first = await self.repository.resolve(
            IdentityPrincipal(
                external_org_id="org-acme",
                external_user_id="user-42",
            ),
        )
        second = await self.repository.resolve(
            IdentityPrincipal(
                external_org_id="org-beta",
                external_user_id="user-42",
            ),
        )

        self.assertEqual(second.user_id, first.user_id)
        self.assertNotEqual(second.tenant_id, first.tenant_id)
        self.assertNotEqual(second.membership_id, first.membership_id)

        tables = self.repository._table_definitions()
        async with self.engine.connect() as connection:
            user_count = await connection.scalar(
                select(func.count()).select_from(tables["users"]),
            )
            membership_count = await connection.scalar(
                select(func.count()).select_from(tables["memberships"]),
            )
        self.assertEqual(user_count, 1)
        self.assertEqual(membership_count, 2)

    async def test_unprovisioned_org_is_rejected_without_shadow_records(
        self,
    ) -> None:
        with self.assertRaises(TenantNotProvisionedError) as error:
            await self.repository.resolve(
                IdentityPrincipal(
                    external_org_id="org-missing",
                    external_user_id="user-42",
                ),
            )

        self.assertEqual(error.exception.code, "tenant_not_provisioned")
        self.assertEqual(error.exception.error_code, "tenant_not_provisioned")
        tables = self.repository._table_definitions()
        async with self.engine.connect() as connection:
            user_count = await connection.scalar(
                select(func.count()).select_from(tables["users"]),
            )
            membership_count = await connection.scalar(
                select(func.count()).select_from(tables["memberships"]),
            )
        self.assertEqual(user_count, 0)
        self.assertEqual(membership_count, 0)

    async def test_inactive_tenant_is_rejected_before_shadow_user_creation(self) -> None:
        await _provision_tenant(
            self.engine,
            self.repository,
            external_org_id="org-suspended",
            status="suspended",
        )

        with self.assertRaises(TenantIdentityStatusError) as error:
            await self.repository.resolve(
                IdentityPrincipal(
                    external_org_id="org-suspended",
                    external_user_id="user-42",
                ),
            )

        self.assertEqual(error.exception.subject, "tenant")
        tables = self.repository._table_definitions()
        async with self.engine.connect() as connection:
            user_count = await connection.scalar(
                select(func.count()).select_from(tables["users"]),
            )
        self.assertEqual(user_count, 0)

    async def test_inactive_user_and_membership_are_rejected(self) -> None:
        await _provision_tenant(
            self.engine,
            self.repository,
            external_org_id="org-status",
        )
        principal = IdentityPrincipal(
            external_org_id="org-status",
            external_user_id="user-42",
        )
        identity = await self.repository.resolve(principal)
        tables = self.repository._table_definitions()

        async with self.engine.begin() as connection:
            await connection.execute(
                update(tables["users"])
                .where(tables["users"].c.id == identity.user_id)
                .values(status="locked"),
            )
        with self.assertRaises(TenantIdentityStatusError) as user_error:
            await self.repository.resolve(principal)
        self.assertEqual(user_error.exception.subject, "user")

        async with self.engine.begin() as connection:
            await connection.execute(
                update(tables["users"])
                .where(tables["users"].c.id == identity.user_id)
                .values(status="active"),
            )
            await connection.execute(
                update(tables["memberships"])
                .where(tables["memberships"].c.id == identity.membership_id)
                .values(status="removed"),
            )
        with self.assertRaises(TenantIdentityStatusError) as membership_error:
            await self.repository.resolve(principal)
        self.assertEqual(membership_error.exception.subject, "membership")
