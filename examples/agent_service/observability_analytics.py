# -*- coding: utf-8 -*-
"""Application-owned event storage and aggregation for project observability.

This module deliberately has no AgentScope dependency.  The runtime adapter
records a small, payload-free event shape here, while the admin API consumes
the aggregate projection.  Keeping those two concerns separate lets the
service change its observability backend without changing the AgentScope
runtime or the administrator UI contract.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil
from threading import RLock
from typing import Any


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


@dataclass(frozen=True)
class ObservabilityEvent:
    """Bounded event fields that are safe for operational aggregation."""

    occurred_at: datetime
    event_name: str
    component: str
    result: str
    duration_seconds: float | None = None
    request_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    membership_id: str | None = None
    session_id: str | None = None
    agent_name: str | None = None
    model: str | None = None
    tool: str | None = None
    tool_kind: str | None = None
    mcp_server: str | None = None
    route: str | None = None
    method: str | None = None
    status_code: int | None = None
    error_code: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ObservabilityEventStore:
    """Bounded, process-local event store used by the admin projection.

    The store is intentionally an adapter, not the system of record.  It
    keeps enough recent data for an administrator to understand the current
    process and can later be replaced by a database or telemetry backend
    without changing the event contract.
    """

    def __init__(self, *, max_events: int = 50_000) -> None:
        self.max_events = max(1, max_events)
        self._events: list[ObservabilityEvent] = []
        self._lock = RLock()

    def record(
        self,
        event_name: str,
        *,
        component: str,
        result: str,
        duration_seconds: float | None = None,
        occurred_at: datetime | None = None,
        **attributes: Any,
    ) -> ObservabilityEvent:
        event = ObservabilityEvent(
            occurred_at=_utc(occurred_at or datetime.now(timezone.utc)),
            event_name=event_name,
            component=component,
            result=result,
            duration_seconds=(
                max(0.0, float(duration_seconds))
                if duration_seconds is not None
                else None
            ),
            request_id=_text(attributes.get("request_id")),
            trace_id=_text(attributes.get("trace_id")),
            user_id=_text(attributes.get("user_id")),
            tenant_id=_text(attributes.get("tenant_id")),
            membership_id=_text(attributes.get("membership_id")),
            session_id=_text(attributes.get("session_id")),
            agent_name=_text(attributes.get("agent_name")),
            model=_text(attributes.get("model")),
            tool=_text(attributes.get("tool")),
            tool_kind=_text(attributes.get("tool_kind")),
            mcp_server=_text(attributes.get("mcp_server")),
            route=_text(attributes.get("route")),
            method=_text(attributes.get("method")),
            status_code=(
                int(attributes["status_code"])
                if attributes.get("status_code") is not None
                else None
            ),
            error_code=_text(attributes.get("error_code")),
            input_tokens=_non_negative_int(attributes.get("input_tokens")),
            output_tokens=_non_negative_int(attributes.get("output_tokens")),
        )
        with self._lock:
            self._events.append(event)
            overflow = len(self._events) - self.max_events
            if overflow > 0:
                del self._events[:overflow]
        return event

    def replace(self, events: list[ObservabilityEvent]) -> None:
        """Restore a bounded event window from the durable event store."""
        with self._lock:
            self._events = list(events[-self.max_events :])

    def query(
        self,
        *,
        start: datetime,
        end: datetime,
        tenant_id: str | None = None,
    ) -> list[ObservabilityEvent]:
        normalized_start = _utc(start)
        normalized_end = _utc(end)
        with self._lock:
            return [
                event
                for event in self._events
                if normalized_start <= event.occurred_at < normalized_end
                and (tenant_id is None or event.tenant_id == tenant_id)
            ]

    def summarize(
        self,
        *,
        start: datetime,
        end: datetime,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        events = self.query(start=start, end=end, tenant_id=tenant_id)
        request_events = [
            event
            for event in events
            if self._is_business_request(event)
        ]
        successful_requests = sum(
            event.result == "success" for event in request_events
        )
        failed_requests = len(request_events) - successful_requests
        request_duration = [
            event.duration_seconds
            for event in request_events
            if event.duration_seconds is not None
        ]

        return {
            "event_count": len(events),
            "request_count": len(request_events),
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "success_rate": (
                successful_requests / len(request_events)
                if request_events
                else 0.0
            ),
            "average_response_time_seconds": (
                sum(request_duration) / len(request_duration)
                if request_duration
                else None
            ),
            "active_user_count": len(
                {event.user_id for event in events if event.user_id}
            ),
            "daily": self._daily(events, start=start, end=end),
            "models": self._component_rows(events, "model", "model"),
            "agents": self._component_rows(events, "agent", "agent_name"),
            "tools": self._component_rows(events, "tool", "tool"),
            "failures": self._failures(events),
            "failure_by_component": self._failure_breakdown(events),
            "failure_by_type": self._failure_type_breakdown(events),
        }

    def failure_center(
        self,
        *,
        start: datetime,
        end: datetime,
        component: str | None = None,
        error_type: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return the cross-component failure projection for administrators."""
        events = [
            event
            for event in self.query(
                start=start,
                end=end,
                tenant_id=tenant_id,
            )
            if event.result != "success"
            and (not component or event.component == component)
            and (not user_id or event.user_id == user_id)
            and (
                not error_type
                or self._failure_type(event) == error_type
            )
        ]
        return {
            "failure_count": len(events),
            "failure_by_component": self._failure_breakdown(events),
            "failure_by_type": self._failure_type_breakdown(events),
            "failures": self._failures(events, limit=limit),
        }

    def component_detail(
        self,
        component: str,
        *,
        start: datetime,
        end: datetime,
        name: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a drill-down projection for one runtime component."""
        key_name = {
            "model": "model",
            "agent": "agent_name",
            "tool": "tool",
        }.get(component)
        if key_name is None:
            raise ValueError(f"Unsupported observability component: {component}")

        all_events = self.query(
            start=start,
            end=end,
            tenant_id=tenant_id,
        )
        component_events = [
            event for event in all_events if event.component == component
        ]
        available_user_ids = sorted(
            {event.user_id for event in component_events if event.user_id},
        )
        events = component_events
        if name:
            events = [
                event
                for event in events
                if (_text(getattr(event, key_name)) or "unknown") == name
            ]
        if user_id:
            events = [event for event in events if event.user_id == user_id]
        rows = self._component_rows(
            events if component != "agent" else all_events,
            component,
            key_name,
        )
        token_events = (
            [
                event
                for event in all_events
                if event.component == "model"
                and event.agent_name
                and (not name or event.agent_name == name)
                and (not user_id or event.user_id == user_id)
            ]
            if component == "agent"
            else events
        )
        durations = [
            event.duration_seconds
            for event in events
            if event.duration_seconds is not None
        ]
        success_count = sum(event.result == "success" for event in events)
        return {
            "component": component,
            "call_count": len(events),
            "success_count": success_count,
            "failure_count": len(events) - success_count,
            "success_rate": success_count / len(events) if events else 0.0,
            "average_duration_seconds": (
                sum(durations) / len(durations) if durations else None
            ),
            "p95_duration_seconds": self._p95(durations),
            "timeout_count": sum(
                self._is_timeout(event) for event in events
            ),
            "input_tokens": sum(event.input_tokens for event in token_events),
            "output_tokens": sum(event.output_tokens for event in token_events),
            "total_tokens": sum(event.total_tokens for event in token_events),
            "daily": self._daily(events, start=start, end=end),
            "items": rows,
            "failures": self._failures(events),
            "available_user_ids": available_user_ids,
            "user_breakdown": self._user_breakdown(events),
            "agent_breakdown": (
                self._agent_breakdown(events)
                if component == "tool"
                else []
            ),
            "executions": (
                self._tool_execution_records(events)
                if component == "tool"
                else []
            ),
            "mcp_servers": (
                self._mcp_server_breakdown(events)
                if component == "tool"
                else []
            ),
        }

    @staticmethod
    def _is_timeout(event: ObservabilityEvent) -> bool:
        return bool(
            event.error_code
            and "timeout" in event.error_code.casefold(),
        )

    @staticmethod
    def _agent_breakdown(
        events: list[ObservabilityEvent],
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for event in events:
            if event.component == "tool":
                counts[_text(event.agent_name) or "unknown"] += 1
        total = sum(counts.values())
        return [
            {
                "agent_name": agent_name,
                "call_count": count,
                "percentage": count / total if total else 0.0,
            }
            for agent_name, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

    @staticmethod
    def _tool_execution_records(
        events: list[ObservabilityEvent],
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        tool_events = [event for event in events if event.component == "tool"]
        tool_events.sort(key=lambda event: event.occurred_at, reverse=True)
        return [
            {
                "occurred_at": event.occurred_at.isoformat(),
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "user_id": event.user_id,
                "agent_name": event.agent_name,
                "result": event.result,
                "duration_seconds": event.duration_seconds,
                "error_code": event.error_code,
                "tool_kind": event.tool_kind or "Tool",
                "mcp_server": event.mcp_server,
            }
            for event in tool_events[: max(1, min(limit, 200))]
        ]

    @staticmethod
    def _mcp_server_breakdown(
        events: list[ObservabilityEvent],
    ) -> list[dict[str, Any]]:
        groups: dict[str, list[ObservabilityEvent]] = defaultdict(list)
        for event in events:
            if event.component == "tool" and event.mcp_server:
                groups[event.mcp_server].append(event)

        rows: list[dict[str, Any]] = []
        for server_name, group in groups.items():
            durations = [
                event.duration_seconds
                for event in group
                if event.duration_seconds is not None
            ]
            rows.append(
                {
                    "server_name": server_name,
                    # Runtime health is not inferred from call outcomes. A
                    # health probe can replace this value later without
                    # changing the admin response shape.
                    "status": "unknown",
                    "tool_count": len(
                        {event.tool for event in group if event.tool}
                    ),
                    "call_count": len(group),
                    "average_duration_seconds": (
                        sum(durations) / len(durations)
                        if durations
                        else None
                    ),
                    "failure_count": sum(
                        event.result != "success" for event in group
                    ),
                },
            )
        rows.sort(key=lambda row: (-row["call_count"], row["server_name"]))
        return rows[:100]

    @staticmethod
    def _p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[max(0, ceil(len(ordered) * 0.95) - 1)]

    @staticmethod
    def _is_business_request(event: ObservabilityEvent) -> bool:
        """Keep control-plane and telemetry reads out of business volume."""
        if event.event_name != "http.request.completed":
            return False
        route = (event.route or "").rstrip("/") or "/"
        excluded_prefixes = (
            "/admin",
            "/observability",
            "/auth",
            "/health",
            "/docs",
            "/redoc",
        )
        if route == "/openapi.json" or route.startswith(excluded_prefixes):
            return False
        return True

    @staticmethod
    def _user_breakdown(
        events: list[ObservabilityEvent],
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for event in events:
            if event.user_id:
                counts[event.user_id] += 1
        total = sum(counts.values())
        return [
            {
                "user_id": user_id,
                "call_count": count,
                "percentage": count / total if total else 0.0,
            }
            for user_id, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

    def agent_detail(
        self,
        agent_name: str,
        *,
        start: datetime,
        end: datetime,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Return Agent executions together with related model and Tool work."""
        normalized_name = _text(agent_name) or "unknown"
        all_events = self.query(
            start=start,
            end=end,
            tenant_id=tenant_id,
        )
        agent_events = [
            event
            for event in all_events
            if event.component == "agent"
            and (_text(event.agent_name) or "unknown") == normalized_name
        ]
        related_events = [
            event
            for event in all_events
            if event.agent_name == normalized_name
            and event.component in {"model", "tool"}
        ]
        agent_events.sort(key=lambda event: event.occurred_at, reverse=True)
        executions = [
            self._agent_execution_record(event, related_events)
            for event in agent_events[: max(1, min(limit, 200))]
        ]
        durations = [
            event.duration_seconds
            for event in agent_events
            if event.duration_seconds is not None
        ]
        success_count = sum(event.result == "success" for event in agent_events)
        model_events = [
            event for event in related_events if event.component == "model"
        ]
        return {
            "agent_name": normalized_name,
            "call_count": len(agent_events),
            "success_count": success_count,
            "failure_count": len(agent_events) - success_count,
            "success_rate": success_count / len(agent_events)
            if agent_events
            else 0.0,
            "average_duration_seconds": (
                sum(durations) / len(durations) if durations else None
            ),
            "input_tokens": sum(event.input_tokens for event in model_events),
            "output_tokens": sum(event.output_tokens for event in model_events),
            "total_tokens": sum(event.total_tokens for event in model_events),
            "tool_call_count": sum(
                event.component == "tool" for event in related_events
            ),
            "executions": executions,
        }

    @staticmethod
    def _agent_execution_record(
        execution: ObservabilityEvent,
        related_events: list[ObservabilityEvent],
    ) -> dict[str, Any]:
        related = [
            event
            for event in related_events
            if ObservabilityEventStore._same_trace(execution, event)
        ]
        user_id = execution.user_id or next(
            (event.user_id for event in related if event.user_id),
            None,
        )
        request_id = execution.request_id or next(
            (event.request_id for event in related if event.request_id),
            None,
        )
        trace_id = execution.trace_id or next(
            (event.trace_id for event in related if event.trace_id),
            None,
        )
        failures = [event for event in related if event.result != "success"]
        return {
            "occurred_at": execution.occurred_at.isoformat(),
            "request_id": request_id,
            "trace_id": trace_id,
            "user_id": user_id,
            "result": execution.result,
            "duration_seconds": execution.duration_seconds,
            "input_tokens": sum(event.input_tokens for event in related),
            "output_tokens": sum(event.output_tokens for event in related),
            "total_tokens": sum(event.total_tokens for event in related),
            "model_call_count": sum(
                event.component == "model" for event in related
            ),
            "tool_call_count": sum(event.component == "tool" for event in related),
            "error_code": (
                execution.error_code
                or (failures[0].error_code if failures else None)
            ),
        }

    @staticmethod
    def _same_trace(
        left: ObservabilityEvent,
        right: ObservabilityEvent,
    ) -> bool:
        for field in ("trace_id", "request_id", "session_id"):
            left_value = getattr(left, field)
            right_value = getattr(right, field)
            if left_value and right_value:
                return left_value == right_value
        return False

    def trace_detail(
        self,
        trace_id: str,
        *,
        start: datetime,
        end: datetime,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a payload-free chronological event chain for one trace."""
        normalized_trace_id = _text(trace_id) or ""
        events = [
            event
            for event in self.query(
                start=start,
                end=end,
                tenant_id=tenant_id,
            )
            if event.trace_id == normalized_trace_id
        ]
        events.sort(key=lambda event: event.occurred_at)
        request_id = next(
            (event.request_id for event in events if event.request_id),
            None,
        )
        return {
            "trace_id": normalized_trace_id,
            "request_id": request_id,
            "events": [self._trace_event(event) for event in events],
        }

    @staticmethod
    def _trace_event(event: ObservabilityEvent) -> dict[str, Any]:
        return {
            "occurred_at": event.occurred_at.isoformat(),
            "event_name": event.event_name,
            "component": event.component,
            "result": event.result,
            "request_id": event.request_id,
            "trace_id": event.trace_id,
            "user_id": event.user_id,
            "agent_name": event.agent_name,
            "model": event.model,
            "tool": event.tool,
            "tool_kind": event.tool_kind,
            "mcp_server": event.mcp_server,
            "skill_name": None,
            "route": event.route,
            "duration_seconds": event.duration_seconds,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "total_tokens": event.total_tokens,
            "error_code": event.error_code,
        }

    @staticmethod
    def _component_rows(
        events: list[ObservabilityEvent],
        component: str,
        key_name: str,
    ) -> list[dict[str, Any]]:
        if component == "agent":
            return ObservabilityEventStore._agent_rows(events)

        groups: dict[str, list[ObservabilityEvent]] = defaultdict(list)
        for event in events:
            if event.component != component:
                continue
            key = _text(getattr(event, key_name)) or "unknown"
            groups[key].append(event)

        rows: list[dict[str, Any]] = []
        for name, group in groups.items():
            durations = [
                event.duration_seconds
                for event in group
                if event.duration_seconds is not None
            ]
            success_count = sum(event.result == "success" for event in group)
            last_occurred_at = max(event.occurred_at for event in group)
            rows.append(
                {
                    "name": name,
                    "last_occurred_at": last_occurred_at.isoformat(),
                    "call_count": len(group),
                    "success_count": success_count,
                    "failure_count": len(group) - success_count,
                    "success_rate": success_count / len(group) if group else 0.0,
                    "average_duration_seconds": (
                        sum(durations) / len(durations) if durations else None
                    ),
                    "input_tokens": sum(event.input_tokens for event in group),
                    "output_tokens": sum(event.output_tokens for event in group),
                    "total_tokens": sum(event.total_tokens for event in group),
                    "tool_call_count": 0,
                    "timeout_count": sum(
                        ObservabilityEventStore._is_timeout(event)
                        for event in group
                    ),
                    "tool_kind": (
                        next(
                            (
                                event.tool_kind
                                for event in group
                                if event.tool_kind
                            ),
                            "Tool",
                        )
                        if component == "tool"
                        else ""
                    ),
                    "mcp_server": (
                        next(
                            (
                                event.mcp_server
                                for event in group
                                if event.mcp_server
                            ),
                            None,
                        )
                        if component == "tool"
                        else None
                    ),
                    # Keep IDs in the application-owned projection. The admin
                    # API resolves them to current usernames at read time so
                    # this store remains independent from authentication.
                    "user_ids": sorted(
                        {
                            event.user_id
                            for event in group
                            if event.user_id
                        },
                    ),
                },
            )
        rows.sort(
            key=lambda row: (row["last_occurred_at"], row["name"]),
            reverse=True,
        )
        return rows[:100]

    @staticmethod
    def _agent_rows(events: list[ObservabilityEvent]) -> list[dict[str, Any]]:
        """Aggregate Agent executions with their model and Tool activity."""
        executions: dict[str, list[ObservabilityEvent]] = defaultdict(list)
        related: dict[str, list[ObservabilityEvent]] = defaultdict(list)
        for event in events:
            agent_name = _text(event.agent_name)
            if not agent_name:
                continue
            if event.component == "agent":
                executions[agent_name].append(event)
            elif event.component in {"model", "tool"}:
                related[agent_name].append(event)

        rows: list[dict[str, Any]] = []
        for name, group in executions.items():
            related_events = related.get(name, [])
            durations = [
                event.duration_seconds
                for event in group
                if event.duration_seconds is not None
            ]
            success_count = sum(event.result == "success" for event in group)
            all_events = [*group, *related_events]
            rows.append(
                {
                    "name": name,
                    "last_occurred_at": max(
                        event.occurred_at for event in all_events
                    ).isoformat(),
                    "call_count": len(group),
                    "success_count": success_count,
                    "failure_count": len(group) - success_count,
                    "success_rate": success_count / len(group) if group else 0.0,
                    "average_duration_seconds": (
                        sum(durations) / len(durations) if durations else None
                    ),
                    "input_tokens": sum(
                        event.input_tokens for event in related_events
                    ),
                    "output_tokens": sum(
                        event.output_tokens for event in related_events
                    ),
                    "total_tokens": sum(
                        event.total_tokens for event in related_events
                    ),
                    "tool_call_count": sum(
                        event.component == "tool" for event in related_events
                    ),
                    "user_ids": sorted(
                        {
                            event.user_id
                            for event in all_events
                            if event.user_id
                        },
                    ),
                },
            )
        rows.sort(
            key=lambda row: (row["last_occurred_at"], row["name"]),
            reverse=True,
        )
        return rows[:100]

    @staticmethod
    def _daily(
        events: list[ObservabilityEvent],
        *,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        daily: dict[date, dict[str, int]] = defaultdict(
            lambda: {
                "requests": 0,
                "errors": 0,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        for event in events:
            row = daily[event.occurred_at.date()]
            if ObservabilityEventStore._is_business_request(event):
                row["requests"] += 1
                row["calls"] += 1
                row["errors"] += int(event.result != "success")
            elif event.component in {"model", "agent", "tool"}:
                row["calls"] += 1
                row["errors"] += int(event.result != "success")
            row["input_tokens"] += event.input_tokens
            row["output_tokens"] += event.output_tokens
            row["total_tokens"] += event.total_tokens

        start_date = _utc(start).date()
        end_date = _utc(end).date()
        rows: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            rows.append({"date": current.isoformat(), **daily[current]})
            current += timedelta(days=1)
        return rows

    @staticmethod
    def _failures(
        events: list[ObservabilityEvent],
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        failures = [event for event in events if event.result != "success"]
        failures.sort(key=lambda event: event.occurred_at, reverse=True)
        return [
            {
                "occurred_at": event.occurred_at.isoformat(),
                "event_name": event.event_name,
                "component": event.component,
                "category": event.component,
                "error_type": ObservabilityEventStore._failure_type(event),
                "error_code": event.error_code
                or (
                    f"http_{event.status_code}"
                    if event.status_code is not None
                    else "operation_failed"
                ),
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "user_id": event.user_id,
                "session_id": event.session_id,
                "agent_name": event.agent_name,
                "model": event.model,
                "tool": event.tool,
                "tool_kind": event.tool_kind,
                "mcp_server": event.mcp_server,
                "skill_name": None,
                "route": event.route,
                "duration_seconds": event.duration_seconds,
            }
            for event in failures[: max(1, min(limit, 500))]
        ]

    @staticmethod
    def _failure_type(event: ObservabilityEvent) -> str:
        code = (event.error_code or "").casefold()
        if "timeout" in code or event.status_code in {408, 504}:
            return "timeout"
        if (
            "permission" in code
            or "forbidden" in code
            or event.status_code in {401, 403}
        ):
            return "permission"
        if "rate" in code and "limit" in code or event.status_code == 429:
            return "rate_limit"
        if (
            "upstream" in code
            or "connection" in code
            or "unavailable" in code
            or (event.status_code is not None and event.status_code >= 500)
        ):
            return "upstream"
        return "system"

    @staticmethod
    def _failure_breakdown(events: list[ObservabilityEvent]) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for event in events:
            if event.result != "success":
                counts[event.component] += 1
        return [
            {"key": key, "count": count}
            for key, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

    @staticmethod
    def _failure_type_breakdown(
        events: list[ObservabilityEvent],
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for event in events:
            if event.result != "success":
                counts[ObservabilityEventStore._failure_type(event)] += 1
        return [
            {"key": key, "count": count}
            for key, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]


__all__ = ["ObservabilityEvent", "ObservabilityEventStore"]
