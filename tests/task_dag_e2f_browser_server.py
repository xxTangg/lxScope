"""Loopback-only, isolated S01 browser acceptance service for DAG-E2-F.

Run only with explicit disposable PostgreSQL/Redis URLs and the existing
Logto configuration copied from the authorized local test service. The app
mounts the real Task router and auth router; its model and KB adapters are
deterministic synthetic fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_ROOT = Path(__file__).resolve().parents[1]
_AGENT_SERVICE = _ROOT / "examples" / "agent_service"
sys.path.insert(0, str(_AGENT_SERVICE))
sys.path.insert(0, str(_ROOT / "tests"))

import task_dag_e2_non_linear_s01_test as e2  # noqa: E402
import task_r7_nine_scenarios_integration_test as r7  # noqa: E402
import task_sop_r6_integration_test as r6  # noqa: E402

from agentscope.app.deps import get_current_user_id  # noqa: E402
from auth import load_auth_from_env  # noqa: E402
from examples.agent_service.task._service import TaskService  # noqa: E402
from examples.agent_service.task._template_models import (  # noqa: E402
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry  # noqa: E402
from examples.agent_service.task._template_service import TaskTemplateService  # noqa: E402
from examples.agent_service.task._router import task_router  # noqa: E402
from examples.agent_service.task._executor import StepExecutionResult  # noqa: E402
from redis.asyncio import Redis  # noqa: E402


_AUTH_REDIS_URL = os.getenv("LXSCOPE_TASK_E2F_AUTH_REDIS_URL", "")
_CONTROL_SECRET = os.getenv("LXSCOPE_TASK_E2F_CONTROL_SECRET", "")
_UI_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:18000",
    "http://127.0.0.1:18000",
]


def _validate_isolation() -> None:
    if os.getenv("LXSCOPE_TASK_E2F_BROWSER") != "1":
        raise RuntimeError("Set LXSCOPE_TASK_E2F_BROWSER=1 to start this test-only app.")
    if not _AUTH_REDIS_URL or not _CONTROL_SECRET:
        raise RuntimeError("Isolated auth Redis URL and test-control secret are required.")
    if not os.getenv("LXSCOPE_TASK_TEST_DATABASE_URL"):
        raise RuntimeError("An isolated disposable PostgreSQL test URL is required.")
    from urllib.parse import urlsplit

    auth = urlsplit(_AUTH_REDIS_URL)
    if auth.hostname not in {"127.0.0.1", "localhost", "::1"} or auth.path != "/13":
        raise RuntimeError("Browser auth storage must use isolated loopback Redis database /13.")
    if os.getenv("LXSCOPE_TASK_TEST_REDIS_ISOLATED") != "1":
        raise RuntimeError("Explicit isolated Redis confirmation is required.")


class _RedisAuthStorage:
    def __init__(self, client: Redis) -> None:
        self._client = client

    def get_client(self) -> Redis:
        return self._client


class _SyntheticKnowledgeBase:
    """Test-only authorized KB port; never calls a real gateway or MCP."""

    def __init__(self) -> None:
        self.owner_id: str | None = None
        self.calls: list[dict[str, str]] = []
        self.active = 0
        self.max_active = 0
        self._source_text = (
            "R8 E2-F 合成项目资料：项目名称、建设范围和背景均为演示数据，"
            "所有建议仅供草稿审阅，不构成正式立项结论。"
        )
        self._view = SimpleNamespace(
            id=r7.KB_ID,
            name="R8 E2-F 合成授权知识库",
            owner_id="isolated-test-owner",
            description="仅含合成 S01 项目资料，不连接真实知识库。",
            document_count=1,
            chunk_count=1,
            status_counts=SimpleNamespace(ready=1),
        )
        self._record = SimpleNamespace(
            id=r7.SOURCE_ID,
            status="ready",
            updated_at=datetime.now(timezone.utc),
            data=SimpleNamespace(filename="R8-E2F-合成项目资料.md"),
        )

    def _check_user(self, user_id: str) -> None:
        if self.owner_id is None:
            self.owner_id = user_id
        if user_id != self.owner_id:
            raise PermissionError("Synthetic S01 KB is scoped to the first authenticated test identity.")

    async def list_knowledge_base_views(
        self,
        user_id: str,
        *,
        knowledge_base_id: str | None = None,
        page: int = 1,
        page_size: int = 128,
    ) -> tuple[list[Any], int]:
        self._check_user(user_id)
        if knowledge_base_id is not None and knowledge_base_id != r7.KB_ID:
            return [], 0
        if page != 1:
            return [], 1
        return [self._view], 1

    async def get_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_ids: list[str],
    ) -> list[Any]:
        self._check_user(user_id)
        if knowledge_base_id != r7.KB_ID:
            return []
        return [self._record] if r7.SOURCE_ID in document_ids else []

    async def search(
        self,
        user_id: str,
        knowledge_base_id: str,
        query: str,
        *,
        top_k: int,
    ) -> list[Any]:
        self._check_user(user_id)
        if knowledge_base_id != r7.KB_ID or not query.strip() or top_k < 1:
            raise ValueError("Synthetic KB request has an invalid scope or query.")
        capability = "project.rule_check" if "规则" in query or "办法" in query else "project.similar_search"
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        status = "succeeded"
        try:
            # Keep the two read-only calls independently scheduled and visible.
            await asyncio.sleep(0.15)
            return [
                SimpleNamespace(
                    document_id=r7.SOURCE_ID,
                    score=0.91,
                    chunk=SimpleNamespace(
                        content=SimpleNamespace(text=self._source_text),
                        chunk_index=0,
                        total_chunks=1,
                        source="R8-E2F-合成项目资料.md",
                    ),
                ),
            ]
        finally:
            self.calls.append(
                {
                    "user_id": user_id,
                    "knowledge_base_id": knowledge_base_id,
                    "capability": capability,
                    "status": status,
                    "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                },
            )
            self.active -= 1


class _BrowserExecutor(r7._SyntheticModelAndWorkspace):
    """Deterministic local model and durable test artifact writer."""

    def __init__(self, inputs: dict[str, Any], root: Path) -> None:
        super().__init__(e2.SCENARIO_ID, inputs)
        self.root = root
        self.calls_log = root / "executor-calls.jsonl"
        self._pause_used = False
        self._draft_failure_used = False

    def _artifact_path(self, path: str) -> Path:
        suffix = Path(path).suffix[:16]
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
        target = (self.root / f"{digest}{suffix}").resolve()
        if self.root not in target.parents:
            raise ValueError("Artifact escaped the isolated browser-test directory.")
        return target

    async def execute_node(self, *, node, previous_output, context):
        with self.calls_log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "run_id": context.run_id,
                        "node_id": node.id,
                        "node_order": node.order,
                    },
                )
                + "\n",
            )
        if node.order == 1 and not self._pause_used:
            self._pause_used = True
            self.entered.set()
            await self.release.wait()
        if node.order == 4 and not self._draft_failure_used:
            self._draft_failure_used = True
            return StepExecutionResult(
                text="E2-F 一次性合成可重试故障。",
                status="failed",
                reason="E2-F 一次性合成可重试故障。",
            )
        return await super().execute_node(
            node=node,
            previous_output=previous_output,
            context=context,
        )

    async def write_artifact(self, *, path: str, data: bytes, context):
        del context
        if not data:
            raise ValueError("A synthetic artifact cannot be empty.")
        self._artifact_path(path).write_bytes(data)
        self.artifacts[path] = data
        return len(data)

    async def read_artifact(self, *, path: str, context):
        del context
        data = self._artifact_path(path).read_bytes()
        self.artifacts[path] = data
        return data


_validate_isolation()
_auth_redis = Redis.from_url(_AUTH_REDIS_URL, decode_responses=True)
_auth = load_auth_from_env(storage=_RedisAuthStorage(_auth_redis))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    backend = await r6._Backend(
        "postgres",
        os.environ["LXSCOPE_TASK_TEST_DATABASE_URL"],
    ).open()
    temporary = tempfile.TemporaryDirectory(prefix="task-e2f-browser-")
    root = Path(temporary.name).resolve()
    registry = TemplateRegistry()
    template = registry.get_template_version(e2.SCENARIO_ID, e2.SCENARIO_VERSION)
    if template is None:
        await backend.close()
        temporary.cleanup()
        raise RuntimeError("The exact authorized S01 template version is unavailable.")
    inputs = r7._inputs_for(e2.SCENARIO_ID, template)
    executor = _BrowserExecutor(inputs, root)
    service = TaskService(
        backend.store,
        preview_executor=executor,
        artifact_writer=executor,
        enable_dag_scheduler=True,
        enable_dag_editing=True,
        dag_scenario_test_allowlist={(e2.SCENARIO_ID, e2.SCENARIO_VERSION)},
    )
    app.state.task_store = backend.store
    app.state.task_service = service
    app.state.task_template_registry = registry
    app.state.task_inputs = inputs
    app.state.knowledge_base_service = _SyntheticKnowledgeBase()
    app.state.e2f_executor = executor
    app.state.e2f_root = root
    app.state.e2f_backend = backend
    app.state.e2f_owner = None
    try:
        yield
    finally:
        await service.shutdown()
        await backend.close()
        await _auth_redis.aclose()
        temporary.cleanup()


app = FastAPI(title="Task DAG E2-F isolated browser acceptance", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_UI_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-E2F-Test-Control",
        "X-Request-ID",
    ],
)
app.include_router(_auth.router)
app.include_router(task_router)
app.dependency_overrides[get_current_user_id] = _auth.get_current_user_id


@app.get("/health")
async def _health() -> dict[str, Any]:
    return {"version": "e2f-isolated-test", "components": {"api": "ready", "task": "ready"}}


@app.post("/__e2f/release-profile")
async def _release_profile(
    request: Request,
    test_control: str | None = Header(default=None, alias="X-E2F-Test-Control"),
) -> dict[str, str]:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Loopback only.")
    if not _CONTROL_SECRET or test_control != _CONTROL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid isolated test-control token.")
    app.state.e2f_executor.release.set()
    return {"released": "profile-node"}


@app.get("/__e2f/diagnostics")
async def _diagnostics(
    _user_id: str = Depends(_auth.get_current_user_id),
) -> JSONResponse:
    kb = app.state.knowledge_base_service
    return JSONResponse(
        {
            "service": "isolated-e2f-test",
            "database": "temporary-postgresql",
            "knowledge_base": "synthetic-only",
            "owner_id": app.state.e2f_owner,
            "knowledge_calls": kb.calls,
            "knowledge_max_concurrency": kb.max_active,
            "executor_calls": (
                app.state.e2f_executor.calls_log.read_text(encoding="utf-8").splitlines()
                if app.state.e2f_executor.calls_log.exists()
                else []
            ),
        },
    )

