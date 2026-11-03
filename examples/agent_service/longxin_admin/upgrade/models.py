"""API contracts for the isolated upgrade and backup module."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ArtifactType = Literal["app", "core"]
UpgradeState = Literal[
    "pending",
    "downloading",
    "backing_up",
    "applying",
    "health_check",
    "completed",
    "failed",
    "rolled_back",
]


class ReleaseMeta(BaseModel):
    type: ArtifactType
    version: str
    file: str
    size: int
    sha256: str
    uploaded_at: str
    uploaded_by: str
    manifest: dict[str, object] = Field(default_factory=dict)


class ReleaseCatalogResponse(BaseModel):
    app: ReleaseMeta | None = None
    core: ReleaseMeta | None = None
    releases: list[ReleaseMeta] = Field(default_factory=list)
    installed_versions: dict[str, str | None] = Field(default_factory=dict)
    target_configured: dict[str, bool] = Field(default_factory=dict)
    request_id: str = ""


class UploadReleaseResponse(ReleaseMeta):
    request_id: str = ""


class AdminApplyRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    admin_password: str = Field(min_length=1, max_length=1024)


class RemoteUpgradeRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    artifact_type: ArtifactType
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    size_bytes: int = Field(gt=0)
    download_url: str = Field(min_length=1, max_length=2048)
    issued_at: str
    expires_at: str


class RollbackRequest(BaseModel):
    backup_id: str = Field(min_length=1, max_length=160)
    admin_password: str = Field(min_length=1, max_length=1024)


class BackupRestoreRequest(BaseModel):
    confirm: bool = False
    admin_password: str = Field(min_length=1, max_length=1024)


class BackupDeleteRequest(BaseModel):
    confirm: bool = False
    reason: str = Field(min_length=4, max_length=300)


class BackupMeta(BaseModel):
    backup_id: str
    artifact_type: ArtifactType
    version: str
    path: str
    operation_id: str
    created_at: str
    size_bytes: int = 0


class BackupListResponse(BaseModel):
    backups: list[BackupMeta] = Field(default_factory=list)
    total: int
    request_id: str = ""


class UpgradeOperation(BaseModel):
    operation_id: str
    artifact_type: ArtifactType
    version: str
    state: UpgradeState
    source: Literal["admin", "sales_hub"]
    request_id: str = ""
    backup_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None


class UpgradeOperationListResponse(BaseModel):
    operations: list[UpgradeOperation] = Field(default_factory=list)
    total: int
    request_id: str = ""


class UpgradeStatusResponse(BaseModel):
    app_version: str | None = None
    core_version: str | None = None
    health: Literal["ok", "degraded", "unknown"] = "unknown"
    latest_operation: UpgradeOperation | None = None
    target_configured: dict[str, bool] = Field(default_factory=dict)
    request_id: str = ""
