# -*- coding: utf-8 -*-
"""Tests for the application-level per-user Token usage projection."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase

from examples.agent_service.token_usage_analytics import collect_token_usage


class FakeAuth:
    async def list_accounts(self):
        return [
            SimpleNamespace(id="u-1", username="alice", role="user", status="active"),
            SimpleNamespace(id="u-2", username="bob", role="user", status="active"),
            SimpleNamespace(id="u-deleted", username="gone", role="user", status="deleted"),
        ]

    async def get_token_usage(self, user_id: str, *, start, end):
        del start, end
        values = {
            "u-1": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "message_count": 2, "session_count": 1},
            "u-2": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5, "message_count": 1, "session_count": 1},
        }
        value = values[user_id]
        return SimpleNamespace(
            cache_input_tokens=0,
            cache_creation_input_tokens=0,
            **value,
        )


class TokenUsageAnalyticsTest(IsolatedAsyncioTestCase):
    async def test_collects_and_sorts_non_deleted_users(self) -> None:
        result = await collect_token_usage(
            FakeAuth(),
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        self.assertEqual(result["user_count"], 2)
        self.assertEqual(result["total_tokens"], 20)
        self.assertEqual([row["username"] for row in result["users"]], ["alice", "bob"])
        self.assertEqual(result["users"][0]["total_tokens"], 15)
