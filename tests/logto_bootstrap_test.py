# -*- coding: utf-8 -*-
"""Tests for the idempotent Logto bootstrap workflow."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


from deploy.logto import bootstrap as bootstrap_module  # noqa: E402


class _FakeManagementClient:
    def __init__(self) -> None:
        self.resources: list[dict[str, Any]] = []
        self.scopes: list[dict[str, Any]] = []
        self.roles: list[dict[str, Any]] = []
        self.created_requests = 0

    def paged_get(self, path: str, *, params=None):
        del params
        if path == "/resources":
            return [
                {
                    **resource,
                    "scopes": [
                        scope
                        for scope in self.scopes
                        if scope["resourceId"] == resource["id"]
                    ],
                }
                for resource in self.resources
            ]
        if path.endswith("/scopes") and path.startswith("/resources/"):
            resource_id = path.split("/")[2]
            return [scope for scope in self.scopes if scope["resourceId"] == resource_id]
        if path == "/organization-roles":
            return self.roles
        if path.endswith("/resource-scopes"):
            role_id = path.split("/")[2]
            role = next(role for role in self.roles if role["id"] == role_id)
            return role["resourceScopes"]
        raise AssertionError(f"unexpected GET {path}")

    def request(self, method: str, path: str, *, params=None, payload=None):
        del params
        self.assert_post(method)
        self.created_requests += 1
        if path == "/resources":
            resource = {
                "id": "resource-1",
                "name": payload["name"],
                "indicator": payload["indicator"],
            }
            self.resources.append(resource)
            return resource
        if path.startswith("/resources/") and path.endswith("/scopes"):
            resource_id = path.split("/")[2]
            scope = {
                "id": f"scope-{len(self.scopes) + 1}",
                "resourceId": resource_id,
                "name": payload["name"],
                "description": payload["description"],
            }
            self.scopes.append(scope)
            return scope
        if path == "/organization-roles":
            role = {
                "id": f"role-{len(self.roles) + 1}",
                "name": payload["name"],
                "resourceScopes": [],
            }
            self.roles.append(role)
            return role
        if path.endswith("/resource-scopes"):
            role_id = path.split("/")[2]
            role = next(role for role in self.roles if role["id"] == role_id)
            for scope_id in payload["scopeIds"]:
                scope = next(scope for scope in self.scopes if scope["id"] == scope_id)
                role["resourceScopes"].append(scope)
            return {}
        raise AssertionError(f"unexpected POST {path}")

    @staticmethod
    def assert_post(method: str) -> None:
        if method != "POST":
            raise AssertionError(f"expected POST, got {method}")


class LogtoBootstrapTest(unittest.TestCase):
    def test_bootstrap_is_additive_and_idempotent(self) -> None:
        client = _FakeManagementClient()
        config = (
            {"name": "lxScope API", "identifier": "https://api.lxscope.local"},
            [
                bootstrap_module.PermissionSpec("agent:use", "Use lxScope agents"),
                bootstrap_module.PermissionSpec(
                    "resource:manage",
                    "Manage lxScope resources",
                ),
                bootstrap_module.PermissionSpec(
                    "member:manage",
                    "Manage tenant members",
                ),
                bootstrap_module.PermissionSpec(
                    "tenant:manage",
                    "Manage tenant settings",
                ),
                bootstrap_module.PermissionSpec(
                    "resource:read",
                    "Read lxScope resources",
                ),
                bootstrap_module.PermissionSpec(
                    "platform:manage",
                    "Manage platform-wide lxScope resources",
                ),
                bootstrap_module.PermissionSpec(
                    "platform:upgrade",
                    "Run platform upgrade operations",
                ),
                bootstrap_module.PermissionSpec(
                    "platform:integration",
                    "Manage platform integrations",
                ),
                bootstrap_module.PermissionSpec(
                    "platform:observe",
                    "Observe platform-wide metrics and audit data",
                ),
            ],
            [
                bootstrap_module.RoleSpec(
                    "admin",
                    (
                        "tenant:manage",
                        "resource:manage",
                        "member:manage",
                        "resource:read",
                        "agent:use",
                    ),
                ),
                bootstrap_module.RoleSpec(
                    "member",
                    ("resource:read", "agent:use"),
                ),
                bootstrap_module.RoleSpec(
                    "platform_admin",
                    (
                        "tenant:manage",
                        "resource:manage",
                        "member:manage",
                        "resource:read",
                        "agent:use",
                        "platform:manage",
                        "platform:upgrade",
                        "platform:integration",
                        "platform:observe",
                    ),
                ),
            ],
        )

        with patch.object(bootstrap_module, "load_config", return_value=config):
            bootstrap_module.bootstrap(Path("config.yaml"), client=client)
        first_request_count = client.created_requests

        self.assertEqual(len(client.resources), 1)
        self.assertEqual(len(client.scopes), 9)
        self.assertEqual(len(client.roles), 3)
        self.assertEqual(
            sorted(len(role["resourceScopes"]) for role in client.roles),
            [2, 5, 9],
        )

        with patch.object(bootstrap_module, "load_config", return_value=config):
            bootstrap_module.bootstrap(Path("config.yaml"), client=client)

        self.assertEqual(client.created_requests, first_request_count)
        self.assertEqual(len(client.resources), 1)
        self.assertEqual(len(client.scopes), 9)
        self.assertEqual(len(client.roles), 3)

    @unittest.skipUnless(
        importlib.util.find_spec("yaml") is not None,
        "PyYAML is not available in this environment",
    )
    def test_repository_config_loads(self) -> None:
        config_path = Path(__file__).parents[1] / "deploy" / "logto" / "config.yaml"
        resource, permissions, roles = bootstrap_module.load_config(config_path)

        self.assertEqual(resource["identifier"], "https://api.lxscope.local")
        self.assertEqual(
            [permission.name for permission in permissions],
            [
                "agent:use",
                "resource:manage",
                "member:manage",
                "tenant:manage",
                "resource:read",
            ],
        )
        self.assertEqual(
            [role.name for role in roles],
            ["admin", "platform_admin", "member"],
        )


if __name__ == "__main__":
    unittest.main()
