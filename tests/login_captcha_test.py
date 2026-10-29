"""Targeted integration tests for local JWT login CAPTCHA enforcement."""
from __future__ import annotations

import asyncio
import base64
import sys
import threading
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
sys.path.insert(0, str(SERVICE_DIR))

from auth import JWTAuthService  # noqa: E402
from login_captcha import (  # noqa: E402
    LoginCaptchaError,
    LoginCaptchaService,
    _CONSUME_CHALLENGE_SCRIPT,
    _CREATE_CHALLENGE_SCRIPT,
    _INCREMENT_LIMIT_SCRIPT,
)


class _MemoryRedis:
    """Implement the narrow async Redis/Lua surface used by these tests."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self._lock = threading.Lock()

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        del ex
        with self._lock:
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

    async def delete(self, key: str) -> int:
        with self._lock:
            return int(self.values.pop(key, None) is not None)

    async def eval(self, script: str, key_count: int, *args: Any) -> Any:
        del key_count
        with self._lock:
            if script == _INCREMENT_LIMIT_SCRIPT:
                key, _window = args
                count = int(self.values.get(key, 0)) + 1
                self.values[key] = count
                return count
            if script == _CREATE_CHALLENGE_SCRIPT:
                challenge_key, state_key, digest, _ttl, _state_ttl = args
                if challenge_key in self.values or state_key in self.values:
                    return 0
                self.values[challenge_key] = digest
                self.values[state_key] = "active"
                return 1
            if script == _CONSUME_CHALLENGE_SCRIPT:
                challenge_key, state_key, _state_ttl = args
                digest = self.values.pop(challenge_key, None)
                if digest is not None:
                    self.values[state_key] = "used"
                    return [1, digest]
                state = self.values.get(state_key)
                if state == "active":
                    self.values[state_key] = "expired"
                    return [0, "expired"]
                if state == "used":
                    return [0, "used"]
                if state == "expired":
                    return [0, "expired"]
                return [0, "missing"]
        raise AssertionError("Unexpected Redis script")


class _Storage:
    def __init__(self, redis: _MemoryRedis) -> None:
        self.redis = redis

    def get_client(self) -> _MemoryRedis:
        return self.redis


def _captcha_code(response: Any) -> dict[str, str]:
    return {
        "captchaChallengeId": response["challengeId"],
        "captchaText": "AB234",
    }


class LoginCaptchaApiTest(TestCase):
    def setUp(self) -> None:
        self.redis = _MemoryRedis()
        self.storage = _Storage(self.redis)
        self.auth = JWTAuthService(
            {"admin": ("admin-id", "admin-password", "admin")},
            "a" * 32,
            storage=self.storage,
            captcha_enabled=True,
        )
        assert self.auth._captcha is not None
        self.auth._captcha._code_factory = lambda: "AB234"
        app = FastAPI()
        app.include_router(self.auth.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def _issue(self) -> dict[str, Any]:
        response = self.client.get("/auth/captcha")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _login(self, challenge: dict[str, Any], *, password: str = "admin-password"):
        return self.client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": password,
                **_captcha_code(challenge),
            },
        )

    def test_challenge_returns_only_image_and_opaque_id(self) -> None:
        challenge = self._issue()

        self.assertTrue(challenge["enabled"])
        self.assertEqual(challenge["expiresIn"], 300)
        self.assertNotIn("answer", challenge)
        self.assertNotIn("captchaText", challenge)
        self.assertTrue(challenge["imageDataUrl"].startswith("data:image/png;base64,"))
        image_bytes = base64.b64decode(challenge["imageDataUrl"].split(",", 1)[1])
        self.assertTrue(image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotIn(b"AB234", image_bytes)
        self.assertTrue(
            all("AB234" not in str(value) for value in self.redis.values.values()),
        )

    def test_missing_wrong_expired_forged_and_reused_challenges_are_rejected(self) -> None:
        missing = self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "admin-password"},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["detail"]["code"], "captcha_required")

        wrong_challenge = self._issue()
        wrong = self.client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": "admin-password",
                "captchaChallengeId": wrong_challenge["challengeId"],
                "captchaText": "WRONG",
            },
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json()["detail"]["code"], "captcha_invalid")
        replay = self._login(wrong_challenge)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"]["code"], "captcha_used")

        expired_challenge = self._issue()
        self.redis.values.pop(
            f"longxin:auth:captcha:challenge:{expired_challenge['challengeId']}",
        )
        expired = self._login(expired_challenge)
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(expired.json()["detail"]["code"], "captcha_expired")

        forged = self.client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": "admin-password",
                "captchaChallengeId": "not-a-real-challenge",
                "captchaText": "AB234",
            },
        )
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(forged.json()["detail"]["code"], "captcha_invalid")
        self.assertNotIn("access_token", forged.json())

    def test_correct_captcha_and_password_login_successfully_once(self) -> None:
        challenge = self._issue()

        logged_in = self._login(challenge)
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.json()["user"]["id"], "admin-id")
        self.assertTrue(logged_in.json()["access_token"])

        replay = self._login(challenge)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"]["code"], "captcha_used")

    def test_failed_password_consumes_challenge_and_registration_cannot_bypass(self) -> None:
        registration_without_captcha = self.client.post(
            "/auth/register",
            json={"username": "alice", "password": "alice-password"},
        )
        self.assertEqual(registration_without_captcha.status_code, 400)
        self.assertEqual(
            registration_without_captcha.json()["detail"]["code"],
            "captcha_required",
        )

        challenge = self._issue()
        failed_password = self._login(challenge, password="incorrect-password")
        self.assertEqual(failed_password.status_code, 401)
        replay = self._login(challenge)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"]["code"], "captcha_used")

        registration_challenge = self._issue()
        registered = self.client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "alice-password",
                **_captcha_code(registration_challenge),
            },
        )
        self.assertEqual(registered.status_code, 201)
        self.assertTrue(registered.json()["access_token"])

    def test_challenge_issue_is_rate_limited(self) -> None:
        assert self.auth._captcha is not None
        self.auth._captcha._max_challenges_per_window = 1

        self.assertEqual(self.client.get("/auth/captcha").status_code, 200)
        limited = self.client.get("/auth/captcha")

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"]["code"], "captcha_rate_limited")


class LoginCaptchaConcurrencyTest(IsolatedAsyncioTestCase):
    async def test_two_service_instances_cannot_consume_one_challenge_twice(self) -> None:
        redis = _MemoryRedis()
        storage = _Storage(redis)
        first = LoginCaptchaService(storage, "s" * 32, code_factory=lambda: "AB234")
        second = LoginCaptchaService(storage, "s" * 32, code_factory=lambda: "AB234")
        challenge = await first.issue("192.0.2.20")

        outcomes = await asyncio.gather(
            first.verify(challenge["challengeId"], "AB234", "192.0.2.20"),
            second.verify(challenge["challengeId"], "AB234", "192.0.2.20"),
            return_exceptions=True,
        )

        self.assertEqual(sum(outcome is None for outcome in outcomes), 1)
        failures = [outcome for outcome in outcomes if isinstance(outcome, LoginCaptchaError)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "captcha_used")

    async def test_challenge_is_bound_to_issuing_ip_and_attempts_are_limited(self) -> None:
        service = LoginCaptchaService(
            _Storage(_MemoryRedis()),
            "s" * 32,
            max_attempts_per_window=1,
            code_factory=lambda: "AB234",
        )
        challenge = await service.issue("192.0.2.20")

        with self.assertRaises(LoginCaptchaError) as changed_ip:
            await service.verify(challenge["challengeId"], "AB234", "192.0.2.21")
        self.assertEqual(changed_ip.exception.code, "captcha_invalid")

        with self.assertRaises(LoginCaptchaError) as first_attempt:
            await service.verify("unknown", "AB234", "192.0.2.20")
        self.assertEqual(first_attempt.exception.code, "captcha_invalid")

        with self.assertRaises(LoginCaptchaError) as limited:
            await service.verify("unknown", "AB234", "192.0.2.20")
        self.assertEqual(limited.exception.code, "captcha_rate_limited")


if __name__ == "__main__":
    import unittest

    unittest.main()
