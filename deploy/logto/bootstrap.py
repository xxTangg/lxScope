# -*- coding: utf-8 -*-
"""Idempotently bootstrap lxScope's Logto API resource and organization roles.

The script deliberately only creates missing objects and missing role-scope
links. Existing Logto objects are never updated or deleted, so running it is
safe for an environment that already has the configuration managed in the
Logto Console.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
PAGE_SIZE = 100


class BootstrapError(RuntimeError):
    """Raised when Logto bootstrap cannot complete safely."""


class ManagementApiError(BootstrapError):
    """Raised for a non-successful Logto Management API response."""

    def __init__(self, status: int, method: str, path: str, detail: str) -> None:
        super().__init__(f"Logto API {method} {path} failed ({status}): {detail}")
        self.status = status
        self.method = method
        self.path = path


@dataclass(frozen=True)
class PermissionSpec:
    name: str
    description: str | None


@dataclass(frozen=True)
class RoleSpec:
    name: str
    permissions: tuple[str, ...]
    description: str | None = None


def _text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        requirement = "required" if required else "must be a string when provided"
        raise BootstrapError(f"config field {field!r} is {requirement}")
    return value.strip()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapError(f"config field {field!r} must be a mapping")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise BootstrapError(f"config field {field!r} must be a list")
    return value


def load_config(
    path: Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, str], list[PermissionSpec], list[RoleSpec]]:
    """Load and validate the declarative bootstrap configuration."""

    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise BootstrapError(
            "PyYAML is required to read deploy/logto/config.yaml. "
            "Install the project dependencies first."
        ) from exc

    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file) or {}
    except OSError as exc:
        raise BootstrapError(f"cannot read Logto config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise BootstrapError(f"invalid YAML in {path}: {exc}") from exc

    document = _mapping(raw, "root")
    api_resource = _mapping(document.get("api_resource"), "api_resource")
    resource_name = _text(api_resource.get("name"), "api_resource.name")
    resource_identifier = _text(
        api_resource.get("identifier"),
        "api_resource.identifier",
    )
    resource = {"name": resource_name, "identifier": resource_identifier}

    permissions: list[PermissionSpec] = []
    permission_names: set[str] = set()
    for index, item in enumerate(_list(document.get("permissions"), "permissions")):
        permission = _mapping(item, f"permissions[{index}]")
        name = _text(permission.get("name"), f"permissions[{index}].name")
        if name in permission_names:
            raise BootstrapError(f"duplicate permission {name!r} in config")
        permission_names.add(name)
        permissions.append(
            PermissionSpec(
                name=name,
                description=_text(
                    permission.get("description"),
                    f"permissions[{index}].description",
                    required=False,
                ),
            ),
        )

    roles: list[RoleSpec] = []
    role_names: set[str] = set()
    for index, item in enumerate(_list(document.get("roles"), "roles")):
        role = _mapping(item, f"roles[{index}]")
        name = _text(role.get("name"), f"roles[{index}].name")
        if name in role_names:
            raise BootstrapError(f"duplicate organization role {name!r} in config")
        role_names.add(name)
        role_permissions = [
            _text(permission, f"roles[{index}].permissions[{permission_index}]")
            for permission_index, permission in enumerate(
                _list(role.get("permissions"), f"roles[{index}].permissions"),
            )
        ]
        unknown_permissions = sorted(set(role_permissions) - permission_names)
        if unknown_permissions:
            raise BootstrapError(
                f"role {name!r} references undefined permissions: "
                + ", ".join(unknown_permissions),
            )
        roles.append(
            RoleSpec(
                name=name,
                permissions=tuple(role_permissions),
                description=_text(
                    role.get("description"),
                    f"roles[{index}].description",
                    required=False,
                ),
            ),
        )

    return resource, permissions, roles


def _json_body(raw: bytes, *, fallback: Any = None) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return fallback


def _error_detail(raw: bytes) -> str:
    payload = _json_body(raw)
    if isinstance(payload, Mapping):
        for key in ("message", "error_description", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    text = raw.decode("utf-8", errors="replace").strip()
    return text or "no response body"


def _collection(payload: Any, *, path: str) -> list[Mapping[str, Any]]:
    """Normalize Logto's array response and common paginated envelopes."""

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = next(
            (
                payload[key]
                for key in ("data", "items", "results")
                if isinstance(payload.get(key), list)
            ),
            None,
        )
        if items is None:
            raise BootstrapError(f"Logto API {path} returned an unexpected collection")
    else:
        raise BootstrapError(f"Logto API {path} returned an unexpected collection")

    if not all(isinstance(item, Mapping) for item in items):
        raise BootstrapError(f"Logto API {path} returned an invalid collection item")
    return list(items)


class LogtoManagementClient:
    """Small stdlib-only client for the Logto token and Management APIs."""

    def __init__(
        self,
        endpoint: str,
        app_id: str,
        app_secret: str,
        management_api_resource: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.endpoint = endpoint.strip().rstrip("/")
        self.app_id = app_id
        self.app_secret = app_secret
        self.management_api_resource = management_api_resource
        self.timeout = timeout
        self.api_base = f"{self.endpoint}/api"
        self._access_token: str | None = None

    @classmethod
    def from_env(cls) -> "LogtoManagementClient":
        required = (
            "LOGTO_ENDPOINT",
            "LOGTO_M2M_APP_ID",
            "LOGTO_M2M_APP_SECRET",
            "LOGTO_MANAGEMENT_API_RESOURCE",
        )
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            raise BootstrapError(
                "missing required environment variable(s): " + ", ".join(missing),
            )
        return cls(*(os.environ[name].strip() for name in required))

    def _access_token_request(self) -> str:
        credentials = base64.b64encode(
            f"{self.app_id}:{self.app_secret}".encode("utf-8"),
        ).decode("ascii")
        request = Request(
            url=f"{self.endpoint}/oidc/token",
            data=urlencode(
                {
                    "grant_type": "client_credentials",
                    "resource": self.management_api_resource,
                    "scope": "all",
                },
            ).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = _json_body(response.read(), fallback={})
        except HTTPError as exc:
            raise ManagementApiError(
                exc.code,
                "POST",
                "/oidc/token",
                _error_detail(exc.read()),
            ) from exc
        except URLError as exc:
            raise BootstrapError(f"cannot reach Logto token endpoint: {exc.reason}") from exc

        token = payload.get("access_token") if isinstance(payload, Mapping) else None
        if not isinstance(token, str) or not token.strip():
            raise BootstrapError("Logto token response did not contain access_token")
        return token

    def _ensure_access_token(self) -> str:
        if self._access_token is None:
            self._access_token = self._access_token_request()
        return self._access_token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        query = urlencode(
            [(key, str(value)) for key, value in (params or {}).items()]
        )
        url = urljoin(f"{self.api_base}/", path.lstrip("/"))
        if query:
            url = f"{url}?{query}"
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._ensure_access_token()}",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url=url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return _json_body(response.read(), fallback={})
        except HTTPError as exc:
            raise ManagementApiError(
                exc.code,
                method,
                path,
                _error_detail(exc.read()),
            ) from exc
        except URLError as exc:
            raise BootstrapError(f"cannot reach Logto Management API: {exc.reason}") from exc

    def paged_get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> list[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        page = 1
        while True:
            page_params = dict(params or {})
            page_params.update(page=page, page_size=PAGE_SIZE)
            page_items = _collection(
                self.request("GET", path, params=page_params),
                path=path,
            )
            items.extend(page_items)
            if len(page_items) < PAGE_SIZE:
                return items
            page += 1


def _id(item: Mapping[str, Any], *, kind: str) -> str:
    value = item.get("id")
    if not isinstance(value, str) or not value:
        raise BootstrapError(f"Logto {kind} response did not contain an id")
    return value


def _matching(
    items: list[Mapping[str, Any]],
    field: str,
    value: str,
) -> Mapping[str, Any] | None:
    return next((item for item in items if item.get(field) == value), None)


def _find_or_create(
    client: LogtoManagementClient,
    *,
    list_path: str,
    match_field: str,
    match_value: str,
    create_path: str,
    create_payload: Mapping[str, Any],
    kind: str,
) -> Mapping[str, Any]:
    existing = _matching(client.paged_get(list_path), match_field, match_value)
    if existing is not None:
        return existing
    try:
        created = client.request("POST", create_path, payload=create_payload)
    except ManagementApiError as exc:
        # A concurrent bootstrap may have created the object after our list.
        if exc.status != 422:
            raise
        existing = _matching(client.paged_get(list_path), match_field, match_value)
        if existing is not None:
            return existing
        raise BootstrapError(
            f"Logto rejected creation of {kind} {match_value!r}: {exc}"
        ) from exc
    if not isinstance(created, Mapping):
        raise BootstrapError(f"Logto create {kind} returned an unexpected response")
    return created


def bootstrap(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    client: LogtoManagementClient | None = None,
) -> None:
    """Apply the declarative lxScope Logto configuration."""

    resource_spec, permission_specs, role_specs = load_config(config_path)
    client = client or LogtoManagementClient.from_env()

    resource = _find_or_create(
        client,
        list_path="/resources",
        match_field="indicator",
        match_value=resource_spec["identifier"],
        create_path="/resources",
        create_payload={
            "name": resource_spec["name"],
            "indicator": resource_spec["identifier"],
        },
        kind="API Resource",
    )
    resource_id = _id(resource, kind="API Resource")

    raw_scopes = resource.get("scopes")
    if isinstance(raw_scopes, list):
        scopes = _collection(raw_scopes, path=f"/resources/{resource_id}/scopes")
    else:
        scopes = client.paged_get(f"/resources/{resource_id}/scopes")
    scopes_by_name = {
        item["name"]: item
        for item in scopes
        if isinstance(item.get("name"), str)
    }

    permission_ids: dict[str, str] = {}
    for permission in permission_specs:
        scope = scopes_by_name.get(permission.name)
        if scope is None:
            scope = _find_or_create(
                client,
                list_path=f"/resources/{resource_id}/scopes",
                match_field="name",
                match_value=permission.name,
                create_path=f"/resources/{resource_id}/scopes",
                create_payload={
                    "name": permission.name,
                    "description": permission.description,
                },
                kind="permission",
            )
            scopes_by_name[permission.name] = scope
        permission_ids[permission.name] = _id(scope, kind="permission")

    roles_by_name = {
        item["name"]: item
        for item in client.paged_get("/organization-roles")
        if isinstance(item.get("name"), str)
    }
    for role_spec in role_specs:
        role = roles_by_name.get(role_spec.name)
        if role is None:
            role = _find_or_create(
                client,
                list_path="/organization-roles",
                match_field="name",
                match_value=role_spec.name,
                create_path="/organization-roles",
                create_payload={
                    "name": role_spec.name,
                    "description": role_spec.description,
                    "type": "User",
                    "organizationScopeIds": [],
                    "resourceScopeIds": [],
                },
                kind="organization role",
            )
            roles_by_name[role_spec.name] = role

        role_id = _id(role, kind="organization role")
        raw_role_scopes = role.get("resourceScopes")
        if isinstance(raw_role_scopes, list):
            role_scopes = _collection(
                raw_role_scopes,
                path=f"/organization-roles/{role_id}/resource-scopes",
            )
        else:
            role_scopes = client.paged_get(
                f"/organization-roles/{role_id}/resource-scopes",
            )
        current_scope_ids = {
            item.get("id")
            for item in role_scopes
            if isinstance(item.get("id"), str)
        }
        desired_scope_ids = [permission_ids[name] for name in role_spec.permissions]
        missing_scope_ids = [
            scope_id
            for scope_id in desired_scope_ids
            if scope_id not in current_scope_ids
        ]
        if missing_scope_ids:
            client.request(
                "POST",
                f"/organization-roles/{role_id}/resource-scopes",
                payload={"scopeIds": missing_scope_ids},
            )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the declarative Logto YAML config.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print("=====================")
    print("lxScope Logto Bootstrap")
    print()
    try:
        bootstrap(args.config)
        _, permissions, roles = load_config(args.config)
    except BootstrapError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print("[OK] API Resource")
    for permission in permissions:
        print(f"[OK] Permission {permission.name}")
    for role in roles:
        print(f"[OK] Organization Role {role.name}")
    print()
    print("Completed.")
    print()
    print("=====================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
