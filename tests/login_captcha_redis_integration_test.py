"""Optional real-Redis verification for atomic, cross-instance consumption."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from uuid import uuid4

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
sys.path.insert(0, str(SERVICE_DIR))

from login_captcha import LoginCaptchaError, LoginCaptchaService  # noqa: E402


class _Storage:
    def __init__(self, redis_client) -> None:
        self._redis_client = redis_client

    def get_client(self):
        return self._redis_client


@skipUnless(os.getenv("CAPTCHA_TEST_REDIS_URL"), "set CAPTCHA_TEST_REDIS_URL for Redis Lua integration")
class RedisAtomicCaptchaTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from redis.asyncio import Redis

        self.redis = Redis.from_url(
            os.environ["CAPTCHA_TEST_REDIS_URL"],
            decode_responses=True,
        )
        await self.redis.ping()
        self.prefix = f"longxin:auth:captcha:test:{uuid4().hex}"
        self.storage = _Storage(self.redis)

    async def asyncTearDown(self) -> None:
        keys = [key async for key in self.redis.scan_iter(match=f"{self.prefix}:*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.aclose()

    async def test_lua_allows_only_one_instance_to_consume_the_challenge(self) -> None:
        first = LoginCaptchaService(
            self.storage,
            "r" * 32,
            code_factory=lambda: "AB234",
            key_prefix=self.prefix,
        )
        second = LoginCaptchaService(
            self.storage,
            "r" * 32,
            code_factory=lambda: "AB234",
            key_prefix=self.prefix,
        )
        challenge = await first.issue("192.0.2.25")

        outcomes = await asyncio.gather(
            first.verify(challenge["challengeId"], "AB234", "192.0.2.25"),
            second.verify(challenge["challengeId"], "AB234", "192.0.2.25"),
            return_exceptions=True,
        )

        self.assertEqual(sum(outcome is None for outcome in outcomes), 1)
        failures = [outcome for outcome in outcomes if isinstance(outcome, LoginCaptchaError)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "captcha_used")


if __name__ == "__main__":
    import unittest

    unittest.main()
