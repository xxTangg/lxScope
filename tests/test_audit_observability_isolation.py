"""Phase 5 audit and observability tenant isolation tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from examples.agent_service.audit_store import visible_audit_events
from examples.agent_service.observability_analytics import ObservabilityEventStore


class AuditObservabilityIsolationTest(unittest.TestCase):
    def test_tenant_a_cannot_view_tenant_b_audit_events(self) -> None:
        events = [
            {"event_id": "a-1", "tenant_id": "tenant-a", "action": "skill.delete"},
            {"event_id": "b-1", "tenant_id": "tenant-b", "action": "skill.delete"},
        ]

        visible = visible_audit_events(events, tenant_id="tenant-a")

        self.assertEqual([event["event_id"] for event in visible], ["a-1"])

    def test_platform_admin_can_view_global_audit_events(self) -> None:
        events = [
            {"event_id": "a-1", "tenant_id": "tenant-a"},
            {"event_id": "b-1", "tenant_id": "tenant-b"},
        ]

        visible = visible_audit_events(
            events,
            tenant_id="tenant-a",
            platform_admin=True,
        )

        self.assertEqual({event["event_id"] for event in visible}, {"a-1", "b-1"})

    def test_observability_query_is_tenant_scoped(self) -> None:
        store = ObservabilityEventStore()
        now = datetime.now(timezone.utc)
        store.record(
            "model.call.completed",
            component="model",
            result="success",
            occurred_at=now,
            tenant_id="tenant-a",
            model="model-a",
        )
        store.record(
            "model.call.completed",
            component="model",
            result="success",
            occurred_at=now + timedelta(seconds=1),
            tenant_id="tenant-b",
            model="model-b",
        )

        events = store.query(
            start=now - timedelta(seconds=1),
            end=now + timedelta(seconds=2),
            tenant_id="tenant-a",
        )

        self.assertEqual([event.model for event in events], ["model-a"])


if __name__ == "__main__":
    unittest.main()
