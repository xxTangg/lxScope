# -*- coding: utf-8 -*-
"""Tests for the application-owned project observability layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from examples.agent_service.observability_analytics import ObservabilityEventStore
from examples.agent_service.project_observability import (
    ObservabilityMetrics,
    ObservabilitySettings,
    ProjectObservability,
)


class ProjectObservabilityTest(TestCase):
    """Keep project observability independent from AgentScope internals."""

    def test_metrics_render_with_stable_low_cardinality_labels(self) -> None:
        metrics = ObservabilityMetrics()
        metrics.increment(
            "lxscope_http_requests_total",
            method="GET",
            route="/health",
            status_class="2xx",
            result="success",
        )
        metrics.observe_duration(
            "lxscope_http_request_duration_seconds",
            0.25,
            method="GET",
            route="/health",
        )

        rendered = metrics.render_prometheus()

        self.assertIn(
            'lxscope_http_requests_total{method="GET",result="success",'
            'route="/health",status_class="2xx"} 1',
            rendered,
        )
        self.assertIn(
            'lxscope_http_request_duration_seconds_count{method="GET",'
            'route="/health"} 1',
            rendered,
        )
        self.assertIn(
            'lxscope_http_request_duration_seconds_sum{method="GET",'
            'route="/health"} 0.250000000',
            rendered,
        )

    def test_event_logging_does_not_emit_payload_fields(self) -> None:
        observability = ProjectObservability(
            ObservabilitySettings(enabled=True),
        )

        with patch(
            "examples.agent_service.project_observability.logger.info",
        ) as log_info:
            observability.record_event(
                "tool.call.completed",
                component="tool",
                result="success",
                tool="Read",
                input="secret-input",
                output="secret-output",
            )

        logged = str(log_info.call_args)
        self.assertIn("tool.call.completed", logged)
        self.assertIn('"tool": "Read"', logged)
        self.assertNotIn("secret-input", logged)
        self.assertNotIn("secret-output", logged)

    def test_disabled_observability_is_a_noop(self) -> None:
        observability = ProjectObservability(
            ObservabilitySettings(enabled=False),
        )

        observability.record_http(
            method="GET",
            route="/health",
            status_code=200,
            duration_seconds=0.01,
        )

        self.assertEqual(observability.metrics.render_prometheus(), "")

    def test_event_store_aggregates_project_dimensions(self) -> None:
        store = ObservabilityEventStore(max_events=10)
        now = datetime.now(timezone.utc)
        store.record(
            "http.request.completed",
            component="http",
            result="success",
            duration_seconds=0.25,
            user_id="user-1",
        )
        store.record(
            "http.request.completed",
            component="http",
            result="success",
            duration_seconds=0.01,
            occurred_at=now,
            route="/admin/observability/overview",
            user_id="user-1",
        )
        store.record(
            "model.call.completed",
            component="model",
            result="success",
            duration_seconds=1.5,
            occurred_at=now,
            model="demo-model",
            user_id="user-1",
            input_tokens=10,
            output_tokens=4,
        )
        store.record(
            "tool.call.completed",
            component="tool",
            result="failed",
            tool="search",
            error_code="TimeoutError",
        )

        summary = store.summarize(
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )

        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["active_user_count"], 1)
        self.assertEqual(summary["models"][0]["total_tokens"], 14)
        self.assertEqual(summary["models"][0]["user_ids"], ["user-1"])
        self.assertEqual(summary["models"][0]["last_occurred_at"], now.isoformat())
        self.assertEqual(summary["tools"][0]["failure_count"], 1)
        self.assertEqual(summary["failures"][0]["error_code"], "TimeoutError")

        model_detail = store.component_detail(
            "model",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
            name="demo-model",
            user_id="user-1",
        )
        self.assertEqual(model_detail["call_count"], 1)
        self.assertEqual(model_detail["p95_duration_seconds"], 1.5)
        self.assertEqual(model_detail["user_breakdown"][0]["call_count"], 1)

    def test_agent_rows_include_tool_calls_and_model_tokens(self) -> None:
        store = ObservabilityEventStore(max_events=10)
        now = datetime.now(timezone.utc)
        store.record(
            "agent.reply.completed",
            component="agent",
            result="success",
            duration_seconds=0.8,
            occurred_at=now,
            agent_name="research_agent",
            request_id="req-1",
            trace_id="trace-1",
            user_id="user-1",
        )
        store.record(
            "model.call.completed",
            component="model",
            result="success",
            duration_seconds=0.4,
            occurred_at=now,
            agent_name="research_agent",
            request_id="req-1",
            trace_id="trace-1",
            model="demo-model",
            input_tokens=100,
            output_tokens=20,
            user_id="user-1",
        )
        store.record(
            "tool.call.completed",
            component="tool",
            result="success",
            duration_seconds=0.2,
            occurred_at=now,
            agent_name="research_agent",
            request_id="req-1",
            trace_id="trace-1",
            tool="search",
            user_id="user-1",
        )

        component_detail = store.component_detail(
            "agent",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )
        row = component_detail["items"][0]
        detail = store.agent_detail(
            "research_agent",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )

        self.assertEqual(row["name"], "research_agent")
        self.assertEqual(row["call_count"], 1)
        self.assertEqual(row["total_tokens"], 120)
        self.assertEqual(row["tool_call_count"], 1)
        self.assertEqual(detail["executions"][0]["trace_id"], "trace-1")
        self.assertEqual(detail["executions"][0]["tool_call_count"], 1)

        trace = store.trace_detail(
            "trace-1",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )
        self.assertEqual(len(trace["events"]), 3)
        self.assertEqual(trace["request_id"], "req-1")

    def test_tool_detail_exposes_callers_timeouts_and_mcp_server_rows(self) -> None:
        store = ObservabilityEventStore(max_events=10)
        now = datetime.now(timezone.utc)
        for result, error_code, user_id in (
            ("success", None, "user-1"),
            ("failed", "TimeoutError", "user-2"),
        ):
            store.record(
                "tool.call.completed",
                component="tool",
                result=result,
                error_code=error_code,
                duration_seconds=1.0,
                occurred_at=now,
                tool="web_search",
                tool_kind="MCP",
                mcp_server="search-server",
                agent_name="research_agent",
                user_id=user_id,
            )

        detail = store.component_detail(
            "tool",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
            name="web_search",
        )

        self.assertEqual(detail["timeout_count"], 1)
        self.assertEqual(detail["agent_breakdown"][0]["call_count"], 2)
        self.assertEqual(detail["executions"][0]["tool_kind"], "MCP")
        self.assertEqual(detail["mcp_servers"][0]["server_name"], "search-server")
        self.assertEqual(detail["mcp_servers"][0]["status"], "unknown")

    def test_failure_center_classifies_failures_and_keeps_trace_links(self) -> None:
        store = ObservabilityEventStore(max_events=10)
        now = datetime.now(timezone.utc)
        store.record(
            "model.call.completed",
            component="model",
            result="failed",
            occurred_at=now,
            model="demo-model",
            error_code="RateLimitError",
            trace_id="trace-model",
        )
        store.record(
            "tool.call.completed",
            component="tool",
            result="failed",
            occurred_at=now + timedelta(seconds=1),
            tool="database",
            error_code="TimeoutError",
            trace_id="trace-tool",
        )

        detail = store.failure_center(
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )

        self.assertEqual(detail["failure_count"], 2)
        self.assertEqual(detail["failure_by_type"][0]["key"], "rate_limit")
        self.assertEqual(detail["failures"][0]["trace_id"], "trace-tool")
