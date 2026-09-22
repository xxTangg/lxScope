"""Safe release validation, backup, replacement, health check, and rollback.

This module deliberately owns the filesystem and upgrade state.  The AgentScope
application only wires the service into the app and supplies the existing
customer-token verifier; it does not need to know how releases are stored.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tarfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile

from auth import AuthUser, JWTAuthService
from identity.dependencies import get_bound_membership_id, get_bound_tenant_id
from identity.tenant_keys import tenant_scoped_key
from longxin_admin.distributed_lock import DistributedLease

from .models import (
    ArtifactType,
    BackupDeleteRequest,
    BackupListResponse,
    BackupMeta,
    ReleaseCatalogResponse,
    ReleaseMeta,
    RemoteUpgradeRequest,
    UpgradeOperation,
    UpgradeOperationListResponse,
    UpgradeStatusResponse,
)


UPGRADE_PREFIX = "longxin:upgrade:v1"
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_PACKAGE_BYTES = int(os.getenv("LONGXIN_UPGRADE_MAX_BYTES", str(512 * 1024 * 1024)))
_MAX_TAR_MEMBERS = int(os.getenv("LONGXIN_UPGRADE_MAX_FILES", "10000"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error("invalid_upgrade_request", f"{field} must be an ISO-8601 timestamp.", 422) from exc
    if parsed.tzinfo is None:
        raise _error("invalid_upgrade_request", f"{field} must include a timezone.", 422)
    return parsed.astimezone(timezone.utc)


def _safe_version(version: str) -> str:
    if not _VERSION_RE.fullmatch(version):
        raise _error("invalid_version", "The release version contains unsafe characters.", 422)
    return version


def _safe_member_name(name: str) -> PurePosixPath:
    # tarfile names use POSIX separators even on Windows.
    from pathlib import PurePosixPath

    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise _error("unsafe_archive_path", "The release contains an unsafe archive path.", 400)
    if any(part in {"", "."} for part in path.parts):
        raise _error("unsafe_archive_path", "The release contains an unsafe archive path.", 400)
    return path


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class UpgradeService:
    """Replaceable application service for app/core releases."""

    def __init__(
        self,
        storage: Any,
        auth: JWTAuthService,
        audit_store_provider: Any | None = None,
    ) -> None:
        self._storage = storage
        self._auth = auth
        self._audit_store_provider = audit_store_provider
        data_root = Path(
            os.getenv("LONGXIN_DATA_DIR", os.getenv("LONGXIN_UPGRADE_DATA_DIR", "data")),
        ).expanduser().resolve()
        self._data_root = data_root
        self._release_root = data_root / "releases"
        self._backup_root = data_root / "backups"
        self._staging_root = data_root / "upgrade-staging"
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def _mutation_lock(self) -> DistributedLease:
        return DistributedLease(
            self._client,
            self._lock,
            f"{UPGRADE_PREFIX}:mutation-lock",
        )

    def _client(self) -> Any:
        client = self._storage.get_client()
        if client is None:
            raise _error("storage_not_ready", "Upgrade storage is not ready.", 503)
        return client

    def _audit_store(self) -> Any | None:
        provider = self._audit_store_provider
        return provider() if provider is not None else None

    @staticmethod
    def _audit_key() -> str:
        return tenant_scoped_key("longxin:admin:v1", "audit")

    @staticmethod
    def _release_key(artifact_type: str, version: str) -> str:
        return f"{UPGRADE_PREFIX}:release:{artifact_type}:{version}"

    @staticmethod
    def _release_index_key(artifact_type: str) -> str:
        return f"{UPGRADE_PREFIX}:releases:{artifact_type}"

    @staticmethod
    def _backup_key(backup_id: str) -> str:
        return f"{UPGRADE_PREFIX}:backup:{backup_id}"

    @staticmethod
    def _backup_index_key() -> str:
        return f"{UPGRADE_PREFIX}:backups"

    @staticmethod
    def _operation_key(operation_id: str) -> str:
        return f"{UPGRADE_PREFIX}:operation:{operation_id}"

    @staticmethod
    def _idempotency_key(scope: str, key: str) -> str:
        return f"{UPGRADE_PREFIX}:idempotency:{scope}:{sha256(key.encode()).hexdigest()}"

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    async def _read_json(self, key: str) -> dict[str, Any] | None:
        raw = await self._client().get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    async def _write_json(self, key: str, value: dict[str, Any]) -> None:
        await self._client().set(key, json.dumps(value, ensure_ascii=False))

    async def _read_list(self, key: str) -> list[str]:
        values = await self._client().lrange(key, 0, -1)
        result: list[str] = []
        for value in values:
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            if isinstance(value, str):
                result.append(value)
        return result

    @staticmethod
    def _release_view(value: dict[str, Any], request_id: str = "") -> ReleaseMeta:
        return ReleaseMeta.model_validate({**value, "request_id": request_id})

    @staticmethod
    def _operation_view(value: dict[str, Any], request_id: str = "") -> UpgradeOperation:
        return UpgradeOperation.model_validate({**value, "request_id": request_id})

    @staticmethod
    def _backup_view(value: dict[str, Any]) -> BackupMeta:
        return BackupMeta.model_validate(value)

    def _release_path(self, release: dict[str, Any]) -> Path:
        filename = Path(str(release["file"]))
        if filename.name != filename or filename.suffixes[-2:] != [".tar", ".gz"]:
            raise _error("invalid_release_path", "The release file path is invalid.", 500)
        path = (self._release_root / filename).resolve()
        if self._release_root not in path.parents:
            raise _error("invalid_release_path", "The release file path is outside releases/.", 500)
        return path

    def _target_path(self, artifact_type: ArtifactType) -> Path:
        env_name = "LONGXIN_APP_TARGET_DIR" if artifact_type == "app" else "LONGXIN_CORE_TARGET_DIR"
        configured = os.getenv(env_name)
        if not configured:
            raise _error(
                "upgrade_target_not_configured",
                f"{env_name} must be configured before applying an upgrade.",
                503,
            )
        target = Path(configured).expanduser().resolve()
        if self._data_root == target or self._data_root in target.parents:
            raise _error(
                "upgrade_target_invalid",
                "The upgrade target must not be inside LONGXIN_DATA_DIR.",
                409,
            )
        return target

    def _target_configured(self) -> dict[str, bool]:
        return {
            "app": bool(os.getenv("LONGXIN_APP_TARGET_DIR")),
            "core": bool(os.getenv("LONGXIN_CORE_TARGET_DIR")),
        }

    async def current_versions(self) -> dict[str, str | None]:
        result: dict[str, str | None] = {
            "app": os.getenv("LONGXIN_APP_VERSION", "3.8.1"),
            "core": os.getenv("LONGXIN_CORE_VERSION") or None,
        }
        for artifact_type in ("app", "core"):
            value = await self._read_json(f"{UPGRADE_PREFIX}:installed:{artifact_type}")
            if value and isinstance(value.get("version"), str):
                result[artifact_type] = value["version"]
        return result

    async def _validate_archive(
        self,
        path: Path,
        artifact_type: ArtifactType,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        if path.stat().st_size > _MAX_PACKAGE_BYTES:
            raise _error("release_too_large", "The release exceeds the configured size limit.", 413)
        try:
            with tarfile.open(path, mode="r:gz") as archive:
                members = archive.getmembers()
                if len(members) > _MAX_TAR_MEMBERS:
                    raise _error("release_too_many_files", "The release contains too many files.", 400)
                for member in members:
                    _safe_member_name(member.name)
                    if member.issym() or member.islnk() or member.isdev():
                        raise _error("unsafe_archive_entry", "Symlinks and device files are not allowed.", 400)
                manifest_member = next(
                    (member for member in members if _safe_member_name(member.name).name == "manifest.json"),
                    None,
                )
                if manifest_member is None:
                    raise _error("manifest_missing", "The release must contain manifest.json.", 400)
                if manifest_member.size > 1024 * 1024:
                    raise _error("manifest_too_large", "manifest.json is too large.", 400)
                stream = archive.extractfile(manifest_member)
                if stream is None:
                    raise _error("manifest_invalid", "manifest.json could not be read.", 400)
                try:
                    manifest = json.loads(stream.read())
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _error("manifest_invalid", "manifest.json is not valid JSON.", 400) from exc
        except (tarfile.ReadError, OSError) as exc:
            raise _error("release_invalid", "The release is not a readable .tar.gz archive.", 400) from exc
        if not isinstance(manifest, dict):
            raise _error("manifest_invalid", "manifest.json must contain an object.", 400)
        manifest_type = manifest.get("artifact_type", manifest.get("type"))
        manifest_version = manifest.get("version")
        if manifest_type != artifact_type:
            raise _error("artifact_type_mismatch", "The package type does not match the request.", 409)
        if not isinstance(manifest_version, str) or _safe_version(manifest_version) != manifest_version:
            raise _error("manifest_invalid", "manifest.json contains an invalid version.", 400)
        if expected_version is not None and manifest_version != expected_version:
            raise _error("version_mismatch", "The package version does not match the request.", 409)
        listed_files = manifest.get("files")
        if listed_files is not None:
            if not isinstance(listed_files, list) or any(not isinstance(item, str) for item in listed_files):
                raise _error("manifest_invalid", "manifest.json files must be a string array.", 400)
            names = {_safe_member_name(member.name).as_posix() for member in members}
            missing = [item for item in listed_files if item not in names]
            if missing:
                raise _error("manifest_files_missing", "manifest.json lists files absent from the archive.", 400)
        return manifest

    async def _save_release(
        self,
        *,
        artifact_type: ArtifactType,
        version: str,
        path: Path,
        uploaded_by: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        size, digest = await asyncio.to_thread(_sha256_file, path)
        release = {
            "type": artifact_type,
            "version": version,
            "file": path.name,
            "size": size,
            "sha256": digest,
            "uploaded_at": _now(),
            "uploaded_by": uploaded_by,
            "manifest": manifest,
        }
        existing = await self._read_json(self._release_key(artifact_type, version))
        if existing is not None and existing.get("sha256") != digest:
            raise _error("release_version_conflict", "Another package already uses this version.", 409)
        await self._write_json(self._release_key(artifact_type, version), release)
        versions = await self._read_list(self._release_index_key(artifact_type))
        if version not in versions:
            await self._client().rpush(self._release_index_key(artifact_type), version)
        return release

    async def upload_release(
        self,
        artifact_type: ArtifactType,
        version: str,
        upload: UploadFile,
        actor: AuthUser,
        *,
        idempotency_key: str,
    ) -> ReleaseMeta:
        version = _safe_version(version)
        if not upload.filename or not upload.filename.lower().endswith((".tar.gz", ".tgz")):
            raise _error("invalid_release_filename", "The release must be a .tar.gz archive.", 400)
        idem_key = self._idempotency_key(f"upload:{actor.id}", idempotency_key)
        async with self._mutation_lock():
            previous = await self._read_json(idem_key)
            if previous is not None:
                return self._release_view(previous)
            self._release_root.mkdir(parents=True, exist_ok=True)
            self._staging_root.mkdir(parents=True, exist_ok=True)
            temp_path = self._staging_root / f"upload-{uuid4().hex}.tar.gz"
            try:
                size = 0
                with temp_path.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > _MAX_PACKAGE_BYTES:
                            raise _error("release_too_large", "The release exceeds the configured size limit.", 413)
                        stream.write(chunk)
                manifest = await self._validate_archive(temp_path, artifact_type, version)
                final_path = self._release_root / f"{artifact_type}-{version}.tar.gz"
                existing = await self._read_json(self._release_key(artifact_type, version))
                digest = (await asyncio.to_thread(_sha256_file, temp_path))[1]
                if existing is not None and existing.get("sha256") != digest:
                    raise _error("release_version_conflict", "Another package already uses this version.", 409)
                if final_path.exists():
                    old_digest = (await asyncio.to_thread(_sha256_file, final_path))[1]
                    if old_digest != digest:
                        raise _error("release_version_conflict", "Another package already uses this version.", 409)
                else:
                    await asyncio.to_thread(os.replace, temp_path, final_path)
                release = await self._save_release(
                    artifact_type=artifact_type,
                    version=version,
                    path=final_path,
                    uploaded_by=actor.username,
                    manifest=manifest,
                )
                await self._write_json(idem_key, release)
                return self._release_view(release)
            finally:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)

    async def _release(self, artifact_type: ArtifactType, version: str) -> dict[str, Any]:
        _safe_version(version)
        release = await self._read_json(self._release_key(artifact_type, version))
        if release is None:
            raise _error("release_not_found", "The requested release was not found.", 404)
        return release

    async def list_releases(self, request_id: str = "") -> ReleaseCatalogResponse:
        releases: list[dict[str, Any]] = []
        for artifact_type in ("app", "core"):
            for version in await self._read_list(self._release_index_key(artifact_type)):
                value = await self._read_json(self._release_key(artifact_type, version))
                if value is not None:
                    releases.append(value)
        releases.sort(key=lambda item: str(item.get("uploaded_at", "")), reverse=True)
        latest: dict[str, ReleaseMeta | None] = {"app": None, "core": None}
        for item in releases:
            if latest[item["type"]] is None:
                latest[item["type"]] = self._release_view(item, request_id)
        return ReleaseCatalogResponse(
            app=latest["app"],
            core=latest["core"],
            releases=[self._release_view(item, request_id) for item in releases],
            installed_versions=await self.current_versions(),
            target_configured=self._target_configured(),
            request_id=request_id,
        )

    async def list_backups(self, limit: int = 50, request_id: str = "") -> BackupListResponse:
        values: list[BackupMeta] = []
        for backup_id in reversed(await self._read_list(self._backup_index_key())):
            value = await self._read_json(self._backup_key(backup_id))
            if value is not None and not value.get("deleted"):
                values.append(self._backup_view(value))
            if len(values) >= limit:
                break
        return BackupListResponse(backups=values, total=len(values), request_id=request_id)

    async def list_operations(self, limit: int = 50, request_id: str = "") -> UpgradeOperationListResponse:
        values: list[dict[str, Any]] = []
        async for key in self._client().scan_iter(match=f"{UPGRADE_PREFIX}:operation:*", count=100):
            value = await self._read_json(key)
            if value is not None:
                values.append(value)
        values.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
        return UpgradeOperationListResponse(
            operations=[self._operation_view(item, request_id) for item in values[:limit]],
            total=min(len(values), limit),
            request_id=request_id,
        )

    async def operation(self, operation_id: str, request_id: str = "") -> UpgradeOperation:
        value = await self._read_json(self._operation_key(operation_id))
        if value is None:
            raise _error("operation_not_found", "Upgrade operation not found.", 404)
        return self._operation_view(value, request_id)

    async def status(self, request_id: str = "") -> UpgradeStatusResponse:
        versions = await self.current_versions()
        operations = await self.list_operations(limit=1, request_id=request_id)
        latest = operations.operations[0] if operations.operations else None
        if latest is None:
            health = "unknown"
        elif latest.state == "completed":
            health = "ok"
        elif latest.state in {"failed", "rolled_back"}:
            health = "degraded"
        else:
            health = "unknown"
        return UpgradeStatusResponse(
            app_version=versions.get("app"),
            core_version=versions.get("core"),
            health=health,
            latest_operation=latest,
            target_configured=self._target_configured(),
            request_id=request_id,
        )

    async def _create_operation(
        self,
        *,
        operation_id: str,
        artifact_type: ArtifactType,
        version: str,
        source: str,
        request_id: str,
    ) -> dict[str, Any]:
        operation = {
            "operation_id": operation_id,
            "artifact_type": artifact_type,
            "version": version,
            "state": "pending",
            "source": source,
            "request_id": request_id,
            "backup_id": None,
            "started_at": _now(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        await self._write_json(self._operation_key(operation_id), operation)
        return operation

    async def _update_operation(self, operation_id: str, **updates: Any) -> dict[str, Any]:
        operation = await self._read_json(self._operation_key(operation_id))
        if operation is None:
            raise _error("operation_not_found", "Upgrade operation not found.", 404)
        operation.update(updates)
        await self._write_json(self._operation_key(operation_id), operation)
        return operation

    def _schedule(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _require_admin_password(self, actor: AuthUser, password: str) -> None:
        if not await self._auth.verify_password(actor.id, password):
            raise _error(
                "admin_password_invalid",
                "The current admin password is invalid.",
                403,
            )

    async def start_admin_upgrade(
        self,
        artifact_type: ArtifactType,
        version: str,
        actor: AuthUser,
        *,
        admin_password: str,
        idempotency_key: str,
        request_id: str,
    ) -> UpgradeOperation:
        await self._require_admin_password(actor, admin_password)
        release = await self._release(artifact_type, version)
        idem_key = self._idempotency_key(f"admin-upgrade:{actor.id}", idempotency_key)
        idem_payload = {"artifact_type": artifact_type, "version": version}
        async with self._mutation_lock():
            previous = await self._read_json(idem_key)
            if previous is not None:
                if previous.get("idempotency_fingerprint") != self._fingerprint(idem_payload):
                    raise _error(
                        "idempotency_key_reused",
                        "The idempotency key was already used with another request.",
                        409,
                    )
                return self._operation_view(previous["result"], request_id)
            operation = await self._create_operation(
                operation_id=f"upgrade-{uuid4().hex}",
                artifact_type=artifact_type,
                version=version,
                source="admin",
                request_id=request_id,
            )
            await self._write_json(
                idem_key,
                {
                    "idempotency_fingerprint": self._fingerprint(idem_payload),
                    "result": operation,
                },
            )
        self._schedule(self._run_release(operation["operation_id"], release))
        return self._operation_view(operation, request_id)

    async def start_remote_upgrade(
        self,
        artifact_type: ArtifactType,
        body: RemoteUpgradeRequest,
        *,
        authorization: str,
        idempotency_key: str,
        request_id: str,
    ) -> UpgradeOperation:
        if body.artifact_type != artifact_type:
            raise _error("artifact_type_mismatch", "The package type does not match the request path.", 409)
        issued_at = _parse_time(body.issued_at, "issued_at")
        expires_at = _parse_time(body.expires_at, "expires_at")
        now = datetime.now(timezone.utc)
        if expires_at <= now or expires_at <= issued_at:
            raise _error("upgrade_request_expired", "The upgrade request is expired or has an invalid window.", 409)
        parsed = urlparse(body.download_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise _error("invalid_download_url", "download_url must be an HTTP(S) URL.", 422)
        idem_key = self._idempotency_key(f"sales-upgrade:{artifact_type}", idempotency_key)
        idem_payload = {
            "artifact_type": artifact_type,
            **body.model_dump(mode="json"),
        }
        async with self._mutation_lock():
            previous = await self._read_json(idem_key)
            if previous is not None:
                if previous.get("idempotency_fingerprint") != self._fingerprint(idem_payload):
                    raise _error(
                        "idempotency_key_reused",
                        "The idempotency key was already used with another request.",
                        409,
                    )
                return self._operation_view(previous["result"], request_id)
            existing_operation = await self._read_json(self._operation_key(body.operation_id))
            if existing_operation is not None:
                if (
                    existing_operation.get("artifact_type") != artifact_type
                    or existing_operation.get("version") != body.version
                ):
                    raise _error(
                        "operation_id_conflict",
                        "operation_id is already used for another upgrade.",
                        409,
                    )
                await self._write_json(
                    idem_key,
                    {
                        "idempotency_fingerprint": self._fingerprint(idem_payload),
                        "result": existing_operation,
                    },
                )
                return self._operation_view(existing_operation, request_id)
            operation = await self._create_operation(
                operation_id=body.operation_id,
                artifact_type=artifact_type,
                version=_safe_version(body.version),
                source="sales_hub",
                request_id=request_id,
            )
            await self._write_json(
                idem_key,
                {
                    "idempotency_fingerprint": self._fingerprint(idem_payload),
                    "result": operation,
                },
            )
        self._schedule(
            self._run_remote_release(
                operation["operation_id"],
                body,
                authorization,
            ),
        )
        return self._operation_view(operation, request_id)

    async def _run_remote_release(
        self,
        operation_id: str,
        body: RemoteUpgradeRequest,
        authorization: str,
    ) -> None:
        try:
            release = await self._release(body.artifact_type, body.version)
            if release.get("sha256", "").lower() != body.sha256.lower() or int(release.get("size", 0)) != body.size_bytes:
                raise _error("release_metadata_mismatch", "The stored release metadata does not match the request.", 409)
        except HTTPException as exc:
            if exc.status_code != 404:
                await self._fail_operation(operation_id, exc)
                return
            try:
                await self._update_operation(operation_id, state="downloading")
                path = await self._download_remote(body.download_url, authorization, body.size_bytes)
                size, digest = await asyncio.to_thread(_sha256_file, path)
                if size != body.size_bytes or digest.lower() != body.sha256.lower():
                    raise _error("release_checksum_mismatch", "The downloaded package failed size or SHA-256 validation.", 409)
                manifest = await self._validate_archive(path, body.artifact_type, body.version)
                self._release_root.mkdir(parents=True, exist_ok=True)
                final_path = self._release_root / f"{body.artifact_type}-{body.version}.tar.gz"
                await asyncio.to_thread(os.replace, path, final_path)
                release = await self._save_release(
                    artifact_type=body.artifact_type,
                    version=body.version,
                    path=final_path,
                    uploaded_by="sales_hub",
                    manifest=manifest,
                )
            except Exception as exc:  # task boundary: persist a safe error
                await self._fail_operation(operation_id, exc)
                return
        await self._run_release(operation_id, release)

    async def _download_remote(self, url: str, authorization: str, expected_size: int) -> Path:
        self._staging_root.mkdir(parents=True, exist_ok=True)
        path = self._staging_root / f"remote-{uuid4().hex}.tar.gz"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=5.0),
                follow_redirects=False,
            ) as client:
                response = await client.get(url, headers={"Authorization": authorization})
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > _MAX_PACKAGE_BYTES:
                    raise _error("release_too_large", "The release exceeds the configured size limit.", 413)
                if content_length and int(content_length) != expected_size:
                    raise _error("release_size_mismatch", "The download size does not match the release metadata.", 409)
                if len(response.content) > _MAX_PACKAGE_BYTES:
                    raise _error("release_too_large", "The release exceeds the configured size limit.", 413)
                await asyncio.to_thread(path.write_bytes, response.content)
        except HTTPException:
            path.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, ValueError, OSError) as exc:
            path.unlink(missing_ok=True)
            raise _error("release_download_failed", "The upgrade package could not be downloaded.", 502) from exc
        return path

    async def _fail_operation(self, operation_id: str, error: Exception) -> None:
        if isinstance(error, HTTPException) and isinstance(error.detail, dict):
            detail = error.detail
        else:
            detail = {"code": "upgrade_failed", "message": str(error)}
        await self._update_operation(
            operation_id,
            state="failed",
            finished_at=_now(),
            error=detail,
        )

    async def _run_release(self, operation_id: str, release: dict[str, Any]) -> None:
        artifact_type: ArtifactType = release["type"]
        backup: dict[str, Any] | None = None
        try:
            await self._update_operation(operation_id, state="backing_up")
            backup = await self._create_backup(operation_id, release)
            await self._update_operation(operation_id, state="applying", backup_id=backup["backup_id"])
            result = await self._apply_files(release, backup)
            await self._update_operation(operation_id, state="health_check")
            health = await self._restart_and_check()
            if not health["ok"]:
                await self._restore_backup(backup)
                await self._update_operation(
                    operation_id,
                    state="rolled_back",
                    finished_at=_now(),
                    result={"apply": result, "health": health, "rollback": "completed"},
                    error={"code": "health_check_failed", "message": "Health check failed; the previous release was restored."},
                )
                return
            installed = await self._write_installed_version(artifact_type, release["version"])
            await self._update_operation(
                operation_id,
                state="completed",
                finished_at=_now(),
                result={"apply": result, "health": health, "installed_version": installed},
            )
        except Exception as exc:  # task boundary: never lose operation state
            if backup is not None:
                try:
                    await self._restore_backup(backup)
                    await self._update_operation(
                        operation_id,
                        state="rolled_back",
                        finished_at=_now(),
                        result={"rollback": "completed", "backup_id": backup["backup_id"]},
                        error={"code": "upgrade_failed_rolled_back", "message": str(exc)},
                    )
                    return
                except Exception as rollback_error:
                    await self._fail_operation(
                        operation_id,
                        _error(
                            "rollback_failed",
                            f"Upgrade failed and automatic rollback also failed: {rollback_error}",
                            500,
                        ),
                    )
                    return
            await self._fail_operation(operation_id, exc)

    async def _create_backup(self, operation_id: str, release: dict[str, Any]) -> dict[str, Any]:
        artifact_type: ArtifactType = release["type"]
        target = self._target_path(artifact_type)
        backup_id = f"backup-{artifact_type}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:10]}"
        backup_dir = self._backup_root / artifact_type / backup_id
        payload_dir = backup_dir / "payload"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_dir():
                raise _error("upgrade_target_invalid", "The configured upgrade target is not a directory.", 409)
            await asyncio.to_thread(shutil.copytree, target, payload_dir)
        else:
            payload_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "backup_id": backup_id,
            "artifact_type": artifact_type,
            "version": release["version"],
            "path": str(backup_dir),
            "operation_id": operation_id,
            "created_at": _now(),
            "target_existed": target.exists(),
            "size_bytes": await asyncio.to_thread(_directory_size, payload_dir),
        }
        await self._write_json(self._backup_key(backup_id), metadata)
        await self._client().rpush(self._backup_index_key(), backup_id)
        return metadata

    async def _extract_release(self, release: dict[str, Any], operation_id: str) -> Path:
        path = self._release_path(release)
        if not path.is_file():
            raise _error("release_file_missing", "The release archive is missing from releases/.", 404)
        size, digest = await asyncio.to_thread(_sha256_file, path)
        if size != int(release["size"]) or digest.lower() != str(release["sha256"]).lower():
            raise _error("release_checksum_mismatch", "The stored release failed SHA-256 validation.", 409)
        await self._validate_archive(path, release["type"], release["version"])
        staging = self._staging_root / f"apply-{operation_id}"
        if staging.exists():
            await asyncio.to_thread(shutil.rmtree, staging)
        staging.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, mode="r:gz") as archive:
            archive.extractall(staging)
        entries = list(staging.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return staging

    async def _apply_files(self, release: dict[str, Any], backup: dict[str, Any]) -> dict[str, object]:
        target = self._target_path(release["type"])
        migration_required = bool(release.get("manifest", {}).get("requires_migration", False))
        if migration_required and not os.getenv("LONGXIN_UPGRADE_MIGRATION_COMMAND"):
            raise _error(
                "migration_not_configured",
                "The release requires a database migration, but LONGXIN_UPGRADE_MIGRATION_COMMAND is not configured.",
                503,
            )
        payload = await self._extract_release(release, backup["operation_id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        old_path = payload.parent / "previous-target"
        if old_path.exists():
            await asyncio.to_thread(shutil.rmtree, old_path)
        target_existed = target.exists()
        try:
            if target_existed:
                await asyncio.to_thread(target.rename, old_path)
            await asyncio.to_thread(payload.rename, target)
        except Exception:
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            if old_path.exists():
                await asyncio.to_thread(old_path.rename, target)
            raise
        if old_path.exists():
            await asyncio.to_thread(shutil.rmtree, old_path)
        return {
            "target_dir": str(target),
            "backup_id": backup["backup_id"],
            "migration": "configured" if migration_required else "not_required",
        }

    async def _restart_and_check(self) -> dict[str, object]:
        restart_command = os.getenv("LONGXIN_UPGRADE_RESTART_COMMAND")
        restart_state = "not_configured"
        if restart_command:
            process = await asyncio.create_subprocess_shell(restart_command)
            try:
                return_code = await asyncio.wait_for(process.wait(), timeout=60)
            except asyncio.TimeoutError as exc:
                process.kill()
                raise _error("restart_timeout", "The configured restart command timed out.", 504) from exc
            if return_code != 0:
                raise _error("restart_failed", "The configured restart command failed.", 502)
            restart_state = "completed"
        health_url = os.getenv("LONGXIN_UPGRADE_HEALTHCHECK_URL", "http://127.0.0.1:8001/health")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(health_url)
            healthy = response.status_code in {200, 204}
        except httpx.HTTPError:
            healthy = False
        return {
            "ok": healthy,
            "restart": restart_state,
            "health_url": health_url,
            "status_code": response.status_code if "response" in locals() else None,
        }

    async def _write_installed_version(self, artifact_type: ArtifactType, version: str) -> str:
        await self._write_json(
            f"{UPGRADE_PREFIX}:installed:{artifact_type}",
            {"artifact_type": artifact_type, "version": version, "updated_at": _now()},
        )
        return version

    async def _restore_backup(self, backup: dict[str, Any]) -> None:
        path = Path(backup["path"]).resolve()
        if self._backup_root not in path.parents:
            raise _error("invalid_backup_path", "The backup path is outside backups/.", 500)
        target = self._target_path(backup["artifact_type"])
        payload = path / "payload"
        if not payload.is_dir():
            raise _error("backup_missing", "The backup payload is missing.", 404)
        target_existed = bool(backup.get("target_existed", True))
        restore_stage = self._staging_root / f"restore-{uuid4().hex}"
        await asyncio.to_thread(shutil.copytree, payload, restore_stage)
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target)
        if not target_existed:
            await asyncio.to_thread(shutil.rmtree, restore_stage)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(restore_stage.rename, target)

    async def start_rollback(
        self,
        backup_id: str,
        actor: AuthUser,
        *,
        admin_password: str,
        idempotency_key: str,
        request_id: str,
    ) -> UpgradeOperation:
        await self._require_admin_password(actor, admin_password)
        backup = await self._read_json(self._backup_key(backup_id))
        if backup is None:
            raise _error("backup_not_found", "The requested backup was not found.", 404)
        idem_key = self._idempotency_key(f"rollback:{actor.id}", idempotency_key)
        idem_payload = {"backup_id": backup_id}
        async with self._mutation_lock():
            previous = await self._read_json(idem_key)
            if previous is not None:
                if previous.get("idempotency_fingerprint") != self._fingerprint(idem_payload):
                    raise _error(
                        "idempotency_key_reused",
                        "The idempotency key was already used with another request.",
                        409,
                    )
                return self._operation_view(previous["result"], request_id)
            operation = await self._create_operation(
                operation_id=f"rollback-{uuid4().hex}",
                artifact_type=backup["artifact_type"],
                version=backup["version"],
                source="admin",
                request_id=request_id,
            )
            operation["backup_id"] = backup_id
            await self._write_json(self._operation_key(operation["operation_id"]), operation)
            await self._write_json(
                idem_key,
                {
                    "idempotency_fingerprint": self._fingerprint(idem_payload),
                    "result": operation,
                },
            )
        self._schedule(self._run_rollback(operation["operation_id"], backup))
        return self._operation_view(operation, request_id)

    async def delete_backup(
        self,
        backup_id: str,
        actor: AuthUser,
        body: BackupDeleteRequest,
        *,
        idempotency_key: str,
        request_id: str,
    ) -> None:
        if not body.confirm:
            raise _error("confirmation_required", "Explicit confirmation is required.", 400)
        idem_key = self._idempotency_key(f"delete-backup:{actor.id}", idempotency_key)
        idem_payload = {
            "operation": "delete_backup",
            "backup_id": backup_id,
            "confirm": body.confirm,
            "reason": body.reason,
        }
        async with self._mutation_lock():
            previous = await self._read_json(idem_key)
            if previous is not None:
                if previous.get("idempotency_fingerprint") != self._fingerprint(idem_payload):
                    raise _error(
                        "idempotency_key_reused",
                        "The idempotency key was already used with another request.",
                        409,
                    )
                return
            backup = await self._read_json(self._backup_key(backup_id))
            if backup is None or backup.get("deleted"):
                raise _error("backup_not_found", "The requested backup was not found.", 404)
            operation = await self._read_json(self._operation_key(str(backup.get("operation_id", ""))))
            if operation and operation.get("state") in {
                "pending",
                "downloading",
                "backing_up",
                "applying",
                "health_check",
            }:
                raise _error("backup_in_use", "The backup belongs to an active upgrade operation.", 409)
            path = Path(str(backup.get("path", ""))).resolve()
            if self._backup_root not in path.parents or path == self._backup_root:
                raise _error("invalid_backup_path", "The backup path is outside backups/.", 500)
            if path.exists():
                if not path.is_dir():
                    raise _error("invalid_backup_path", "The backup path is not a directory.", 500)
                await asyncio.to_thread(shutil.rmtree, path)
            client = self._client()
            if hasattr(client, "lrem"):
                await client.lrem(self._backup_index_key(), 0, backup_id)
            backup["deleted"] = True
            backup["deleted_at"] = _now()
            await self._write_json(self._backup_key(backup_id), backup)
            event = {
                "event_id": f"evt-{uuid4().hex}",
                "tenant_id": (
                    str(get_bound_tenant_id())
                    if get_bound_tenant_id() is not None
                    else actor.tenant_id
                ),
                "actor_membership_id": (
                    actor.membership_id
                    or (
                        str(get_bound_membership_id())
                        if get_bound_membership_id() is not None
                        else actor.id
                    )
                ),
                "actor_type": "admin",
                "actor_id": actor.id,
                "actor_name": actor.username,
                "target_user_id": None,
                "target_user_name": None,
                "action": "backup.delete",
                "resource_type": "backup",
                "resource_id": backup_id,
                "resource": {"type": "backup", "id": backup_id},
                "reason": body.reason,
                "request_id": request_id,
                "status": "completed",
                "result_summary": "Backup metadata and payload removed.",
                "created_at": _now(),
            }
            store = self._audit_store()
            if store is not None:
                try:
                    await store.record(event)
                except Exception:
                    pass
            await client.rpush(
                self._audit_key(),
                json.dumps(event, ensure_ascii=False),
            )
            await self._write_json(
                idem_key,
                {
                    "idempotency_fingerprint": self._fingerprint(idem_payload),
                    "result": {"state": "completed", "backup_id": backup_id},
                },
            )

    async def _run_rollback(self, operation_id: str, backup: dict[str, Any]) -> None:
        try:
            await self._update_operation(operation_id, state="applying")
            await self._restore_backup(backup)
            await self._update_operation(operation_id, state="health_check")
            health = await self._restart_and_check()
            if not health["ok"]:
                raise _error("health_check_failed", "Health check failed after rollback.", 502)
            await self._update_operation(
                operation_id,
                state="completed",
                finished_at=_now(),
                result={"rollback": "completed", "health": health, "restored_backup_id": backup["backup_id"]},
            )
        except Exception as exc:
            await self._fail_operation(operation_id, exc)
