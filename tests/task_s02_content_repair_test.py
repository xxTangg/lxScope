"""Bounded S02 v2 content-repair regression coverage."""

import hashlib
import json
from unittest import IsolatedAsyncioTestCase, TestCase

from agentscope.sop import SOPPhase

from examples.agent_service.task._evidence_contract import (
    extract_claim_evidence_protocol_repair_inventory,
    extract_locked_claim_evidence_content_conflicts,
    materialize_locked_claim_evidence_content_repair,
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
    validate_locked_claim_evidence_content_repair,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepType,
    TaskNode,
    TaskRunRecord,
    ToolStepConfig,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._sop import TaskStepState
from examples.agent_service.task._store import TaskStore


_OWNER = "s02-content-repair-owner"
_SOURCE_TEXT = (
    "项目资料为合成演示数据；项目范围包括资料收集和边界核对。"
)
_CITATION_ID = "C1"
_SOURCE = {
    "knowledge_base_id": "s02-content-repair-kb",
    "knowledge_base_name": "S02 修订回归知识库",
    "document_id": "s02-project-charter",
    "source": "project-charter.md",
    "chunk_index": 0,
    "content_sha256": hashlib.sha256(
        _SOURCE_TEXT.encode("utf-8")
    ).hexdigest(),
    "source_text": _SOURCE_TEXT,
    "source_text_truncated": False,
    "source_status": "ready",
    "evidence_type": "project_material",
    "authorization_subject": _OWNER,
}


def _citation_payload(evidence_id: str) -> dict[str, object]:
    return {
        "citations": [
            {
                "citation_id": _CITATION_ID,
                "evidence_id": evidence_id,
                "quote": _SOURCE_TEXT,
            }
        ]
    }


def _original_body() -> str:
    return (
        "项目范围包括资料收集和边界核对。[C1] "
        "<!-- task-claim:CL-scope:O-scope -->\n"
        "资料声明数据为合成演示数据；正式立项批复尚未取得，"
        "相关状态待核实。[C1] "
        "<!-- task-claim:CL-mixed:O-mixed -->"
    )


def _baseline_compact_payload() -> dict[str, object]:
    return {
        "claims": [
            {"claim_id": "CL-scope", "kind": "fact"},
            {"claim_id": "CL-mixed", "kind": "unverified"},
        ],
        "unverified_items": [
            {
                "unverified_id": "UV-approval",
                "claim_id": "CL-mixed",
                "occurrence_id": "O-mixed",
                "reason": "正式立项批复尚未取得，相关状态待核实。",
                "expected_evidence_types": ["project_material"],
            }
        ],
    }


def _candidate_body() -> str:
    return (
        "项目范围包括资料收集和边界核对。[C1] "
        "<!-- task-claim:CL-scope:O-scope -->\n"
        "资料声明数据为合成演示数据。[C1] "
        "<!-- task-claim:CL-mixed:O-mixed -->\n"
        "正式立项批复尚未取得，相关状态待核实。 "
        "<!-- task-claim:CL-pending:O-pending -->"
    )


def _candidate_compact_payload() -> dict[str, object]:
    return {
        "claims": [
            {"claim_id": "CL-scope", "kind": "fact"},
            {"claim_id": "CL-mixed", "kind": "fact"},
            {"claim_id": "CL-pending", "kind": "unverified"},
        ],
        "unverified_items": [
            {
                "unverified_id": "UV-approval",
                "claim_id": "CL-pending",
                "occurrence_id": "O-pending",
                "reason": "正式立项批复尚未取得，相关状态待核实。",
                "expected_evidence_types": ["project_material"],
            }
        ],
    }


class TaskS02ContentRepairContractTest(TestCase):
    def test_one_cited_unverified_occurrence_is_detected_from_locked_retry(self):
        evidence = normalize_evidence_snapshot(
            [_SOURCE], authorization_subject=_OWNER
        )[0]
        body = _original_body()
        citations = json.dumps(
            _citation_payload(evidence["evidence_id"]),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        first_attempt = (
            body
            + "\n\n```task-citations\n"
            + citations
            + "\n```\n\n```task-claim-evidence\n{"
        )
        locked = extract_claim_evidence_protocol_repair_inventory(
            first_attempt
        )
        self.assertIsNotNone(locked)
        assert locked is not None

        compact = json.dumps(
            _baseline_compact_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        failed_repair = (
            body
            + "\n\n```task-claim-evidence\n"
            + compact
            + "\n```\n\n```task-citations\n"
            + citations
            + "\n```"
        )
        conflicts = extract_locked_claim_evidence_content_conflicts(
            failed_repair, locked
        )
        self.assertIsNotNone(conflicts)
        assert conflicts is not None
        self.assertEqual(
            conflicts["conflict"],
            {
                "claim_id": "CL-mixed",
                "occurrence_id": "O-mixed",
                "citation_ids": ["C1"],
            },
        )

    def test_targeted_repair_materializes_and_keeps_source_citations_locked(self):
        evidence = normalize_evidence_snapshot(
            [_SOURCE], authorization_subject=_OWNER
        )[0]
        body = _original_body()
        citations = json.dumps(
            _citation_payload(evidence["evidence_id"]),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        first_attempt = (
            body
            + "\n\n```task-citations\n"
            + citations
            + "\n```\n\n```task-claim-evidence\n{"
        )
        locked = extract_claim_evidence_protocol_repair_inventory(
            first_attempt
        )
        assert locked is not None
        failed_repair = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                _baseline_compact_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n"
            + citations
            + "\n```"
        )
        repair_inventory = extract_locked_claim_evidence_content_conflicts(
            failed_repair, locked
        )
        assert repair_inventory is not None

        candidate = (
            _candidate_body()
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                _candidate_compact_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        finalized, repair_check = (
            materialize_locked_claim_evidence_content_repair(
                candidate, repair_inventory
            )
        )
        self.assertTrue(repair_check["passed"], repair_check)
        self.assertEqual(finalized.count("```task-citations"), 1)
        self.assertIn(citations, finalized)
        self.assertTrue(
            validate_locked_claim_evidence_content_repair(
                finalized, repair_inventory
            )["passed"]
        )

        citation_check = validate_claim_evidence_document(
            finalized,
            [evidence],
            authorization_subject=_OWNER,
        )
        self.assertTrue(citation_check["passed"], citation_check)

    def test_targeted_repair_rejects_changes_outside_the_conflicting_line(self):
        evidence = normalize_evidence_snapshot(
            [_SOURCE], authorization_subject=_OWNER
        )[0]
        body = _original_body()
        citations = json.dumps(
            _citation_payload(evidence["evidence_id"]),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        first_attempt = (
            body
            + "\n\n```task-citations\n"
            + citations
            + "\n```\n\n```task-claim-evidence\n{"
        )
        locked = extract_claim_evidence_protocol_repair_inventory(
            first_attempt
        )
        assert locked is not None
        failed_repair = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                _baseline_compact_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n"
            + citations
            + "\n```"
        )
        repair_inventory = extract_locked_claim_evidence_content_conflicts(
            failed_repair, locked
        )
        assert repair_inventory is not None
        changed_elsewhere = _candidate_body().replace(
            "项目范围包括资料收集和边界核对。",
            "项目范围包括资料收集、核对和分析。",
        )
        candidate = (
            changed_elsewhere
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                _candidate_compact_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        _, repair_check = materialize_locked_claim_evidence_content_repair(
            candidate, repair_inventory
        )
        self.assertFalse(repair_check["passed"])
        self.assertEqual(repair_check["reason"], "unrelated_body_change")

        added_fact = _candidate_body().replace(
            "资料声明数据为合成演示数据。[C1]",
            "资料声明数据为合成演示数据；新增未经支持的事实。[C1]",
        )
        candidate_with_added_fact = (
            added_fact
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                _candidate_compact_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        _, repair_check = materialize_locked_claim_evidence_content_repair(
            candidate_with_added_fact, repair_inventory
        )
        self.assertFalse(repair_check["passed"])
        self.assertEqual(
            repair_check["reason"], "target_content_not_preserved"
        )


class _S02RetryExecutor:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.feedback: list[str] = []
        self.prompts: list[str] = []

    async def execute_node(self, *, node, previous_output, context):
        del previous_output
        self.feedback.append(context.verification_feedback)
        self.prompts.append(node.prompt)
        return StepExecutionResult(text=self.outputs.pop(0))


class TaskS02ContentRepairSOPTest(IsolatedAsyncioTestCase):
    async def test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger(self):
        normalized = normalize_evidence_snapshot(
            [_SOURCE], authorization_subject=_OWNER
        )[0]
        body = _original_body()
        citation_block = (
            "```task-citations\n"
            + json.dumps(
                _citation_payload(normalized["evidence_id"]),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        truncated_output = (
            body
            + "\n\n"
            + citation_block
            + "\n\n```task-claim-evidence\n{"
        )
        compact_repair = json.dumps(
            _baseline_compact_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        targeted_repair = (
            _candidate_body()
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                _candidate_compact_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        executor = _S02RetryExecutor(
            [truncated_output, compact_repair, targeted_repair]
        )
        store = TaskStore()
        retrieval_node = TaskNode(
            name="检索项目资料",
            type=StepType.TOOL,
            capability_key="project.similar_search",
            config=ToolStepConfig(
                tool_name="project.similar_search",
                arguments={"query": "项目资料"},
            ),
            order=0,
        )
        final_node = TaskNode(
            name="整理策划输出",
            type=StepType.AGENT,
            config=AgentStepConfig(prompt="整理策划报告。"),
            max_attempts=3,
            order=1,
        )
        run = TaskRunRecord(
            task_id="s02-content-repair-task",
            user_id=_OWNER,
            task_revision=1,
            nodes=[retrieval_node, final_node],
            node_runs=[
                NodeRunRecord(
                    node_id=retrieval_node.id,
                    name=retrieval_node.name,
                    prompt=retrieval_node.prompt,
                    type=StepType.TOOL,
                    order=0,
                    status=NodeRunStatus.SUCCEEDED,
                    output="已检索授权项目资料。",
                    result={"status": "ready", "evidence": [_SOURCE]},
                    metadata={"capability_key": "project.similar_search"},
                ),
                NodeRunRecord(
                    node_id=final_node.id,
                    name=final_node.name,
                    prompt=final_node.prompt,
                    type=StepType.AGENT,
                    order=1,
                ),
            ],
            knowledge_base_ids=["s02-content-repair-kb"],
            input="项目 P-1001",
            status=RunStatus.RUNNING,
            engine="sop",
            source_template_id="s02_project_planning",
            source_template_version="2.0.0",
        )
        await store.save_run(run)
        service = TaskService(store, preview_executor=executor)
        engine = service._build_sop_engine(run, None)
        final_step = engine.sop.steps[-1]
        state = TaskStepState()

        try:
            for _ in range(3):
                async for _event in final_step.reply_stream(None, state):
                    pass
                if state.phase in {SOPPhase.COMPLETED, SOPPhase.FAILED}:
                    break

            self.assertEqual(state.phase, SOPPhase.COMPLETED, state.metadata)
            self.assertEqual(len(executor.feedback), 3)
            self.assertIn("检测到协议修复结果", executor.feedback[2])
            self.assertIn("CL-mixed:O-mixed", executor.feedback[2])
            self.assertIn("Task 对 S02 v2 最终输出仅授权一次局部内容修订", executor.prompts[2])
            self.assertTrue(
                state.metadata["task_s02_targeted_content_repair_check"][
                    "passed"
                ]
            )
            self.assertTrue(state.metadata["evidence_ledger"]["passed"])
            self.assertEqual(
                state.metadata["evidence_ledger"]["verification_scope"][
                    "final_citation_recheck"
                ],
                "passed",
            )
            self.assertIn("CL-pending", state.output)
            self.assertEqual(state.output.count("```task-citations"), 1)
        finally:
            await service.shutdown()
