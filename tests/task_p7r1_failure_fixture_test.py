"""Deidentified structural reproduction of the submitted S02 Run failure."""

import hashlib
import json
from unittest import TestCase

from examples.agent_service.task._citation_verifier import (
    verify_task_citations,
)
from examples.agent_service.task._evidence_contract import (
    claim_evidence_failure_is_retryable,
    compare_claim_evidence_inventories,
    extract_claim_evidence_inventory,
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
)


_OWNER = "p7r1-redacted-owner"
_SOURCE_TEXT = "资料显示咨询范围包括资料收集和边界核对。"
_SUBMITTED_RUN_COUNTS = {"structure": 29, "citation": 61, "unverified": 5}


def _failure_fixture():
    """Model the actual 15-claim/14-binding/47-marker/5-UV shape."""

    source = {
        "knowledge_base_id": "redacted-kb",
        "knowledge_base_name": "脱敏授权知识库",
        "document_id": "redacted-document",
        "source": "脱敏项目资料.md",
        "chunk_index": 0,
        "content_sha256": hashlib.sha256(
            _SOURCE_TEXT.encode("utf-8")
        ).hexdigest(),
        "source_text": _SOURCE_TEXT,
        "source_text_truncated": False,
        "source_status": "ready",
        "evidence_type": "project_material",
    }
    evidence = normalize_evidence_snapshot(
        [source], authorization_subject=_OWNER
    )
    evidence_id = evidence[0]["evidence_id"]

    body_parts = []
    for index in range(1, 15):
        claim_id = f"CL-{index:02d}"
        if index in {2, 4}:
            body_parts.append(
                f"正文对 {claim_id} 作了改写，与声明不同。"
                "[C1] "
                f"<!-- task-claim:{claim_id} -->"
            )
        else:
            line = (
                f"{_SOURCE_TEXT} <!-- task-claim:{claim_id} -->"
            )
            body_parts.extend((line, line))
    # The two rewritten claims carry a marker; forty-five more positions
    # complete the submitted Run's total of forty-seven unanchored markers.
    body_parts.append("引用位置：" + " ".join("[C1]" for _ in range(45)))

    claims = [
        {
            "claim_id": f"CL-{index:02d}",
            "kind": "fact",
            "anchor": f"CL-{index:02d}",
            "text": _SOURCE_TEXT,
            "citation_ids": ["C1"],
        }
        for index in range(1, 15)
    ]
    claims.append(
        {
            "claim_id": "CL-15",
            "kind": "unverified",
            "anchor": "CL-15",
            "text": "多个独立事项仍待核实。",
            "citation_ids": [],
        }
    )
    bindings = [
        {
            "claim_id": f"CL-{index:02d}",
            "citation_id": "C1",
            "evidence_id": evidence_id,
            "quote": _SOURCE_TEXT,
        }
        for index in range(1, 15)
    ]
    unverified_items = [
        {
            "unverified_id": f"UV-{index:02d}",
            "claim_id": "CL-15",
            "reason": f"脱敏待核实事项 {index} 缺少授权资料。",
            "expected_evidence_types": ["project_material"],
        }
        for index in range(1, 6)
    ]
    payload = {
        "claims": claims,
        "bindings": bindings,
        "unverified_items": unverified_items,
    }
    citation = {
        "citation_id": "C1",
        "knowledge_base_id": source["knowledge_base_id"],
        "document_id": source["document_id"],
        "chunk_index": source["chunk_index"],
        "content_sha256": source["content_sha256"],
        "quote": _SOURCE_TEXT,
    }
    document = (
        "\n\n".join(body_parts)
        + "\n\n```task-claim-evidence\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n```\n\n```task-citations\n"
        + json.dumps(
            {"citations": [citation]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n```"
    )
    citation_result = verify_task_citations(
        document, evidence, authorization_subject=_OWNER
    )
    return document, evidence, citation_result


class TaskP7R1FailureFixtureTest(TestCase):
    def test_fixture_captures_submitted_failure_without_cascaded_diagnostics(self):
        document, evidence, citation_result = _failure_fixture()
        result = validate_claim_evidence_document(
            document,
            evidence,
            authorization_subject=_OWNER,
            citation_verification=citation_result,
        )
        counts = {
            name: len(items)
            for name, items in result["diagnostics"].items()
            if items
        }
        self.assertEqual(
            _SUBMITTED_RUN_COUNTS,
            {"structure": 29, "citation": 61, "unverified": 5},
        )
        self.assertEqual(counts, {"structure": 15, "citation": 61, "unverified": 5})
        self.assertEqual(
            len(
                [
                    item
                    for item in result["diagnostics"]["structure"]
                    if item["error_code"] == "unregistered_claim_anchor"
                ]
            ),
            0,
        )
        self.assertEqual(
            sum(
                item["error_code"] == "binding_not_declared_by_claim"
                for item in result["diagnostics"]["citation"]
            ),
            0,
        )
        self.assertEqual(
            sum(
                item["error_code"] == "binding_claim_not_validated"
                for item in result["diagnostics"]["citation"]
            ),
            14,
        )

    def test_submitted_anchor_and_linkage_failures_are_bounded_retryable(self):
        document, evidence, citation_result = _failure_fixture()
        result = validate_claim_evidence_document(
            document,
            evidence,
            authorization_subject=_OWNER,
            citation_verification=citation_result,
        )
        self.assertTrue(
            claim_evidence_failure_is_retryable(result["diagnostics"])
        )
        inventory = extract_claim_evidence_inventory(document)
        self.assertIsNotNone(inventory)
        self.assertEqual(len(inventory["claim_ids"]), 15)
        self.assertEqual(len(inventory["unverified_ids"]), 5)

        # The sanitized real Run reported this pre-fix cascade in addition to
        # the primary placement errors. Its source category was empty.
        submitted_diagnostics = {
            "structure": [
                {"error_code": "claim_anchor_count"},
                {"error_code": "claim_text_not_at_anchor"},
                {"error_code": "unregistered_claim_anchor"},
            ],
            "source": [],
            "citation": [
                {"error_code": "binding_not_declared_by_claim"},
                {"error_code": "citation_without_claim_anchor"},
            ],
            "unverified": [
                {"error_code": "unverified_claim_missing"}
            ],
        }
        self.assertTrue(
            claim_evidence_failure_is_retryable(submitted_diagnostics)
        )

        current = dict(inventory)
        current["claim_ids"] = inventory["claim_ids"][1:]
        current["unverified_ids"] = inventory["unverified_ids"][1:]
        comparison = compare_claim_evidence_inventories(
            inventory, current
        )
        self.assertFalse(comparison["passed"])
        self.assertEqual(comparison["missing_claim_ids"], ["CL-01"])
        self.assertEqual(comparison["missing_unverified_ids"], ["UV-01"])

    def test_source_or_quote_failures_are_never_retryable(self):
        self.assertFalse(
            claim_evidence_failure_is_retryable(
                {
                    "structure": [],
                    "source": [
                        {"error_code": "evidence_id_not_in_run"}
                    ],
                    "citation": [],
                    "unverified": [],
                }
            )
        )
        self.assertFalse(
            claim_evidence_failure_is_retryable(
                {
                    "structure": [],
                    "source": [],
                    "citation": [
                        {"error_code": "quote_unrelated_to_claim"}
                    ],
                    "unverified": [],
                }
            )
        )
