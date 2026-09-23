"""FastAPI routes for administrator upgrades and Sales Hub callbacks."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile

from auth import AuthUser

from .models import (
    AdminApplyRequest,
    BackupDeleteRequest,
    BackupListResponse,
    BackupRestoreRequest,
    ReleaseCatalogResponse,
    RemoteUpgradeRequest,
    RollbackRequest,
    UploadReleaseResponse,
    UpgradeOperation,
    UpgradeOperationListResponse,
    UpgradeStatusResponse,
)
from .service import UpgradeService, _error


upgrade_router = APIRouter(tags=["longxin-upgrades"])


def _service(request: Request) -> UpgradeService:
    service = getattr(request.app.state, "upgrade_service", None)
    if service is None:
        raise _error("upgrade_not_configured", "Upgrade service is not configured.", 503)
    return service


async def _current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthUser:
    return await request.app.state.auth.get_current_user(authorization)


async def _require_admin(user: AuthUser = Depends(_current_user)) -> AuthUser:
    if user.role != "admin" or user.status != "active":
        raise _error("admin_required", "Administrator access is required.", 403)
    return user


def _artifact_type(value: str) -> str:
    if value not in {"app", "core"}:
        raise _error("invalid_artifact_type", "artifact_type must be app or core.", 422)
    return value


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("X-Request-ID", "")


@upgrade_router.get("/admin/upgrades", response_model=ReleaseCatalogResponse)
async def admin_releases(
    request: Request,
    _: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> ReleaseCatalogResponse:
    return await service.list_releases(_request_id(request))


@upgrade_router.get("/admin/upgrade/status", response_model=UpgradeStatusResponse)
async def admin_upgrade_status(
    request: Request,
    _: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UpgradeStatusResponse:
    return await service.status(_request_id(request))


@upgrade_router.post("/admin/upgrades/releases/{artifact_type}", response_model=UploadReleaseResponse, status_code=201)
async def admin_upload_release(
    artifact_type: str,
    request: Request,
    version: str = Query(min_length=1, max_length=64),
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UploadReleaseResponse:
    artifact_type = _artifact_type(artifact_type)
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    result = await service.upload_release(
        artifact_type,
        version,
        file,
        actor,
        idempotency_key=idempotency_key,
    )
    return UploadReleaseResponse(**result.model_dump(), request_id=_request_id(request))


@upgrade_router.post("/admin/upgrades/{artifact_type}/apply", response_model=UpgradeOperation)
async def admin_apply_release(
    artifact_type: str,
    body: AdminApplyRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UpgradeOperation:
    artifact_type = _artifact_type(artifact_type)
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.start_admin_upgrade(
        artifact_type,
        body.version,
        actor,
        admin_password=body.admin_password,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@upgrade_router.get("/admin/upgrades/backups", response_model=BackupListResponse)
async def admin_backups(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> BackupListResponse:
    return await service.list_backups(limit, _request_id(request))


@upgrade_router.post("/admin/upgrades/rollback", response_model=UpgradeOperation)
async def admin_rollback(
    body: RollbackRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UpgradeOperation:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.start_rollback(
        body.backup_id,
        actor,
        admin_password=body.admin_password,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@upgrade_router.post(
    "/admin/backups/{backup_id}/restore",
    response_model=UpgradeOperation,
)
async def admin_restore_backup(
    backup_id: str,
    body: BackupRestoreRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UpgradeOperation:
    if not body.confirm:
        raise _error("confirmation_required", "Explicit confirmation is required.", 400)
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.start_rollback(
        backup_id,
        actor,
        admin_password=body.admin_password,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@upgrade_router.delete("/admin/backups/{backup_id}", status_code=204)
async def admin_delete_backup(
    backup_id: str,
    body: BackupDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> None:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    await service.delete_backup(
        backup_id,
        actor,
        body,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@upgrade_router.get("/admin/upgrades/operations", response_model=UpgradeOperationListResponse)
async def admin_operations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UpgradeOperationListResponse:
    return await service.list_operations(limit, _request_id(request))


@upgrade_router.get("/admin/upgrades/operations/{operation_id}", response_model=UpgradeOperation)
async def admin_operation(
    operation_id: str,
    request: Request,
    _: AuthUser = Depends(_require_admin),
    service: UpgradeService = Depends(_service),
) -> UpgradeOperation:
    return await service.operation(operation_id, _request_id(request))


@upgrade_router.post("/integration/sales/v1/upgrades/{artifact_type}", response_model=UpgradeOperation)
async def sales_hub_upgrade(
    artifact_type: str,
    body: RemoteUpgradeRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: UpgradeService = Depends(_service),
) -> UpgradeOperation:
    artifact_type = _artifact_type(artifact_type)
    authorizer = getattr(request.app.state, "sales_hub_authorizer", None)
    if authorizer is None:
        raise _error("sales_hub_not_configured", "Sales Hub authorization is not configured.", 503)
    await authorizer(authorization)
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    if not authorization:
        raise _error("invalid_customer_token", "The Sales Hub token is invalid.", 401)
    return await service.start_remote_upgrade(
        artifact_type,
        body,
        authorization=authorization,
        idempotency_key=idempotency_key,
        request_id=_request_id(request) or f"req-{uuid4().hex}",
    )
