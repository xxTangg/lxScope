# -*- coding: utf-8 -*-
"""Tenant-scoped token usage projections for administrator dashboards."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _usage_row(
    *,
    user_id: str,
    username: str,
    role: str,
    tenant_id: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    message_count: int = 0,
    session_count: int = 0,
    cost: Any = 0,
) -> dict[str, Any]:
    total_tokens = max(0, int(input_tokens)) + max(0, int(output_tokens))
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "username": username,
        "role": role,
        "input_tokens": max(0, int(input_tokens)),
        "output_tokens": max(0, int(output_tokens)),
        "cache_input_tokens": max(0, int(cache_input_tokens)),
        "cache_creation_input_tokens": max(0, int(cache_creation_input_tokens)),
        "total_tokens": total_tokens,
        "message_count": max(0, int(message_count)),
        "session_count": max(0, int(session_count)),
        "cost": str(cost or "0"),
    }


async def _collect_quota_ledger(
    quota_service: Any,
    *,
    start: datetime,
    end: datetime,
    tenant_id: str | None,
    platform_admin: bool,
    tenant_member_provider: Any | None,
) -> dict[str, Any]:
    rows = await quota_service.list_ledger(
        limit=100_000,
        entry_type="model_usage",
        tenant_id=tenant_id,
        since=start,
        until=end,
        global_scope=platform_admin,
    )
    names: dict[tuple[str | None, str], tuple[str, str]] = {}
    if tenant_member_provider is not None and tenant_id is not None:
        provider = (
            tenant_member_provider()
            if callable(tenant_member_provider)
            else tenant_member_provider
        )
        if provider is not None:
            for member in await provider.list_members(tenant_id):
                member_id = str(member.get("membership_id"))
                names[(str(tenant_id), member_id)] = (
                    str(member.get("display_name") or member.get("username") or member_id),
                    str(member.get("membership_role") or "member"),
                )

    grouped: dict[tuple[str | None, str], dict[str, Any]] = {}
    for entry in rows:
        membership_id = str(entry.get("membership_id") or "unknown")
        entry_tenant = (
            str(entry.get("tenant_id"))
            if entry.get("tenant_id") is not None
            else tenant_id
        )
        key = (entry_tenant, membership_id)
        display_name, role = names.get(key, (membership_id, "member"))
        row = grouped.setdefault(
            key,
            _usage_row(
                user_id=membership_id,
                username=display_name,
                role=role,
                tenant_id=entry_tenant,
            ),
        )
        tokens = max(0, int(entry.get("tokens") or 0))
        row["output_tokens"] += tokens
        row["total_tokens"] += tokens
        row["message_count"] += 1
        row["cost"] = str(
            float(row.get("cost") or 0) + float(entry.get("cost") or 0)
        )

    user_rows = sorted(
        grouped.values(),
        key=lambda row: (-row["total_tokens"], row["username"]),
    )
    totals = {
        "input_tokens": sum(row["input_tokens"] for row in user_rows),
        "output_tokens": sum(row["output_tokens"] for row in user_rows),
        "cache_input_tokens": sum(row["cache_input_tokens"] for row in user_rows),
        "cache_creation_input_tokens": sum(
            row["cache_creation_input_tokens"] for row in user_rows
        ),
        "total_tokens": sum(row["total_tokens"] for row in user_rows),
        "message_count": sum(row["message_count"] for row in user_rows),
        "session_count": sum(row["session_count"] for row in user_rows),
        "cost": str(sum(float(row.get("cost") or 0) for row in user_rows)),
    }
    return {**totals, "user_count": len(user_rows), "users": user_rows}


async def collect_token_usage(
    auth: Any,
    *,
    start: datetime,
    end: datetime,
    tenant_id: str | None = None,
    platform_admin: bool = False,
    quota_service: Any | None = None,
    tenant_member_provider: Any | None = None,
) -> dict[str, Any]:
    """Return current-tenant usage, or global usage for platform admins."""
    if quota_service is not None and getattr(quota_service, "enabled", False):
        if tenant_id is not None or platform_admin:
            return await _collect_quota_ledger(
                quota_service,
                start=start,
                end=end,
                tenant_id=tenant_id,
                platform_admin=platform_admin,
                tenant_member_provider=tenant_member_provider,
            )

    if tenant_id is not None:
        provider = (
            tenant_member_provider()
            if callable(tenant_member_provider)
            else tenant_member_provider
        )
        if provider is None:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "total_tokens": 0,
                "message_count": 0,
                "session_count": 0,
                "user_count": 0,
                "users": [],
            }
        accounts = [
            {
                "id": str(member["membership_id"]),
                "username": str(
                    member.get("display_name")
                    or member.get("username")
                    or member["membership_id"]
                ),
                "role": str(member.get("membership_role") or "member"),
                "status": str(member.get("membership_status") or "active"),
            }
            for member in await provider.list_members(tenant_id)
        ]
    else:
        accounts = await auth.list_accounts()

    user_rows: list[dict[str, Any]] = []
    for account in accounts:
        account_status = (
            account.get("status") if isinstance(account, dict)
            else getattr(account, "status", None)
        )
        if account_status == "deleted":
            continue
        account_id = (
            account.get("id") if isinstance(account, dict)
            else getattr(account, "id")
        )
        username = (
            account.get("username") if isinstance(account, dict)
            else getattr(account, "username")
        )
        role = (
            account.get("role", "user") if isinstance(account, dict)
            else getattr(account, "role", "user")
        )
        usage = await auth.get_token_usage(account_id, start=start, end=end)
        user_rows.append(
            _usage_row(
                tenant_id=tenant_id,
                user_id=str(account_id),
                username=str(username),
                role=str(role),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_input_tokens=usage.cache_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                message_count=usage.message_count,
                session_count=usage.session_count,
            ),
        )

    user_rows.sort(key=lambda row: (-row["total_tokens"], row["username"]))
    totals = {
        "input_tokens": sum(row["input_tokens"] for row in user_rows),
        "output_tokens": sum(row["output_tokens"] for row in user_rows),
        "cache_input_tokens": sum(row["cache_input_tokens"] for row in user_rows),
        "cache_creation_input_tokens": sum(
            row["cache_creation_input_tokens"] for row in user_rows
        ),
        "total_tokens": sum(row["total_tokens"] for row in user_rows),
        "message_count": sum(row["message_count"] for row in user_rows),
        "session_count": sum(row["session_count"] for row in user_rows),
    }
    return {**totals, "user_count": len(user_rows), "users": user_rows}


__all__ = ["collect_token_usage"]
