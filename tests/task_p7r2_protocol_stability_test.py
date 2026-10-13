"""P7-R2 protocol diagnostics and scale regression coverage."""

import hashlib
import json
from unittest import TestCase

from examples.agent_service.task._citation_verifier import (
    verify_task_citations,
)
from examples.agent_service.task._scenario_evidence_policies import (
    get_scenario_evidence_policy,
)
from examples.agent_service.task._evidence_contract import (
    extract_claim_evidence_protocol_repair_inventory,
    materialize_task_claim_evidence_protocol_repair,
    materialize_task_citations_from_bindings,
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
)


_OWNER = "p7r2-redacted-owner"


def _source(text: str) -> dict[str, object]:
    return {
        "knowledge_base_id": "redacted-kb",
        "knowledge_base_name": "授权测试知识库",
        "document_id": "redacted-document",
        "source": "脱敏测试资料.md",
        "chunk_index": 0,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_text": text,
        "source_text_truncated": False,
        "source_status": "ready",
        "evidence_type": "project_material",
    }


def _citation_block(evidence: dict[str, object], quote: str) -> str:
    return (
        "\n\n```task-citations\n"
        + json.dumps(
            {
                "citations": [
                    {
                        "citation_id": "C1",
                        "evidence_id": evidence["evidence_id"],
                        "quote": quote,
                    }
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n```"
    )


class TaskP7R2ProtocolStabilityTest(TestCase):
    def test_large_claim_occurrence_binding_document_with_79_claims(self):
        source_text = "\n".join(
            f"项目事项 DEMO-{index:03d} 状态已登记。"
            for index in range(1, 80)
        )
        source = _source(source_text)
        evidence = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )
        evidence_record = evidence[0]
        claims = []
        bindings = []
        body_lines = []
        for index in range(1, 80):
            claim_id = f"CL-{index:03d}"
            occurrence_id = f"O-{index:03d}"
            text = f"项目事项 DEMO-{index:03d} 状态已登记。"
            body_lines.append(
                f"{text} [C1] <!-- task-claim:{claim_id}:{occurrence_id} -->"
            )
            claims.append(
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
            )
            bindings.append(
                {
                    "claim_id": claim_id,
                    "occurrence_id": occurrence_id,
                    "citation_id": "C1",
                    "evidence_id": evidence_record["evidence_id"],
                    "quote": text,
                }
            )
        payload = {
            "claims": claims,
            "bindings": bindings,
            "unverified_items": [],
        }
        document = (
            "\n\n".join(body_lines)
            + "\n\n```task-claim-evidence\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n```"
            + _citation_block(evidence_record, "项目事项 DEMO-001 状态已登记。")
        )

        result = validate_claim_evidence_document(
            document, evidence, authorization_subject=_OWNER
        )

        self.assertTrue(result["passed"], result["all_diagnostics"])
        self.assertEqual(len(result["claims"]), 79)
        self.assertEqual(len(result["bindings"]), 79)
        self.assertEqual(
            result["verification_scope"]["authorized_source_identity_and_hash"],
            "passed",
        )

    def test_analysis_only_document_can_materialize_an_empty_citation_index(self):
        body = (
            "分析可先按现有资料核对字段完整性。 "
            "<!-- task-claim:CL-analysis:O-01 -->\n\n"
            "建议在资料补齐后再进行现场排期。 "
            "<!-- task-claim:CL-recommendation:O-01 -->"
        )
        claims = [
            {
                "claim_id": "CL-analysis",
                "kind": "analysis",
                "anchor": "CL-analysis",
                "text": "分析可先按现有资料核对字段完整性。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-01",
                        "text": "分析可先按现有资料核对字段完整性。",
                        "citation_ids": [],
                    }
                ],
            },
            {
                "claim_id": "CL-recommendation",
                "kind": "recommendation",
                "anchor": "CL-recommendation",
                "text": "建议在资料补齐后再进行现场排期。",
                "citation_ids": [],
                "occurrences": [
                    {
                        "occurrence_id": "O-01",
                        "text": "建议在资料补齐后再进行现场排期。",
                        "citation_ids": [],
                    }
                ],
            },
        ]
        document = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                {"claims": claims, "bindings": [], "unverified_items": []},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        finalized, actions = materialize_task_citations_from_bindings(
            document, [], authorization_subject=_OWNER
        )

        self.assertEqual(
            actions,
            ["materialized_task-citations_from_run_bindings"],
        )
        citation_check = verify_task_citations(
            finalized, [], authorization_subject=_OWNER
        )
        self.assertTrue(citation_check["passed"], citation_check["issues"])
        result = validate_claim_evidence_document(
            finalized, [], authorization_subject=_OWNER
        )
        self.assertTrue(result["passed"], result["all_diagnostics"])

    def test_s02_policy_still_requires_citations_for_project_facts(self):
        source = _source("项目编号 DEMO-001")
        evidence = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )
        body = "项目编号 DEMO-001。 <!-- task-claim:CL-fact:O-fact -->"
        claim = {
            "claim_id": "CL-fact",
            "kind": "fact",
            "anchor": "CL-fact",
            "text": "项目编号 DEMO-001。",
            "citation_ids": [],
            "occurrences": [
                {
                    "occurrence_id": "O-fact",
                    "text": "项目编号 DEMO-001。",
                    "citation_ids": [],
                }
            ],
        }
        document = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                {"claims": [claim], "bindings": [], "unverified_items": []},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n{\"citations\":[]}\n```"
        )

        result = validate_claim_evidence_document(
            document,
            evidence,
            authorization_subject=_OWNER,
            policy=get_scenario_evidence_policy(
                "s02_project_planning", "2.0.0"
            ),
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "claim_missing_citations",
            {item["error_code"] for item in result["all_diagnostics"]},
        )

    def test_protocol_parse_failures_have_distinct_phase_diagnostics(self):
        source = _source("项目编号 DEMO-001。")
        evidence = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )
        evidence_record = evidence[0]
        body = "项目编号 DEMO-001。[C1] <!-- task-claim:CL-code:O-01 -->"
        citation = _citation_block(evidence_record, "项目编号 DEMO-001。")
        cases = [
            (
                "invalid_json",
                body + "\n\n```task-claim-evidence\n{\"claims\":[}\n```" + citation,
                "invalid_json",
            ),
            (
                "incomplete_fence",
                body + "\n\n```task-claim-evidence\n{\"claims\":[" + citation,
                "incomplete_claim_block",
            ),
            (
                "duplicate_json_key",
                body
                + "\n\n```task-claim-evidence\n"
                + '{"claims":[],"claims":[],"bindings":[],"unverified_items":[]}\n```'
                + citation,
                "duplicate_json_key",
            ),
            (
                "schema_mismatch",
                body
                + "\n\n```task-claim-evidence\n"
                + '{"claims":[{"claim_id":"CL-code","kind":"invalid"}],"bindings":[],"unverified_items":[]}\n```'
                + citation,
                "invalid_claim_record",
            ),
        ]
        for label, document, expected_code in cases:
            with self.subTest(label=label):
                result = validate_claim_evidence_document(
                    document,
                    evidence,
                    authorization_subject=_OWNER,
                )
                codes = {
                    item["error_code"] for item in result["all_diagnostics"]
                }
                self.assertIn(expected_code, codes)
                self.assertEqual(
                    result["verification_scope"][
                        "authorized_source_identity_and_hash"
                    ],
                    "not_evaluated",
                )
                self.assertEqual(
                    result["verification_scope"]["semantic_support"],
                    "not_evaluated",
                )
                diagnostic = next(
                    item
                    for item in result["all_diagnostics"]
                    if item["error_code"] == expected_code
                )
                self.assertTrue(diagnostic.get("phase"))
                if label == "invalid_json":
                    self.assertRegex(diagnostic["location"], r"line:\d+:column:\d+")
                if label == "schema_mismatch":
                    self.assertIn("claims[0]", diagnostic["location"])

    def test_multiple_claim_anchors_on_one_table_row_are_rejected(self):
        source = _source("事实一 DEMO-001；事实二 DEMO-002")
        evidence = normalize_evidence_snapshot(
            [source], authorization_subject=_OWNER
        )
        record = evidence[0]
        body = (
            "| 事实一 DEMO-001 [C1] <!-- task-claim:CL-one:O-one --> "
            "| 事实二 DEMO-002 [C1] <!-- task-claim:CL-two:O-two --> |"
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
                ("CL-one", "O-one", "事实一 DEMO-001"),
                ("CL-two", "O-two", "事实二 DEMO-002"),
            )
        ]
        bindings = [
            {
                "claim_id": claim["claim_id"],
                "occurrence_id": claim["occurrences"][0]["occurrence_id"],
                "citation_id": "C1",
                "evidence_id": record["evidence_id"],
                "quote": claim["text"],
            }
            for claim in claims
        ]
        payload = {
            "claims": claims,
            "bindings": bindings,
            "unverified_items": [],
        }
        document = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n```"
            + _citation_block(record, record["source_text"])
        )

        result = validate_claim_evidence_document(
            document, evidence, authorization_subject=_OWNER
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "multiple_claims_in_table_row",
            {item["error_code"] for item in result["all_diagnostics"]},
        )

    def test_protocol_repair_rejects_extra_code_fence_and_oversized_body(self):
        body = "项目编号 DEMO-001 [C1] <!-- task-claim:CL-one:O-one -->"
        incomplete = body + "\n\n```task-claim-evidence\n{"
        inventory = extract_claim_evidence_protocol_repair_inventory(
            incomplete
        )
        self.assertIsNotNone(inventory)
        assert inventory is not None
        fenced_response = (
            "```json\n"
            + '{"claims":[],"citations":[],"bindings":[],"unverified_items":[]}'
            + "\n```"
        )
        repaired, actions = materialize_task_claim_evidence_protocol_repair(
            inventory["body"], fenced_response, inventory
        )
        self.assertEqual(repaired, inventory["body"])
        self.assertEqual(actions, [])

        oversized_body = (
            "项目背景 "
            + "x" * 16_001
            + " [C1] <!-- task-claim:CL-long:O-long -->"
        )
        self.assertIsNone(
            extract_claim_evidence_protocol_repair_inventory(
                oversized_body + "\n\n```task-claim-evidence\n{"
            )
        )
