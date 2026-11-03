# -*- coding: utf-8 -*-
"""Tests for durable project-level observability storage."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import create_async_engine

from examples.agent_service.observability_analytics import ObservabilityEvent
from examples.agent_service.project_observability_store import (
    PostgresProjectObservabilityStore,
)


class ProjectObservabilityStoreTest(IsolatedAsyncioTestCase):
    async def test_round_trip_keeps_component_dimensions(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        store = PostgresProjectObservabilityStore("", engine=engine)
        await store.initialize()

        await store.record(
            ObservabilityEvent(
                occurred_at=datetime.now(timezone.utc),
                event_name="model.call.completed",
                component="model",
                result="success",
                duration_seconds=1.25,
                request_id="req-1",
                trace_id="trace-1",
                user_id="user-1",
                agent_name="research_agent",
                model="demo-model",
                input_tokens=100,
                output_tokens=20,
            ),
        )

        events = await store.load_recent(limit=10)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].component, "model")
        self.assertEqual(events[0].model, "demo-model")
        self.assertEqual(events[0].total_tokens, 120)

        await store.close()
        await engine.dispose()
