# -*- coding: utf-8 -*-
"""Application-level aggregation of persisted per-user model token usage."""
from __future__ import annotations

from datetime import datetime
from typing import Any


async def collect_token_usage(
    auth: Any,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Return a bounded per-user token usage projection for administrators.

    Authentication already owns the Redis message history and its usage
    extraction rules.  This module only composes that existing capability into
    an analytics response; it does not create a second token ledger.
    """
    accounts = await auth.list_accounts()
    user_rows: list[dict[str, Any]] = []
    for account in accounts:
        if account.status == "deleted":
            continue
        usage = await auth.get_token_usage(
            account.id,
            start=start,
            end=end,
        )
        user_rows.append(
            {
                "user_id": account.id,
                "username": account.username,
                "role": account.role,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_input_tokens": usage.cache_input_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                "total_tokens": usage.total_tokens,
                "message_count": usage.message_count,
                "session_count": usage.session_count,
            },
        )

    user_rows.sort(
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
    }
    return {
        **totals,
        "user_count": len(user_rows),
        "users": user_rows,
    }


__all__ = ["collect_token_usage"]
