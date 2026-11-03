"""Task generic Claim/Evidence contract and S02 adapter tests."""

import hashlib
import json
from unittest import TestCase

from examples.agent_service.task._citation_verifier import (
    verify_task_citations,
)
from examples.agent_service.task._evidence_contract import (
    EvidencePolicy,
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
)
from examples.agent_service.task._s02_project_planning_evidence import (
    materialize_s02_project_planning_ledger,
)


_OWNER = "claim-evidence-owner"
_KB = "authorized-kb"
_PROJECT_ID = "project-doc"
_TEMPLATE_ID = "template-doc"


def _source(kind, document_id, name, text, chunk_index=0):
    return {
        "knowledge_base_id": _KB,
        "knowledge_base_name": "授权知识库",
        "document_id": document_id,
        "source": name,
        "chunk_index": chunk_index,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_text": text,
        "source_text_truncated": False,
        "source_status": "ready",
        "evidence_type": kind,
    }


def _citation(citation_id, source):
    return {
        "citation_id": citation_id,
        "knowledge_base_id": source["knowledge_base_id"],
        "document_id": source["document_id"],
        "chunk_index": source["chunk_index"],
        "content_sha256": source["content_sha256"],
        "quote": source["source_text"],
    }


def _document(body, evidence, claims, bindings, unverified_items=None):
    normalized = normalize_evidence_snapshot(
        evidence,
        authorization_subject=_OWNER,
        collected_at="2026-10-10T00:00:00+00:00",
    )
    citations = []
    citation_sources = {}
    for binding in bindings:
        source = next(
            item
            for item in normalized
            if item["evidence_id"] == binding["evidence_id"]
        )
        citation_sources.setdefault(binding["citation_id"], source)
    for citation_id, source in citation_sources.items():
        citations.append(_citation(citation_id, source))
    payload = {
        "claims": claims,
        "bindings": bindings,
        "unverified_items": unverified_items or [],
    }
    return (
        body
        + "\n\n```task-claim-evidence\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n```\n\n```task-citations\n"
        + json.dumps(
            {"citations": citations},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n```"
    )


def _claim(claim_id, kind, text, citation_ids):
    return {
        "claim_id": claim_id,
        "kind": kind,
        "anchor": claim_id,
        "text": text,
        "citation_ids": citation_ids,
    }


def _binding(claim_id, citation_id, source, quote):
    source_id = normalize_evidence_snapshot(
        [source], authorization_subject=_OWNER
    )[0]["evidence_id"]
    return {
        "claim_id": claim_id,
        "citation_id": citation_id,
        "evidence_id": source_id,
        "quote": quote,
    }


class TaskClaimEvidenceContractTest(TestCase):
    def test_template_limitation_requires_explicit_scenario_evidence_policy(self):
        source = _source(
            "planning_template",
            _TEMPLATE_ID,
            "策划报告编制模板.docx",
            "策划模板属于演示材料，不是发布的企业制度、监管要求或正式标准。",
        )
        text = "策划模板属于演示材料，不是发布的企业制度。[C1]"
        document = _document(
            f"{text} <!-- task-claim:CL-limitation -->",
            [source],
            [
                _claim(
                    "CL-limitation",
                    "template_limitation",
                    text,
                    ["C1"],
                )
            ],
            [
                _binding(
                    "CL-limitation",
                    "C1",
                    source,
                    "策划模板属于演示材料，不是发布的企业制度",
                )
            ],
        )

        result = validate_claim_evidence_document(
            document,
            [source],
            authorization_subject=_OWNER,
            policy=EvidencePolicy(
                policy_id="unrelated-scene",
                allowed_evidence_types_by_claim_kind={
                    "fact": ["project_material"],
                },
                require_citation_claim_kinds=["fact"],
                manual_review_claim_kinds=["fact"],
            ),
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "claim_kind_not_allowed_by_policy",
            {item["error_code"] for item in result["diagnostics"]["source"]},
        )

    def test_scenario_policy_rejects_claims_using_wrong_source_type(self):
        source = _source(
            "planning_template",
            _TEMPLATE_ID,
            "策划报告编制模板.docx",
            "报告应包含研究目标。",
        )
        document = _document(
            "报告应包含研究目标。[C1] <!-- task-claim:CL-goal -->",
            [source],
            [_claim("CL-goal", "fact", "报告应包含研究目标。", ["C1"])],
            [_binding("CL-goal", "C1", source, source["source_text"])],
        )
        policy = EvidencePolicy(
            policy_id="s02.2.0.0",
            allowed_evidence_types_by_claim_kind={
                "fact": ["project_material"],
                "rule": ["planning_template"],
            },
            require_citation_claim_kinds=["fact", "rule"],
            manual_review_claim_kinds=["fact", "rule"],
        )

        result = validate_claim_evidence_document(
            document,
            [source],
            authorization_subject=_OWNER,
            policy=policy,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "evidence_type_not_allowed",
            {
                item["error_code"]
                for item in result["diagnostics"]["source"]
            },
        )
        self.assertEqual(result["verification_scope"]["policy_id"], policy.policy_id)

    def test_scenario_policy_marks_supported_claims_for_review_and_maps_unverified(
        self,
    ):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "设备台账数量：64台。",
        )
        body = (
            "设备台账数量为64台。[C1] <!-- task-claim:CL-equipment -->\n"
            "项目预算尚待确认。 <!-- task-claim:CL-budget -->"
        )
        claims = [
            _claim("CL-equipment", "fact", "设备台账数量为64台。", ["C1"]),
            _claim("CL-budget", "unverified", "项目预算尚待确认。", []),
        ]
        bindings = [
            _binding(
                "CL-equipment",
                "C1",
                source,
                "设备台账数量：64台",
            )
        ]
        unverified_items = [
            {
                "unverified_id": "UV-budget",
                "claim_id": "CL-budget",
                "reason": "本次授权来源中没有项目预算原文。",
                "expected_evidence_types": ["project_material"],
            }
        ]
        document = _document(
            body,
            [source],
            claims,
            bindings,
            unverified_items,
        )
        policy = EvidencePolicy(
            policy_id="s02.2.0.0",
            allowed_evidence_types_by_claim_kind={
                "fact": ["project_material"],
            },
            require_citation_claim_kinds=["fact"],
            manual_review_claim_kinds=["fact"],
        )

        result = validate_claim_evidence_document(
            document,
            [source],
            authorization_subject=_OWNER,
            policy=policy,
        )

        self.assertTrue(result["passed"], result["all_diagnostics"])
        self.assertEqual(
            result["verification_scope"]["semantic_review_claim_ids"],
            ["CL-equipment"],
        )
        self.assertEqual(
            result["unverified_items"][0]["unverified_id"], "UV-budget"
        )

    def test_one_evidence_can_support_multiple_independent_claims(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "项目建筑面积：12000平方米。设备台账数量：64台。",
        )
        body = (
            "## 项目概况\n"
            "项目建筑面积为12000平方米。[C1] <!-- task-claim:CL-area -->\n"
            "设备台账数量为64台。[C1] <!-- task-claim:CL-equipment -->"
        )
        claims = [
            _claim("CL-area", "fact", "项目建筑面积为12000平方米。", ["C1"]),
            _claim("CL-equipment", "fact", "设备台账数量为64台。", ["C1"]),
        ]
        bindings = [
            _binding("CL-area", "C1", source, "建筑面积：12000平方米"),
            _binding("CL-equipment", "C1", source, "设备台账数量：64台"),
        ]
        document = _document(body, [source], claims, bindings)

        result = materialize_s02_project_planning_ledger(
            document,
            [source],
            authorization_subject=_OWNER,
        )

        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(len(result["ledger"]), 2)
        self.assertEqual(
            {item["claim_id"] for item in result["ledger"]},
            {"CL-area", "CL-equipment"},
        )
        self.assertEqual(
            {item["quote"] for item in result["ledger"]},
            {"建筑面积：12000平方米", "设备台账数量：64台"},
        )

    def test_unrelated_quote_and_mismatched_fact_value_are_rejected(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "项目预算：800万元。",
        )
        body = "项目建筑面积为12000平方米。[C1] <!-- task-claim:CL-area -->"
        document = _document(
            body,
            [source],
            [_claim("CL-area", "fact", "项目建筑面积为12000平方米。", ["C1"])],
            [_binding("CL-area", "C1", source, source["source_text"])],
        )

        result = validate_claim_evidence_document(
            document,
            [source],
            authorization_subject=_OWNER,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "quote_unrelated_to_claim",
            {item["error_code"] for item in result["all_diagnostics"]},
        )

    def test_same_citation_id_can_be_reused_but_each_claim_needs_binding(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "项目建筑面积：12000平方米。设备台账数量：64台。",
        )
        body = (
            "项目建筑面积为12000平方米。[C1] <!-- task-claim:CL-area -->\n"
            "设备台账数量为64台。[C1] <!-- task-claim:CL-equipment -->"
        )
        claims = [
            _claim("CL-area", "fact", "项目建筑面积为12000平方米。", ["C1"]),
            _claim("CL-equipment", "fact", "设备台账数量为64台。", ["C1"]),
        ]
        document = _document(
            body,
            [source],
            claims,
            [_binding("CL-area", "C1", source, "建筑面积：12000平方米")],
        )

        result = validate_claim_evidence_document(
            document,
            [source],
            authorization_subject=_OWNER,
        )

        self.assertFalse(result["passed"])
        self.assertTrue(
            any(
                item["claim_id"] == "CL-equipment"
                for item in result["all_diagnostics"]
            )
        )

    def test_claim_marker_set_must_match_that_claims_declared_ids(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "项目建筑面积：12000平方米。",
        )
        body = "项目建筑面积为12000平方米。[C1] <!-- task-claim:CL-area -->"
        document = _document(
            body,
            [source],
            [_claim("CL-area", "fact", "项目建筑面积为12000平方米。", ["C2"])],
            [_binding("CL-area", "C2", source, "项目建筑面积：12000平方米")],
        )

        result = validate_claim_evidence_document(
            document,
            [source],
            authorization_subject=_OWNER,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "claim_marker_mismatch",
            {item["error_code"] for item in result["all_diagnostics"]},
        )

    def test_b_clause_can_span_adjacent_chunks_but_not_a_gap(self):
        project = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "项目名称：示范工程。",
        )
        first = _source(
            "planning_template", _TEMPLATE_ID, "策划模板.docx", "B-01", 0
        )
        second = _source(
            "planning_template",
            _TEMPLATE_ID,
            "策划模板.docx",
            "方案应明确项目目标与验收条件。",
            1,
        )
        third = _source(
            "planning_template",
            _TEMPLATE_ID,
            "策划模板.docx",
            "方案应明确项目目标与验收条件。",
            2,
        )
        body = "依据 B-01，方案应明确项目目标与验收条件。[C1][C2] <!-- task-claim:CL-rule -->"
        claims = [
            _claim("CL-rule", "rule", "依据 B-01，方案应明确项目目标与验收条件。", ["C1", "C2"])
        ]
        valid = _document(
            body,
            [first, second, project],
            claims,
            [
                _binding("CL-rule", "C1", first, "B-01"),
                _binding("CL-rule", "C2", second, "方案应明确项目目标与验收条件。"),
            ],
        )
        passed = materialize_s02_project_planning_ledger(
            valid,
            [first, second, project],
            authorization_subject=_OWNER,
        )
        self.assertTrue(passed["passed"], passed["issues"])

        invalid = _document(
            body,
            [first, third, project],
            claims,
            [
                _binding("CL-rule", "C1", first, "B-01"),
                _binding("CL-rule", "C2", third, "方案应明确项目目标与验收条件。"),
            ],
        )
        rejected = materialize_s02_project_planning_ledger(
            invalid,
            [first, third, project],
            authorization_subject=_OWNER,
        )
        self.assertFalse(rejected["passed"])
        self.assertTrue(
            any(
                item["error_code"] == "template_clause_quote_insufficient"
                for item in rejected["all_diagnostics"]
            )
        )

    def test_markdown_table_fact_maps_to_row_anchor_and_ledger(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "建筑面积：12000平方米。",
        )
        body = (
            "| 项目面积 | 当前值 |\n"
            "| --- | --- |\n"
            "| 项目建筑面积 | 12000平方米 [C1] <!-- task-claim:CL-area --> |"
        )
        document = _document(
            body,
            [source],
            [_claim("CL-area", "fact", "项目建筑面积 12000平方米", ["C1"])],
            [_binding("CL-area", "C1", source, "建筑面积：12000平方米")],
        )

        result = materialize_s02_project_planning_ledger(
            document,
            [source],
            authorization_subject=_OWNER,
        )

        self.assertTrue(result["passed"], result["issues"])
        self.assertIn("CL-area", result["document"])
        self.assertEqual(result["ledger"][0]["claim_id"], "CL-area")

    def test_multiple_unverified_items_map_independently(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "项目名称：示范工程。",
        )
        body = (
            "项目名称为示范工程。[C1] <!-- task-claim:CL-name -->\n"
            "| 项目事实 | 当前状态 |\n| --- | --- |\n"
            "| 设备台账数量 | 待核实 <!-- task-claim:CL-equipment --> |\n"
            "| 建设周期 | 待核实 <!-- task-claim:CL-period --> |"
        )
        claims = [
            _claim("CL-name", "fact", "项目名称为示范工程。", ["C1"]),
            _claim("CL-equipment", "unverified", "设备台账数量待核实", []),
            _claim("CL-period", "unverified", "建设周期待核实", []),
        ]
        bindings = [_binding("CL-name", "C1", source, "项目名称：示范工程")]
        unverified = [
            {
                "unverified_id": "UV-equipment",
                "claim_id": "CL-equipment",
                "reason": "没有检索到设备台账原文。",
                "expected_evidence_types": ["project_material"],
            },
            {
                "unverified_id": "UV-period",
                "claim_id": "CL-period",
                "reason": "没有检索到建设周期原文。",
                "expected_evidence_types": ["project_material"],
            },
        ]
        document = _document(body, [source], claims, bindings, unverified)

        result = materialize_s02_project_planning_ledger(
            document,
            [source],
            authorization_subject=_OWNER,
        )

        self.assertTrue(result["passed"], result["issues"])
        rows = {
            item.get("unverified_id"): item
            for item in result["ledger"]
            if item.get("unverified_id")
        }
        self.assertEqual(set(rows), {"UV-equipment", "UV-period"})
        self.assertIn("设备台账", rows["UV-equipment"]["claim"])
        self.assertIn("建设周期", rows["UV-period"]["claim"])

    def test_invalid_schema_unauthorized_source_and_forged_hash_fail_closed(
        self,
    ):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "建筑面积：12000平方米。",
        )
        body = "项目建筑面积为12000平方米。[C1] <!-- task-claim:CL-area -->"
        claims = [_claim("CL-area", "fact", "项目建筑面积为12000平方米。", ["C1"])]
        bindings = [_binding("CL-area", "C1", source, source["source_text"])]
        valid = _document(body, [source], claims, bindings)

        invalid_schema = valid.replace(
            '"anchor":"CL-area",', '"anchor":"other",'
        )
        schema_result = validate_claim_evidence_document(
            invalid_schema,
            [source],
            authorization_subject=_OWNER,
        )
        self.assertFalse(schema_result["passed"])
        self.assertIn(
            "anchor_mismatch",
            {item["error_code"] for item in schema_result["all_diagnostics"]},
        )

        unauthorized = {**source, "authorization_subject": "another-user"}
        unauthorized_result = validate_claim_evidence_document(
            valid,
            [unauthorized],
            authorization_subject=_OWNER,
        )
        self.assertFalse(unauthorized_result["passed"])
        self.assertTrue(
            any(
                item["error_type"] == "source"
                for item in unauthorized_result["all_diagnostics"]
            )
        )

        forged = {**source, "content_sha256": "a" * 64}
        forged_bindings = [
            _binding("CL-area", "C1", forged, forged["source_text"])
        ]
        forged_result = validate_claim_evidence_document(
            _document(body, [forged], claims, forged_bindings),
            [forged],
            authorization_subject=_OWNER,
        )
        self.assertFalse(forged_result["passed"])
        self.assertTrue(
            any(
                "哈希" in item["message"]
                for item in forged_result["all_diagnostics"]
            )
        )

    def test_final_rewrite_is_checked_against_claim_and_quote_again(self):
        source = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "建筑面积：12000平方米。",
        )
        body = "项目建筑面积为12000平方米。[C1] <!-- task-claim:CL-area -->"
        claims = [_claim("CL-area", "fact", "项目建筑面积为12000平方米。", ["C1"])]
        bindings = [_binding("CL-area", "C1", source, source["source_text"])]
        original = _document(body, [source], claims, bindings)
        self.assertTrue(verify_task_citations(original, [source])["passed"])

        rewritten = original.replace("12000平方米", "13000平方米", 1)
        result = materialize_s02_project_planning_ledger(
            rewritten,
            [source],
            authorization_subject=_OWNER,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(
            result["diagnostics"]["structure"]
            or result["diagnostics"]["citation"]
        )

    def test_old_snapshot_gets_run_owner_scoped_id_without_migration(self):
        legacy = _source(
            "project_material",
            _PROJECT_ID,
            "项目委托资料.docx",
            "建筑面积：12000平方米。",
        )
        legacy.pop("source_status")
        legacy.pop("source_text_truncated")
        first = normalize_evidence_snapshot(
            [legacy], authorization_subject=_OWNER
        )[0]
        second = normalize_evidence_snapshot(
            [legacy], authorization_subject=_OWNER
        )[0]

        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertEqual(first["authorization_subject"], _OWNER)
        self.assertTrue(first["evidence_id"].startswith("EV-"))

        truncated_legacy = {
            **legacy,
            "content_sha256": hashlib.sha256(
                "建筑面积：12000平方米。以及其他原文".encode("utf-8")
            ).hexdigest(),
        }
        body = "项目建筑面积为12000平方米。[C1]"
        citation_document = (
            body
            + "\n\n```task-citations\n"
            + json.dumps(
                {"citations": [_citation("C1", truncated_legacy)]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )
        self.assertTrue(
            verify_task_citations(
                citation_document,
                [truncated_legacy],
            )["passed"]
        )

    def test_generic_citation_can_reference_a_supported_tool_record_shape(
        self,
    ):
        text = "专家执业年限为15年。"
        tool_record = {
            "source_type": "tool_result",
            "authorization_subject": _OWNER,
            "record_id": "expert-record-17",
            "call_id": "tool-call-42",
            "field_path": "experts[0].practice_years",
            "source": "expert.registry.search",
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_text": text,
            "source_text_truncated": False,
            "source_status": "ready",
            "evidence_type": "business_record",
        }
        evidence = normalize_evidence_snapshot(
            [tool_record], authorization_subject=_OWNER
        )
        evidence_id = evidence[0]["evidence_id"]
        body = "专家执业年限为15年。[C1] <!-- task-claim:CL-years -->"
        claim = _claim("CL-years", "fact", "专家执业年限为15年。", ["C1"])
        binding = {
            "claim_id": "CL-years",
            "citation_id": "C1",
            "evidence_id": evidence_id,
            "quote": text,
        }
        document = (
            body
            + "\n\n```task-claim-evidence\n"
            + json.dumps(
                {
                    "claims": [claim],
                    "bindings": [binding],
                    "unverified_items": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "evidence_id": evidence_id,
                            "quote": text,
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        result = validate_claim_evidence_document(
            document,
            evidence,
            authorization_subject=_OWNER,
        )

        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(
            result["verification_scope"][
                "authorized_source_identity_and_hash"
            ],
            "passed",
        )


__all__ = ["TaskClaimEvidenceContractTest"]
