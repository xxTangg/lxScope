"""Regression coverage for versioned node and Claim occurrence contracts."""

import hashlib
import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

from examples.agent_service.task._citation_verifier import (
    verify_task_citations,
)
from examples.agent_service.task._evidence_contract import (
    claim_evidence_protocol_envelope_failure_is_retryable,
    claim_evidence_protocol_repair_body_matches,
    compare_claim_evidence_inventories,
    detect_claim_evidence_protocol,
    extract_claim_evidence_inventory,
    extract_claim_evidence_protocol_repair_inventory,
    materialize_task_citations_from_bindings,
    materialize_task_claim_evidence_protocol_repair,
    normalize_evidence_snapshot,
    normalize_task_protocol_format,
    validate_claim_evidence_document,
    wrap_claim_evidence_repair_payload_for_validation,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    CreateTaskRequest,
    ExecutionContext,
    InputStepConfig,
    NodeOutputContract,
    TaskNode,
    TaskInputQuestion,
    TaskInputReply,
    TaskRunRecord,
    TaskRunRequest,
    ToolStepConfig,
)
from examples.agent_service.task._service import TaskService, _with_citation_prompt
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_models import TaskTemplateDefinition
from examples.agent_service.task._s02_project_planning_evidence import (
    materialize_s02_project_planning_ledger,
)
from examples.agent_service.task._scenario_evidence_policies import (
    get_scenario_evidence_policy,
)
from examples.agent_service.task._output_validation import (
    validate_structured_result,
)
from examples.agent_service.task._agentscope_executor import (
    _render_task_value,
)
from examples.agent_service.task._models import AgentStepConfig, StepType
from examples.agent_service.task._models import StepVerdict
from examples.agent_service.task._sop import TaskSOPStep, TaskStepState


_OWNER = "p7r1-contract-owner"
_SOURCE_TEXT = "项目编号 P-1001，项目范围包括资料收集和边界核对。"


def _source(text=_SOURCE_TEXT):
    return {
        "knowledge_base_id": "p7r1-kb",
        "knowledge_base_name": "测试授权知识库",
        "document_id": "project-record",
        "source": "project-record.md",
        "chunk_index": 0,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_text": text,
        "source_text_truncated": False,
        "source_status": "ready",
        "evidence_type": "project_material",
    }


def _document(body, evidence, claims, bindings, unverified_items=None):
    normalized = normalize_evidence_snapshot(
        evidence,
        authorization_subject=_OWNER,
        collected_at="2026-10-12T00:00:00+00:00",
    )
    verified_citations = {}
    for binding in bindings:
        source = next(
            item
            for item in normalized
            if item["evidence_id"] == binding["evidence_id"]
        )
        verified_citations.setdefault(
            binding["citation_id"],
            {
                "citation_id": binding["citation_id"],
                "knowledge_base_id": source["knowledge_base_id"],
                "document_id": source["document_id"],
                "chunk_index": source["chunk_index"],
                "content_sha256": source["content_sha256"],
                "quote": binding["quote"],
            },
        )
    payload = {
        "claims": claims,
        "bindings": bindings,
        "unverified_items": unverified_items or [],
    }
    citation_block = {
        "citations": list(verified_citations.values())
    }
    document = (
        body
        + "\n\n```task-claim-evidence\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n```\n\n```task-citations\n"
        + json.dumps(citation_block, ensure_ascii=False, separators=(",", ":"))
        + "\n```"
    )
    return document, normalized


def _binding(claim_id, occurrence_id, citation_id, evidence, quote):
    return {
        "claim_id": claim_id,
        "occurrence_id": occurrence_id,
        "citation_id": citation_id,
        "evidence_id": evidence["evidence_id"],
        "quote": quote,
    }


class TaskP7R1OutputContractTest(TestCase):
    def test_citation_output_contract_requires_authorized_snapshot_and_agent(self):
        contract = NodeOutputContract(
            contract_id="test.report-citations.v1",
            output_type="markdown",
            validation_mode="citations",
            provenance_requirement="authorized_run_snapshot",
        )
        self.assertEqual(contract.validation_mode, "citations")

        with self.assertRaises(ValueError):
            NodeOutputContract(
                contract_id="test.report-citations.v1",
                output_type="markdown",
                validation_mode="citations",
            )
        with self.assertRaises(ValueError):
            NodeOutputContract(
                contract_id="test.report-citations.v1",
                output_type="structured_json",
                validation_mode="citations",
                provenance_requirement="authorized_run_snapshot",
            )
        with self.assertRaises(ValueError):
            TaskNode(
                name="引用核验工具",
                type=StepType.TOOL,
                config=ToolStepConfig(tool_name="report.citation_check"),
                output_contract=contract,
            )

    def test_s02_final_prompt_avoids_duplicate_report_and_keeps_both_protocols(self):
        retrieval = TaskNode(
            name="检索策划模板",
            type=StepType.TOOL,
            config=ToolStepConfig(
                tool_name="planning.template_retrieve",
                arguments={},
            ),
            order=0,
            capability_key="planning.template_retrieve",
        )
        final_node = TaskNode(
            name="整理策划输出",
            type=StepType.AGENT,
            config=AgentStepConfig(prompt="整理已有策划材料。"),
            order=1,
        )
        run = TaskRunRecord(
            task_id="s02-prompt-task",
            user_id=_OWNER,
            task_revision=1,
            nodes=[retrieval, final_node],
            node_runs=[],
            source_template_id="s02_project_planning",
            source_template_version="2.0.0",
        )

        prompted = _with_citation_prompt(run, final_node)

        self.assertIn("八章正文各写一次", prompted.prompt)
        self.assertIn("不要分别重印完整大纲、完整初稿", prompted.prompt)
        self.assertIn("Task 服务端会从协议区块生成来源证据台账", prompted.prompt)
        self.assertIn("task-claim-evidence", prompted.prompt)
        self.assertIn("task-citations", prompted.prompt)
        self.assertIn("每个 binding 的 quote 必须是本次运行授权证据", prompted.prompt)
        self.assertIn("JSON 与围栏必须闭合", prompted.prompt)
        self.assertNotIn("引用核验协议（Task 服务端会逐项", prompted.prompt)
        self.assertLess(len(prompted.prompt), 6_000)

    def test_s07_final_uses_the_shared_strict_claim_schema(self):
        retrieval = TaskNode(
            name="检索报告资料",
            type=StepType.TOOL,
            config=ToolStepConfig(
                tool_name="report.rag_retrieve",
                arguments={},
            ),
            order=0,
            capability_key="report.rag_retrieve",
        )
        final_node = TaskNode(
            name="整理报告草稿",
            type=StepType.AGENT,
            config=AgentStepConfig(prompt="整理报告内容。"),
            order=1,
        )
        run = TaskRunRecord(
            task_id="s07-prompt-task",
            user_id=_OWNER,
            task_revision=1,
            nodes=[retrieval, final_node],
            node_runs=[],
            source_template_id="s07_report_generation",
            source_template_version="3.0.0",
        )

        prompted = _with_citation_prompt(run, final_node)

        self.assertIn("Claim 为 `claim_id`、`kind`", prompted.prompt)
        self.assertIn("`anchor`（与 claim_id 相同）", prompted.prompt)
        self.assertIn("Occurrence 为 `occurrence_id`、`text`", prompted.prompt)
        self.assertIn("不得添加 `section` 等额外字段", prompted.prompt)
        self.assertIn("紧凑单行 JSON", prompted.prompt)
        self.assertIn(
            "缺失时 Task 服务只会在完整绑定无歧义的条件下",
            prompted.prompt,
        )
        self.assertNotIn("旧版单位置写法", prompted.prompt)
        self.assertNotIn("TASK_CITATION_PROMPT", prompted.prompt)

    def test_materializes_citation_index_from_validated_run_bindings(self):
        source = _source()
        normalized = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "项目编号 P-1001。[C1] "
            "<!-- task-claim:CL-project:O-01 -->\n\n"
            "分析可先核对资料完整性。 "
            "<!-- task-claim:CL-analysis:O-01 -->\n\n"
            "建议确认数据边界后再编排现场工作。 "
            "<!-- task-claim:CL-recommendation:O-01 -->"
        )
        claims = [
            {
                "claim_id": "CL-project",
                "kind": "fact",
                "anchor": "CL-project",
                "text": "项目编号 P-1001。",
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": "O-01",
                        "text": "项目编号 P-1001。",
                        "citation_ids": ["C1"],
                    }
                ],
            },
            {
                "claim_id": "CL-analysis",
                "kind": "analysis",
                "anchor": "CL-analysis",
                "text": "分析可先核对资料完整性。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-01",
                        "text": "分析可先核对资料完整性。",
                        "citation_ids": [],
                    }
                ],
            },
            {
                "claim_id": "CL-recommendation",
                "kind": "recommendation",
                "anchor": "CL-recommendation",
                "text": "建议确认数据边界后再编排现场工作。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-01",
                        "text": "建议确认数据边界后再编排现场工作。",
                        "citation_ids": [],
                    }
                ],
            },
        ]
        bindings = [
            _binding(
                "CL-project",
                "O-01",
                "C1",
                normalized,
                "项目编号 P-1001",
            )
        ]
        document, run_evidence = _document(body, [source], claims, bindings)
        document = document[: document.index("\n\n```task-citations")]

        finalized, actions = materialize_task_citations_from_bindings(
            document,
            run_evidence,
            authorization_subject=_OWNER,
        )

        self.assertEqual(
            actions,
            ["materialized_task-citations_from_run_bindings"],
        )
        self.assertEqual(finalized.count("```task-citations"), 1)
        citation_verification = verify_task_citations(
            finalized,
            run_evidence,
            authorization_subject=_OWNER,
        )
        self.assertTrue(
            citation_verification["passed"],
            citation_verification["issues"],
        )
        claim_validation = validate_claim_evidence_document(
            finalized,
            [source],
            authorization_subject=_OWNER,
        )
        self.assertTrue(
            claim_validation["passed"],
            claim_validation["all_diagnostics"],
        )

    def test_materializer_accepts_uncited_analysis_and_cited_recommendation(self):
        source = _source()
        normalized = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "项目编号 P-1001。[C1] <!-- task-claim:CL-fact:O-fact -->\n\n"
            "分析先核对现有资料。 <!-- task-claim:CL-analysis:O-analysis -->\n\n"
            "建议基于项目编号 P-1001 的资料继续核对。[C1] "
            "<!-- task-claim:CL-recommendation:O-recommendation -->"
        )
        claims = [
            {
                "claim_id": "CL-fact",
                "kind": "fact",
                "anchor": "CL-fact",
                "text": "项目编号 P-1001。",
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": "O-fact",
                        "text": "项目编号 P-1001。",
                        "citation_ids": ["C1"],
                    }
                ],
            },
            {
                "claim_id": "CL-analysis",
                "kind": "analysis",
                "anchor": "CL-analysis",
                "text": "分析先核对现有资料。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-analysis",
                        "text": "分析先核对现有资料。",
                        "citation_ids": [],
                    }
                ],
            },
            {
                "claim_id": "CL-recommendation",
                "kind": "recommendation",
                "anchor": "CL-recommendation",
                "text": "建议基于项目编号 P-1001 的资料继续核对。",
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": "O-recommendation",
                        "text": "建议基于项目编号 P-1001 的资料继续核对。",
                        "citation_ids": ["C1"],
                    }
                ],
            },
        ]
        bindings = [
            _binding(
                "CL-fact",
                "O-fact",
                "C1",
                normalized,
                "项目编号 P-1001",
            ),
            _binding(
                "CL-recommendation",
                "O-recommendation",
                "C1",
                normalized,
                "项目编号 P-1001",
            ),
        ]
        document, run_evidence = _document(body, [source], claims, bindings)
        document = document[: document.index("\n\n```task-citations")]

        finalized, actions = materialize_task_citations_from_bindings(
            document,
            run_evidence,
            authorization_subject=_OWNER,
            policy=get_scenario_evidence_policy(
                "s02_project_planning", "2.0.0"
            ),
        )

        self.assertEqual(
            actions,
            ["materialized_task-citations_from_run_bindings"],
        )
        validation = validate_claim_evidence_document(
            finalized,
            run_evidence,
            authorization_subject=_OWNER,
            policy=get_scenario_evidence_policy(
                "s02_project_planning", "2.0.0"
            ),
        )
        self.assertTrue(validation["passed"], validation["all_diagnostics"])
        self.assertEqual(
            set(validation["verification_scope"].get("semantic_review_claim_ids", [])),
            {"CL-fact", "CL-analysis", "CL-recommendation"},
        )

    def test_materializer_fails_closed_for_ambiguous_or_untrusted_bindings(self):
        source = _source()
        other_source = _source("项目范围需要独立确认。")
        second_source = dict(other_source, document_id="other-record")
        normalized_sources = normalize_evidence_snapshot(
            [source, second_source], authorization_subject=_OWNER
        )
        by_id = {item["document_id"]: item for item in normalized_sources}
        body = (
            "项目编号 P-1001。[C1] <!-- task-claim:CL-one:O-one -->\n\n"
            "项目范围需要独立确认。[C1] <!-- task-claim:CL-two:O-two -->"
        )
        claims = [
            {
                "claim_id": claim_id,
                "kind": "fact",
                "anchor": claim_id,
                "text": text,
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": occurrence_id,
                        "text": text,
                        "citation_ids": ["C1"],
                    }
                ],
            }
            for claim_id, occurrence_id, text in (
                ("CL-one", "O-one", "项目编号 P-1001。"),
                ("CL-two", "O-two", "项目范围需要独立确认。"),
            )
        ]
        ambiguous_bindings = [
            _binding(
                "CL-one",
                "O-one",
                "C1",
                by_id["project-record"],
                "项目编号 P-1001",
            ),
            _binding(
                "CL-two",
                "O-two",
                "C1",
                by_id["other-record"],
                "项目范围需要独立确认。",
            ),
        ]
        ambiguous = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                {
                    "claims": claims,
                    "bindings": ambiguous_bindings,
                    "unverified_items": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        unchanged, actions = materialize_task_citations_from_bindings(
            ambiguous,
            [source, second_source],
            authorization_subject=_OWNER,
        )
        self.assertEqual(unchanged, ambiguous)
        self.assertEqual(actions, [])

        single_source = _document(
            "项目编号 P-1001。[C1] <!-- task-claim:CL-one:O-one -->",
            [source],
            [claims[0]],
            [ambiguous_bindings[0]],
        )[0]
        single_source = single_source[: single_source.index("\n\n```task-citations")]
        forged_quote = single_source.replace(
            '"quote":"项目编号 P-1001"', '"quote":"伪造原文"'
        )
        unchanged, actions = materialize_task_citations_from_bindings(
            forged_quote,
            [source],
            authorization_subject=_OWNER,
        )
        self.assertEqual(unchanged, forged_quote)
        self.assertEqual(actions, [])

    def test_materializer_does_not_repair_partial_or_existing_protocol_blocks(self):
        source = _source()
        normalized = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = "项目编号 P-1001。[C1] <!-- task-claim:CL-p:O-01 -->"
        claim = {
            "claim_id": "CL-p",
            "kind": "fact",
            "anchor": "CL-p",
            "text": "项目编号 P-1001。",
            "citation_ids": ["C1"],
            "occurrences": [
                {
                    "occurrence_id": "O-01",
                    "text": "项目编号 P-1001。",
                    "citation_ids": ["C1"],
                }
            ],
        }
        payload = {
            "claims": [claim],
            "bindings": [
                _binding(
                    "CL-p",
                    "O-01",
                    "C1",
                    normalized,
                    "项目编号 P-1001",
                )
            ],
            "unverified_items": [],
        }
        base = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n```"
        )
        incomplete_claim = base[: base.index("\n```")]
        partial_citation = base + "\n\n```task-citations\n{"
        existing_citation = base + "\n\n```task-citations\n{}\n```"
        incompatible_claim_shape = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "CL-p",
                            "occurrences": [
                                {
                                    "occurrence_id": "O-01",
                                    "section": "report section",
                                    "text": "项目编号 P-1001。",
                                    "citation_ids": ["C1"],
                                }
                            ],
                            "citation_ids": ["C1"],
                        }
                    ],
                    "bindings": payload["bindings"],
                    "unverified_items": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        for document in (
            incomplete_claim,
            partial_citation,
            existing_citation,
            incompatible_claim_shape,
        ):
            with self.subTest(document=document[-40:]):
                finalized, actions = materialize_task_citations_from_bindings(
                    document,
                    [source],
                    authorization_subject=_OWNER,
                )
                self.assertEqual(finalized, document)
                self.assertEqual(actions, [])

    def test_incomplete_current_protocol_never_falls_back_to_legacy(self):
        source = _source()
        document = (
            "# 策划材料\n\n"
            "项目编号 P-1001。[C1] <!-- task-claim:CL-project:O-01 -->\n\n"
            "```task-claim-evidence\n"
            '{"claims":[{"claim_id":"CL-project","kind":"fact",'
            '"anchor":"CL-project","text":"项目编号 P-1001。",'
            '"citation_ids":["C1"],"occurrences":[{"occurrence_id":'
            '"O-01","text":"项目编号 P-1001。","citation_ids":["C1"]}]}],'
            '"bindings":[{"claim_id":"CL-project","occurrence_id":"O-01",'
            '"citation_id":"C1","evidence_id":"'
            + "EV-000000000000000000000000"
            + '","quote":"项目编号 P-1001"}],"unverified_items":[]}'
            # The model stopped before closing the Claim–Evidence fence and
            # never emitted the required task-citations block.
        )

        self.assertEqual(
            detect_claim_evidence_protocol(document), "claim_evidence"
        )
        result = materialize_s02_project_planning_ledger(
            document, [source], authorization_subject=_OWNER
        )
        codes = {
            item["error_code"]
            for item in result["all_diagnostics"]
        }
        messages = "\n".join(result["issues"])
        self.assertFalse(result["passed"])
        self.assertIn("incomplete_claim_block", codes)
        self.assertIn("citation_block_missing", codes)
        self.assertNotIn("legacy_compatibility_validation", codes)
        self.assertIn("task-citations", messages)
        self.assertTrue(
            claim_evidence_protocol_envelope_failure_is_retryable(
                result["diagnostics"]
            )
        )

    def test_truncated_current_protocol_can_only_retry_with_body_fingerprint(self):
        body = (
            "报告事实 P-1001。[C1] "
            "<!-- task-claim:CL-project:O-main -->\n"
        )
        output = (
            body
            + "\n```task-claim-evidence\n"
            + '{"claims":[{"claim_id":"CL-project","kind":"verified",'
            + '"occurrences":[{"section":"项目概况"'
        )
        inventory = extract_claim_evidence_protocol_repair_inventory(output)
        self.assertIsNotNone(inventory)
        assert inventory is not None
        self.assertEqual(inventory["mode"], "protocol_envelope_only")
        self.assertEqual(inventory["body"], body + "\n")
        self.assertTrue(
            claim_evidence_protocol_repair_body_matches(output, inventory)
        )
        self.assertFalse(
            claim_evidence_protocol_repair_body_matches(
                output.replace("报告事实", "改写事实"), inventory
            )
        )

        diagnostics = {
            "structure": [
                {"error_code": "claim_block_count"},
            ],
            "source": [],
            "citation": [
                {"error_code": "citation_protocol_failed"},
            ],
            "unverified": [],
        }
        self.assertTrue(
            claim_evidence_protocol_envelope_failure_is_retryable(diagnostics)
        )
        diagnostics["source"] = [{"error_code": "source_identity_failed"}]
        self.assertFalse(
            claim_evidence_protocol_envelope_failure_is_retryable(diagnostics)
        )

    def test_misordered_complete_citations_are_locked_and_revalidated(self):
        source = _source()
        normalized = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "项目编号 P-1001。[C1] "
            "<!-- task-claim:CL-project:O-main -->"
        )
        fence = chr(96) * 3
        citation_payload = {
            "citations": [
                {
                    "citation_id": "C1",
                    "evidence_id": normalized["evidence_id"],
                    "quote": "项目编号 P-1001，项目范围包括资料收集和边界核对。",
                }
            ]
        }
        broken_output = (
            body
            + "\n\n"
            + fence
            + "task-citations\n"
            + json.dumps(
                citation_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            + fence
            + "\n\n"
            + fence
            + "task-claim-evidence\n{\"claims\":["
        )
        inventory = extract_claim_evidence_protocol_repair_inventory(
            broken_output
        )
        self.assertIsNotNone(inventory)
        assert inventory is not None
        self.assertEqual(
            inventory["mode"],
            "protocol_envelope_with_locked_citations",
        )
        self.assertEqual(inventory["locked_citations"], citation_payload["citations"])
        self.assertTrue(
            claim_evidence_protocol_repair_body_matches(
                body
                + "\n\n"
                + fence
                + "task-claim-evidence\n{}"
                + "\n\n"
                + fence
                + "task-citations\n"
                + json.dumps(
                    citation_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
                + fence,
                inventory,
            )
        )

        compact_payload = {
            "claims": [{"claim_id": "CL-project", "kind": "fact"}],
            "bindings": [
                {
                    "claim_id": "CL-project",
                    "occurrence_id": "O-main",
                    "citation_id": "C1",
                    "evidence_id": normalized["evidence_id"],
                    "quote": "项目编号 P-1001",
                }
            ],
            "unverified_items": [],
        }
        repaired, actions = materialize_task_claim_evidence_protocol_repair(
            inventory["body"],
            json.dumps(compact_payload, ensure_ascii=False),
            inventory,
        )
        self.assertEqual(
            actions,
            ["materialized_claim_evidence_from_compact_repair"],
        )
        self.assertTrue(
            claim_evidence_protocol_repair_body_matches(repaired, inventory)
        )
        self.assertLess(
            repaired.index(fence + "task-claim-evidence"),
            repaired.index(fence + "task-citations"),
        )
        self.assertTrue(
            verify_task_citations(
                repaired,
                [normalized],
                authorization_subject=_OWNER,
            )["passed"]
        )
        self.assertTrue(
            validate_claim_evidence_document(
                repaired,
                [normalized],
                authorization_subject=_OWNER,
            )["passed"]
        )

        invalid_citation_payload = {
            "citations": [
                {
                    **citation_payload["citations"][0],
                    "evidence_id": "EV-000000000000000000000000",
                }
            ]
        }
        invalid_source_output = (
            body
            + "\n\n"
            + fence
            + "task-citations\n"
            + json.dumps(invalid_citation_payload, ensure_ascii=False)
            + "\n"
            + fence
            + "\n\n"
            + fence
            + "task-claim-evidence\n{\"claims\":["
        )
        invalid_inventory = extract_claim_evidence_protocol_repair_inventory(
            invalid_source_output
        )
        self.assertIsNotNone(invalid_inventory)
        assert invalid_inventory is not None
        invalid_repaired, invalid_actions = (
            materialize_task_claim_evidence_protocol_repair(
                invalid_inventory["body"],
                json.dumps(compact_payload, ensure_ascii=False),
                invalid_inventory,
            )
        )
        self.assertEqual(invalid_actions, [])
        self.assertEqual(invalid_repaired, invalid_inventory["body"])
        self.assertFalse(
            verify_task_citations(
                invalid_repaired,
                [normalized],
                authorization_subject=_OWNER,
            )["passed"]
        )

    def test_protocol_repair_refuses_ambiguous_or_existing_citation_blocks(self):
        body = "事实 [C1] <!-- task-claim:CL-fact:O-01 -->"
        open_claim = body + "\n```task-claim-evidence\n{"
        self.assertIsNone(
            extract_claim_evidence_protocol_repair_inventory(
                open_claim + "\n```task-citations\n{\n```"
            )
        )
        self.assertIsNone(
            extract_claim_evidence_protocol_repair_inventory(
                open_claim + "\n```task-claim-evidence\n{}"
            )
        )
        self.assertIsNone(
            extract_claim_evidence_protocol_repair_inventory(
                "```task-evidence-claims\n{}\n```\n" + open_claim
            )
        )

    def test_compact_protocol_repair_builds_server_owned_occurrences(self):
        source = _source()
        normalized = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "项目编号 P-1001。[C1] "
            "<!-- task-claim:CL-project:O-main -->\n\n"
            "预算仍需审批确认。<!-- task-claim:CL-budget:O-budget -->"
        )
        compact_payload = {
            "claims": [
                {"claim_id": "CL-project", "kind": "fact"},
                {"claim_id": "CL-budget", "kind": "unverified"},
            ],
            "citations": [
                {
                    "citation_id": "C1",
                    "evidence_id": normalized["evidence_id"],
                    "quote": "项目编号 P-1001",
                }
            ],
            "bindings": [
                {
                    "claim_id": "CL-project",
                    "occurrence_id": "O-main",
                    "citation_id": "C1",
                    "evidence_id": normalized["evidence_id"],
                    "quote": "项目编号 P-1001",
                }
            ],
            "unverified_items": [
                {
                    "unverified_id": "UV-budget",
                    "claim_id": "CL-budget",
                    "occurrence_id": "O-budget",
                    "reason": "未提供预算审批依据。",
                    "expected_evidence_types": ["project_material"],
                }
            ],
        }
        document, actions = materialize_task_claim_evidence_protocol_repair(
            body,
            json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":")),
        )
        self.assertEqual(
            actions,
            ["materialized_claim_evidence_from_compact_repair"],
        )
        self.assertTrue(document.startswith(body))
        citation_verification = verify_task_citations(
            document, [normalized], authorization_subject=_OWNER
        )
        self.assertTrue(citation_verification["passed"], citation_verification)
        evidence_validation = validate_claim_evidence_document(
            document,
            [normalized],
            authorization_subject=_OWNER,
            citation_verification=citation_verification,
        )
        self.assertTrue(evidence_validation["passed"], evidence_validation)

    def test_unmaterializable_repair_keeps_double_newline_body_and_fails_closed(self):
        source = _source()
        normalized = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "| 建筑面积 | 待核实 | 资料中未提供 [C1] | "
            "<!-- task-claim:CL-table:O-table -->\n\n"
        )
        broken_envelope = body + "```task-claim-evidence\n{"
        inventory = extract_claim_evidence_protocol_repair_inventory(
            broken_envelope
        )
        self.assertIsNotNone(inventory)
        assert inventory is not None
        self.assertEqual(inventory["body"], body)
        self.assertEqual(inventory["separator"], "")

        # This compact declaration is internally contradictory: it marks an
        # occurrence with [C1] as unverified. Metadata-only repair must refuse.
        compact_payload = json.dumps(
            {
                "claims": [
                    {"claim_id": "CL-table", "kind": "unverified"}
                ],
                "bindings": [
                    {
                        "claim_id": "CL-table",
                        "occurrence_id": "O-table",
                        "citation_id": "C1",
                        "evidence_id": normalized["evidence_id"],
                        "quote": "项目编号 P-1001",
                    }
                ],
                "citations": [
                    {
                        "citation_id": "C1",
                        "evidence_id": normalized["evidence_id"],
                        "quote": "项目编号 P-1001",
                    }
                ],
                "unverified_items": [
                    {
                        "unverified_id": "UV-table",
                        "claim_id": "CL-table",
                        "occurrence_id": "O-table",
                        "reason": "该表格条目仍待核实。",
                        "expected_evidence_types": ["project_material"],
                    }
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        unmaterialized, materialize_actions = (
            materialize_task_claim_evidence_protocol_repair(
                body, compact_payload, inventory
            )
        )
        self.assertEqual(unmaterialized, body)
        self.assertEqual(materialize_actions, [])

        wrapped, wrap_actions = wrap_claim_evidence_repair_payload_for_validation(
            body, compact_payload, inventory
        )
        self.assertEqual(
            wrap_actions,
            ["wrapped_unmaterialized_payload_for_full_validation"],
        )
        self.assertTrue(wrapped.startswith(body))
        self.assertTrue(
            claim_evidence_protocol_repair_body_matches(wrapped, inventory)
        )

        citation_verification = verify_task_citations(
            wrapped, [normalized], authorization_subject=_OWNER
        )
        self.assertFalse(citation_verification["passed"])
        self.assertTrue(
            any(
                "task-citations JSON 区块" in issue
                for issue in citation_verification["issues"]
            )
        )
        evidence_validation = validate_claim_evidence_document(
            wrapped,
            [normalized],
            authorization_subject=_OWNER,
            citation_verification=citation_verification,
        )
        self.assertFalse(evidence_validation["passed"])

    def test_incomplete_new_claim_block_still_checks_citation_source(self):
        source = _source()
        document = (
            "项目编号 P-1001。[C1] <!-- task-claim:CL-project:O-01 -->\n\n"
            "```task-claim-evidence\n{\"claims\":[\n\n"
            "```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "evidence_id": "EV-000000000000000000000000",
                            "quote": "不是来源中的原文",
                        }
                    ]
                },
                ensure_ascii=False,
            )
            + "\n```"
        )
        result = materialize_s02_project_planning_ledger(
            document, [source], authorization_subject=_OWNER
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["diagnostics"]["citation"] == [])
        self.assertTrue(
            any(
                "不属于本次运行检索到的授权来源" in issue
                for issue in result["issues"]
            )
        )
        self.assertNotIn(
            "legacy_compatibility_validation",
            {item["error_code"] for item in result["all_diagnostics"]},
        )

    def test_retry_inventory_tracks_occurrences_claims_and_unverified_ids(self):
        payload = {
            "claims": [
                {
                    "claim_id": "CL-fact",
                    "kind": "fact",
                    "anchor": "CL-fact",
                    "text": "项目编号为 P-1001。",
                    "citation_ids": ["C1"],
                    "occurrences": [
                        {
                            "occurrence_id": "O-outline",
                            "text": "项目编号为 P-1001。",
                            "citation_ids": ["C1"],
                        },
                        {
                            "occurrence_id": "O-body",
                            "text": "项目编号为 P-1001。",
                            "citation_ids": ["C1"],
                        },
                    ],
                },
                {
                    "claim_id": "CL-pending",
                    "kind": "unverified",
                    "anchor": "CL-pending",
                    "text": "预算待核实。",
                    "citation_ids": [],
                    "occurrences": [],
                },
            ],
            "bindings": [],
            "unverified_items": [
                {
                    "unverified_id": "UV-budget",
                    "claim_id": "CL-pending",
                    "reason": "尚无预算审批资料。",
                    "expected_evidence_types": ["project_material"],
                }
            ],
        }
        document = (
            "```task-claim-evidence\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n```"
        )
        inventory = extract_claim_evidence_inventory(document)
        self.assertEqual(
            inventory,
            {
                "claim_ids": ["CL-fact", "CL-pending"],
                "claims": {
                    "CL-fact": {
                        "kind": "fact",
                        "text": "项目编号为 P-1001。",
                        "citation_ids": ["C1"],
                    },
                    "CL-pending": {
                        "kind": "unverified",
                        "text": "预算待核实。",
                        "citation_ids": [],
                    },
                },
                "occurrences": [
                    {
                        "claim_id": "CL-fact",
                        "occurrence_id": "O-body",
                        "text": "项目编号为 P-1001。",
                        "citation_ids": ["C1"],
                    },
                    {
                        "claim_id": "CL-fact",
                        "occurrence_id": "O-outline",
                        "text": "项目编号为 P-1001。",
                        "citation_ids": ["C1"],
                    },
                ],
                "unverified_ids": ["UV-budget"],
                "unverified_items": {
                    "UV-budget": {
                        "status": "pending_verification",
                        "reason": "尚无预算审批资料。",
                        "expected_evidence_types": ["project_material"],
                    }
                },
            },
        )
        current = {
            **inventory,
            "occurrences": inventory["occurrences"][:1],
            "claim_ids": ["CL-fact"],
            "unverified_ids": [],
        }
        comparison = compare_claim_evidence_inventories(
            inventory, current
        )
        self.assertFalse(comparison["passed"])
        self.assertEqual(comparison["missing_claim_ids"], ["CL-pending"])
        self.assertEqual(
            comparison["missing_occurrences"],
            [{"claim_id": "CL-fact", "occurrence_id": "O-outline"}],
        )
        self.assertEqual(
            comparison["missing_unverified_ids"], ["UV-budget"]
        )

        changed = {
            **inventory,
            "claims": {
                **inventory["claims"],
                "CL-fact": {
                    **inventory["claims"]["CL-fact"],
                    "text": "项目编号信息已删除。",
                },
            },
        }
        changed_comparison = compare_claim_evidence_inventories(
            inventory, changed
        )
        self.assertFalse(changed_comparison["passed"])
        self.assertEqual(
            changed_comparison["changed_claim_ids"], ["CL-fact"]
        )

    def test_node_output_contract_is_optional_and_legacy_schema_resolves(self):
        node = TaskNode(
            name="JSON 输出",
            type=StepType.AGENT,
            config=AgentStepConfig(prompt="Return JSON"),
            output_schema={
                "type": "object",
                "properties": {"accepted": {"type": "boolean"}},
                "required": ["accepted"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            node.effective_output_contract.contract_id,
            "task.output-schema",
        )
        self.assertEqual(
            node.effective_output_contract.downstream_binding,
            "validated_result",
        )
        self.assertIsNone(
            TaskNode(
                name="Legacy text",
                config=AgentStepConfig(prompt="Return text"),
            ).effective_output_contract
        )

    def test_template_compiler_preserves_optional_contract_on_existing_v2(self):
        template_path = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "agent_service"
            / "task"
            / "templates"
            / "s01_project_init.v2.json"
        )
        template = TaskTemplateDefinition.model_validate_json(
            template_path.read_text(encoding="utf-8")
        )
        step_index = next(
            index
            for index, step in enumerate(template.steps)
            if step.kind == "agent"
        )
        schema = {
            "type": "object",
            "properties": {"project_code": {"type": "string"}},
            "required": ["project_code"],
            "additionalProperties": False,
        }
        changed_steps = list(template.steps)
        changed_steps[step_index] = changed_steps[step_index].model_copy(
            update={
                "output_contract": NodeOutputContract(
                    contract_id="test.project.v1",
                    output_type="structured_json",
                    schema=schema,
                    validation_mode="json_schema",
                    downstream_binding="validated_result",
                )
            }
        )
        candidate = template.model_copy(update={"steps": changed_steps})
        compiled = TaskTemplateCompiler().compile_nodes(
            candidate, [], require_tool_bindings=False
        )
        node = compiled.nodes[step_index]
        self.assertEqual(
            node.effective_output_contract.contract_id,
            "test.project.v1",
        )
        self.assertEqual(
            node.effective_output_contract.json_schema,
            schema,
        )

    def test_contract_rejects_unvalidated_structured_binding(self):
        with self.assertRaises(ValueError):
            NodeOutputContract(
                contract_id="test.output.v1",
                output_type="structured_json",
                downstream_binding="validated_result",
            )

    def test_schema_validation_returns_the_normalized_result_for_next_node(self):
        schema = {
            "type": "object",
            "properties": {"project_code": {"type": "string"}},
            "required": ["project_code"],
            "additionalProperties": False,
        }
        validation = validate_structured_result(
            schema,
            {
                "status": "completed",
                "output": '{"project_code":"P-1001"}',
                "reason": "",
                "questions": [],
            },
            agent_output_json=True,
        )
        self.assertTrue(validation.passed)
        self.assertEqual(validation.value, {"project_code": "P-1001"})

    def test_previous_result_is_typed_only_when_validated_value_exists(self):
        context = ExecutionContext(
            user_id=_OWNER,
            task_id="task",
            run_id="run",
            task_revision=1,
            previous_result={"project_code": "P-1001"},
        )
        self.assertEqual(
            _render_task_value(
                {"project": "{{previous_result}}"},
                "display text",
                context,
                typed=True,
            ),
            {"project": {"project_code": "P-1001"}},
        )
        self.assertEqual(
            _render_task_value(
                "result={{previous_result}}", "", context
            ),
            'result={"project_code":"P-1001"}',
        )
        with self.assertRaisesRegex(ValueError, "没有通过输出契约校验"):
            _render_task_value(
                "{{previous_result}}",
                "",
                context.model_copy(update={"previous_result": None}),
                typed=True,
            )

    def test_one_claim_can_have_two_explicit_occurrences(self):
        evidence = _source()
        normalized = normalize_evidence_snapshot(
            [evidence], authorization_subject=_OWNER
        )[0]
        body = (
            "## 大纲\n"
            "项目编号 P-1001，项目范围包括资料收集和边界核对。[C1] "
            "<!-- task-claim:CL-scope:O-outline -->\n\n"
            "## 正文\n"
            "<!-- task-claim:CL-scope:O-body -->项目编号 P-1001，"
            "项目范围包括资料收集和边界核对。[C1]"
        )
        claims = [
            {
                "claim_id": "CL-scope",
                "kind": "fact",
                "anchor": "CL-scope",
                "text": "项目范围包括资料收集和边界核对。",
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": "O-outline",
                        "text": "项目编号 P-1001，项目范围包括资料收集和边界核对。",
                        "citation_ids": ["C1"],
                    },
                    {
                        "occurrence_id": "O-body",
                        "text": "项目编号 P-1001，项目范围包括资料收集和边界核对。",
                        "citation_ids": ["C1"],
                    },
                ],
            }
        ]
        bindings = [
            _binding(
                "CL-scope",
                occurrence_id,
                "C1",
                normalized,
                "项目编号 P-1001，项目范围包括资料收集和边界核对。",
            )
            for occurrence_id in ("O-outline", "O-body")
        ]
        document, sources = _document(body, [evidence], claims, bindings)
        result = validate_claim_evidence_document(
            document, sources, authorization_subject=_OWNER
        )
        self.assertTrue(result["passed"], result["all_diagnostics"])
        self.assertEqual(len(result["claims"][0]["occurrences"]), 2)
        self.assertEqual(len(result["bindings"]), 2)

    def test_multiple_unverified_items_are_bound_to_distinct_occurrences(self):
        body = (
            "## 待核实\n"
            "项目面积待核实。 <!-- task-claim:CL-pending:O-area -->\n\n"
            "预算待核实。 <!-- task-claim:CL-pending:O-budget -->"
        )
        claims = [
            {
                "claim_id": "CL-pending",
                "kind": "unverified",
                "anchor": "CL-pending",
                "text": "项目有两项待核实数据。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-area",
                        "text": "项目面积待核实。",
                        "citation_ids": [],
                    },
                    {
                        "occurrence_id": "O-budget",
                        "text": "预算待核实。",
                        "citation_ids": [],
                    },
                ],
            }
        ]
        unverified = [
            {
                "unverified_id": unverified_id,
                "claim_id": "CL-pending",
                "occurrence_id": occurrence_id,
                "reason": "授权来源缺少该项证据。",
                "expected_evidence_types": ["project_material"],
            }
            for unverified_id, occurrence_id in (
                ("UV-area", "O-area"),
                ("UV-budget", "O-budget"),
            )
        ]
        document, sources = _document(
            body, [], claims, [], unverified_items=unverified
        )
        result = validate_claim_evidence_document(
            document, sources, authorization_subject=_OWNER
        )
        self.assertTrue(result["passed"], result["all_diagnostics"])
        self.assertEqual(
            {item["unverified_id"] for item in result["unverified_items"]},
            {"UV-area", "UV-budget"},
        )

    def test_anchor_text_accepts_before_and_after_marker_and_table_rows(self):
        evidence = _source()
        normalized = normalize_evidence_snapshot(
            [evidence], authorization_subject=_OWNER
        )[0]
        quote = "项目编号 P-1001"
        body = (
            "<!-- task-claim:CL-before:O-01 -->项目编号 P-1001 [C1]\n\n"
            "| 项目编号 | P-1001 [C1] <!-- task-claim:CL-table:O-01 --> |"
        )
        claims = [
            {
                "claim_id": claim_id,
                "kind": "fact",
                "anchor": claim_id,
                "text": quote,
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": occurrence_id,
                        "text": quote,
                        "citation_ids": ["C1"],
                    }
                ],
            }
            for claim_id, occurrence_id in (
                ("CL-before", "O-01"),
                ("CL-table", "O-01"),
            )
        ]
        bindings = [
            _binding(
                claim_id,
                "O-01",
                "C1",
                normalized,
                quote,
            )
            for claim_id in ("CL-before", "CL-table")
        ]
        document, sources = _document(body, [evidence], claims, bindings)
        result = validate_claim_evidence_document(
            document, sources, authorization_subject=_OWNER
        )
        self.assertTrue(result["passed"], result["all_diagnostics"])

    def test_invalid_legacy_anchor_does_not_misreport_declared_binding(self):
        evidence = _source()
        normalized = normalize_evidence_snapshot(
            [evidence], authorization_subject=_OWNER
        )[0]
        body = (
            "项目编号 P-1001 [C1] <!-- task-claim:CL-duplicate -->\n\n"
            "项目编号 P-1001 [C1] <!-- task-claim:CL-duplicate -->"
        )
        claims = [
            {
                "claim_id": "CL-duplicate",
                "kind": "fact",
                "anchor": "CL-duplicate",
                "text": "项目编号 P-1001",
                "citation_ids": ["C1"],
            }
        ]
        bindings = [
            _binding(
                "CL-duplicate",
                None,
                "C1",
                normalized,
                "项目编号 P-1001",
            )
        ]
        document, sources = _document(body, [evidence], claims, bindings)
        result = validate_claim_evidence_document(
            document, sources, authorization_subject=_OWNER
        )
        codes = {
            item["error_code"]
            for group in result["diagnostics"].values()
            for item in group
        }
        self.assertIn("claim_anchor_count", codes)
        self.assertIn("binding_claim_not_validated", codes)
        self.assertNotIn("binding_not_declared_by_claim", codes)

    def test_empty_citation_block_is_valid_only_without_citation_markers(self):
        body = (
            "项目面积待核实。 <!-- task-claim:CL-pending:O-area -->"
        )
        claims = [
            {
                "claim_id": "CL-pending",
                "kind": "unverified",
                "anchor": "CL-pending",
                "text": "待核实数据。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-area",
                        "text": "项目面积待核实。",
                        "citation_ids": [],
                    }
                ],
            }
        ]
        pending = [
            {
                "unverified_id": "UV-area",
                "claim_id": "CL-pending",
                "occurrence_id": "O-area",
                "reason": "没有授权来源。",
                "expected_evidence_types": ["project_material"],
            }
        ]
        document, sources = _document(
            body, [], claims, [], unverified_items=pending
        )
        self.assertTrue(
            validate_claim_evidence_document(
                document, sources, authorization_subject=_OWNER
            )["passed"]
        )
        bad_document = document.replace(
            "项目面积待核实。", "项目面积待核实。[C1]"
        )
        failed = validate_claim_evidence_document(
            bad_document, sources, authorization_subject=_OWNER
        )
        self.assertFalse(failed["passed"])
        self.assertTrue(failed["diagnostics"]["citation"])

    def test_protocol_normalization_is_lossless_and_refuses_ambiguity(self):
        payload = '{  "claims" : [], "bindings": [], "unverified_items": []  }'
        document = (
            "正文\n\n```task-claim-evidence\n"
            + payload
            + "\n```\n\n```task-citations\n{ \"citations\" : [] }\n```"
        )
        normalized, actions = normalize_task_protocol_format(document)
        self.assertEqual(
            actions,
            ["canonicalized_task-claim-evidence_json_whitespace",
             "canonicalized_task-citations_json_whitespace"],
        )
        self.assertIn('{"claims":[],"bindings":[],"unverified_items":[]}', normalized)
        duplicate_key = document.replace(
            '"claims" : [],', '"claims" : [], "claims" : [],'
        )
        unchanged, actions = normalize_task_protocol_format(duplicate_key)
        self.assertEqual(unchanged, duplicate_key)
        self.assertEqual(actions, [])

    def test_s02_ledger_supports_occurrence_specific_claims_and_pending_items(self):
        project = _source("项目建筑面积为12000平方米。")
        template_text = "B-01 方案应明确项目目标与验收条件。"
        template = {
            **_source(template_text),
            "document_id": "planning-template",
            "source": "planning-template.md",
            "evidence_type": "planning_template",
        }
        sources = normalize_evidence_snapshot(
            [project, template], authorization_subject=_OWNER
        )
        by_type = {item["evidence_type"]: item for item in sources}
        body = (
            "## 摘要\n项目建筑面积为12000平方米。[C1] "
            "<!-- task-claim:CL-area:O-summary -->\n\n"
            "## 项目概况\n<!-- task-claim:CL-area:O-detail -->"
            "项目建筑面积为12000平方米。[C1]\n\n"
            "## 模板要求\n依据 B-01，方案应明确项目目标与验收条件。[C2] "
            "<!-- task-claim:CL-rule:O-rule -->\n\n"
            "## 待核实\n预算待核实。 <!-- task-claim:CL-pending:O-budget -->\n\n"
            "计划周期待核实。 <!-- task-claim:CL-pending:O-duration -->"
        )
        claims = [
            {
                "claim_id": "CL-area",
                "kind": "fact",
                "anchor": "CL-area",
                "text": "项目建筑面积为12000平方米。",
                "citation_ids": ["C1"],
                "occurrences": [
                    {
                        "occurrence_id": "O-summary",
                        "text": "项目建筑面积为12000平方米。",
                        "citation_ids": ["C1"],
                    },
                    {
                        "occurrence_id": "O-detail",
                        "text": "项目建筑面积为12000平方米。",
                        "citation_ids": ["C1"],
                    },
                ],
            },
            {
                "claim_id": "CL-rule",
                "kind": "rule",
                "anchor": "CL-rule",
                "text": "依据 B-01，方案应明确项目目标与验收条件。",
                "citation_ids": ["C2"],
                "occurrences": [
                    {
                        "occurrence_id": "O-rule",
                        "text": "依据 B-01，方案应明确项目目标与验收条件。",
                        "citation_ids": ["C2"],
                    }
                ],
            },
            {
                "claim_id": "CL-pending",
                "kind": "unverified",
                "anchor": "CL-pending",
                "text": "预算和计划周期有待核实。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-budget",
                        "text": "预算待核实。",
                        "citation_ids": [],
                    },
                    {
                        "occurrence_id": "O-duration",
                        "text": "计划周期待核实。",
                        "citation_ids": [],
                    },
                ],
            },
        ]
        bindings = [
            _binding("CL-area", occurrence_id, "C1", by_type["project_material"], project["source_text"])
            for occurrence_id in ("O-summary", "O-detail")
        ] + [
            _binding("CL-rule", "O-rule", "C2", by_type["planning_template"], template_text)
        ]
        unverified = [
            {
                "unverified_id": unverified_id,
                "claim_id": "CL-pending",
                "occurrence_id": occurrence_id,
                "reason": "缺少本次运行的项目委托证据。",
                "expected_evidence_types": ["project_material"],
            }
            for unverified_id, occurrence_id in (
                ("UV-budget", "O-budget"),
                ("UV-duration", "O-duration"),
            )
        ]
        citations = {
            "citations": [
                {
                    "citation_id": "C1",
                    "evidence_id": by_type["project_material"]["evidence_id"],
                    "quote": project["source_text"],
                },
                {
                    "citation_id": "C2",
                    "evidence_id": by_type["planning_template"]["evidence_id"],
                    "quote": template_text,
                },
            ]
        }
        document = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                {"claims": claims, "bindings": bindings, "unverified_items": unverified},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n"
            + json.dumps(citations, ensure_ascii=False, separators=(",", ":"))
            + "\n```"
        )
        result = materialize_s02_project_planning_ledger(
            document, sources, authorization_subject=_OWNER
        )
        self.assertTrue(result["passed"], result["all_diagnostics"])
        self.assertEqual(
            {item["unverified_id"] for item in result["ledger"] if item.get("unverified_id")},
            {"UV-budget", "UV-duration"},
        )
        self.assertEqual(
            sum(item["claim_id"] == "CL-area" for item in result["ledger"]),
            2,
        )


class _ProtocolRunExecutor:
    def __init__(self, *, source, draft_body, final_output, final_outputs=None):
        self.source = source
        self.draft_body = draft_body
        self.final_output = final_output
        self.final_outputs = list(final_outputs or [final_output])
        self.final_feedback = []
        tagged = {
            **source,
            "evidence_type": "report_source",
            "authorization_subject": _OWNER,
        }
        self.evidence_id = normalize_evidence_snapshot(
            [tagged], authorization_subject=_OWNER
        )[0]["evidence_id"]

    async def execute_node(self, *, node, previous_output, context):
        del previous_output
        if node.name == "生成草稿":
            citations = {
                "citations": [
                    {
                        "citation_id": "C1",
                        "evidence_id": self.evidence_id,
                        "quote": "项目编号 P-1001，项目范围包括资料收集和边界核对。",
                    }
                ]
            }
            return StepExecutionResult(
                text=(
                    self.draft_body
                    + "\n\n```task-citations\n"
                    + json.dumps(citations, ensure_ascii=False, separators=(",", ":"))
                    + "\n```"
                )
            )
        if node.name == "整理最终报告":
            self.final_feedback.append(context.verification_feedback)
            output = (
                self.final_outputs.pop(0)
                if len(self.final_outputs) > 1
                else self.final_outputs[0]
            )
            return StepExecutionResult(text=output)
        raise AssertionError(f"unexpected Agent node: {node.name}")


class TaskP7R1ServiceOutputContractTest(IsolatedAsyncioTestCase):
    async def test_sop_finalizer_builds_citation_index_then_runs_full_validation(self):
        source = _source()
        normalized_source = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "项目范围包括资料收集和边界核对。[C1] "
            "<!-- task-claim:CL-report:O-main -->"
        )
        claim_payload = {
            "claims": [
                {
                    "claim_id": "CL-report",
                    "kind": "fact",
                    "anchor": "CL-report",
                    "text": "项目范围包括资料收集和边界核对。",
                    "citation_ids": ["C1"],
                    "occurrences": [
                        {
                            "occurrence_id": "O-main",
                            "text": "项目范围包括资料收集和边界核对。",
                            "citation_ids": ["C1"],
                        }
                    ],
                }
            ],
            "bindings": [
                _binding(
                    "CL-report",
                    "O-main",
                    "C1",
                    normalized_source,
                    "项目范围包括资料收集和边界核对",
                )
            ],
            "unverified_items": [],
        }
        final_output = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                claim_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        fence = chr(96) * 3
        citation_block = (
            fence
            + "task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "evidence_id": normalized_source["evidence_id"],
                            "quote": "项目编号 P-1001，项目范围包括资料收集和边界核对。",
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            + fence
        )
        truncated_output = (
            body
            + "\n\n"
            + citation_block
            + "\n\n"
            + fence
            + "task-claim-evidence\n{\"claims\":[ "
        )
        compact_repair = json.dumps(
            {
                "claims": [
                    {"claim_id": "CL-report", "kind": "fact"}
                ],
                "bindings": [
                    {
                        "claim_id": "CL-report",
                        "occurrence_id": "O-main",
                        "citation_id": "C1",
                        "evidence_id": normalized_source["evidence_id"],
                        "quote": "项目范围包括资料收集和边界核对",
                    }
                ],
                "unverified_items": [],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        executor = _ProtocolRunExecutor(
            source=source,
            draft_body="Draft report [C1]",
            final_output=final_output,
            final_outputs=[truncated_output, compact_repair],
        )
        service = TaskService(TaskStore(), preview_executor=executor)

        class _Gateway:
            async def inspect_task_scope(
                self, user_id, knowledge_base_ids, source_material_ids
            ):
                if user_id != _OWNER or knowledge_base_ids != ["p7r1-kb"]:
                    raise AssertionError("unexpected permission scope")
                return {
                    "knowledge_bases": [
                        {"id": "p7r1-kb", "name": "Task protocol test KB"}
                    ],
                    "source_material_ids": list(source_material_ids),
                }

            async def retrieve_task_evidence(
                self,
                user_id,
                knowledge_base_ids,
                query,
                *,
                source_material_ids,
                top_k,
            ):
                if user_id != _OWNER or knowledge_base_ids != ["p7r1-kb"]:
                    raise AssertionError("unexpected runtime authorization")
                return {
                    "status": "ready",
                    "query": query,
                    "retrieved_at": "2026-10-12T00:00:00+00:00",
                    "knowledge_bases": [
                        {"id": "p7r1-kb", "name": "Task protocol test KB"}
                    ],
                    "evidence": [source],
                }

            def format_task_evidence(self, result):
                return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

        nodes = [
            TaskNode(
                name="收集报告输入",
                type=StepType.INPUT,
                config=InputStepConfig(
                    questions=[
                        TaskInputQuestion(key="project_name", label="项目名称"),
                        TaskInputQuestion(key="required_chapters", label="章节"),
                    ]
                ),
                order=0,
            ),
            TaskNode(
                name="检索授权资料",
                type=StepType.TOOL,
                capability_key="report.rag_retrieve",
                config=ToolStepConfig(
                    tool_name="report.rag_retrieve",
                    arguments={"request": "{{previous_output}}"},
                ),
                order=1,
            ),
            TaskNode(
                name="生成草稿",
                type=StepType.AGENT,
                config=AgentStepConfig(prompt="生成有引用的草稿。"),
                order=2,
            ),
            TaskNode(
                name="核验草稿引用",
                type=StepType.TOOL,
                capability_key="report.citation_check",
                config=ToolStepConfig(
                    tool_name="report.citation_check",
                    arguments={"draft": "{{previous_output}}"},
                ),
                order=3,
            ),
            TaskNode(
                name="记录人工复核",
                type=StepType.INPUT,
                config=InputStepConfig(
                    questions=[
                        TaskInputQuestion(
                            key="report_feedback",
                            label="复核意见",
                            required=False,
                        )
                    ]
                ),
                order=4,
            ),
            TaskNode(
                name="整理最终报告",
                type=StepType.AGENT,
                config=AgentStepConfig(prompt="整理报告。"),
                output_contract=NodeOutputContract(
                    contract_id="test.report.claim-evidence.v1",
                    output_type="markdown",
                    validation_mode="claim_evidence",
                    provenance_requirement="authorized_run_snapshot",
                ),
                order=5,
            ),
        ]
        try:
            task = await service.create_task(
                _OWNER,
                CreateTaskRequest(
                    title="Task Claim–Evidence SOP 回归",
                    goal="验证最终节点引用索引物化和双重校验",
                    nodes=nodes,
                    knowledge_base_ids=["p7r1-kb"],
                ),
            )
            run = await service.create_run(
                _OWNER,
                task.id,
                TaskRunRequest(input="报告项目 P-1001"),
                _Gateway(),  # type: ignore[arg-type]
            )
            assert run is not None

            for answer in (
                {"project_name": "P-1001", "required_chapters": "范围"},
                {"report_feedback": "无新增意见"},
            ):
                active = service._active_runs.get(run.id)
                if active is not None:
                    await active
                parked = await service.get_run(_OWNER, run.id)
                self.assertIsNotNone(parked)
                assert parked is not None
                self.assertIsNotNone(parked.pending_input)
                accepted = await service.answer_run(
                    _OWNER,
                    run.id,
                    TaskInputReply(
                        request_id=parked.pending_input.id,
                        answers=answer,
                    ),
                    _Gateway(),  # type: ignore[arg-type]
                )
                self.assertIsNotNone(accepted)

            active = service._active_runs.get(run.id)
            if active is not None:
                await active
            completed = await service.get_run(_OWNER, run.id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(
                completed.status.value,
                "succeeded",
                json.dumps(
                    {
                        "error": completed.error,
                        "feedback_count": len(executor.final_feedback),
                        "feedback": executor.final_feedback,
                        "metadata": completed.node_runs[-1].metadata,
                        "output_tail": completed.node_runs[-1].output[-1200:],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
            self.assertEqual(len(executor.final_feedback), 2)
            self.assertIn("仅允许进行一次协议包络修复", executor.final_feedback[1])
            self.assertIn(
                "task-citations JSON 区块由服务端锁定",
                executor.final_feedback[1],
            )
            self.assertIn(body, executor.final_feedback[1])
            final_node_run = completed.node_runs[-1]
            self.assertTrue(
                final_node_run.metadata["claim_evidence_protocol_repair_check"][
                    "passed"
                ]
            )
            self.assertEqual(
                final_node_run.metadata["task_protocol_repair"]["status"],
                "materialized",
            )
            self.assertEqual(
                final_node_run.output.count("```task-citations"), 1
            )
            self.assertEqual(final_node_run.output.count("```task-citations"), 1)
            self.assertTrue(
                final_node_run.metadata["final_citation_verification"]["passed"]
            )
            self.assertTrue(
                final_node_run.metadata["claim_evidence_validation"]["passed"]
            )
        finally:
            await service.shutdown()

    async def test_sop_rejected_repair_keeps_body_and_fails_closed(self):
        source = _source()
        normalized_source = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )[0]
        body = (
            "待核实事项表：| 建筑面积 | 待核实 | 资料未提供 [C1] | "
            "<!-- task-claim:CL-table:O-table -->\n\n"
        )
        truncated_output = body + "```task-claim-evidence\n{"
        compact_repair = json.dumps(
            {
                "claims": [
                    {"claim_id": "CL-table", "kind": "unverified"}
                ],
                "bindings": [
                    {
                        "claim_id": "CL-table",
                        "occurrence_id": "O-table",
                        "citation_id": "C1",
                        "evidence_id": normalized_source["evidence_id"],
                        "quote": "项目编号 P-1001",
                    }
                ],
                "citations": [
                    {
                        "citation_id": "C1",
                        "evidence_id": normalized_source["evidence_id"],
                        "quote": "项目编号 P-1001",
                    }
                ],
                "unverified_items": [
                    {
                        "unverified_id": "UV-table",
                        "claim_id": "CL-table",
                        "occurrence_id": "O-table",
                        "reason": "该表格同时包含待核实事项与带引用内容。",
                        "expected_evidence_types": ["project_material"],
                    }
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        executor = _ProtocolRunExecutor(
            source=source,
            draft_body="草稿来源 [C1]",
            final_output=truncated_output,
            final_outputs=[truncated_output, compact_repair],
        )
        service = TaskService(TaskStore(), preview_executor=executor)

        class _Gateway:
            async def inspect_task_scope(
                self, user_id, knowledge_base_ids, source_material_ids
            ):
                if user_id != _OWNER or knowledge_base_ids != ["p7r1-kb"]:
                    raise AssertionError("unexpected permission scope")
                return {
                    "knowledge_bases": [
                        {"id": "p7r1-kb", "name": "Task protocol test KB"}
                    ],
                    "source_material_ids": list(source_material_ids),
                }

            async def retrieve_task_evidence(
                self,
                user_id,
                knowledge_base_ids,
                query,
                *,
                source_material_ids,
                top_k,
            ):
                if user_id != _OWNER or knowledge_base_ids != ["p7r1-kb"]:
                    raise AssertionError("unexpected runtime authorization")
                return {
                    "status": "ready",
                    "query": query,
                    "retrieved_at": "2026-10-12T00:00:00+00:00",
                    "knowledge_bases": [
                        {"id": "p7r1-kb", "name": "Task protocol test KB"}
                    ],
                    "evidence": [source],
                }

            def format_task_evidence(self, result):
                return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

        nodes = [
            TaskNode(
                name="收集报告输入",
                type=StepType.INPUT,
                config=InputStepConfig(
                    questions=[
                        TaskInputQuestion(key="project_name", label="项目名称"),
                        TaskInputQuestion(
                            key="required_chapters", label="所需章节"
                        ),
                    ]
                ),
                order=0,
            ),
            TaskNode(
                name="检索授权资料",
                type=StepType.TOOL,
                capability_key="report.rag_retrieve",
                config=ToolStepConfig(
                    tool_name="report.rag_retrieve",
                    arguments={},
                ),
                order=1,
            ),
            TaskNode(
                name="整理最终报告",
                type=StepType.AGENT,
                config=AgentStepConfig(prompt="整理报告并声明 Claim–Evidence。"),
                output_contract=NodeOutputContract(
                    contract_id="test.report.claim-evidence.repair.v1",
                    output_type="markdown",
                    validation_mode="claim_evidence",
                    provenance_requirement="authorized_run_snapshot",
                ),
                order=2,
            ),
        ]
        try:
            task = await service.create_task(
                _OWNER,
                CreateTaskRequest(
                    title="协议修复失败关闭回归",
                    goal="验证无损封装与严格校验",
                    nodes=nodes,
                    knowledge_base_ids=["p7r1-kb"],
                ),
            )
            run = await service.create_run(
                _OWNER,
                task.id,
                TaskRunRequest(input="报告项目 P-1001"),
                _Gateway(),  # type: ignore[arg-type]
            )
            assert run is not None
            active = service._active_runs.get(run.id)
            if active is not None:
                await active
            parked = await service.get_run(_OWNER, run.id)
            self.assertIsNotNone(parked)
            assert parked is not None
            self.assertIsNotNone(parked.pending_input)
            await service.answer_run(
                _OWNER,
                run.id,
                TaskInputReply(
                    request_id=parked.pending_input.id,
                    answers={
                        "project_name": "P-1001",
                        "required_chapters": "范围",
                    },
                ),
                _Gateway(),  # type: ignore[arg-type]
            )
            active = service._active_runs.get(run.id)
            if active is not None:
                await active
            completed = await service.get_run(_OWNER, run.id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed.status.value, "failed")
            final_node_run = completed.node_runs[-1]
            self.assertIn(
                "task_protocol_repair",
                final_node_run.metadata,
                json.dumps(
                    {
                        "error": completed.error,
                        "node_runs": [
                            {
                                "name": item.name,
                                "status": item.status.value,
                                "error": item.error,
                                "metadata": item.metadata,
                                "output": item.output[-500:],
                            }
                            for item in completed.node_runs
                        ],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
            self.assertEqual(
                final_node_run.metadata["task_protocol_repair"]["status"],
                "wrapped_for_validation",
            )
            self.assertTrue(
                final_node_run.metadata[
                    "claim_evidence_protocol_repair_check"
                ]["passed"]
            )
            self.assertFalse(
                final_node_run.metadata["claim_evidence_validation"]["passed"]
            )
            self.assertIn(body, final_node_run.output)
            self.assertNotIn(
                "正文与前一轮失败输出不一致", completed.error or ""
            )
        finally:
            await service.shutdown()

    async def test_nonretryable_verdict_stays_terminal_after_adapter_rebuild(self):
        calls = []
        node = TaskNode(
            name="证据核验",
            config=AgentStepConfig(prompt="不可重试错误"),
            max_attempts=2,
        )

        async def execute(state):
            del state
            calls.append("execute")
            return StepExecutionResult(text="失败输出")

        async def verify(output, state):
            del output, state
            return StepVerdict(
                passed=False,
                retryable=False,
                message="来源/权限错误，不可重试。",
            )

        state = TaskStepState()
        first_adapter = TaskSOPStep(node, execute, verify)
        async for _ in first_adapter.reply_stream(None, state):
            pass
        self.assertEqual(calls, ["execute"])
        self.assertEqual(state.metadata["task_retryable"], False)

        resumed_adapter = TaskSOPStep(node, execute, verify)
        async for _ in resumed_adapter.reply_stream(None, state):
            pass
        self.assertEqual(calls, ["execute"])
        self.assertEqual(state.phase.value, "failed")

    async def test_validated_result_is_persisted_and_bound_to_next_node(self):
        executor = _StructuredFlowExecutor()
        service = TaskService(TaskStore(), preview_executor=executor)
        schema = {
            "type": "object",
            "properties": {"project_code": {"type": "string"}},
            "required": ["project_code"],
            "additionalProperties": False,
        }
        first = TaskNode(
            name="结构化输入",
            config=AgentStepConfig(prompt="输出 JSON"),
            output_contract=NodeOutputContract(
                contract_id="test.project.v1",
                output_type="structured_json",
                schema=schema,
                validation_mode="json_schema",
                downstream_binding="validated_result",
            ),
        )
        second = TaskNode(
            name="下游处理",
            config=AgentStepConfig(prompt="处理已校验结果"),
            order=1,
        )
        try:
            task = await service.create_task(
                "p7r1-service-owner",
                CreateTaskRequest(goal="验证结构化结果传递", nodes=[first, second]),
            )
            run = await service.create_run(
                "p7r1-service-owner", task.id, TaskRunRequest(input="P-1001")
            )
            await service._active_runs[run.id]
            completed = await service.get_run("p7r1-service-owner", run.id)
            self.assertEqual(completed.status.value, "succeeded")
            self.assertEqual(completed.node_runs[0].output, '{"project_code":"P-1001"}')
            self.assertEqual(
                completed.node_runs[0].result,
                {"project_code": "P-1001"},
            )
            self.assertEqual(
                completed.node_runs[0].metadata["output_contract_validation"]["status"],
                "passed",
            )
            self.assertEqual(
                executor.contexts[-1].previous_result,
                {"project_code": "P-1001"},
            )
            self.assertEqual(
                executor.contexts[-1].history[0]["structured_result"],
                {"project_code": "P-1001"},
            )
        finally:
            await service.shutdown()

    async def test_invalid_output_is_bounded_and_server_status_is_authoritative(self):
        executor = _StructuredFlowExecutor(invalid=True)
        service = TaskService(TaskStore(), preview_executor=executor)
        schema = {
            "type": "object",
            "properties": {"project_code": {"type": "string"}},
            "required": ["project_code"],
            "additionalProperties": False,
        }
        node = TaskNode(
            name="结构化输入",
            config=AgentStepConfig(prompt="输出 JSON"),
            max_attempts=8,
            output_contract=NodeOutputContract(
                contract_id="test.project.v1",
                output_type="structured_json",
                schema=schema,
                validation_mode="json_schema",
                downstream_binding="validated_result",
            ),
        )
        try:
            task = await service.create_task(
                "p7r1-invalid-owner",
                CreateTaskRequest(goal="拒绝无效结构", nodes=[node]),
            )
            run = await service.create_run(
                "p7r1-invalid-owner", task.id, TaskRunRequest(input="P-1001")
            )
            await service._active_runs[run.id]
            failed = await service.get_run("p7r1-invalid-owner", run.id)
            self.assertEqual(failed.status.value, "failed")
            self.assertEqual(executor.calls, ["结构化输入", "结构化输入"])
            self.assertEqual(failed.node_runs[0].metadata["attempts"], 2)
            self.assertEqual(
                failed.node_runs[0].metadata["authoritative_output_status"]["error_code"],
                "TASK_OUTPUT_SCHEMA_MISMATCH",
            )
            self.assertTrue(
                failed.node_runs[0].output.startswith(
                    "> **Task 服务端验收：失败**"
                )
            )
            self.assertEqual(
                failed.node_runs[0].result["output"],
                '{"wrong":"P-1001"}',
            )
        finally:
            await service.shutdown()


class _StructuredFlowExecutor:
    def __init__(self, *, invalid=False):
        self.invalid = invalid
        self.calls = []
        self.contexts = []

    async def execute_node(self, *, node, previous_output, context):
        del previous_output
        self.calls.append(node.name)
        self.contexts.append(context)
        if node.name == "结构化输入":
            output = '{"wrong":"P-1001"}' if self.invalid else '{"project_code":"P-1001"}'
            return StepExecutionResult(
                text=output,
                result={
                    "status": "completed",
                    "output": output,
                    "reason": "",
                    "questions": [],
                },
            )
        return StepExecutionResult(text="下游完成", result={"status": "completed"})
