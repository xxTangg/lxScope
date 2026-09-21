# -*- coding: utf-8 -*-
"""Repository for binding trusted external principals to lxScope identities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .models import (
    IdentityPrincipal,
    TenantIdentity,
    TenantNotProvisionedError,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TenantBindingRepository:
    """Resolve external organization/user IDs through the app database.

    ``database`` is the existing :class:`ApplicationDatabase` (or its
    initialized SQLAlchemy engine).  The repository owns no engine lifecycle
    and never creates a tenant when an external organization is unknown.
    """

    def __init__(
        self,
        database: Any,
        *,
        identity_provider: str = "logto",
    ) -> None:
        provider = identity_provider.strip().lower()
        if not provider:
            raise ValueError("identity_provider must be a non-empty string")
        self._database = database
        # AsyncEngine exposes ``engine`` as a synchronous proxy, so detect it
        # before unwrapping ApplicationDatabase.engine.
        self._engine = (
            database
            if hasattr(database, "sync_engine")
            else getattr(database, "engine", database)
        )
        self._identity_provider = provider
        self._tables: dict[str, Any] | None = None

    def _table_definitions(self) -> dict[str, Any]:
        if self._tables is not None:
            return self._tables

        from sqlalchemy import (
            Column,
            DateTime,
            Index,
            MetaData,
            String,
            Table,
            Uuid,
            UniqueConstraint,
            text,
        )

        schema = (
            "longxin_app"
            if self._engine.dialect.name == "postgresql"
            else None
        )
        metadata = MetaData(schema=schema)
        uuid_type = Uuid(as_uuid=True)
        self._tables = {
            "tenants": Table(
                "tenants",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("code", String(64), nullable=False),
                Column("name", String(128), nullable=False),
                Column(
                    "status",
                    String(32),
                    nullable=False,
                    server_default=text("'active'"),
                ),
                Column(
                    "identity_provider",
                    String(32),
                    nullable=False,
                    server_default=text("'local'"),
                ),
                Column("external_org_id", String(255)),
                Column(
                    "created_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "updated_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Index(
                    "uq_tenants_identity_provider_external_org",
                    "identity_provider",
                    "external_org_id",
                    unique=True,
                    postgresql_where=text("external_org_id IS NOT NULL"),
                    sqlite_where=text("external_org_id IS NOT NULL"),
                ),
            ),
            "users": Table(
                "users",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("username", String(128), nullable=False),
                Column("external_user_id", String(255)),
                Column(
                    "email",
                    String(255),
                ),
                Column("password_hash", String),
                Column(
                    "status",
                    String(32),
                    nullable=False,
                    server_default=text("'active'"),
                ),
                Column(
                    "system_role",
                    String(32),
                    nullable=False,
                    server_default=text("'user'"),
                ),
                Column(
                    "created_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "updated_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                UniqueConstraint(
                    "external_user_id",
                    name="uq_users_external_user_id",
                ),
            ),
            "memberships": Table(
                "tenant_memberships",
                metadata,
                Column("id", uuid_type, primary_key=True),
                Column("tenant_id", uuid_type, nullable=False),
                Column("user_id", uuid_type, nullable=False),
                Column(
                    "role",
                    String(32),
                    nullable=False,
                    server_default=text("'member'"),
                ),
                Column(
                    "status",
                    String(32),
                    nullable=False,
                    server_default=text("'active'"),
                ),
                Column("display_name", String(128)),
                Column(
                    "joined_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "created_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                Column(
                    "updated_at",
                    DateTime(timezone=True),
                    server_default=text("CURRENT_TIMESTAMP"),
                ),
                UniqueConstraint(
                    "tenant_id",
                    "user_id",
                    name="uq_tenant_memberships_tenant_user",
                ),
            ),
        }
        return self._tables

    @staticmethod
    def _insert_ignore_conflicts(
        table: Any,
        values: dict[str, Any],
        connection: Any,
    ) -> Any:
        """Build a dialect-aware idempotent insert using bound values."""

        dialect_name = connection.dialect.name
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy import insert

        statement = insert(table).values(**values)
        if hasattr(statement, "on_conflict_do_nothing"):
            statement = statement.on_conflict_do_nothing()
        return statement

    @staticmethod
    def _principal(
        principal: IdentityPrincipal | Mapping[str, Any],
        *,
        default_provider: str,
    ) -> IdentityPrincipal:
        if isinstance(principal, IdentityPrincipal):
            return principal
        if isinstance(principal, Mapping):
            return IdentityPrincipal.from_mapping(
                principal,
                identity_provider=default_provider,
            )
        raise TypeError("principal must be an IdentityPrincipal or mapping")

    async def resolve(
        self,
        principal: IdentityPrincipal | Mapping[str, Any],
    ) -> TenantIdentity:
        """Resolve a principal to internal tenant, user, and membership IDs."""

        normalized = self._principal(
            principal,
            default_provider=self._identity_provider,
        )
        tables = self._table_definitions()
        tenants = tables["tenants"]
        users = tables["users"]
        memberships = tables["memberships"]

        from sqlalchemy import select

        async with self._engine.begin() as connection:
            tenant_result = await connection.execute(
                select(
                    tenants.c.id,
                    tenants.c.identity_provider,
                    tenants.c.external_org_id,
                    tenants.c.status,
                ).where(
                    tenants.c.identity_provider
                    == normalized.identity_provider,
                    tenants.c.external_org_id == normalized.external_org_id,
                ),
            )
            tenant_row = tenant_result.mappings().first()
            if tenant_row is None:
                raise TenantNotProvisionedError(
                    identity_provider=normalized.identity_provider,
                    external_org_id=normalized.external_org_id,
                )

            user_id = uuid5(
                NAMESPACE_URL,
                "lxscope:shadow-user:"
                f"{normalized.identity_provider}:"
                f"{normalized.external_user_id}",
            )
            username = f"{normalized.identity_provider}-{user_id.hex}"
            user_values = {
                "id": user_id,
                "username": username,
                "external_user_id": normalized.external_user_id,
                "email": normalized.email,
                "system_role": "user",
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            await connection.execute(
                self._insert_ignore_conflicts(users, user_values, connection),
            )
            user_result = await connection.execute(
                select(
                    users.c.id,
                    users.c.status,
                    users.c.email,
                ).where(
                    users.c.external_user_id == normalized.external_user_id,
                ),
            )
            user_row = user_result.mappings().first()
            if user_row is None:
                raise RuntimeError(
                    "Shadow user insert did not produce a user row",
                )

            actual_user_id = user_row["id"]
            tenant_id = tenant_row["id"]
            membership_id = uuid5(
                NAMESPACE_URL,
                f"lxscope:membership:{tenant_id}:{actual_user_id}",
            )
            display_name = (
                normalized.display_name
                or normalized.username
                or normalized.email
                or normalized.external_user_id
            )
            membership_values = {
                "id": membership_id,
                "tenant_id": tenant_id,
                "user_id": actual_user_id,
                "role": "member",
                "status": "active",
                "display_name": display_name,
                "joined_at": _utc_now(),
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            await connection.execute(
                self._insert_ignore_conflicts(
                    memberships,
                    membership_values,
                    connection,
                ),
            )
            membership_result = await connection.execute(
                select(
                    memberships.c.id,
                    memberships.c.role,
                    memberships.c.status,
                    memberships.c.display_name,
                ).where(
                    memberships.c.tenant_id == tenant_id,
                    memberships.c.user_id == actual_user_id,
                ),
            )
            membership_row = membership_result.mappings().first()
            if membership_row is None:
                raise RuntimeError(
                    "Membership insert did not produce a membership row",
                )

            return TenantIdentity(
                tenant_id=tenant_id,
                user_id=actual_user_id,
                membership_id=membership_row["id"],
                identity_provider=tenant_row["identity_provider"],
                external_org_id=tenant_row["external_org_id"],
                external_user_id=normalized.external_user_id,
                role=membership_row["role"],
                status=membership_row["status"],
                display_name=membership_row["display_name"],
            )


__all__ = ["TenantBindingRepository"]
