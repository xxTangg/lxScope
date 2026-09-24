# -*- coding: utf-8 -*-
"""Admin-only read API for the Skill observability dashboard.

The application-level repository remains the only layer that knows how raw
events become metrics.  This module is deliberately limited to authentication,
time-window validation, and a stable response contract for the frontend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from admin_api import require_admin
    from auth import AuthUser
    from identity.context import current_tenant_id
    from token_usage_analytics import collect_token_usage
except ModuleNotFoundError:
    from examples.agent_service.admin_api import require_admin
    from examples.agent_service.auth import AuthUser
    from examples.agent_service.identity.context import current_tenant_id
    from examples.agent_service.token_usage_analytics import collect_token_usage


class SkillDailyAnalytics(BaseModel):
    date: str
    event_count: int
    exposed: int
    invoked: int
    completed: int


class SkillTopAnalytics(BaseModel):
    skill_name: str
    invoked_count: int


class SkillSnapshot(BaseModel):
    visible_count: int
    after_count: int


class SkillFailureBreakdown(BaseModel):
    key: str
    count: int


class SkillFailureRecord(BaseModel):
    occurred_at: datetime
    event_name: str
    stage: str
    error_code: str
    result: str
    user_id: str
    session_id: str | None = None
    skill_name: str | None = None
    duration_seconds: float | None = None


class TokenUserUsage(BaseModel):
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


class TokenUsageAnalytics(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_input_tokens: int
    cache_creation_input_tokens: int
    total_tokens: int
    message_count: int
    session_count: int
    user_count: int
    users: list[TokenUserUsage]


class SkillAnalyticsResponse(BaseModel):
    start: datetime
    end: datetime
    skill_data_available: bool
    token_usage: TokenUsageAnalytics
    event_count: int
    reconcile: dict[str, int]
    lifecycle: dict[str, int]
    completed: dict[str, int]
    failure_count: int
    execution_failure_count: int
    execution_failure_rate: float = Field(ge=0)
    failure_by_stage: list[SkillFailureBreakdown]
    failure_by_error: list[SkillFailureBreakdown]
    recent_failures: list[SkillFailureRecord]
    actual_usage_rate: float = Field(ge=0)
    average_reconcile_duration_seconds: float | None = Field(default=None, ge=0)
    latest_snapshot: SkillSnapshot | None = None
    daily: list[SkillDailyAnalytics]
    top_skills: list[SkillTopAnalytics]


skill_analytics_router = APIRouter(
    prefix="/admin/analytics",
    tags=["admin-analytics"],
)


def _empty_skill_analytics() -> dict[str, Any]:
    """Keep Token analysis available when optional Skill storage is absent."""
    return {
        "event_count": 0,
        "reconcile": {
            "success": 0,
            "partial": 0,
            "failed": 0,
            "skipped": 0,
            "started": 0,
        },
        "lifecycle": {"exposed": 0, "invoked": 0, "completed": 0},
        "completed": {"success": 0, "failed": 0, "other": 0},
        "failure_count": 0,
        "execution_failure_count": 0,
        "execution_failure_rate": 0.0,
        "failure_by_stage": [],
        "failure_by_error": [],
        "recent_failures": [],
        "actual_usage_rate": 0.0,
        "average_reconcile_duration_seconds": None,
        "latest_snapshot": None,
        "daily": [],
        "top_skills": [],
    }


def _utc(value: datetime) -> datetime:
    """Normalize browser-provided ISO timestamps to aware UTC values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@skill_analytics_router.get(
    "/skills",
    response_model=SkillAnalyticsResponse,
)
async def get_skill_analytics(
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    _: AuthUser = Depends(require_admin),
) -> SkillAnalyticsResponse:
    """Return aggregate Skill usage metrics for the admin dashboard."""
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

    store: Any = getattr(request.app.state, "skill_observation_store", None)
    skill_data_available = store is not None
    if store is None:
        result = _empty_skill_analytics()
    else:
        try:
            tenant_user_ids = (
                [account.id for account in await request.app.state.auth.list_accounts()]
                if current_tenant_id()
                else None
            )
            result = await store.query_skill_analytics(
                start=normalized_start,
                end=normalized_end,
                user_ids=tenant_user_ids,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Skill observability analytics are temporarily unavailable.",
            ) from exc

    auth: Any = getattr(request.app.state, "auth", None)
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

    return SkillAnalyticsResponse(
        start=normalized_start,
        end=normalized_end,
        skill_data_available=skill_data_available,
        token_usage=token_usage,
        **result,
    )


__all__ = ["SkillAnalyticsResponse", "skill_analytics_router"]
