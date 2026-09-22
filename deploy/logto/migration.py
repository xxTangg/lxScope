# -*- coding: utf-8 -*-
"""Migrate a complete lxScope Logto installation.

The migration is intentionally additive and repeatable.  It provisions the
static lxScope authorization model through ``bootstrap.py``, makes the SPA
application usable at the configured public URL, creates optional Logto
Organizations and users, assigns organization roles, and emits SQL bindings
for lxScope's application-owned tenant table.

Passwords are never exported.  A user can be created during import only when
the manifest points at a password environment variable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

try:  # Script execution: python deploy/logto/migration.py
    from bootstrap import (
        BootstrapError,
        LogtoManagementClient,
        ManagementApiError,
        _id,
        _mapping,
        _text,
        bootstrap,
    )
except ImportError:  # Package/import-based test execution
    from .bootstrap import (  # type: ignore[no-redef]
        BootstrapError,
        LogtoManagementClient,
        ManagementApiError,
        _id,
        _mapping,
        _text,
        bootstrap,
    )


DEFAULT_MANIFEST_PATH = Path(__file__).with_name("migration.yaml")
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("generated")
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.groups()
            configured = os.getenv(name)
            return configured if configured else (default or "")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


def _load_document(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as document_file:
            if path.suffix.lower() == ".json":
                raw = json.load(document_file)
            else:
                import yaml

                raw = yaml.safe_load(document_file) or {}
    except ModuleNotFoundError as exc:
        raise BootstrapError(
            "PyYAML is required to read the Logto migration manifest. "
            "Install the project dependencies first."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"cannot read Logto migration manifest {path}: {exc}") from exc
    except Exception as exc:
        if exc.__class__.__module__ == "yaml.error":
            raise BootstrapError(f"invalid YAML in {path}: {exc}") from exc
        raise

    document = _expand_environment(raw)
    if not isinstance(document, Mapping):
        raise BootstrapError("Logto migration manifest root must be a mapping")
    return dict(document)


def _string(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        requirement = "required" if required else "must be a string when provided"
        raise BootstrapError(f"manifest field {field!r} is {requirement}")
    return value.strip()


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise BootstrapError(f"manifest field {field!r} must be a list")
    return value


def _absolute_url(value: str, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BootstrapError(f"manifest field {field!r} must be an absolute HTTP(S) URL")
    return value.rstrip("/")


def _validate_manifest(document: Mapping[str, Any]) -> None:
    tenant_id = _string(document.get("logto_tenant_id", "default"), "logto_tenant_id")
    if len(tenant_id or "") > 21:
        raise BootstrapError("manifest field 'logto_tenant_id' must be at most 21 characters")

    application = document.get("browser_application", {})
    if not isinstance(application, Mapping):
        raise BootstrapError("manifest field 'browser_application' must be a mapping")
    if application.get("enabled", True):
        public_url = _string(application.get("public_url"), "browser_application.public_url")
        _absolute_url(public_url or "", "browser_application.public_url")
        for key in ("redirect_paths", "post_logout_redirect_paths"):
            for index, path in enumerate(_list(application.get(key, []), f"browser_application.{key}")):
                value = _string(path, f"browser_application.{key}[{index}]")
                if not value.startswith("/"):
                    raise BootstrapError(
                        f"manifest field browser_application.{key}[{index}] must start with '/'"
                    )

    organizations = _list(document.get("organizations", []), "organizations")
    organization_codes: set[str] = set()
    for index, item in enumerate(organizations):
        organization = _mapping(item, f"organizations[{index}]")
        code = _string(organization.get("code"), f"organizations[{index}].code")
        if code in organization_codes:
            raise BootstrapError(f"duplicate organization code {code!r} in manifest")
        organization_codes.add(code or "")
        _string(organization.get("name"), f"organizations[{index}].name")
        for user_index, user in enumerate(_list(organization.get("users", []), f"organizations[{index}].users")):
            user_doc = _mapping(user, f"organizations[{index}].users[{user_index}]")
            if not any(user_doc.get(key) for key in ("id", "username", "primary_email", "email")):
                raise BootstrapError(
                    f"organizations[{index}].users[{user_index}] needs id, username, or email"
                )
            _string(user_doc.get("role", "member"), f"organizations[{index}].users[{user_index}].role")

    demo_data = document.get("demo_data")
    if demo_data is not None:
        demo = _mapping(demo_data, "demo_data")
        demo_code = _string(demo.get("code"), "demo_data.code")
        if demo_code in organization_codes:
            raise BootstrapError(f"demo_data.code {demo_code!r} duplicates an organization code")
        _string(demo.get("name"), "demo_data.name")
        for user_index, user in enumerate(_list(demo.get("users", []), "demo_data.users")):
            user_doc = _mapping(user, f"demo_data.users[{user_index}]")
            if not any(user_doc.get(key) for key in ("id", "username", "primary_email", "email")):
                raise BootstrapError(
                    f"demo_data.users[{user_index}] needs id, username, or email"
                )
            _string(user_doc.get("role", "member"), f"demo_data.users[{user_index}].role")


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    document = _load_document(path)
    _validate_manifest(document)
    return document


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _uri_entries(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []
    values: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            values.append(entry)
        elif isinstance(entry, Mapping):
            for key in ("uri", "value", "url"):
                if isinstance(entry.get(key), str):
                    values.append(entry[key])
                    break
    return values


def _application_uri_entries(values: list[str], existing: Any) -> list[Any]:
    """Use the target Logto version's existing URI representation when possible."""

    if isinstance(existing, list) and existing and isinstance(existing[0], Mapping):
        first = existing[0]
        if "value" in first:
            return [{"value": value} for value in values]
        return [{"uri": value, "isRegex": False} for value in values]
    return values


def _merge_uris(desired: list[str], existing: Any) -> list[str]:
    """Add deployment URLs without removing existing local/dev URLs."""

    merged: list[str] = []
    for value in _uri_entries(existing) + desired:
        if value not in merged:
            merged.append(value)
    return merged


def _application_payload(
    application: Mapping[str, Any],
    *,
    redirect_uris: list[str],
    logout_uris: list[str],
    existing_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    existing_metadata = dict(existing_metadata or {})
    existing_redirects = existing_metadata.get("redirectUris")
    existing_logout = existing_metadata.get("postLogoutRedirectUris")
    merged_redirects = _merge_uris(redirect_uris, existing_redirects)
    merged_logout = _merge_uris(logout_uris, existing_logout)
    existing_metadata.update(
        {
            "redirectUris": _application_uri_entries(merged_redirects, existing_redirects),
            "postLogoutRedirectUris": _application_uri_entries(merged_logout, existing_logout),
            "corsAllowedOrigins": list(
                dict.fromkeys(
                    _uri_entries(existing_metadata.get("corsAllowedOrigins"))
                    + ([_origin(redirect_uris[0])] if redirect_uris else [])
                )
            ),
        }
    )
    return {
        "name": _string(application.get("name", "lxScope Web"), "browser_application.name"),
        "description": _string(
            application.get("description", "lxScope web application"),
            "browser_application.description",
            required=False,
        ),
        "type": "SPA",
        "oidcClientMetadata": existing_metadata,
    }


def ensure_browser_application(
    client: LogtoManagementClient,
    application: Mapping[str, Any],
) -> Mapping[str, Any]:
    public_url = _absolute_url(
        _string(application.get("public_url"), "browser_application.public_url") or "",
        "browser_application.public_url",
    )
    redirect_uris = [
        f"{public_url}{path}"
        for path in _list(application.get("redirect_paths", ["/callback"]), "browser_application.redirect_paths")
    ]
    logout_uris = [
        f"{public_url}{path}"
        for path in _list(
            application.get("post_logout_redirect_paths", ["/"]),
            "browser_application.post_logout_redirect_paths",
        )
    ]
    configured_id = _string(application.get("id"), "browser_application.id", required=False)
    id_env = _string(application.get("id_env"), "browser_application.id_env", required=False)
    application_id = configured_id or (os.getenv(id_env, "").strip() if id_env else "")

    existing: Mapping[str, Any] | None = None
    if application_id:
        try:
            response = client.request("GET", f"/applications/{application_id}")
        except ManagementApiError as exc:
            if exc.status != 404:
                raise
        else:
            if isinstance(response, Mapping):
                existing = response
    if existing is None and not application_id:
        name = _string(application.get("name", "lxScope Web"), "browser_application.name")
        existing = next(
            (item for item in client.paged_get("/applications") if item.get("name") == name),
            None,
        )

    if existing is None:
        payload = _application_payload(
            application,
            redirect_uris=redirect_uris,
            logout_uris=logout_uris,
        )
        try:
            created = client.request("POST", "/applications", payload=payload)
        except ManagementApiError as exc:
            # Logto versions before the URI metadata migration accepted plain
            # strings; current versions accept objects. Retry only the known
            # validation statuses with the legacy representation.
            if exc.status not in {400, 422}:
                raise
            payload["oidcClientMetadata"] = {
                **payload["oidcClientMetadata"],
                "redirectUris": redirect_uris,
                "postLogoutRedirectUris": logout_uris,
            }
            created = client.request("POST", "/applications", payload=payload)
        if not isinstance(created, Mapping):
            raise BootstrapError("Logto create browser application returned an unexpected response")
        return created

    application_id = _id(existing, kind="browser application")
    existing_metadata = existing.get("oidcClientMetadata")
    payload = _application_payload(
        application,
        redirect_uris=redirect_uris,
        logout_uris=logout_uris,
        existing_metadata=existing_metadata if isinstance(existing_metadata, Mapping) else None,
    )
    try:
        updated = client.request("PATCH", f"/applications/{application_id}", payload=payload)
    except ManagementApiError as exc:
        if exc.status not in {400, 422}:
            raise
        payload["oidcClientMetadata"] = {
            **payload["oidcClientMetadata"],
            "redirectUris": redirect_uris,
            "postLogoutRedirectUris": logout_uris,
        }
        updated = client.request("PATCH", f"/applications/{application_id}", payload=payload)
    return updated if isinstance(updated, Mapping) else {**existing, **payload, "id": application_id}


def _user_match(user: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    user_id = _string(spec.get("id"), "user.id", required=False)
    if user_id and user.get("id") == user_id:
        return True
    username = _string(spec.get("username"), "user.username", required=False)
    if username and user.get("username") == username:
        return True
    email = _string(spec.get("primary_email", spec.get("email")), "user.primary_email", required=False)
    return bool(email and (user.get("primaryEmail") == email or user.get("email") == email))


def ensure_user(
    client: LogtoManagementClient,
    spec: Mapping[str, Any],
) -> Mapping[str, Any]:
    users = client.paged_get("/users")
    existing = next((user for user in users if _user_match(user, spec)), None)
    if existing is not None:
        return existing

    password_env = _string(spec.get("password_env"), "user.password_env", required=False)
    password = os.getenv(password_env, "") if password_env else ""
    if not password:
        raise BootstrapError(
            "cannot create Logto user without a password; set password_env for "
            f"user {spec.get('username') or spec.get('primary_email') or '<unknown>'!r}"
        )
    payload: dict[str, Any] = {"password": password}
    for manifest_key, logto_key in (
        ("username", "username"),
        ("primary_email", "primaryEmail"),
        ("email", "primaryEmail"),
        ("name", "name"),
        ("avatar", "avatar"),
    ):
        value = _string(spec.get(manifest_key), f"user.{manifest_key}", required=False)
        if value and logto_key not in payload:
            payload[logto_key] = value
    if not any(payload.get(key) for key in ("username", "primaryEmail")):
        raise BootstrapError("a new Logto user needs username or primary_email")
    try:
        created = client.request("POST", "/users", payload=payload)
    except ManagementApiError as exc:
        if exc.status != 422:
            raise
        existing = next((user for user in client.paged_get("/users") if _user_match(user, spec)), None)
        if existing is None:
            raise BootstrapError(f"Logto rejected creation of user: {exc}") from exc
        return existing
    if not isinstance(created, Mapping):
        raise BootstrapError("Logto create user returned an unexpected response")
    return created


def _role_id_by_name(roles: list[Mapping[str, Any]], name: str) -> str:
    role = next((item for item in roles if str(item.get("name", "")).lower() == name.lower()), None)
    if role is None:
        raise BootstrapError(f"Logto organization role {name!r} is not configured")
    return _id(role, kind="organization role")


def ensure_organization_member(
    client: LogtoManagementClient,
    *,
    organization_id: str,
    user_id: str,
    role_id: str,
) -> None:
    members = client.paged_get(f"/organizations/{organization_id}/users")
    if not any(str(member.get("id") or member.get("userId")) == user_id for member in members):
        try:
            client.request(
                "POST",
                f"/organizations/{organization_id}/users",
                payload={"userIds": [user_id]},
            )
        except ManagementApiError as exc:
            if exc.status not in {400, 409, 422}:
                raise
    client.request(
        "PUT",
        f"/organizations/{organization_id}/users/{user_id}/roles",
        payload={"organizationRoleIds": [role_id]},
    )


def ensure_organization(
    client: LogtoManagementClient,
    *,
    tenant_id: str,
    spec: Mapping[str, Any],
    roles: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    code = _string(spec.get("code"), "organization.code") or ""
    name = _string(spec.get("name"), "organization.name") or ""
    organizations = client.paged_get("/organizations")
    existing = next(
        (
            organization
            for organization in organizations
            if isinstance(organization.get("customData"), Mapping)
            and organization["customData"].get("lxscopeCode") == code
        ),
        None,
    )
    if existing is None:
        existing = next((organization for organization in organizations if organization.get("name") == name), None)
    if existing is None:
        existing = client.request(
            "POST",
            "/organizations",
            payload={
                "tenantId": tenant_id,
                "name": name,
                "description": _string(spec.get("description"), "organization.description", required=False),
                "customData": {"lxscopeCode": code},
            },
        )
    if not isinstance(existing, Mapping):
        raise BootstrapError(f"Logto organization {code!r} returned an unexpected response")
    organization_id = _id(existing, kind="organization")
    result_users: list[Mapping[str, Any]] = []
    for user_spec in _list(spec.get("users", []), "organization.users"):
        user = ensure_user(client, _mapping(user_spec, "organization.users[]"))
        user_id = _id(user, kind="user")
        role_name = _string(_mapping(user_spec, "organization.users[]").get("role", "member"), "user.role") or "member"
        role_id = _role_id_by_name(roles, role_name)
        ensure_organization_member(
            client,
            organization_id=organization_id,
            user_id=user_id,
            role_id=role_id,
        )
        result_users.append({
            "id": user_id,
            "username": user.get("username"),
            "primaryEmail": user.get("primaryEmail"),
            "name": user.get("name"),
            "role": role_name,
        })
    return existing, result_users


def _sql(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def tenant_uuid(spec: Mapping[str, Any]) -> str:
    explicit = _string(spec.get("tenant_id"), "organization.tenant_id", required=False)
    if explicit:
        try:
            return str(UUID(explicit))
        except ValueError as exc:
            raise BootstrapError("organization.tenant_id must be a valid UUID") from exc
    code = _string(spec.get("code"), "organization.code") or ""
    return str(uuid5(NAMESPACE_URL, f"lxscope:tenant:{code}"))


def build_tenant_bindings_sql(bindings: list[Mapping[str, Any]]) -> str:
    lines = [
        "-- Generated by deploy/logto/migration.py; apply after 0004_logto_tenant_binding.sql.",
        "-- This file is additive and only upserts Logto organization -> lxScope tenant bindings.",
        "BEGIN;",
    ]
    for binding in bindings:
        lines.append(
            "INSERT INTO longxin_app.tenants "
            "(id, code, name, status, identity_provider, external_org_id) VALUES ("
            f"{_sql(binding['tenant_id'])}, { _sql(binding['code']) }, "
            f"{_sql(binding['name'])}, 'active', 'logto', { _sql(binding['external_org_id']) }) "
            "ON CONFLICT (identity_provider, external_org_id) "
            "WHERE external_org_id IS NOT NULL DO UPDATE SET "
            "code = EXCLUDED.code, name = EXCLUDED.name, status = 'active', updated_at = NOW();"
        )
    lines.extend(["COMMIT;", ""])
    return "\n".join(lines)


def _write_outputs(
    output_dir: Path,
    result: Mapping[str, Any],
    *,
    api_resource: str | None = None,
    logto_endpoint: str | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "migration-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    browser_application = result.get("browser_application")
    if isinstance(browser_application, Mapping) and browser_application.get("id"):
        env_lines = [
            "# Generated by deploy/logto/migration.py; review before copying to .env",
            f"VITE_LOGTO_APP_ID={browser_application['id']}",
        ]
        if logto_endpoint:
            env_lines.append(f"VITE_LOGTO_ENDPOINT={logto_endpoint}")
        if api_resource:
            env_lines.append(f"VITE_LOGTO_API_RESOURCE={api_resource}")
        (output_dir / "migration.env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    sql = result.get("tenant_bindings_sql")
    if isinstance(sql, str):
        (output_dir / "tenant-bindings.sql").write_text(sql, encoding="utf-8")


def apply_migration(
    manifest_path: Path,
    config_path: Path,
    output_dir: Path,
    *,
    seed_demo: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    client = LogtoManagementClient.from_env()
    bootstrap(config_path, client=client)

    resource_identifier = _string(
        _mapping(_load_document(config_path).get("api_resource"), "api_resource").get("identifier"),
        "api_resource.identifier",
    )
    result: dict[str, Any] = {
        "api_resource": resource_identifier,
        "browser_application": None,
        "organizations": [],
        "tenant_bindings_sql": None,
        "seed_demo": seed_demo,
    }

    application = _mapping(manifest.get("browser_application", {}), "browser_application")
    if application.get("enabled", True):
        browser_application = ensure_browser_application(client, application)
        result["browser_application"] = {
            "id": browser_application.get("id"),
            "name": browser_application.get("name"),
            "redirectUris": _uri_entries(
                _mapping(browser_application.get("oidcClientMetadata", {}), "oidcClientMetadata").get("redirectUris")
                if isinstance(browser_application.get("oidcClientMetadata"), Mapping)
                else []
            ),
        }

    role_items = client.paged_get("/organization-roles")
    tenant_id = _string(manifest.get("logto_tenant_id", "default"), "logto_tenant_id") or "default"
    bindings: list[Mapping[str, Any]] = []
    organization_specs = list(_list(manifest.get("organizations", []), "organizations"))
    if seed_demo:
        demo_data = manifest.get("demo_data")
        if demo_data is None:
            raise BootstrapError("--seed-demo requires demo_data in the migration manifest")
        organization_specs.append(demo_data)
    for organization_spec in organization_specs:
        organization, users = ensure_organization(
            client,
            tenant_id=tenant_id,
            spec=_mapping(organization_spec, "organizations[]"),
            roles=role_items,
        )
        spec = _mapping(organization_spec, "organizations[]")
        code = _string(spec.get("code"), "organization.code") or ""
        name = _string(spec.get("name"), "organization.name") or ""
        organization_id = _id(organization, kind="organization")
        bindings.append({
            "tenant_id": tenant_uuid(spec),
            "code": code,
            "name": name,
            "external_org_id": organization_id,
        })
        result["organizations"].append({
            "id": organization_id,
            "code": code,
            "name": name,
            "users": users,
        })
    result["tenant_bindings_sql"] = build_tenant_bindings_sql(bindings)
    _write_outputs(
        output_dir,
        result,
        api_resource=resource_identifier,
        logto_endpoint=os.getenv("LOGTO_ENDPOINT", "").strip(),
    )
    return result


def export_migration(
    manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    client = LogtoManagementClient.from_env()
    organizations: list[dict[str, Any]] = []
    for organization in client.paged_get("/organizations"):
        organization_id = _id(organization, kind="organization")
        custom_data = organization.get("customData")
        custom_code = custom_data.get("lxscopeCode") if isinstance(custom_data, Mapping) else None
        code = custom_code if isinstance(custom_code, str) and custom_code else f"external_{organization_id}"
        users: list[dict[str, Any]] = []
        for member in client.paged_get(f"/organizations/{organization_id}/users"):
            user_id = _id(member, kind="organization member")
            roles = client.paged_get(f"/organizations/{organization_id}/users/{user_id}/roles")
            role_name = next(
                (str(role.get("name")) for role in roles if isinstance(role.get("name"), str)),
                "member",
            )
            users.append({
                "id": user_id,
                "username": member.get("username"),
                "primary_email": member.get("primaryEmail"),
                "name": member.get("name"),
                "role": role_name,
            })
        organizations.append({
            "code": code,
            "name": organization.get("name", code),
            "description": organization.get("description"),
            "users": users,
        })

    applications = client.paged_get("/applications")
    app_id = _string(
        _mapping(manifest.get("browser_application", {}), "browser_application").get("id"),
        "browser_application.id",
        required=False,
    ) or os.getenv("VITE_LOGTO_APP_ID", "").strip()
    application = next((item for item in applications if item.get("id") == app_id), None) if app_id else None
    exported = {
        "logto_tenant_id": manifest.get("logto_tenant_id", "default"),
        "browser_application": {
            "enabled": True,
            "id": application.get("id") if application else app_id,
            "name": application.get("name") if application else manifest.get("browser_application", {}).get("name", "lxScope Web"),
            "public_url": manifest.get("browser_application", {}).get("public_url", "http://localhost:8000"),
            "redirect_paths": manifest.get("browser_application", {}).get("redirect_paths", ["/callback"]),
            "post_logout_redirect_paths": manifest.get("browser_application", {}).get("post_logout_redirect_paths", ["/"]),
        },
        "organizations": organizations,
        "notes": [
            "Passwords and password digests are intentionally excluded.",
            "Set password_env for users that must be created in a new Logto instance.",
        ],
    }
    _write_outputs(output_dir, exported)
    (output_dir / "migration-manifest.json").write_text(
        json.dumps(exported, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return exported


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "apply", "export"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="Create the manifest's demo organization and demo users.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "validate":
            _load_document(args.config)
            if os.getenv("LOGTO_ENDPOINT", "").strip() and os.getenv("LOGTO_M2M_APP_ID", "").strip():
                print("[OK] Manifest and Logto environment validated")
            else:
                print("[OK] Manifest and static config validated")
                print("[INFO] apply/export additionally require Logto M2M environment variables")
            return 0
        if args.command == "apply":
            result = apply_migration(
                args.manifest,
                args.config,
                args.output_dir,
                seed_demo=args.seed_demo,
            )
            print(f"[OK] Logto migration complete: {args.output_dir}")
            print(f"[INFO] browser application: {result.get('browser_application', {}).get('id') if result.get('browser_application') else 'disabled'}")
            print(f"[INFO] organizations: {len(result.get('organizations', []))}")
            return 0
        exported = export_migration(args.manifest, args.output_dir)
        print(f"[OK] Logto export complete: {args.output_dir / 'migration-manifest.json'}")
        print(f"[INFO] organizations: {len(exported.get('organizations', []))}")
        return 0
    except BootstrapError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
