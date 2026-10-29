"""Tests for explicit, additive Logto CAPTCHA provisioning."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy" / "logto"))

from bootstrap import (  # noqa: E402
    BootstrapError,
    ManagementApiError,
    _apply_login_captcha,
)


class _Client:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.fail_on = fail_on

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.requests.append((method, path, payload))
        if path == self.fail_on:
            raise ManagementApiError(503, method, path, "provider key rejected")
        return {}


class LogtoCaptchaBootstrapTest(TestCase):
    def test_disabled_by_default_and_does_not_change_existing_provider(self) -> None:
        client = _Client()

        with patch.dict("os.environ", {}, clear=False):
            for name in (
                "LXSCOPE_LOGTO_CAPTCHA_ENABLED",
                "LOGTO_CAPTCHA_PROVIDER",
                "LOGTO_CAPTCHA_SITE_KEY",
                "LOGTO_CAPTCHA_SECRET_KEY",
            ):
                patcher = patch.dict("os.environ", {name: ""})
                patcher.start()
                self.addCleanup(patcher.stop)
            _apply_login_captcha(client)

        self.assertEqual(client.requests, [])

    def test_enabled_requires_external_provider_keys(self) -> None:
        client = _Client()

        with patch.dict(
            "os.environ",
            {
                "LXSCOPE_LOGTO_CAPTCHA_ENABLED": "true",
                "LOGTO_CAPTCHA_SITE_KEY": "",
                "LOGTO_CAPTCHA_SECRET_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaises(BootstrapError) as failure:
                _apply_login_captcha(client)

        self.assertIn("LOGTO_CAPTCHA_SITE_KEY", str(failure.exception))
        self.assertEqual(client.requests, [])

    def test_enabled_turnstile_configures_provider_then_enables_sign_in_policy(self) -> None:
        client = _Client()
        site_key = "turnstile-site-key"
        secret_key = "do-not-print-this-secret"

        with patch.dict(
            "os.environ",
            {
                "LXSCOPE_LOGTO_CAPTCHA_ENABLED": "true",
                "LOGTO_CAPTCHA_PROVIDER": "Turnstile",
                "LOGTO_CAPTCHA_SITE_KEY": site_key,
                "LOGTO_CAPTCHA_SECRET_KEY": secret_key,
            },
            clear=False,
        ):
            _apply_login_captcha(client)

        self.assertEqual([item[1] for item in client.requests], ["/captcha-provider", "/sign-in-exp"])
        self.assertEqual(
            client.requests[0][2],
            {
                "config": {
                    "type": "Turnstile",
                    "siteKey": site_key,
                    "secretKey": secret_key,
                },
            },
        )
        self.assertEqual(client.requests[1][2], {"captchaPolicy": {"enabled": True}})

    def test_provider_errors_do_not_echo_credentials(self) -> None:
        secret_key = "very-sensitive-turnstile-secret"
        client = _Client(fail_on="/captcha-provider")

        with patch.dict(
            "os.environ",
            {
                "LXSCOPE_LOGTO_CAPTCHA_ENABLED": "true",
                "LOGTO_CAPTCHA_SITE_KEY": "site-key",
                "LOGTO_CAPTCHA_SECRET_KEY": secret_key,
            },
            clear=False,
        ):
            with self.assertRaises(BootstrapError) as failure:
                _apply_login_captcha(client)

        self.assertNotIn(secret_key, str(failure.exception))


if __name__ == "__main__":
    import unittest

    unittest.main()
