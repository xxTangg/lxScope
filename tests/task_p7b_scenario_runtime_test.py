"""Isolated checks for Task-local P7-B scenario capabilities.

All records here are explicitly synthetic. These tests verify deterministic
adapters and their Task/SOP wiring; they do not claim access to live business
systems, a production knowledge base, or a production model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    NodeRunRecord,
    RunStatus,
    StepType,
    StepVerdict,
    TaskNode,
    TaskInputReply,
    TaskRunRecord,
    TaskRunRequest,
    ToolStepConfig,
)
from examples.agent_service.task._scenario_runtime import (
    execute_scenario_capability,
    retrieve_scenario_evidence,
)
from examples.agent_service.task._evidence_contract import (
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
)
from examples.agent_service.task._knowledge import TaskKnowledgeError
from examples.agent_service.task._scenario_evidence_policies import (
    get_scenario_evidence_policy,
)
from examples.agent_service.task._service import (
    TaskService,
    _retrieval_result_for_run,
)
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_models import TemplateInstantiateRequest
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService


def _result(capability: str, values: dict[str, str], prior=None):
    return execute_scenario_capability(
        capability,
        input_values=values,
        prior_results=prior or {},
        run_id="synthetic-run-p7b",
    )


class ScenarioRuntimeTest(TestCase):
    def test_s03_matches_json_field_lists_and_excludes_unknown_conflicts(self):
        inputs = {
            "professional_fields": '["工程", "能源"]',
            "required_expert_count": "1",
            "expert_records_json": json.dumps(
                [
                    {
                        "expert_id": "demo-clear",
                        "name": "合成候选甲",
                        "professional_fields": ["工程", "能源"],
                        "conflict_status": "clear",
                    },
                    {
                        "expert_id": "demo-unknown",
                        "name": "合成候选乙",
                        "professional_fields": ["工程"],
                        "conflict_status": "unknown",
                    },
                    {
                        "expert_id": "demo-conflict",
                        "name": "合成候选丙",
                        "professional_fields": ["能源"],
                        "conflict_status": "conflict",
                    },
                ],
                ensure_ascii=False,
            ),
        }

        loaded = _result("expert.user_data_lookup", inputs)
        self.assertEqual(loaded.status, "completed")
        checked = _result(
            "expert.user_conflict_check",
            inputs,
            {"expert.user_data_lookup": loaded.result},
        )
        ranked = _result(
            "expert.user_rank",
            inputs,
            {
                "expert.user_data_lookup": loaded.result,
                "expert.user_conflict_check": checked.result,
            },
        )

        self.assertEqual(ranked.result["candidates"][0]["expert_id"], "demo-clear")
        self.assertEqual(ranked.result["candidates"][0]["score"], 100.0)
        self.assertEqual(len(ranked.result["candidates"]), 1)
        self.assertEqual(len(ranked.result["excluded_candidates"]), 2)
        self.assertFalse(ranked.result["official_expert_database"])
        self.assertTrue(ranked.result["requires_human_review"])

    def test_s03_demo_input_parses_quoted_fields_and_keeps_stable_ties(self):
        demo_root = (
            Path(__file__).resolve().parents[1]
            / "examples/agent_service/task/docs/p7b-demo-data/inputs"
        )
        demo = json.loads(
            (demo_root / "s03-expert-matching.v3.json").read_text(
                encoding="utf-8"
            )
        )["inputs"]
        runtime = {
            **demo,
            "professional_fields": '"综合能源", "设备资产管理"',
            "required_expert_count": str(demo["required_expert_count"]),
        }

        loaded = _result("expert.user_data_lookup", runtime)
        self.assertEqual(loaded.status, "completed")
        checked = _result(
            "expert.user_conflict_check",
            runtime,
            {"expert.user_data_lookup": loaded.result},
        )
        ranked = _result(
            "expert.user_rank",
            runtime,
            {
                "expert.user_data_lookup": loaded.result,
                "expert.user_conflict_check": checked.result,
            },
        )

        self.assertEqual(
            loaded.result["required_fields"],
            ["综合能源", "设备资产管理"],
        )
        self.assertEqual(
            [item["expert_id"] for item in ranked.result["candidates"]],
            ["DEMO-EXPERT-001", "DEMO-EXPERT-002"],
        )
        self.assertEqual(
            [item["score"] for item in ranked.result["candidates"]],
            [50.0, 50.0],
        )
        self.assertEqual(
            ranked.result["excluded_candidates"][0]["expert_id"],
            "DEMO-EXPERT-003",
        )

    def test_s03_unpaired_quoted_field_fails_closed(self):
        result = _result(
            "expert.user_data_lookup",
            {
                "professional_fields": '"综合能源, 设备资产管理',
                "expert_records_json": "[]",
            },
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.metadata["scenario_capability_error"]["code"],
            "TASK_SCENARIO_INPUT_INVALID",
        )

    def test_s03_invalid_or_missing_expert_data_fails_closed(self):
        missing = _result("expert.user_data_lookup", {"professional_fields": ""})
        self.assertEqual(missing.status, "failed")
        duplicate_ids = _result(
            "expert.user_data_lookup",
            {
                "professional_fields": "工程",
                "expert_records_json": json.dumps(
                    [
                        {"expert_id": "same", "professional_fields": ["工程"]},
                        {"expert_id": "same", "professional_fields": ["工程"]},
                    ],
                ),
            },
        )
        self.assertEqual(duplicate_ids.status, "failed")

    def test_s04_decimal_costs_and_materials_are_deterministic_drafts(self):
        inputs = {
            "meeting_topic": "合成会议",
            "meeting_date": "2026-10-12 09:00",
            "location": "合成会议室",
            "participant_count": "3",
            "agenda": "范围确认;风险讨论",
            "fee_rates_json": json.dumps(
                {
                    "rule_id": "synthetic-fee-v1",
                    "currency": "CNY",
                    "rates": {
                        "venue_fixed": "10.00",
                        "catering_per_person": "2.10",
                        "materials_per_person": "0.30",
                        "transport_per_person": "4.00",
                    },
                },
            ),
        }
        costs = _result("meeting.cost_calculate", inputs)
        self.assertEqual(costs.status, "completed")
        self.assertEqual(costs.result["total"], "29.20")
        self.assertFalse(costs.result["official_fee_policy_verified"])

        materials = _result(
            "meeting.material_generate",
            inputs,
            {"meeting.cost_calculate": costs.result},
        )
        checked = _result(
            "meeting.material_check",
            inputs,
            {
                "meeting.cost_calculate": costs.result,
                "meeting.material_generate": materials.result,
            },
        )
        self.assertEqual(materials.result["notification"]["status"], "draft_only")
        self.assertFalse(materials.result["notification"]["sent"])
        self.assertEqual(checked.result["status"], "passed")
        self.assertFalse(checked.result["fee_policy_verified"])

        inputs["fee_rates_json"] = json.dumps(
            {"rule_id": "synthetic-negative", "rates": {"venue_fixed": "-1"}},
        )
        self.assertEqual(_result("meeting.cost_calculate", inputs).status, "failed")

    def test_s05_imports_only_user_transcript_and_does_not_claim_asr(self):
        imported = _result(
            "meeting.transcript_import",
            {
                "meeting_id": "synthetic-meeting-1",
                "transcript_text": "演示发言：需要在下周提交风险清单。",
                "agenda": "风险讨论",
            },
        )
        self.assertEqual(imported.status, "completed")
        self.assertFalse(imported.result["transcription_performed"])
        self.assertEqual(imported.result["source_kind"], "user_supplied_transcript")
        self.assertEqual(
            _result("meeting.transcript_import", {"meeting_id": "missing"}).status,
            "failed",
        )

    def test_s06_classifies_deduplicates_and_preserves_opinion_ids(self):
        inputs = {
            "opinion_records_json": json.dumps(
                [
                    {"opinion_id": "demo-1", "text": " \u589e\u52a0 \u591c\u95f4\u7167\u660e ", "source_reference": "p1"},
                    {"opinion_id": "demo-2", "text": "\u589e\u52a0  \u591c\u95f4\u7167\u660e", "source_reference": "p2"},
                    {"opinion_id": "demo-3", "text": "优化道路排水"},
                ],
                ensure_ascii=False,
            ),
            "classification_rules_json": json.dumps(
                [
                    {"category": "照明", "keywords": ["照明"]},
                    {"category": "交通", "keywords": ["道路"]},
                ],
                ensure_ascii=False,
            ),
        }
        imported = _result("opinion.text_import", inputs)
        classified = _result(
            "opinion.classify",
            inputs,
            {"opinion.text_import": imported.result},
        )
        self.assertEqual(classified.result["original_count"], 3)
        self.assertEqual(classified.result["unique_count"], 2)
        self.assertEqual(classified.result["duplicate_count"], 1)
        self.assertEqual(classified.result["items"][0]["opinion_ids"], ["demo-1", "demo-2"])
        self.assertFalse(classified.result["ocr_performed"])

    def test_s08_normalizes_metadata_checks_completeness_and_flags_duplicates(self):
        inputs = {
            "project_id": "synthetic-project",
            "archive_metadata_json": json.dumps(
                [
                    {"file_name": "report.docx", "category": "报告", "owner": "甲"},
                    {"file_name": "REPORT.DOCX", "category": "报告"},
                ],
                ensure_ascii=False,
            ),
            "required_metadata_fields_json": '["owner", "document_date"]',
        }
        manifest = _result("archive.document_metadata", inputs)
        checked = _result(
            "archive.completeness_check",
            inputs,
            {"archive.document_metadata": manifest.result},
        )
        self.assertEqual(manifest.result["duplicate_count"], 1)
        self.assertFalse(manifest.result["file_content_read"])
        self.assertFalse(manifest.result["archive_system_write_performed"])
        self.assertEqual(checked.result["incomplete_count"], 2)
        self.assertEqual(checked.result["items"][0]["missing_fields"], ["document_date"])
        no_checklist = _result(
            "archive.completeness_check",
            {"required_metadata_fields_json": ""},
            {"archive.document_metadata": manifest.result},
        )
        self.assertEqual(no_checklist.status, "failed")

        unsafe_path = _result(
            "archive.document_metadata",
            {"archive_metadata_json": '[{"file_name":"../private.docx"}]'},
        )
        self.assertEqual(unsafe_path.status, "failed")

    def test_s09_uses_decimal_and_detects_duplicates_without_official_verification(self):
        inputs = {
            "project_id": "synthetic-project",
            "expense_category": "差旅",
            "invoice_records_json": json.dumps(
                [
                    {
                        "invoice_id": "demo-invoice-1",
                        "invoice_code": "CODE-1",
                        "invoice_number": "NO-1",
                        "expense_category": "差旅",
                        "amount": "10.10",
                        "tax": "0.90",
                    },
                    {
                        "invoice_id": "demo-invoice-2",
                        "invoice_code": "CODE-1",
                        "invoice_number": "NO-1",
                        "expense_category": "差旅",
                        "amount": "10.10",
                        "tax": "0.90",
                    },
                ],
                ensure_ascii=False,
            ),
            "expense_rules_json": json.dumps(
                {
                    "rule_id": "synthetic-expense-v1",
                    "allowed_categories": ["差旅"],
                    "max_per_invoice": "20",
                    "max_total": "20",
                },
                ensure_ascii=False,
            ),
        }
        imported = _result("expense.invoice_import", inputs)
        deduped = _result(
            "expense.invoice_verify_dedupe",
            inputs,
            {"expense.invoice_import": imported.result},
        )
        checked = _result(
            "expense.rule_check",
            inputs,
            {"expense.invoice_verify_dedupe": deduped.result},
        )
        self.assertEqual(checked.result["total"], "22.00")
        self.assertEqual(deduped.result["duplicate_count"], 1)
        self.assertIn(
            "duplicate_in_input",
            {item["code"] for item in checked.result["violations"]},
        )
        self.assertFalse(checked.result["invoice_authenticity_verified"])
        self.assertFalse(checked.result["erp_submitted"])

        inconsistent_total = json.loads(inputs["invoice_records_json"])
        inconsistent_total[0]["total"] = "999"
        inputs["invoice_records_json"] = json.dumps(
            inconsistent_total,
            ensure_ascii=False,
        )
        self.assertEqual(_result("expense.invoice_import", inputs).status, "failed")

        inputs["invoice_records_json"] = json.dumps(
            [
                {
                    "invoice_id": "demo-invalid",
                    "invoice_code": "C",
                    "invoice_number": "N",
                    "expense_category": "差旅",
                    "amount": "1",
                    "tax": "0",
                    "total": "-1",
                },
            ],
        )
        self.assertEqual(_result("expense.invoice_import", inputs).status, "failed")

        inputs["invoice_records_json"] = json.dumps(
            [
                {
                    "invoice_id": "demo-valid",
                    "invoice_code": "C",
                    "invoice_number": "N",
                    "expense_category": "差旅",
                    "amount": "1",
                    "tax": "0",
                },
            ],
        )
        inputs["expense_rules_json"] = json.dumps({"rule_id": "empty-policy"})
        imported = _result("expense.invoice_import", inputs)
        deduped = _result(
            "expense.invoice_verify_dedupe",
            inputs,
            {"expense.invoice_import": imported.result},
        )
        self.assertEqual(
            _result(
                "expense.rule_check",
                inputs,
                {"expense.invoice_verify_dedupe": deduped.result},
            ).status,
            "failed",
        )


class _SyntheticKnowledgeGateway:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    async def retrieve_task_evidence(
        self,
        user_id,
        knowledge_base_ids,
        query,
        *,
        source_material_ids,
        top_k,
    ):
        self.calls.append(
            (user_id, list(knowledge_base_ids), query, list(source_material_ids), top_k)
        )
        return {
            "status": "ready" if self.evidence else "not_found",
            "retrieved_at": "2026-10-12T00:00:00+00:00",
            "knowledge_bases": [
                {"id": item, "name": f"Synthetic KB {item}"}
                for item in knowledge_base_ids
            ],
            "evidence": self.evidence,
        }

    def format_task_evidence(self, result):
        return json.dumps(result, ensure_ascii=False)


class ScenarioKnowledgeAdapterTest(IsolatedAsyncioTestCase):
    async def test_rag_keeps_selected_scope_and_server_derived_evidence(self):
        source_text = "Synthetic authorized source: project scope and requirements."
        evidence = {
            "knowledge_base_id": "kb-selected-1",
            "knowledge_base_name": "Synthetic authorized KB",
            "document_id": "doc-synthetic-1",
            "source": "synthetic-source.docx",
            "chunk_index": 2,
            "content_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
            "source_text": source_text,
            "source_status": "ready",
        }
        gateway = _SyntheticKnowledgeGateway([evidence])
        result = await retrieve_scenario_evidence(
            "project.similar_search",
            gateway=gateway,
            user_id="synthetic-owner",
            knowledge_base_ids=["kb-selected-1"],
            source_material_ids=["doc-synthetic-1"],
            input_values={"project_name": "Synthetic project", "project_brief": "scope"},
            authorization_subject="synthetic-owner",
        )

        self.assertEqual(gateway.calls[0][0:2], ("synthetic-owner", ["kb-selected-1"]))
        self.assertEqual(gateway.calls[0][3], ["doc-synthetic-1"])
        self.assertEqual(result.result["status"], "ready")
        stored = result.result["evidence"][0]
        self.assertTrue(stored["evidence_id"])
        self.assertEqual(stored["content_sha256"], evidence["content_sha256"])
        self.assertEqual(stored["evidence_type"], "project_material")
        self.assertEqual(stored["authorization_subject"], "synthetic-owner")

    async def test_s07_retrieval_without_evidence_fails_closed(self):
        gateway = _SyntheticKnowledgeGateway([])
        result = await retrieve_scenario_evidence(
            "report.rag_retrieve",
            gateway=gateway,
            user_id="synthetic-owner",
            knowledge_base_ids=["kb-selected-1"],
            source_material_ids=[],
            input_values={"project_name": "Synthetic project", "required_chapters": "scope"},
            authorization_subject="synthetic-owner",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["status"], "not_found")

    async def test_rule_retrieval_without_rule_source_is_not_a_pass(self):
        gateway = _SyntheticKnowledgeGateway(
            [
                {
                    "knowledge_base_id": "kb-selected-1",
                    "knowledge_base_name": "Synthetic KB",
                    "document_id": "ordinary-doc",
                    "source": "ordinary-project-notes.txt",
                    "chunk_index": 0,
                    "content_sha256": "a" * 64,
                    "source_text": "Synthetic ordinary project note without a rule.",
                    "source_status": "ready",
                },
            ],
        )
        result = await retrieve_scenario_evidence(
            "project.rule_check",
            gateway=gateway,
            user_id="synthetic-owner",
            knowledge_base_ids=["kb-selected-1"],
            source_material_ids=[],
            input_values={"industry": "synthetic", "scope": "planning"},
            authorization_subject="synthetic-owner",
        )
        self.assertEqual(result.result["status"], "not_found")
        self.assertFalse(result.result["automated_compliance_conclusion"])

    async def test_rag_permission_failure_is_not_replaced_with_synthetic_evidence(self):
        class _DeniedGateway(_SyntheticKnowledgeGateway):
            async def retrieve_task_evidence(self, *args, **kwargs):
                del args, kwargs
                raise TaskKnowledgeError("permission_denied", "Synthetic denied scope.")

        result = await retrieve_scenario_evidence(
            "project.similar_search",
            gateway=_DeniedGateway([]),
            user_id="synthetic-owner",
            knowledge_base_ids=["kb-selected-1"],
            source_material_ids=["source-not-authorized"],
            input_values={"project_name": "Synthetic project", "project_brief": "scope"},
            authorization_subject="synthetic-owner",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.metadata["knowledge_error_code"], "permission_denied")
        self.assertIsNone(result.result)

    async def test_s07_claim_evidence_contract_uses_scenario_policy(self):
        source_text = "\u672c\u9879\u76ee\u5305\u62ec\u4e09\u4e2a\u9636\u6bb5\u3002"
        raw_source = {
            "knowledge_base_id": "kb-selected-1",
            "knowledge_base_name": "Synthetic authorized KB",
            "document_id": "report-doc-1",
            "source": "synthetic-report-source.docx",
            "chunk_index": 0,
            "content_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
            "source_text": source_text,
            "source_status": "ready",
            "evidence_type": "report_source",
        }
        evidence = normalize_evidence_snapshot(
            [raw_source],
            authorization_subject="synthetic-owner",
        )
        evidence_id = evidence[0]["evidence_id"]
        claim_text = "\u672c\u9879\u76ee\u5305\u62ec\u4e09\u4e2a\u9636\u6bb5\u3002"
        claim_payload = {
            "claims": [
                {
                    "claim_id": "CL-phases",
                    "kind": "fact",
                    "anchor": "CL-phases",
                    "text": claim_text,
                    "citation_ids": ["C1"],
                },
            ],
            "bindings": [
                {
                    "claim_id": "CL-phases",
                    "citation_id": "C1",
                    "evidence_id": evidence_id,
                    "quote": "\u5305\u62ec\u4e09\u4e2a\u9636\u6bb5",
                },
            ],
            "unverified_items": [],
        }
        citations = {
            "citations": [
                {
                    "citation_id": "C1",
                    "evidence_id": evidence_id,
                    "quote": "\u5305\u62ec\u4e09\u4e2a\u9636\u6bb5",
                },
            ],
        }
        document = (
            f"{claim_text}[C1] <!-- task-claim:CL-phases -->\n\n"
            "```task-claim-evidence\n"
            + json.dumps(claim_payload, separators=(",", ":"))
            + "\n```\n\n```task-citations\n"
            + json.dumps(citations, separators=(",", ":"))
            + "\n```"
        )
        policy = get_scenario_evidence_policy("s07_report_generation", "3.0.0")
        self.assertIsNotNone(policy)
        verification = validate_claim_evidence_document(
            document,
            evidence,
            authorization_subject="synthetic-owner",
            policy=policy,
        )
        self.assertTrue(verification["passed"], verification["all_diagnostics"])
        self.assertEqual(
            verification["verification_scope"]["semantic_review_claim_ids"],
            ["CL-phases"],
        )

    async def test_saved_retrievals_keep_their_node_identity_after_a_failure(self):
        nodes = [
            TaskNode(
                name="Synthetic failed retrieval",
                type=StepType.TOOL,
                capability_key="project.similar_search",
                config=ToolStepConfig(
                    tool_name="project.similar_search",
                    arguments={},
                ),
                order=0,
            ),
            TaskNode(
                name="Synthetic successful retrieval",
                type=StepType.TOOL,
                capability_key="report.rag_retrieve",
                config=ToolStepConfig(
                    tool_name="report.rag_retrieve",
                    arguments={},
                ),
                order=1,
            ),
        ]
        run = TaskRunRecord(
            task_id="synthetic-task",
            user_id="synthetic-owner",
            task_revision=1,
            nodes=nodes,
            node_runs=[],
            source_template_id="s07_report_generation",
            source_template_version="3.0.0",
        )
        source_text = "Synthetic report source."
        successful = {
            "status": "ready",
            "query": "synthetic report query",
            "retrieved_at": "2026-10-12T00:00:00+00:00",
            "knowledge_bases": [{"id": "kb-selected-1", "name": "Synthetic KB"}],
            "evidence": [
                {
                    "knowledge_base_id": "kb-selected-1",
                    "knowledge_base_name": "Synthetic KB",
                    "document_id": "synthetic-report-doc",
                    "source": "synthetic-report.docx",
                    "chunk_index": 0,
                    "content_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "source_text": source_text,
                    "source_status": "ready",
                    "evidence_type": "report_source",
                    "authorization_subject": "synthetic-owner",
                },
            ],
        }
        current = run.model_copy(
            update={
                "node_runs": [
                    NodeRunRecord(
                        node_id=nodes[0].id,
                        name=nodes[0].name,
                        prompt="",
                        order=0,
                        result={"status": "failed"},
                    ),
                    NodeRunRecord(
                        node_id=nodes[1].id,
                        name=nodes[1].name,
                        prompt="",
                        order=1,
                        result=successful,
                    ),
                ],
            },
        )

        aggregate = _retrieval_result_for_run(run, current, before_order=2)

        self.assertIsNotNone(aggregate)
        self.assertEqual(
            aggregate["scenario_retrievals"][0]["capability_key"],
            "report.rag_retrieve",
        )
        self.assertEqual(len(aggregate["evidence"]), 1)


class _SyntheticExecutor:
    def __init__(self):
        self.files = {}

    async def list_tools(self, _context):
        return []

    async def execute_node(self, *, node, previous_output, context):
        del context
        return StepExecutionResult(
            text=f"Synthetic draft from {node.name}. Previous: {previous_output}",
        )

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True)

    async def preflight_artifact_writer(self, *, context):
        del context
        return True, "Synthetic isolated writer is available."

    async def write_artifact(self, *, path, data, context):
        del context
        self.files[path] = data
        return len(data)

    async def read_artifact(self, *, path, context):
        del context
        return self.files[path]


class ScenarioSopIntegrationTest(IsolatedAsyncioTestCase):
    async def test_s04_tool_results_flow_through_official_sop_and_synthetic_writer(self):
        executor = _SyntheticExecutor()
        service = TaskService(
            TaskStore(),
            preview_executor=executor,
            artifact_writer=executor,
        )
        try:
            template = TemplateRegistry().get_current_template(
                "s04_meeting_preparation"
            )
            self.assertIsNotNone(template)
            assert template is not None
            template_service = TaskTemplateService(TemplateRegistry(), service)
            task = await template_service.instantiate(
                "synthetic-owner",
                template.template_id,
                TemplateInstantiateRequest(
                    template_version=template.template_version,
                    inputs={
                        "meeting_topic": "Synthetic Task meeting",
                        "meeting_date": "2026-10-12 09:00",
                        "participant_count": 2,
                        "fee_rates_json": json.dumps(
                            {
                                "rule_id": "synthetic-fee",
                                "rates": {"catering_per_person": "5.25"},
                            },
                        ),
                    },
                ),
            )
            run = await service.create_run(
                "synthetic-owner",
                task.id,
                TaskRunRequest(input="Synthetic meeting preparation run"),
            )
            await service._active_runs[run.id]
            run = await service.get_run("synthetic-owner", run.id)
            while run is not None and run.status is RunStatus.AWAITING_INPUT:
                values = {
                    "meeting_topic": "Synthetic Task meeting",
                    "meeting_date": "2026-10-12 09:00",
                    "participant_count": "2",
                    "fee_rates_json": json.dumps(
                        {
                            "rule_id": "synthetic-fee",
                            "rates": {"catering_per_person": "5.25"},
                        },
                    ),
                    "material_feedback": "合成验收：无修改",
                }
                answers = {
                    question.key: values.get(question.key, "")
                    for question in run.pending_input.questions
                }
                updated = await service.answer_run(
                    "synthetic-owner",
                    run.id,
                    TaskInputReply(
                        request_id=run.pending_input.id,
                        answers=answers,
                    ),
                )
                active = service._active_runs.get(run.id)
                if active:
                    await active
                run = await service.get_run("synthetic-owner", updated.id)

            self.assertIsNotNone(run)
            assert run is not None
            self.assertEqual(run.status, RunStatus.SUCCEEDED)
            cost_index = next(
                index
                for index, node in enumerate(run.nodes)
                if node.capability_key == "meeting.cost_calculate"
            )
            material_index = next(
                index
                for index, node in enumerate(run.nodes)
                if node.capability_key == "meeting.material_generate"
            )
            self.assertEqual(run.node_runs[cost_index].result["total"], "10.50")
            self.assertEqual(
                run.node_runs[material_index].input,
                run.node_runs[cost_index].output,
            )
            self.assertTrue(run.artifacts)
            stored_bytes = next(iter(executor.files.values()))
            self.assertTrue(stored_bytes)
            self.assertTrue(stored_bytes.startswith(b"PK"))
        finally:
            await service.shutdown()


def test_v3_template_versions_are_bounded_and_inputs_describe_actual_sources():
    root = Path(__file__).resolve().parents[1] / "examples" / "agent_service" / "task" / "templates"
    registry = TemplateRegistry()
    current = {item.template_id: item for item in registry.list_templates()}
    assert len(current) == 9
    assert all(len(item.steps) <= 8 for item in current.values())

    for version in ("1.0.0", "2.0.0"):
        for template_id in current:
            path = root / f"{template_id}.v{version[0]}.json"
            if path.exists():
                assert json.loads(path.read_text(encoding="utf-8"))["template_version"] == version

    s05 = current["s05_meeting_minutes"]
    assert "meeting.transcript_import" in s05.required_capabilities
    assert "document.render" in s05.required_capabilities
    assert "meeting.audio_transcribe" not in s05.required_capabilities
    assert "audio" not in s05.description.casefold()
