# -*- coding: utf-8 -*-
"""Admin read APIs for the project-level observability module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from admin_api import require_admin
    from auth import AuthUser
    from identity.context import current_tenant_id
    from observability_analytics import ObservabilityEventStore
    from token_usage_analytics import collect_token_usage
except ModuleNotFoundError:
    from examples.agent_service.admin_api import require_admin
    from examples.agent_service.auth import AuthUser
    from examples.agent_service.identity.context import current_tenant_id
    from examples.agent_service.observability_analytics import ObservabilityEventStore
    from examples.agent_service.token_usage_analytics import collect_token_usage


class ObservabilityDaily(BaseModel):
    date: str
    requests: int
    errors: int
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ObservabilityComponentRow(BaseModel):
    name: str
    user_names: list[str] = Field(default_factory=list)
    last_occurred_at: datetime
    tool_call_count: int = 0
    call_count: int
    success_count: int
    failure_count: int
    success_rate: float = Field(ge=0, le=1)
    average_duration_seconds: float | None = Field(default=None, ge=0)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_kind: str | None = None
    mcp_server: str | None = None
    timeout_count: int = 0


class ObservabilityFailure(BaseModel):
    occurred_at: datetime
    event_name: str
    component: str
    error_code: str
    category: str | None = None
    error_type: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    session_id: str | None = None
    agent_name: str | None = None
    model: str | None = None
    tool: str | None = None
    tool_kind: str | None = None
    mcp_server: str | None = None
    skill_name: str | None = None
    route: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class ObservabilityFailureBreakdown(BaseModel):
    key: str
    count: int


class ObservabilityTokenUser(BaseModel):
    user_id: str
    username: str
    role: str
    input_tokens: int
    output_tokens: int
    cache_input_tokens: int
    cache_creation_input_tokens: int
    total_tokens: int
    message_count: int
    session_count: int


class ObservabilityTokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_input_tokens: int
    cache_creation_input_tokens: int
    total_tokens: int
    message_count: int
    session_count: int
    user_count: int
    users: list[ObservabilityTokenUser]


class ObservabilityOverviewResponse(BaseModel):
    start: datetime
    end: datetime
    data_available: bool
    event_count: int
    request_count: int
    successful_requests: int
    failed_requests: int
    success_rate: float = Field(ge=0, le=1)
    active_user_count: int
    average_response_time_seconds: float | None = Field(default=None, ge=0)
    token_usage: ObservabilityTokenUsage
    daily: list[ObservabilityDaily]
    models: list[ObservabilityComponentRow]
    agents: list[ObservabilityComponentRow]
    tools: list[ObservabilityComponentRow]
    failures: list[ObservabilityFailure]
    failure_by_component: list[ObservabilityFailureBreakdown]
    failure_by_type: list[ObservabilityFailureBreakdown]


class ObservabilityFailureCenterResponse(BaseModel):
    start: datetime
    end: datetime
    failure_count: int
    failure_by_component: list[ObservabilityFailureBreakdown]
    failure_by_type: list[ObservabilityFailureBreakdown]
    failures: list[ObservabilityFailure]


class ObservabilityComponentDetailResponse(BaseModel):
    start: datetime
    end: datetime
    component: Literal["model", "agent", "tool"]
    call_count: int
    success_count: int
    failure_count: int
    success_rate: float = Field(ge=0, le=1)
    average_duration_seconds: float | None = Field(default=None, ge=0)
    p95_duration_seconds: float | None = Field(default=None, ge=0)
    input_tokens: int
    output_tokens: int
    total_tokens: int
    daily: list[ObservabilityDaily]
    items: list[ObservabilityComponentRow]
    failures: list[ObservabilityFailure]
    user_options: list[dict[str, str]] = Field(default_factory=list)
    user_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    timeout_count: int = 0
    agent_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    executions: list["ToolExecutionRecord"] = Field(default_factory=list)
    mcp_servers: list["McpServerStatus"] = Field(default_factory=list)


class ToolExecutionRecord(BaseModel):
    occurred_at: datetime
    request_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    agent_name: str | None = None
    result: str
    duration_seconds: float | None = Field(default=None, ge=0)
    error_code: str | None = None
    tool_kind: str = "Tool"
    mcp_server: str | None = None


class McpServerStatus(BaseModel):
    server_name: str
    status: str = "unknown"
    tool_count: int = 0
    call_count: int = 0
    average_duration_seconds: float | None = Field(default=None, ge=0)
    failure_count: int = 0


class AgentExecutionRecord(BaseModel):
    occurred_at: datetime
    request_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    result: str
    duration_seconds: float | None = Field(default=None, ge=0)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_call_count: int = 0
    tool_call_count: int = 0
    error_code: str | None = None


class AgentDetailResponse(BaseModel):
    start: datetime
    end: datetime
    agent_name: str
    call_count: int
    success_count: int
    failure_count: int
    success_rate: float = Field(ge=0, le=1)
    average_duration_seconds: float | None = Field(default=None, ge=0)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_call_count: int = 0
    executions: list[AgentExecutionRecord]


class ObservabilityTraceEvent(BaseModel):
    occurred_at: datetime
    event_name: str
    component: str
    result: str
    request_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    agent_name: str | None = None
    model: str | None = None
    tool: str | None = None
    tool_kind: str | None = None
    mcp_server: str | None = None
    skill_name: str | None = None
    route: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error_code: str | None = None


class ObservabilityTraceResponse(BaseModel):
    trace_id: str
    request_id: str | None = None
    events: list[ObservabilityTraceEvent]


observability_analytics_router = APIRouter(
    prefix="/admin/observability",
    tags=["admin-observability"],
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _events_for_current_tenant(request: Request) -> Any:
    observability = getattr(request.app.state, "observability", None)
    store = getattr(observability, "events", None)
    tenant_id = current_tenant_id()
    if store is None or tenant_id is None:
        return store

    tenant_prefix = f"{tenant_id}::"
    events = [
        event
        for event in store.snapshot()
        if event.user_id and event.user_id.startswith(tenant_prefix)
    ]
    scoped = ObservabilityEventStore(max_events=max(1, len(events)))
    scoped.replace(events)
    return scoped


def _failure_type_from_code(error_code: str | None) -> str:
    code = (error_code or "").casefold()
    if "timeout" in code:
        return "timeout"
    if "permission" in code or "forbidden" in code or "denied" in code:
        return "permission"
    if "rate" in code and "limit" in code:
        return "rate_limit"
    if "upstream" in code or "connection" in code or "unavailable" in code:
        return "upstream"
    return "system"


async def _load_skill_failures(
    request: Request,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Read Skill failures into the same shape as project runtime failures."""
    skill_store = getattr(request.app.state, "skill_observation_store", None)
    if skill_store is None:
        return []
    user_ids = None
    if current_tenant_id():
        auth = getattr(request.app.state, "auth", None)
        user_ids = [account.id for account in await auth.list_accounts()] if auth else []
    try:
        skill_summary = await skill_store.query_skill_analytics(
            start=start,
            end=end,
            user_ids=user_ids,
        )
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for failure in skill_summary.get("recent_failures", []):
        error_code = failure.get("error_code") or "skill_failed"
        occurred_at = failure.get("occurred_at")
        if isinstance(occurred_at, datetime):
            occurred_at = _utc(occurred_at).isoformat()
        rows.append(
            {
                "occurred_at": occurred_at,
                "event_name": failure.get("event_name", "skill.completed"),
                "component": "skill",
                "category": "skill",
                "error_type": _failure_type_from_code(error_code),
                "error_code": error_code,
                "request_id": None,
                "trace_id": None,
                "user_id": failure.get("user_id"),
                "username": None,
                "session_id": failure.get("session_id"),
                "agent_name": None,
                "model": None,
                "tool": None,
                "tool_kind": None,
                "mcp_server": None,
                "skill_name": failure.get("skill_name"),
                "route": None,
                "duration_seconds": failure.get("duration_seconds"),
            },
        )
    return rows


def _merge_failure_breakdown(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("key")
        if key:
            counts[key] = counts.get(key, 0) + int(row.get("count", 0))
    return [
        {"key": key, "count": count}
        for key, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _period(
    *,
    start: datetime | None,
    end: datetime | None,
    days: int,
) -> tuple[datetime, datetime]:
    normalized_end = _utc(end) if end is not None else datetime.now(timezone.utc)
    normalized_start = (
        _utc(start)
        if start is not None
        else normalized_end - timedelta(days=days)
    )
    if normalized_start >= normalized_end:
        raise HTTPException(
            status_code=400,
            detail="The analytics start time must be before the end time.",
        )
    return normalized_start, normalized_end


def _empty_summary() -> dict[str, Any]:
    return {
        "event_count": 0,
        "request_count": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "success_rate": 0.0,
        "active_user_count": 0,
        "average_response_time_seconds": None,
        "daily": [],
        "models": [],
        "agents": [],
        "tools": [],
        "failures": [],
        "failure_by_component": [],
        "failure_by_type": [],
    }


def _empty_component_detail(component: str) -> dict[str, Any]:
    return {
        "component": component,
        "call_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "success_rate": 0.0,
        "average_duration_seconds": None,
        "p95_duration_seconds": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "daily": [],
        "items": [],
        "failures": [],
        "available_user_ids": [],
        "user_breakdown": [],
        "timeout_count": 0,
        "agent_breakdown": [],
        "executions": [],
        "mcp_servers": [],
    }


async def _username_by_id(auth: Any) -> dict[str, str]:
    """Build a best-effort user directory at API read time."""
    username_by_id: dict[str, str] = {}
    list_accounts = getattr(auth, "list_accounts", None)
    if callable(list_accounts):
        try:
            accounts = await list_accounts()
        except Exception:
            accounts = []
        username_by_id = {
            account.id: account.username
            for account in accounts
            if getattr(account, "id", None) and getattr(account, "username", None)
        }

    return username_by_id


async def _resolve_component_user_names(
    auth: Any,
    rows: list[dict[str, Any]],
) -> None:
    """Resolve event user IDs without coupling the event store to auth."""
    username_by_id = await _username_by_id(auth)
    for row in rows:
        user_ids = row.pop("user_ids", [])
        row["user_names"] = [
            username_by_id.get(user_id, user_id)
            for user_id in user_ids
        ]


async def _resolve_component_users(
    auth: Any,
    detail: dict[str, Any],
) -> None:
    username_by_id = await _username_by_id(auth)
    available_user_ids = detail.pop("available_user_ids", [])
    detail["user_options"] = [
        {"user_id": user_id, "username": username_by_id.get(user_id, user_id)}
        for user_id in available_user_ids
    ]
    for item in detail.get("user_breakdown", []):
        user_id = item.get("user_id")
        item["username"] = username_by_id.get(user_id, user_id)
    for execution in detail.get("executions", []):
        user_id = execution.get("user_id")
        execution["username"] = username_by_id.get(user_id, user_id)


async def _resolve_failure_users(
    auth: Any,
    rows: list[dict[str, Any]],
) -> None:
    username_by_id = await _username_by_id(auth)
    for row in rows:
        user_id = row.get("user_id")
        row["username"] = username_by_id.get(user_id, user_id)


@observability_analytics_router.get(
    "/overview",
    response_model=ObservabilityOverviewResponse,
    summary="Return project-level runtime analytics",
)
async def get_observability_overview(
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    _: AuthUser = Depends(require_admin),
) -> ObservabilityOverviewResponse:
    """Return one stable projection for the admin observability dashboard."""
    normalized_start, normalized_end = _period(start=start, end=end, days=days)

    observability = getattr(request.app.state, "observability", None)
    store = _events_for_current_tenant(request)
    if store is None:
        summary = _empty_summary()
        data_available = False
    else:
        summary = store.summarize(start=normalized_start, end=normalized_end)
        data_available = bool(
            getattr(getattr(observability, "settings", None), "enabled", True),
        )

    skill_failures = await _load_skill_failures(
        request,
        start=normalized_start,
        end=normalized_end,
    )
    if skill_failures:
        summary["failures"] = [
            *summary["failures"],
            *skill_failures,
        ]
        summary["failures"].sort(
            key=lambda row: row.get("occurred_at") or "",
            reverse=True,
        )
        summary["failures"] = summary["failures"][:50]
        skill_type_counts: dict[str, int] = {}
        for row in skill_failures:
            key = row["error_type"]
            skill_type_counts[key] = skill_type_counts.get(key, 0) + 1
        summary["failure_by_component"] = _merge_failure_breakdown(
            [
                *summary["failure_by_component"],
                {"key": "skill", "count": len(skill_failures)},
            ],
        )
        summary["failure_by_type"] = _merge_failure_breakdown(
            [
                *summary["failure_by_type"],
                *[
                    {"key": key, "count": count}
                    for key, count in skill_type_counts.items()
                ],
            ],
        )

    await _resolve_failure_users(
        getattr(request.app.state, "auth", None),
        summary["failures"],
    )

    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        raise HTTPException(
            status_code=503,
            detail="Token usage analytics are temporarily unavailable.",
        )
    try:
        token_usage = await collect_token_usage(
            auth,
            start=normalized_start,
            end=normalized_end,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Token usage analytics are temporarily unavailable.",
        ) from exc

    return ObservabilityOverviewResponse(
        start=normalized_start,
        end=normalized_end,
        data_available=data_available,
        token_usage=ObservabilityTokenUsage(**token_usage),
        **summary,
    )


@observability_analytics_router.get(
    "/failures",
    response_model=ObservabilityFailureCenterResponse,
    summary="Return cross-component runtime failures",
)
async def get_observability_failures(
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    component: str | None = Query(default=None),
    error_type: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    _: AuthUser = Depends(require_admin),
) -> ObservabilityFailureCenterResponse:
    """Return one unified failure center across runtime components."""
    normalized_start, normalized_end = _period(start=start, end=end, days=days)
    observability = getattr(request.app.state, "observability", None)
    store = _events_for_current_tenant(request)
    detail = (
        store.failure_center(
            start=normalized_start,
            end=normalized_end,
            component=component,
            error_type=error_type,
            user_id=user_id,
            limit=limit,
        )
        if store is not None
        else {
            "failure_count": 0,
            "failure_by_component": [],
            "failure_by_type": [],
            "failures": [],
        }
    )
    skill_failures = await _load_skill_failures(
        request,
        start=normalized_start,
        end=normalized_end,
    )
    skill_failures = [
        row
        for row in skill_failures
        if (not component or component == "skill")
        and (not user_id or row.get("user_id") == user_id)
        and (not error_type or row.get("error_type") == error_type)
    ]
    if skill_failures:
        all_failures = [*detail["failures"], *skill_failures]
        all_failures.sort(
            key=lambda row: row.get("occurred_at") or "",
            reverse=True,
        )
        detail["failure_count"] += len(skill_failures)
        detail["failure_by_component"] = _merge_failure_breakdown(
            [
                *detail["failure_by_component"],
                {"key": "skill", "count": len(skill_failures)},
            ],
        )
        detail["failure_by_type"] = _merge_failure_breakdown(
            [
                *detail["failure_by_type"],
                *[
                    {"key": key, "count": sum(
                        row["error_type"] == key for row in skill_failures
                    )}
                    for key in {row["error_type"] for row in skill_failures}
                ],
            ],
        )
        detail["failures"] = all_failures[:limit]
    await _resolve_failure_users(
        getattr(request.app.state, "auth", None),
        detail["failures"],
    )
    return ObservabilityFailureCenterResponse(
        start=normalized_start,
        end=normalized_end,
        **detail,
    )


@observability_analytics_router.get(
    "/{component}",
    response_model=ObservabilityComponentDetailResponse,
    summary="Return drill-down analytics for one runtime component",
)
async def get_observability_component_detail(
    component: Literal["model", "agent", "tool"],
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    name: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    _: AuthUser = Depends(require_admin),
) -> ObservabilityComponentDetailResponse:
    """Return detail data for the model, Agent or Tool/MCP drill-down page."""
    normalized_start, normalized_end = _period(start=start, end=end, days=days)

    observability = getattr(request.app.state, "observability", None)
    store = _events_for_current_tenant(request)
    detail = (
        store.component_detail(
            component,
            start=normalized_start,
            end=normalized_end,
            name=name,
            user_id=user_id,
        )
        if store is not None
        else _empty_component_detail(component)
    )
    await _resolve_component_user_names(
        getattr(request.app.state, "auth", None),
        detail["items"],
    )
    await _resolve_component_users(
        getattr(request.app.state, "auth", None),
        detail,
    )
    return ObservabilityComponentDetailResponse(
        start=normalized_start,
        end=normalized_end,
        **detail,
    )


@observability_analytics_router.get(
    "/agents/{agent_name}",
    response_model=AgentDetailResponse,
    summary="Return execution details for one Agent",
)
async def get_observability_agent_detail(
    agent_name: str,
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    _: AuthUser = Depends(require_admin),
) -> AgentDetailResponse:
    """Return Agent executions and their related model/Tool summaries."""
    normalized_start, normalized_end = _period(start=start, end=end, days=days)
    observability = getattr(request.app.state, "observability", None)
    store = _events_for_current_tenant(request)
    detail = (
        store.agent_detail(
            agent_name,
            start=normalized_start,
            end=normalized_end,
        )
        if store is not None
        else {
            "agent_name": agent_name,
            "call_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": 0.0,
            "average_duration_seconds": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "tool_call_count": 0,
            "executions": [],
        }
    )
    username_by_id = await _username_by_id(
        getattr(request.app.state, "auth", None),
    )
    for execution in detail["executions"]:
        user_id = execution.get("user_id")
        execution["username"] = username_by_id.get(user_id, user_id)
    return AgentDetailResponse(
        start=normalized_start,
        end=normalized_end,
        **detail,
    )


@observability_analytics_router.get(
    "/traces/{trace_id}",
    response_model=ObservabilityTraceResponse,
    summary="Return a payload-free runtime trace",
)
async def get_observability_trace(
    trace_id: str,
    request: Request,
    _: AuthUser = Depends(require_admin),
) -> ObservabilityTraceResponse:
    """Return the chronological event chain for one trace ID."""
    normalized_start, normalized_end = _period(start=None, end=None, days=90)
    observability = getattr(request.app.state, "observability", None)
    store = _events_for_current_tenant(request)
    if store is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    detail = store.trace_detail(
        trace_id,
        start=normalized_start,
        end=normalized_end,
    )
    if not detail["events"]:
        raise HTTPException(status_code=404, detail="Trace not found.")
    username_by_id = await _username_by_id(
        getattr(request.app.state, "auth", None),
    )
    for event in detail["events"]:
        user_id = event.get("user_id")
        event["username"] = username_by_id.get(user_id, user_id)
    return ObservabilityTraceResponse(**detail)


__all__ = [
    "AgentDetailResponse",
    "ObservabilityTraceResponse",
    "ObservabilityComponentDetailResponse",
    "ObservabilityFailureCenterResponse",
    "ObservabilityOverviewResponse",
    "observability_analytics_router",
]
