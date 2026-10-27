"""Task-owned S07 RAG and authorization contract tests.

All knowledge records in this module are synthetic fixtures. These tests
exercise Task routing and error classification, not a live knowledge base.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from examples.agent_service.task._capability_contracts import (
    is_task_native_capability,
)
from examples.agent_service.task._errors import classify_node_error
from examples.agent_service.task._knowledge import (
    TaskKnowledgeError,
    TaskKnowledgeGateway,
)
from examples.agent_service.task._models import (
    NodeRunRecord,
    StepVerdict,
    StepType,
    TaskNode,
    TaskInputReply,
    TaskPreflightRequest,
    TaskRunRecord,
    TaskRunRequest,
    ToolStepConfig,
    UpdateTaskRequest,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._scenario_runtime import (
    native_tool_schema,
    retrieve_scenario_evidence,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_models import TemplateInstantiateRequest
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService
from examples.agent_service.task._evidence_contract import (
    normalize_evidence_snapshot,
    verify_task_citations,
)
from examples.agent_service.task._service import _retrieval_result_for_run


_OWNER = "synthetic-s07-owner"
_KB_ID = "synthetic-s07-kb"
_DOCUMENT_ID = "synthetic-s07-report-doc"


class _SyntheticKnowledgeService:
    """Minimal user-scoped KB service surface used by the Task adapter."""

    def __init__(self, documents: dict[str, str] | None = None) -> None:
        self.documents = documents or {}
        self.view_calls: list[tuple[str, str | None]] = []
        self.status_calls: list[tuple[str, str, list[str]]] = []

    async def list_knowledge_base_views(
        self,
        user_id: str,
        *,
        knowledge_base_id: str | None = None,
        page: int = 1,
        page_size: int = 1,
    ):
        del page, page_size
        self.view_calls.append((user_id, knowledge_base_id))
        if user_id != _OWNER or knowledge_base_id != _KB_ID:
            return [], 0
        view = SimpleNamespace(
            id=_KB_ID,
            name="Synthetic authorized report KB",
            status_counts=SimpleNamespace(ready=sum(
                status == "ready" for status in self.documents.values()
            )),
            owner_id=_OWNER,
        )
        return [view], 1

    async def get_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_ids: list[str],
    ):
        self.status_calls.append((user_id, knowledge_base_id, list(document_ids)))
        if user_id != _OWNER or knowledge_base_id != _KB_ID:
            return []
        return [
            SimpleNamespace(id=document_id, status=self.documents[document_id])
            for document_id in document_ids
            if document_id in self.documents
        ]


class _PreflightGateway:
    def __init__(self, error: TaskKnowledgeError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, list[str], list[str]]] = []

    async def inspect_task_scope(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        source_material_ids: list[str],
    ):
        self.calls.append((user_id, list(knowledge_base_ids), list(source_material_ids)))
        if self.error is not None:
            raise self.error
        return {
            "knowledge_bases": [
                {"id": _KB_ID, "name": "Synthetic authorized report KB"},
            ],
            "source_material_ids": list(source_material_ids),
        }


class _SyntheticTaskExecutor:
    def __init__(self) -> None:
        self.list_tools_calls = 0

    async def list_tools(self, context):
        del context
        self.list_tools_calls += 1
        return []

    async def preflight_artifact_writer(self, *, context):
        del context
        return True, "Synthetic isolated Workspace writer is available."

    async def write_artifact(self, *, path, data, context):
        del path, data, context
        return 1

    async def read_artifact(self, *, path, context):
        del path, context
        return b"synthetic"


def _s07_inputs() -> dict[str, object]:
    return {
        "project_name": "[DEMO/SYNTHETIC] S07 sample report",
        "source_material_ids": [_DOCUMENT_ID],
        "required_chapters": ["scope", "findings"],
        "applicable_rules": [],
    }


class _S07RuntimeGateway(_PreflightGateway):
    def __init__(self) -> None:
        super().__init__()
        source_text = "报告原文：项目范围包括资料核对和计量边界分析。"
        self.source = {
            "knowledge_base_id": _KB_ID,
            "knowledge_base_name": "Synthetic authorized report KB",
            "document_id": _DOCUMENT_ID,
            "source": "authorized-report-source.md",
            "source_updated_at": "2026-10-12T00:00:00+00:00",
            "chunk_index": 0,
            "total_chunks": 1,
            "content_sha256": hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            "source_text": source_text,
            "source_text_truncated": False,
            "source_status": "ready",
        }

    async def retrieve_task_evidence(
        self,
        user_id,
        knowledge_base_ids,
        query,
        *,
        source_material_ids=None,
        top_k=8,
    ):
        del query, top_k
        if (
            user_id != _OWNER
            or knowledge_base_ids != [_KB_ID]
            or source_material_ids != [_DOCUMENT_ID]
        ):
            raise AssertionError("unexpected TaskRun authorization scope")
        return {
            "status": "ready",
            "retrieved_at": "2026-10-13T00:00:00+00:00",
            "knowledge_bases": [
                {"id": _KB_ID, "name": "Synthetic authorized report KB"}
            ],
            "evidence": [dict(self.source)],
        }

    def format_task_evidence(self, result):
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class _S07RuntimeExecutor(_SyntheticTaskExecutor):
    def __init__(self, draft: str) -> None:
        super().__init__()
        self.draft = draft
        self.verification_calls: list[str] = []
        self.artifact_writes = 0

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        if node.name != "生成章节草稿":
            raise AssertionError(f"unexpected Agent step: {node.name}")
        return StepExecutionResult(text=self.draft)

    async def verify_node(self, *, node, output, context):
        del output, context
        self.verification_calls.append(node.name)
        return StepVerdict(passed=True)

    async def write_artifact(self, *, path, data, context):
        del path, data, context
        self.artifact_writes += 1
        return 1


async def _create_s07_runtime_run(draft: str):
    executor = _S07RuntimeExecutor(draft)
    service = TaskService(
        TaskStore(),
        agentscope_executor=executor,
        preview_executor=executor,
    )
    registry = TemplateRegistry()
    task = await TaskTemplateService(registry, service).instantiate(
        _OWNER,
        "s07_report_generation",
        TemplateInstantiateRequest(
            template_version="4.0.0",
            inputs={
                **_s07_inputs(),
                "required_chapters": ["scope"],
            },
        ),
    )
    task = await service.update_task(
        _OWNER,
        task.id,
        UpdateTaskRequest(knowledge_base_ids=[_KB_ID]),
    )
    gateway = _S07RuntimeGateway()
    run = await service.create_run(
        _OWNER,
        task.id,
        TaskRunRequest(input="生成 [DEMO/SYNTHETIC] scope 草稿"),
        gateway,  # type: ignore[arg-type]
    )
    assert run is not None
    active = service._active_runs.get(run.id)
    if active is not None:
        await active
    parked = await service.get_run(_OWNER, run.id)
    assert parked is not None and parked.pending_input is not None
    accepted = await service.answer_run(
        _OWNER,
        run.id,
        TaskInputReply(
            request_id=parked.pending_input.id,
            answers={
                "project_name": "[DEMO/SYNTHETIC] Report",
                "source_material_ids": _DOCUMENT_ID,
                "required_chapters": "scope",
            },
        ),
        gateway,  # type: ignore[arg-type]
    )
    assert accepted is not None
    active = service._active_runs.get(run.id)
    if active is not None:
        await active
    return service, executor, run.id


class S07CitationOutputContractTest(IsolatedAsyncioTestCase):
    async def test_invalid_quote_is_rejected_before_model_verifier_or_artifact(self):
        source = _S07RuntimeGateway().source
        evidence_id = normalize_evidence_snapshot(
            [
                {
                    **source,
                    "evidence_type": "report_source",
                    "authorization_subject": _OWNER,
                }
            ],
            authorization_subject=_OWNER,
        )[0]["evidence_id"]
        draft = (
            "## scope\n内容引用 [C1]\n\n"
            "```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "evidence_id": evidence_id,
                            "quote": "authorized-report-source.md",
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        service, executor, run_id = await _create_s07_runtime_run(draft)
        try:
            failed = await service.get_run(_OWNER, run_id)
            assert failed is not None
            self.assertEqual(failed.status.value, "failed")
            draft_step = next(
                item for item in failed.node_runs if item.name == "生成章节草稿"
            )
            self.assertEqual(
                draft_step.metadata["output_contract_validation"]["status"],
                "failed",
            )
            self.assertIn(
                "摘录与保存的原文片段不一致",
                "；".join(draft_step.metadata["citation_output_validation"]["issues"]),
            )
            self.assertNotIn("生成章节草稿", executor.verification_calls)
            self.assertEqual(executor.artifact_writes, 0)
            self.assertEqual(failed.artifacts, [])
        finally:
            await service.shutdown()

    async def test_valid_citation_reaches_deterministic_citation_tool(self):
        source = _S07RuntimeGateway().source
        evidence_id = normalize_evidence_snapshot(
            [
                {
                    **source,
                    "evidence_type": "report_source",
                    "authorization_subject": _OWNER,
                }
            ],
            authorization_subject=_OWNER,
        )[0]["evidence_id"]
        draft = (
            "## scope\n项目范围包括资料核对和计量边界分析。[C1]\n\n"
            "```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "evidence_id": evidence_id,
                            "quote": source["source_text"],
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        service, executor, run_id = await _create_s07_runtime_run(draft)
        try:
            parked = await service.get_run(_OWNER, run_id)
            assert parked is not None
            self.assertEqual(parked.status.value, "awaiting_input")
            draft_step = next(
                item for item in parked.node_runs if item.name == "生成章节草稿"
            )
            citation_step = next(
                item for item in parked.node_runs if item.name == "核验引用证据"
            )
            self.assertEqual(draft_step.status.value, "succeeded")
            self.assertEqual(
                draft_step.metadata["output_contract_validation"]["status"],
                "passed",
            )
            self.assertEqual(citation_step.status.value, "succeeded")
            self.assertTrue(
                citation_step.metadata["citation_verification"]["passed"]
            )
            self.assertEqual(
                executor.verification_calls,
                ["检索引用证据", "生成章节草稿"],
            )
            self.assertEqual(executor.artifact_writes, 0)
        finally:
            await service.shutdown()


class S07KnowledgeGatewayTest(IsolatedAsyncioTestCase):
    async def test_gateway_distinguishes_accessible_missing_and_unready_sources(self):
        ready = TaskKnowledgeGateway(
            _SyntheticKnowledgeService({_DOCUMENT_ID: "ready"}),
            SimpleNamespace(),
        )
        scope = await ready.inspect_task_scope(
            _OWNER,
            [_KB_ID],
            [_DOCUMENT_ID],
        )
        self.assertEqual(scope["source_material_ids"], [_DOCUMENT_ID])

        missing = TaskKnowledgeGateway(
            _SyntheticKnowledgeService(),
            SimpleNamespace(),
        )
        with self.assertRaises(TaskKnowledgeError) as missing_error:
            await missing.inspect_task_scope(_OWNER, [_KB_ID], [_DOCUMENT_ID])
        self.assertEqual(missing_error.exception.code, "source_material_not_found")
        self.assertEqual(missing_error.exception.details["missing_source_count"], 1)

        pending = TaskKnowledgeGateway(
            _SyntheticKnowledgeService({_DOCUMENT_ID: "indexing"}),
            SimpleNamespace(),
        )
        with self.assertRaises(TaskKnowledgeError) as pending_error:
            await pending.inspect_task_scope(_OWNER, [_KB_ID], [_DOCUMENT_ID])
        self.assertEqual(pending_error.exception.code, "source_material_not_ready")

        inaccessible = TaskKnowledgeGateway(
            _SyntheticKnowledgeService({_DOCUMENT_ID: "ready"}),
            SimpleNamespace(),
        )
        with self.assertRaises(TaskKnowledgeError) as denied_error:
            await inaccessible.inspect_task_scope(
                "synthetic-unrelated-user",
                [_KB_ID],
                [_DOCUMENT_ID],
            )
        self.assertEqual(denied_error.exception.code, "knowledge_base_unavailable")


class S07TaskPreflightTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.executor = _SyntheticTaskExecutor()
        self.task_service = TaskService(
            TaskStore(),
            agentscope_executor=self.executor,
        )
        registry = TemplateRegistry()
        template = registry.get_template_version(
            "s07_report_generation", "4.0.0"
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(template.template_version, "4.0.0")
        self.task = await TaskTemplateService(registry, self.task_service).instantiate(
            _OWNER,
            "s07_report_generation",
            TemplateInstantiateRequest(
                template_version="4.0.0",
                inputs=_s07_inputs(),
            ),
        )
        self.task = await self.task_service.update_task(
            _OWNER,
            self.task.id,
            UpdateTaskRequest(knowledge_base_ids=[_KB_ID]),
        )
        assert self.task is not None

    async def asyncTearDown(self) -> None:
        await self.task_service.shutdown()

    async def test_report_rag_is_task_native_and_does_not_discover_session_tool(self):
        self.assertTrue(is_task_native_capability("report.rag_retrieve"))
        self.assertEqual(
            native_tool_schema("report.rag_retrieve"),
            {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
                "additionalProperties": False,
            },
        )
        gateway = _PreflightGateway()

        result = await self.task_service.preflight_task(
            _OWNER,
            self.task.id,
            TaskPreflightRequest(input="Synthetic S07 preflight"),
            gateway,  # type: ignore[arg-type]
        )

        self.assertIsNotNone(result)
        assert result is not None
        checks = {item.capability_key: item for item in result.capability_checks}
        self.assertTrue(result.ready, [item.message for item in result.issues])
        self.assertEqual(checks["report.rag_retrieve"].status, "available")
        self.assertEqual(checks["report.citation_check"].status, "available")
        self.assertEqual(checks["document.render"].status, "available")
        self.assertEqual(gateway.calls, [(_OWNER, [_KB_ID], [_DOCUMENT_ID])])
        self.assertEqual(self.executor.list_tools_calls, 0)

    async def test_preflight_reports_document_not_found_as_data_error_not_permission(self):
        gateway = _PreflightGateway(
            TaskKnowledgeError(
                "source_material_not_found",
                "部分资料标识未在当前用户可访问的所选知识库中找到。",
                details={"missing_source_count": 1},
            ),
        )
        result = await self.task_service.preflight_task(
            _OWNER,
            self.task.id,
            TaskPreflightRequest(input="Synthetic S07 preflight"),
            gateway,  # type: ignore[arg-type]
        )
        assert result is not None
        checks = {item.capability_key: item for item in result.capability_checks}
        rag = checks["report.rag_retrieve"]
        self.assertFalse(result.ready)
        self.assertEqual(rag.status, "unverified")
        self.assertEqual(rag.error_category, "data_source_not_ready")
        self.assertEqual(rag.error_code, "TASK_KNOWLEDGE_SOURCE_NOT_FOUND")
        self.assertEqual(rag.permission_status, "allowed")
        # Citation validation reuses the evidence snapshot produced by the
        # preceding RAG node, so it does not repeat the same document lookup.
        self.assertEqual(checks["report.citation_check"].status, "available")
        self.assertEqual(len(gateway.calls), 1)

    async def test_preflight_reports_unindexed_document_separately(self):
        gateway = _PreflightGateway(
            TaskKnowledgeError(
                "source_material_not_ready",
                "指定资料尚未完成索引，暂时不能用于可核验引用。",
            ),
        )
        result = await self.task_service.preflight_task(
            _OWNER,
            self.task.id,
            TaskPreflightRequest(input="Synthetic S07 preflight"),
            gateway,  # type: ignore[arg-type]
        )
        assert result is not None
        rag = next(
            item
            for item in result.capability_checks
            if item.capability_key == "report.rag_retrieve"
        )
        self.assertEqual(rag.status, "unverified")
        self.assertEqual(rag.error_category, "data_source_not_ready")
        self.assertEqual(rag.error_code, "TASK_KNOWLEDGE_DOCUMENT_NOT_READY")
        self.assertEqual(rag.permission_status, "allowed")

    async def test_preflight_keeps_inaccessible_knowledge_base_as_permission_denial(self):
        gateway = _PreflightGateway(
            TaskKnowledgeError(
                "knowledge_base_unavailable",
                "所选知识库不存在或当前用户无权访问。",
            ),
        )
        result = await self.task_service.preflight_task(
            _OWNER,
            self.task.id,
            TaskPreflightRequest(input="Synthetic S07 preflight"),
            gateway,  # type: ignore[arg-type]
        )
        assert result is not None
        rag = next(
            item
            for item in result.capability_checks
            if item.capability_key == "report.rag_retrieve"
        )
        self.assertEqual(rag.status, "permission_denied")
        self.assertEqual(rag.error_category, "permission_denied")
        self.assertEqual(rag.error_code, "TASK_KNOWLEDGE_PERMISSION_DENIED")


class S07RunEvidenceBindingTest(IsolatedAsyncioTestCase):
    async def test_citation_check_can_only_use_the_current_run_rag_snapshot(self):
        source_text = "[DEMO/SYNTHETIC] Report source states the verified sample scope."
        source = {
            "knowledge_base_id": _KB_ID,
            "knowledge_base_name": "Synthetic authorized report KB",
            "document_id": _DOCUMENT_ID,
            "source": "synthetic-report-source.md",
            "chunk_index": 2,
            "content_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
            "source_text": source_text,
            "source_status": "ready",
        }

        class _RetrievalGateway:
            async def retrieve_task_evidence(
                self,
                user_id,
                knowledge_base_ids,
                query,
                *,
                source_material_ids,
                top_k,
            ):
                self.args = (
                    user_id,
                    list(knowledge_base_ids),
                    list(source_material_ids),
                    top_k,
                )
                return {
                    "status": "ready",
                    "query": query,
                    "retrieved_at": "2026-10-12T00:00:00+00:00",
                    "knowledge_bases": [
                        {"id": _KB_ID, "name": "Synthetic authorized report KB"},
                    ],
                    "evidence": [source],
                }

            def format_task_evidence(self, result):
                return json.dumps(result, ensure_ascii=False)

        gateway = _RetrievalGateway()
        retrieved = await retrieve_scenario_evidence(
            "report.rag_retrieve",
            gateway=gateway,
            user_id=_OWNER,
            knowledge_base_ids=[_KB_ID],
            source_material_ids=[_DOCUMENT_ID],
            input_values={
                "project_name": "[DEMO/SYNTHETIC] Report",
                "required_chapters": '["scope"]',
            },
            authorization_subject=_OWNER,
        )
        self.assertEqual(retrieved.status, "completed")
        self.assertEqual(gateway.args, (_OWNER, [_KB_ID], [_DOCUMENT_ID], 8))
        evidence = retrieved.result["evidence"]
        record = evidence[0]
        self.assertEqual(record["knowledge_base_id"], _KB_ID)
        self.assertEqual(record["document_id"], _DOCUMENT_ID)
        self.assertEqual(record["chunk_index"], 2)
        self.assertEqual(record["content_sha256"], source["content_sha256"])
        self.assertEqual(record["source_text"], source_text)
        self.assertEqual(record["authorization_subject"], _OWNER)

        rag_node = TaskNode(
            id="s07-rag",
            name="Synthetic S07 RAG",
            type=StepType.TOOL,
            order=0,
            capability_key="report.rag_retrieve",
            config=ToolStepConfig(tool_name="report.rag_retrieve", arguments={}),
        )
        citation_node = TaskNode(
            id="s07-citation",
            name="Synthetic citation checker",
            type=StepType.TOOL,
            order=1,
            capability_key="report.citation_check",
            config=ToolStepConfig(tool_name="report.citation_check", arguments={}),
        )
        run = TaskRunRecord(
            task_id="synthetic-s07-task",
            user_id=_OWNER,
            task_revision=1,
            source_template_id="s07_report_generation",
            source_template_version="3.0.0",
            nodes=[rag_node, citation_node],
            node_runs=[],
        )
        current = run.model_copy(
            update={
                "node_runs": [
                    NodeRunRecord(
                        node_id="s07-rag",
                        name="Synthetic S07 RAG",
                        prompt="",
                        order=0,
                        result=retrieved.result,
                    ),
                ],
            },
        )
        saved = _retrieval_result_for_run(run, current, before_order=1)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["evidence"], evidence)

        citation_id = "C1"
        valid_document = (
            "[DEMO/SYNTHETIC] Report scope is described by the source. [C1]\n\n"
            "```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": citation_id,
                            "evidence_id": record["evidence_id"],
                            "quote": "Report source states the verified sample scope.",
                        },
                    ],
                },
                ensure_ascii=False,
            )
            + "\n```"
        )
        verification = verify_task_citations(
            valid_document,
            saved["evidence"],
            authorization_subject=_OWNER,
        )
        self.assertTrue(verification["passed"], verification["issues"])

        forged_run_evidence = dict(record, evidence_id="EV-from-another-run")
        rejected = verify_task_citations(
            valid_document.replace(record["evidence_id"], "EV-from-another-run"),
            [forged_run_evidence],
            authorization_subject=_OWNER,
        )
        self.assertFalse(rejected["passed"])

    async def test_runtime_error_classification_separates_missing_source_from_denial(self):
        run = TaskRunRecord(
            task_id="synthetic-s07-task",
            user_id=_OWNER,
            task_revision=1,
            nodes=[],
            node_runs=[],
        )
        node = TaskNode(
            id="rag",
            name="Synthetic RAG",
            type=StepType.TOOL,
            order=0,
            capability_key="report.rag_retrieve",
            config=ToolStepConfig(tool_name="report.rag_retrieve", arguments={}),
        )
        not_found = classify_node_error(
            run,
            node,
            {"knowledge_error_code": "source_material_not_found"},
            "Missing in authorized selected KB",
        )
        denied = classify_node_error(
            run,
            node,
            {"knowledge_error_code": "knowledge_base_unavailable"},
            "KB not visible",
        )
        self.assertEqual(not_found.category, "data_source_not_ready")
        self.assertEqual(not_found.code, "TASK_KNOWLEDGE_SOURCE_NOT_FOUND")
        self.assertEqual(denied.category, "permission_denied")
        self.assertEqual(denied.code, "TASK_KNOWLEDGE_PERMISSION_DENIED")


__all__ = [
    "S07KnowledgeGatewayTest",
    "S07RunEvidenceBindingTest",
    "S07TaskPreflightTest",
]
