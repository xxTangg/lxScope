# -*- coding: utf-8 -*-
"""Application-layer tenant isolation regression tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from examples.agent_service.observability_analytics import ObservabilityEventStore
from examples.agent_service.skill_observability_store import (
    PostgresSkillObservationStore,
    SkillObservationEvent,
)
from examples.agent_service.task._models import (
    CreateTaskRequest,
    UpdateTaskRequest,
)
from examples.agent_service.task._postgres_store import PostgresTaskStore
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


class TenantOwnedTaskTest(IsolatedAsyncioTestCase):
    """A resource created under one membership is invisible to another."""

    async def test_other_membership_cannot_read_update_or_delete_task(self) -> None:
        service = TaskService(TaskStore())
        tenant_a_membership = "membership-tenant-a"
        tenant_b_membership = "membership-tenant-b"

        task = await service.create_task(
            tenant_a_membership,
            CreateTaskRequest(title="Tenant A task", goal="private"),
        )

        self.assertIsNone(
            await service.get_task(tenant_b_membership, task.id),
        )
        self.assertIsNone(
            await service.update_task(
                tenant_b_membership,
                task.id,
                UpdateTaskRequest(title="cross-tenant overwrite"),
            ),
        )
        self.assertFalse(
            await service.delete_task(tenant_b_membership, task.id),
        )

        owner_view = await service.get_task(tenant_a_membership, task.id)
        self.assertIsNotNone(owner_view)
        assert owner_view is not None
        self.assertEqual(owner_view.title, "Tenant A task")

    def test_postgres_store_requires_bound_tenant_in_strict_mode(self) -> None:
        """Logto-mode SQL access fails closed without request identity."""
        store = PostgresTaskStore(
            object(),
            provision_default_tenant=False,
            require_tenant_context=True,
        )
        with self.assertRaisesRegex(ValueError, "bound tenant identity"):
            store._effective_tenant_id()  # pylint: disable=protected-access


class TenantScopedObservabilityTest(IsolatedAsyncioTestCase):
    """Observability projections must filter by the trusted tenant key."""

    def test_other_tenant_is_excluded_from_project_projection(self) -> None:
        store = ObservabilityEventStore(max_events=10)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store.record(
            "http.request.completed",
            component="http",
            result="success",
            occurred_at=start + timedelta(minutes=1),
            tenant_id="tenant-a",
            user_id="membership-a",
        )
        store.record(
            "http.request.completed",
            component="http",
            result="success",
            occurred_at=start + timedelta(minutes=2),
            tenant_id="tenant-b",
            user_id="membership-b",
        )

        tenant_a = store.summarize(
            start=start,
            end=start + timedelta(days=1),
            tenant_id="tenant-a",
        )
        tenant_b = store.summarize(
            start=start,
            end=start + timedelta(days=1),
            tenant_id="tenant-b",
        )

        self.assertEqual(tenant_a["event_count"], 1)
        self.assertEqual(tenant_a["active_user_count"], 1)
        self.assertEqual(tenant_b["event_count"], 1)
        self.assertEqual(tenant_b["active_user_count"], 1)

    async def test_sql_skill_projection_is_tenant_scoped(self) -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        store = PostgresSkillObservationStore("", engine=engine)
        await store.initialize()
        tenant_a = uuid4()
        tenant_b = uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for tenant_id in (tenant_a, tenant_b):
            await store.record(
                SkillObservationEvent(
                    event_name="skill.exposed",
                    result="success",
                    user_id="membership",
                    agent_id="agent",
                    tenant_id=tenant_id,  # type: ignore[arg-type]
                    occurred_at=start,
                ),
            )

        tenant_a_result = await store.query_skill_analytics(
            start=start,
            end=start + timedelta(days=1),
            tenant_id=str(tenant_a),
        )
        tenant_b_result = await store.query_skill_analytics(
            start=start,
            end=start + timedelta(days=1),
            tenant_id=str(tenant_b),
        )
        self.assertEqual(tenant_a_result["event_count"], 1)
        self.assertEqual(tenant_b_result["event_count"], 1)
        await engine.dispose()


if __name__ == "__main__":
    import unittest

    unittest.main()
