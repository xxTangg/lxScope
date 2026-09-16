# -*- coding: utf-8 -*-
"""Contract tests for the local administrator upgrade/backup module."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase

from fastapi import UploadFile

SERVICE_DIR = Path(__file__).parents[1] / "examples" / "agent_service"
sys.path.insert(0, str(SERVICE_DIR))

from auth import AuthUser, JWTAuthService  # noqa: E402
from longxin_admin.upgrade.service import UpgradeService  # noqa: E402


class _MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, *, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def rpush(self, key: str, value: Any) -> int:
        self.values.setdefault(key, [])
        self.values[key].append(value)
        return len(self.values[key])

    async def lrange(self, key: str, start: int, end: int) -> list[Any]:
        values = self.values.get(key, [])
        stop = None if end == -1 else end + 1
        return values[start:stop]

    async def scan_iter(self, *, match: str, count: int = 100):
        del count
        prefix, _, suffix = match.partition("*")
        for key in self.values:
            if key.startswith(prefix) and key.endswith(suffix):
                yield key


class _Storage:
    def __init__(self, redis: _MemoryRedis) -> None:
        self._redis = redis

    def get_client(self) -> _MemoryRedis:
        return self._redis


class UpgradeTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.data_dir = root / "longxin-data"
        self.target_dir = root / "installed-app"
        self.target_dir.mkdir()
        (self.target_dir / "old.txt").write_text("before", encoding="utf-8")
        self.previous_data_dir = os.environ.get("LONGXIN_DATA_DIR")
        self.previous_app_target = os.environ.get("LONGXIN_APP_TARGET_DIR")
        os.environ["LONGXIN_DATA_DIR"] = str(self.data_dir)
        os.environ["LONGXIN_APP_TARGET_DIR"] = str(self.target_dir)
        self.redis = _MemoryRedis()
        self.storage = _Storage(self.redis)
        self.auth = JWTAuthService({"admin": ("admin-id", "admin-password", "admin")}, "a" * 32, storage=self.storage)
        self.service = UpgradeService(self.storage, self.auth)

        async def healthy() -> dict[str, object]:
            return {"ok": True, "restart": "test", "health_url": "test", "status_code": 204}

        self.service._restart_and_check = healthy  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("LONGXIN_DATA_DIR", None)
        else:
            os.environ["LONGXIN_DATA_DIR"] = self.previous_data_dir
        if self.previous_app_target is None:
            os.environ.pop("LONGXIN_APP_TARGET_DIR", None)
        else:
            os.environ["LONGXIN_APP_TARGET_DIR"] = self.previous_app_target
        self.temp_dir.cleanup()

    async def test_upload_apply_backup_and_rollback(self) -> None:
        root = Path(self.temp_dir.name)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps({"artifact_type": "app", "version": "3.0.4"}),
            encoding="utf-8",
        )
        package = root / "app-3.0.4.tar.gz"
        with tarfile.open(package, "w:gz") as archive:
            archive.add(manifest, arcname="manifest.json")
            archive.add(self._write_file(root / "new.txt", "after"), arcname="new.txt")

        admin = AuthUser(id="admin-id", username="admin", role="admin")
        with package.open("rb") as stream:
            release = await self.service.upload_release(
                "app",
                "3.0.4",
                UploadFile(filename=package.name, file=stream),
                admin,
                idempotency_key="upload-1",
            )
        self.assertEqual(release.version, "3.0.4")

        operation = await self.service.start_admin_upgrade(
            "app",
            "3.0.4",
            admin,
            idempotency_key="apply-1",
            request_id="req-1",
        )
        await asyncio.sleep(0.1)
        completed = await self.service.operation(operation.operation_id)
        self.assertEqual(completed.state, "completed")
        self.assertTrue((self.target_dir / "new.txt").exists())
        self.assertFalse((self.target_dir / "old.txt").exists())

        backups = await self.service.list_backups()
        self.assertEqual(backups.total, 1)
        rollback = await self.service.start_rollback(
            backups.backups[0].backup_id,
            admin,
            idempotency_key="rollback-1",
            request_id="req-2",
        )
        await asyncio.sleep(0.1)
        rollback_result = await self.service.operation(rollback.operation_id)
        self.assertEqual(rollback_result.state, "completed")
        self.assertTrue((self.target_dir / "old.txt").exists())
        self.assertFalse((self.target_dir / "new.txt").exists())

    @staticmethod
    def _write_file(path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path
