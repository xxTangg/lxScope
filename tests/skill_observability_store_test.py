# -*- coding: utf-8 -*-
"""Tests for the decoupled application-level Skill observation sink."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import ToolCallBlock, ToolResultState
from agentscope.tool import ToolResponse
from sqlalchemy.ext.asyncio import create_async_engine

from examples.agent_service.skill_observability import (
    SkillReconcileSummary,
    SkillUsageMiddleware,
)
from examples.agent_service.skill_observability_store import (
    BestEffortSkillObservationSink,
    PostgresSkillObservationStore,
    SkillObservationEvent,
)


class RecordingSink:
    """Small in-memory sink used to assert the application contract."""

    def __init__(self) -> None:
        self.events: list[SkillObservationEvent] = []

    async def record(self, event: SkillObservationEvent) -> None:
        self.events.append(event)


class FailingSink:
    async def record(self, event: SkillObservationEvent) -> None:
        del event
        raise RuntimeError("database unavailable")


class SkillObservationStoreTest(IsolatedAsyncioTestCase):
    async def test_exposed_and_completed_events_are_bounded(self) -> None:
        sink = RecordingSink()
        summary = SkillReconcileSummary(
            "user-1",
            "agent-1",
            "session-1",
            skill_names=("skill-a",),
            observation_sink=sink,
        )
        middleware = SkillUsageMiddleware(summary)

        await middleware.on_system_prompt(None, "<name>skill-a</name>")

        async def next_handler(**kwargs: Any) -> AsyncGenerator:
            del kwargs
            yield ToolResponse(id="call-1")

        tool_call = ToolCallBlock(
            id="call-1",
            name="Skill",
            input=json.dumps({"skill": "skill-a"}),
        )
        results = [
            item
            async for item in middleware.on_acting(
                None,
                {"tool_call": tool_call},
                next_handler,
            )
        ]

        self.assertIsInstance(results[-1], ToolResponse)
        self.assertEqual(
            [event.event_name for event in sink.events],
            ["skill.exposed", "skill.invoked", "skill.completed"],
        )
        # This unit test intentionally has no real Agent/Toolkit.  The
        # integration test covers the real listed count and tool availability.
        self.assertEqual(sink.events[0].listed_skill_count, 0)
        self.assertEqual(sink.events[1].skill_name, "skill-a")
        self.assertNotIn("prompt", sink.events[0].to_row())
        self.assertNotIn("markdown", sink.events[0].to_row())

    async def test_best_effort_sink_does_not_raise_on_database_failure(self) -> None:
        sink = BestEffortSkillObservationSink(FailingSink())
        await sink.record(
            SkillObservationEvent(
                event_name="skill.completed",
                result="success",
                user_id="u",
                agent_id="a",
            ),
        )

    async def test_skill_execution_failure_contains_safe_reason_and_duration(self) -> None:
        sink = RecordingSink()
        summary = SkillReconcileSummary(
            "user-1",
            "agent-1",
            "session-1",
            skill_names=("skill-a",),
            observation_sink=sink,
        )
        middleware = SkillUsageMiddleware(summary)

        async def failed_handler(**kwargs: Any) -> AsyncGenerator:
            del kwargs
            yield ToolResponse(id="call-failed", state=ToolResultState.ERROR)

        tool_call = ToolCallBlock(
            id="call-failed",
            name="Skill",
            input=json.dumps({"skill": "skill-a"}),
        )
        results = [
            item
            async for item in middleware.on_acting(
                None,
                {"tool_call": tool_call},
                failed_handler,
            )
        ]

        self.assertEqual(results[-1].state, ToolResultState.ERROR)
        failure = sink.events[-1]
        self.assertEqual(failure.event_name, "skill.completed")
        self.assertEqual(failure.result, ToolResultState.ERROR.value)
        self.assertEqual(failure.error_code, "skill_viewer_error")
        self.assertIsNotNone(failure.duration_seconds)

    async def test_database_repository_aggregates_dashboard_metrics(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        store = PostgresSkillObservationStore("", engine=engine)
        await store.initialize()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [
            SkillObservationEvent(
                event_name="skill.reconcile.completed",
                result="success",
                user_id="u",
                agent_id="a",
                occurred_at=start + timedelta(minutes=1),
                duration_seconds=1.5,
                visible_count=2,
                after_count=2,
            ),
            SkillObservationEvent(
                event_name="skill.exposed",
                result="success",
                user_id="u",
                agent_id="a",
                occurred_at=start + timedelta(minutes=2),
                skill_count=2,
            ),
            SkillObservationEvent(
                event_name="skill.invoked",
                result="started",
                user_id="u",
                agent_id="a",
                skill_name="skill-a",
                occurred_at=start + timedelta(minutes=3),
            ),
            SkillObservationEvent(
                event_name="skill.completed",
                result="success",
                user_id="u",
                agent_id="a",
                skill_name="skill-a",
                occurred_at=start + timedelta(minutes=4),
            ),
        ]
        for event in events:
            await store.record(event)

        result = await store.query_skill_analytics(
            start=start,
            end=start + timedelta(days=1),
        )

        self.assertEqual(result["event_count"], 4)
        self.assertEqual(result["reconcile"]["success"], 1)
        self.assertEqual(result["lifecycle"], {"exposed": 1, "invoked": 1, "completed": 1})
        self.assertEqual(result["completed"]["success"], 1)
        self.assertEqual(result["actual_usage_rate"], 1.0)
        self.assertEqual(result["average_reconcile_duration_seconds"], 1.5)
        self.assertEqual(result["latest_snapshot"], {"visible_count": 2, "after_count": 2})
        self.assertEqual(result["top_skills"], [{"skill_name": "skill-a", "invoked_count": 1}])
        self.assertEqual(result["daily"][0]["event_count"], 4)
        await engine.dispose()

    async def test_database_repository_reports_user_failure_stage_and_reason(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        store = PostgresSkillObservationStore("", engine=engine)
        await store.initialize()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await store.record(
            SkillObservationEvent(
                event_name="skill.invoked",
                result="invalid_input",
                user_id="user-failed",
                agent_id="agent-1",
                session_id="session-1",
                skill_name="skill-a",
                error_code="invalid_skill_input",
                occurred_at=start + timedelta(minutes=1),
            ),
        )
        await store.record(
            SkillObservationEvent(
                event_name="skill.completed",
                result="error",
                user_id="user-failed",
                agent_id="agent-1",
                session_id="session-1",
                skill_name="skill-a",
                error_code="skill_viewer_error",
                duration_seconds=2.0,
                occurred_at=start + timedelta(minutes=2),
            ),
        )

        result = await store.query_skill_analytics(
            start=start,
            end=start + timedelta(days=1),
        )

        self.assertEqual(result["failure_count"], 2)
        self.assertEqual(result["execution_failure_count"], 1)
        self.assertEqual(result["execution_failure_rate"], 1.0)
        self.assertEqual(
            result["failure_by_stage"],
            [
                {"key": "execute", "count": 1},
                {"key": "invoke", "count": 1},
            ],
        )
        self.assertEqual(result["failure_by_error"][0]["key"], "invalid_skill_input")
        self.assertEqual(result["recent_failures"][0]["user_id"], "user-failed")
        self.assertEqual(result["recent_failures"][0]["stage"], "execute")
        await engine.dispose()
