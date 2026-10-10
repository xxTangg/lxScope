"""Task-owned RAG and citation integration tests using isolated fixtures.

These tests exercise the Task adapter contract. They do not claim live
knowledge-base or authenticated Workspace integration.
"""

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from examples.agent_service.task._citation_verifier import (
    verify_task_citations,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._knowledge import TaskKnowledgeGateway
from examples.agent_service.task._models import (
    ArtifactConfig,
    ArtifactFormat,
    RunStatus,
    TaskContext,
    TaskInputReply,
    TaskPreflightRequest,
    TaskRunRequest,
    TaskToolSchema,
    UpdateTaskRequest,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._router import task_router
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_models import (
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService


_OWNER = "rag-test-owner"
_KB_ID = "kb-authorized"
_DOCUMENT_ID = "doc-planning"
_SOURCE_TEXT = "项目策划应围绕项目目标、实施路径和投资测算形成可核验方案。"
_CHUNK_HASH = hashlib.sha256(_SOURCE_TEXT.encode("utf-8")).hexdigest()


def _view():
    return SimpleNamespace(
        id=_KB_ID,
        name="授权项目资料",
        description="",
        document_count=1,
        chunk_count=1,
        status_counts=SimpleNamespace(ready=1),
    )


def _document():
    return SimpleNamespace(
        id=_DOCUMENT_ID,
        status="ready",
        updated_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        data=SimpleNamespace(filename="项目策划依据.md"),
    )


def _search_result():
    return SimpleNamespace(
        document_id=_DOCUMENT_ID,
        score=0.93,
        chunk=SimpleNamespace(
            content=SimpleNamespace(text=_SOURCE_TEXT),
            source="项目策划依据.md",
            chunk_index=0,
            total_chunks=1,
        ),
    )


class _KnowledgeBaseFixture:
    """Minimal shape of the existing authorized KnowledgeBaseService API."""

    def __init__(self, *, owner: str = _OWNER, search_error: bool = False):
        self.owner = owner
        self.search_error = search_error
        self.view_calls = []
        self.search_calls = []
        self.document_calls = []
        self.view = _view()

    async def list_knowledge_base_views(
        self,
        user_id,
        *,
        knowledge_base_id=None,
        page=1,
        page_size=128,
    ):
        self.view_calls.append((user_id, knowledge_base_id, page, page_size))
        if user_id != self.owner or knowledge_base_id != _KB_ID:
            return [], 0
        return [self.view], 1

    async def search(self, user_id, knowledge_base_id, query, top_k=5):
        self.search_calls.append((user_id, knowledge_base_id, query, top_k))
        if self.search_error:
            raise RuntimeError("fixture search failure")
        if user_id != self.owner or knowledge_base_id != _KB_ID:
            return []
        return [_search_result()]

    async def get_document_status(
        self,
        user_id,
        knowledge_base_id,
        document_ids,
    ):
        self.document_calls.append(
            (user_id, knowledge_base_id, list(document_ids))
        )
        if user_id != self.owner or knowledge_base_id != _KB_ID:
            return []
        return [_document()] if _DOCUMENT_ID in document_ids else []


class _ArtifactExecutor:
    def __init__(self):
        self.agent_calls = []
        self.files = {}
        self.tool_listing_calls = 0

    async def list_tools(self, context):
        del context
        self.tool_listing_calls += 1
        return [
            TaskToolSchema(
                name="planning.template_retrieve",
                input_schema={"type": "object"},
                is_read_only=True,
            ),
            TaskToolSchema(
                name="document.citation_verify",
                input_schema={"type": "object"},
                is_read_only=True,
            ),
        ]

    async def execute_node(self, *, node, previous_output, context):
        del context
        self.agent_calls.append(node.name)
        self.assert_citation_prompt(node.prompt)
        output = (
            "# 项目策划初稿\n\n"
            f"{_SOURCE_TEXT}[C1]\n\n"
            "```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "knowledge_base_id": _KB_ID,
                            "document_id": _DOCUMENT_ID,
                            "chunk_index": 0,
                            "content_sha256": _CHUNK_HASH,
                            "quote": _SOURCE_TEXT,
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        return StepExecutionResult(text=output)

    @staticmethod
    def assert_citation_prompt(prompt):
        assert "task-citations" in prompt
        assert "逐字摘录" in prompt

    async def verify_node(self, **kwargs):
        del kwargs
        from examples.agent_service.task._models import StepVerdict

        return StepVerdict(passed=True)

    async def preflight_artifact_writer(self, *, context):
        assert context.user_id == _OWNER
        return True, "fixture Workspace boundary available"

    async def write_artifact(self, *, path, data, context):
        assert context.user_id == _OWNER
        self.files[path] = data
        return len(data)

    async def read_artifact(self, *, path, context):
        assert context.user_id == _OWNER
        return self.files[path]


class _NeverPlanner:
    def __init__(self):
        self.calls = 0

    async def plan(self, **kwargs):
        del kwargs
        self.calls += 1
        raise AssertionError("A standard template must not call the Planner.")


class TaskProjectPlanningRagTest(IsolatedAsyncioTestCase):
    async def test_retrieval_preserves_scope_and_source_trace(self):
        service = _KnowledgeBaseFixture()
        gateway = TaskKnowledgeGateway(service, SimpleNamespace())

        result = await gateway.retrieve_task_evidence(
            _OWNER,
            [_KB_ID],
            "项目策划目标",
        )

        evidence = result["evidence"][0]
        self.assertEqual(service.search_calls[0][0], _OWNER)
        self.assertEqual(service.document_calls[0][0], _OWNER)
        self.assertEqual(evidence["document_id"], _DOCUMENT_ID)
        self.assertEqual(evidence["knowledge_base_id"], _KB_ID)
        self.assertEqual(evidence["source"], "项目策划依据.md")
        self.assertEqual(evidence["content_sha256"], _CHUNK_HASH)
        self.assertEqual(evidence["source_text"], _SOURCE_TEXT)
        self.assertEqual(evidence["source_status"], "ready")

    async def test_inaccessible_knowledge_base_is_rejected_before_search(self):
        service = _KnowledgeBaseFixture()
        gateway = TaskKnowledgeGateway(service, SimpleNamespace())

        with self.assertRaisesRegex(RuntimeError, "不存在或当前用户无权"):
            await gateway.retrieve_task_evidence(
                "another-user",
                [_KB_ID],
                "项目策划目标",
            )

        self.assertEqual(service.search_calls, [])

    async def test_search_failure_and_empty_evidence_fail_closed(self):
        failed = TaskKnowledgeGateway(
            _KnowledgeBaseFixture(search_error=True),
            SimpleNamespace(),
        )
        with self.assertRaisesRegex(RuntimeError, "授权知识库检索失败"):
            await failed.retrieve_task_evidence(_OWNER, [_KB_ID], "项目策划目标")

        empty_service = _KnowledgeBaseFixture()
        empty_service.search = _empty_search
        empty = TaskKnowledgeGateway(empty_service, SimpleNamespace())
        with self.assertRaisesRegex(RuntimeError, "没有检索到可引用"):
            await empty.retrieve_task_evidence(_OWNER, [_KB_ID], "项目策划目标")

    async def test_citation_verifier_checks_identity_and_exact_quote(self):
        gateway = TaskKnowledgeGateway(
            _KnowledgeBaseFixture(),
            SimpleNamespace(),
        )
        retrieval = await gateway.retrieve_task_evidence(
            _OWNER,
            [_KB_ID],
            "项目策划目标",
        )
        document = _citation_document()

        verified = verify_task_citations(document, retrieval["evidence"])
        self.assertTrue(verified["passed"])
        self.assertEqual(
            verified["verified_citations"][0]["status"],
            "verified",
        )

        forged = document.replace(
            f'"quote":"{_SOURCE_TEXT}"',
            '"quote":"没有出现在资料中的内容"',
        )
        rejected = verify_task_citations(forged, retrieval["evidence"])
        self.assertFalse(rejected["passed"])
        self.assertTrue(
            any("摘录与保存的原文" in issue for issue in rejected["issues"])
        )

        unlisted_marker = document.replace("[C1]", "[C2]", 1)
        rejected_marker = verify_task_citations(
            unlisted_marker,
            retrieval["evidence"],
        )
        self.assertFalse(rejected_marker["passed"])
        self.assertTrue(
            any(
                "未列入核验清单" in issue
                for issue in rejected_marker["issues"]
            )
        )

        trailing_text = document + "\n新增未核验结论。"
        rejected_trailing = verify_task_citations(
            trailing_text,
            retrieval["evidence"],
        )
        self.assertFalse(rejected_trailing["passed"])
        self.assertIn(
            "task-citations 区块之后",
            rejected_trailing["issues"][0],
        )

    async def test_preflight_searches_and_reports_artifact_gap(self):
        executor = _ArtifactExecutor()
        task_service = TaskService(
            TaskStore(),
            agentscope_executor=executor,
            preview_executor=executor,
            artifact_writer=executor,
        )
        template_service = TaskTemplateService(
            TemplateRegistry(),
            task_service,
            compiler=TaskTemplateCompiler(),
        )
        task = await template_service.instantiate(
            _OWNER,
            "s02_project_planning",
            TemplateInstantiateRequest(
                template_version="1.0.0",
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "形成项目策划初稿",
                    "professional_fields": ["项目管理"],
                },
                source_context=TaskContext(
                    agent_id="agent",
                    session_id="session",
                ),
            ),
        )
        await task_service.update_task(
            _OWNER,
            task.id,
            UpdateTaskRequest(knowledge_base_ids=[_KB_ID]),
        )
        gateway = TaskKnowledgeGateway(
            _KnowledgeBaseFixture(),
            SimpleNamespace(),
        )

        preflight = await task_service.preflight_task(
            _OWNER,
            task.id,
            TaskPreflightRequest(input="项目策划初稿"),
            gateway,
        )

        checks = {
            check.capability_key: check
            for check in preflight.capability_checks
        }
        self.assertEqual(
            checks["planning.template_retrieve"].status,
            "available",
        )
        self.assertIn(
            "实际检索到 1 条",
            checks["planning.template_retrieve"].message,
        )
        self.assertEqual(
            checks["document.citation_verify"].status,
            "available",
        )
        self.assertEqual(checks["document.render"].status, "missing")
        self.assertFalse(preflight.ready)
        await task_service.shutdown()

    async def test_preflight_route_passes_existing_knowledge_service(self):
        executor = _ArtifactExecutor()
        task_service = TaskService(
            TaskStore(),
            agentscope_executor=executor,
            preview_executor=executor,
            artifact_writer=executor,
        )
        template_service = TaskTemplateService(
            TemplateRegistry(),
            task_service,
            compiler=TaskTemplateCompiler(),
        )
        task = await template_service.instantiate(
            _OWNER,
            "s02_project_planning",
            TemplateInstantiateRequest(
                template_version="1.0.0",
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "形成项目策划初稿",
                    "professional_fields": ["项目管理"],
                },
            ),
        )
        await task_service.update_task(
            _OWNER,
            task.id,
            UpdateTaskRequest(knowledge_base_ids=[_KB_ID]),
        )
        app = FastAPI()
        app.include_router(task_router)
        app.state.task_service = task_service
        app.state.knowledge_base_service = _KnowledgeBaseFixture()

        with TestClient(app) as client:
            response = client.post(
                f"/tasks/{task.id}/preflight",
                headers={"X-User-ID": _OWNER},
                json={"input": "项目策划初稿"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        checks = {
            check["capability_key"]: check
            for check in response.json()["capability_checks"]
        }
        self.assertEqual(
            checks["planning.template_retrieve"]["status"],
            "available",
        )
        await task_service.shutdown()

    async def test_s02_custom_copy_runs_rag_review_and_word_writer(self):
        executor = _ArtifactExecutor()
        planner = _NeverPlanner()
        store = TaskStore()
        task_service = TaskService(
            store,
            agentscope_executor=executor,
            preview_executor=executor,
            artifact_writer=executor,
            planner=planner,
        )
        template_service = TaskTemplateService(
            TemplateRegistry(),
            task_service,
            compiler=TaskTemplateCompiler(),
        )
        task = await template_service.instantiate(
            _OWNER,
            "s02_project_planning",
            TemplateInstantiateRequest(
                template_version="1.0.0",
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "形成项目策划初稿",
                    "professional_fields": ["项目管理"],
                },
                source_context=TaskContext(
                    agent_id="agent",
                    session_id="session",
                ),
            ),
        )
        self.assertEqual(planner.calls, 0)
        self.assertEqual(executor.agent_calls, [])
        self.assertEqual(executor.files, {})
        copied = await task_service.create_custom_flow_copy(_OWNER, task.id)
        self.assertIsNotNone(copied)
        final_node = copied.nodes[-1].model_copy(
            update={
                "artifact": ArtifactConfig(
                    format=ArtifactFormat.DOCX,
                    filename="项目策划初稿.docx",
                ),
            },
        )
        custom = await task_service.update_task(
            _OWNER,
            copied.id,
            UpdateTaskRequest(
                knowledge_base_ids=[_KB_ID],
                nodes=[*copied.nodes[:-1], final_node],
            ),
        )
        gateway = TaskKnowledgeGateway(
            _KnowledgeBaseFixture(),
            SimpleNamespace(),
        )

        run = await task_service.create_run(
            _OWNER,
            custom.id,
            TaskRunRequest(),
            gateway,
        )
        await task_service._active_runs[run.id]
        run = await task_service.get_run(_OWNER, run.id)
        self.assertEqual(run.status, RunStatus.AWAITING_INPUT)
        run = await task_service.answer_run(
            _OWNER,
            run.id,
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={
                    "project_name": "测试项目",
                    "project_brief": "形成项目策划初稿",
                    "source_material_ids": _DOCUMENT_ID,
                },
            ),
            gateway,
        )
        await task_service._active_runs[run.id]
        run = await task_service.get_run(_OWNER, run.id)
        self.assertEqual(run.status, RunStatus.AWAITING_INPUT)
        run = await task_service.answer_run(
            _OWNER,
            run.id,
            TaskInputReply(
                request_id=run.pending_input.id,
                answers={"planning_feedback": "无补充意见"},
            ),
            gateway,
        )
        await task_service._active_runs[run.id]
        run = await task_service.get_run(_OWNER, run.id)

        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        self.assertEqual(len(executor.agent_calls), 3)
        self.assertEqual(executor.tool_listing_calls, 0)
        self.assertEqual(planner.calls, 0)
        self.assertEqual(len(executor.files), 1)
        artifact = run.artifacts[0]
        self.assertEqual(artifact.format, ArtifactFormat.DOCX)
        docx_file = executor.files[artifact.path]
        self.assertTrue(docx_file.startswith(b"PK"))
        document = Document(BytesIO(docx_file))
        document_text = "\n".join(
            [paragraph.text for paragraph in document.paragraphs]
            + [
                cell.text
                for table in document.tables
                for row in table.rows
                for cell in row.cells
            ]
        )
        self.assertIn(_SOURCE_TEXT, document_text)
        self.assertIn("task-citations", document_text)
        self.assertIn("[C1]", run.final_output)
        self.assertTrue(
            run.node_runs[4].metadata["citation_verification"]["passed"]
        )
        self.assertTrue(
            run.node_runs[-1].metadata["final_citation_verification"]["passed"]
        )
        await task_service.shutdown()


async def _empty_search(user_id, knowledge_base_id, query, top_k=5):
    del user_id, knowledge_base_id, query, top_k
    return []


def _citation_document():
    payload = {
        "citations": [
            {
                "citation_id": "C1",
                "knowledge_base_id": _KB_ID,
                "document_id": _DOCUMENT_ID,
                "chunk_index": 0,
                "content_sha256": _CHUNK_HASH,
                "quote": _SOURCE_TEXT,
            }
        ]
    }
    return (
        f"# 项目策划初稿\n\n{_SOURCE_TEXT}[C1]\n\n"
        "```task-citations\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n```"
    )
