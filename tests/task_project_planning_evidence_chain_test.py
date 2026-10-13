"""Task-owned project-material provenance and final citation-ledger tests."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

from examples.agent_service.task._citation_verifier import (
    verify_task_citations,
)
from examples.agent_service.task._s02_project_planning_evidence import (
    materialize_s02_project_planning_ledger,
)
from examples.agent_service.task._knowledge import (
    PROJECT_MATERIAL_SEARCH_QUERIES,
    TaskKnowledgeError,
    TaskKnowledgeGateway,
)
from examples.agent_service.task._agentscope_executor import (
    _allow_s02_pending_project_material,
    _s02_missing_project_material_guidance,
)
from examples.agent_service.task._clarification import (
    EffectiveRequirement,
    EffectiveRequirements,
)


_OWNER = "evidence-owner"
_KB_ID = "authorized-kb"
_PROJECT_DOC_ID = "doc-project-brief"
_TEMPLATE_DOC_ID = "doc-planning-template"
_PROJECT_SOURCE = "01_项目委托与需求说明_测试.docx"
_TEMPLATE_SOURCE = "02_策划报告模板.docx"
_PROJECT_CHUNKS = (
    "示范工程项目概况。建筑面积：12000平方米。",
    "设备台账数量：64台。项目预算：800万元。",
    "建设周期：12个月。",
)
_TEMPLATE_QUOTE = "B-01 方案应明确项目目标与验收条件。"


def _view():
    return SimpleNamespace(
        id=_KB_ID,
        name="用户授权知识库",
        description="",
        document_count=2,
        chunk_count=4,
        status_counts=SimpleNamespace(ready=2),
    )


def _document(document_id, filename):
    return SimpleNamespace(
        id=document_id,
        status="ready",
        updated_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        data=SimpleNamespace(filename=filename),
    )


def _result(document_id, source, content, chunk_index, total_chunks):
    return SimpleNamespace(
        document_id=document_id,
        score=0.91 - chunk_index / 100,
        chunk=SimpleNamespace(
            content=SimpleNamespace(text=content),
            source=source,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        ),
    )


class _ProjectKnowledgeFixture:
    """Implements the existing authorized KB service surface only."""

    def __init__(self, mode="project"):
        self.mode = mode
        self.search_calls = []
        self.document_calls = []

    async def list_knowledge_base_views(
        self,
        user_id,
        *,
        knowledge_base_id=None,
        page=1,
        page_size=128,
    ):
        del page, page_size
        if user_id != _OWNER or knowledge_base_id not in (None, _KB_ID):
            return [], 0
        return [_view()], 1

    async def get_document_status(
        self, user_id, knowledge_base_id, document_ids
    ):
        self.document_calls.append(
            (user_id, knowledge_base_id, list(document_ids)),
        )
        if user_id != _OWNER or knowledge_base_id != _KB_ID:
            return []
        documents = {
            _PROJECT_DOC_ID: _document(_PROJECT_DOC_ID, _PROJECT_SOURCE),
            _TEMPLATE_DOC_ID: _document(_TEMPLATE_DOC_ID, _TEMPLATE_SOURCE),
            "doc-case": _document("doc-case", "03_历史案例.docx"),
        }
        return [documents[item] for item in document_ids if item in documents]

    async def search(self, user_id, knowledge_base_id, query, top_k=5):
        self.search_calls.append((user_id, knowledge_base_id, query, top_k))
        if user_id != _OWNER or knowledge_base_id != _KB_ID:
            return []
        if self.mode == "unrelated":
            return [
                _result(
                    "doc-case",
                    "03_历史案例.docx",
                    "历史案例仅供参考，含项目预算和建设周期的假设值。",
                    0,
                    1,
                ),
                _result(
                    _TEMPLATE_DOC_ID,
                    _TEMPLATE_SOURCE,
                    _TEMPLATE_QUOTE,
                    0,
                    1,
                ),
            ][:top_k]
        if query == PROJECT_MATERIAL_SEARCH_QUERIES[1]:
            return [
                _result(
                    _PROJECT_DOC_ID,
                    _PROJECT_SOURCE,
                    _PROJECT_CHUNKS[1],
                    1,
                    len(_PROJECT_CHUNKS),
                ),
            ][:top_k]
        if query == PROJECT_MATERIAL_SEARCH_QUERIES[2]:
            return [
                _result(
                    _PROJECT_DOC_ID,
                    _PROJECT_SOURCE,
                    _PROJECT_CHUNKS[2],
                    2,
                    len(_PROJECT_CHUNKS),
                ),
            ][:top_k]
        return [
            _result(
                _PROJECT_DOC_ID,
                _PROJECT_SOURCE,
                _PROJECT_CHUNKS[0],
                0,
                len(_PROJECT_CHUNKS),
            ),
        ][:top_k]


class ProjectMaterialRetrievalTest(IsolatedAsyncioTestCase):
    async def test_retrieval_is_separate_bounded_authorized_and_chunk_aware(
        self,
    ):
        service = _ProjectKnowledgeFixture()
        gateway = TaskKnowledgeGateway(service, SimpleNamespace())

        result = await gateway.retrieve_project_material_evidence(
            _OWNER,
            [_KB_ID],
            project_name="示范工程",
            source_material_ids=[_PROJECT_DOC_ID],
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            [item["chunk_index"] for item in result["evidence"]],
            [0, 1, 2],
        )
        self.assertEqual(
            [call[2] for call in service.search_calls],
            [
                "示范工程 项目委托 项目概况 项目背景 建设目标",
                *PROJECT_MATERIAL_SEARCH_QUERIES[1:],
            ],
        )
        for evidence in result["evidence"]:
            self.assertEqual(evidence["knowledge_base_id"], _KB_ID)
            self.assertEqual(evidence["knowledge_base_name"], "用户授权知识库")
            self.assertEqual(evidence["document_id"], _PROJECT_DOC_ID)
            self.assertEqual(evidence["source"], _PROJECT_SOURCE)
            self.assertEqual(evidence["evidence_type"], "project_material")
            self.assertRegex(evidence["content_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(evidence["source_status"], "ready")
        self.assertEqual(
            [item["source_text"] for item in result["evidence"]],
            list(_PROJECT_CHUNKS),
        )

    async def test_demo_project_charter_is_classified_and_aggregated_across_chunks(
        self,
    ):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "examples/agent_service/task/docs/p7b-demo-data/knowledge-sources/"
            "DEMO-SOURCE-001-project-charter.md"
        )
        source_text = source_path.read_text(encoding="utf-8")
        header, rest = source_text.split("## 委托背景", maxsplit=1)
        background, facts = rest.split("## 演示输入边界", maxsplit=1)
        demo_chunks = (
            header.strip(),
            "## 委托背景" + background.strip(),
            "## 演示输入边界" + facts.split("## 预期咨询范围", 1)[0].strip(),
        )
        document_id = "authorized-demo-project-charter"
        source_name = source_path.name

        class DemoCharterKnowledgeFixture(_ProjectKnowledgeFixture):
            async def get_document_status(
                self, user_id, knowledge_base_id, document_ids
            ):
                self.document_calls.append(
                    (user_id, knowledge_base_id, list(document_ids))
                )
                if user_id != _OWNER or knowledge_base_id != _KB_ID:
                    return []
                if document_id not in document_ids:
                    return []
                return [_document(document_id, source_name)]

            async def search(self, user_id, knowledge_base_id, query, top_k=5):
                self.search_calls.append(
                    (user_id, knowledge_base_id, query, top_k)
                )
                if user_id != _OWNER or knowledge_base_id != _KB_ID:
                    return []
                if query == PROJECT_MATERIAL_SEARCH_QUERIES[1]:
                    index = 1
                elif query == PROJECT_MATERIAL_SEARCH_QUERIES[2]:
                    index = 2
                else:
                    index = 0
                return [
                    _result(
                        document_id,
                        source_name,
                        demo_chunks[index],
                        index,
                        len(demo_chunks),
                    ),
                ][:top_k]

        service = DemoCharterKnowledgeFixture()
        gateway = TaskKnowledgeGateway(service, SimpleNamespace())
        result = await gateway.retrieve_project_material_evidence(
            _OWNER,
            [_KB_ID],
            project_name="DEMO/SYNTHETIC 澄湖科创园项目",
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            [item["chunk_index"] for item in result["evidence"]],
            [0, 1, 2],
        )
        self.assertEqual(
            {item["source"] for item in result["evidence"]},
            {"DEMO-SOURCE-001-project-charter.md"},
        )
        self.assertTrue(
            any("建筑面积" in item["source_text"] for item in result["evidence"])
        )

    async def test_project_material_not_found_guides_pending_draft_not_fake_evidence(
        self,
    ):
        gateway = TaskKnowledgeGateway(
            _ProjectKnowledgeFixture(mode="unrelated"),
            SimpleNamespace(),
        )
        result = await gateway.retrieve_project_material_evidence(
            _OWNER,
            [_KB_ID],
            project_name="示范工程",
        )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["evidence"], [])
        self.assertIn("不能将用户输入当作知识库证据", result["drafting_guidance"])
        self.assertIn("待核实", result["drafting_guidance"])

    async def test_unrelated_template_and_case_hits_are_not_project_facts(
        self,
    ):
        gateway = TaskKnowledgeGateway(
            _ProjectKnowledgeFixture(mode="unrelated"),
            SimpleNamespace(),
        )

        result = await gateway.retrieve_project_material_evidence(
            _OWNER,
            [_KB_ID],
        )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(
            result["message"],
            "未检索到匹配的项目委托资料。",
        )
        self.assertEqual(
            {
                item["source"]
                for item in result["retrieval_diagnostics"][
                    "rejected_documents"
                ]
            },
            {_TEMPLATE_SOURCE, "03_历史案例.docx"},
        )

    def test_missing_s02_project_source_does_not_hide_other_real_blockers(self):
        history = [
            {
                "structured_result": {
                    "template_matches": [{"source": "authorized-template"}],
                    "project_material_retrieval": {"status": "not_found"},
                },
            },
        ]
        guidance = _s02_missing_project_material_guidance(
            history,
            project_brief_provided=True,
        )
        self.assertIsNotNone(guidance)
        requirements = EffectiveRequirements(
            effective_goal="形成 S02 策划草稿",
            fields=[
                EffectiveRequirement(
                    key="project_commission_material",
                    label="项目委托资料",
                    value="",
                    status="unknown",
                    blocking=True,
                    blocking_reason="没有找到独立项目资料。",
                ),
                EffectiveRequirement(
                    key="required_project_decision",
                    label="必需业务决定",
                    value="",
                    status="unknown",
                    blocking=True,
                    blocking_reason="没有该决定就无法确定范围。",
                ),
            ],
        )
        updated = _allow_s02_pending_project_material(requirements)

        self.assertFalse(updated.fields[0].blocking)
        self.assertEqual(updated.fields[0].status, "unknown")
        self.assertTrue(updated.fields[1].blocking)
        self.assertIsNone(
            _s02_missing_project_material_guidance(
                history,
                project_brief_provided=False,
            )
        )
        self.assertIsNone(
            _s02_missing_project_material_guidance(
                [{"structured_result": {"project_material_retrieval": {"status": "not_found"}}}],
                project_brief_provided=True,
            )
        )

    async def test_inaccessible_knowledge_base_or_selected_document_fails_closed(
        self,
    ):
        gateway = TaskKnowledgeGateway(
            _ProjectKnowledgeFixture(),
            SimpleNamespace(),
        )
        with self.assertRaises(TaskKnowledgeError) as inaccessible:
            await gateway.retrieve_project_material_evidence(
                "another-user",
                [_KB_ID],
                source_material_ids=[_PROJECT_DOC_ID],
            )
        self.assertEqual(
            inaccessible.exception.code, "knowledge_base_unavailable"
        )

        with self.assertRaises(TaskKnowledgeError) as unknown_document:
            await gateway.retrieve_project_material_evidence(
                _OWNER,
                [_KB_ID],
                source_material_ids=["not-authorized"],
            )
        self.assertEqual(
            unknown_document.exception.code,
            "source_material_not_found",
        )

        with self.assertRaises(TaskKnowledgeError) as placeholder_document:
            await gateway.retrieve_task_evidence(
                _OWNER,
                [_KB_ID],
                "DEMO/SYNTHETIC 报告资料",
                source_material_ids=[
                    "__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__"
                ],
            )
        self.assertEqual(
            placeholder_document.exception.code,
            "source_material_not_found",
        )


def _evidence(evidence_type, document_id, source, quote, chunk_index=0):
    return {
        "knowledge_base_id": _KB_ID,
        "knowledge_base_name": "用户授权知识库",
        "document_id": document_id,
        "source": source,
        "chunk_index": chunk_index,
        "content_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "source_text": quote,
        "source_text_truncated": False,
        "source_status": "ready",
        "evidence_type": evidence_type,
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


def _planning_report(evidence, *, project_fact_source=True):
    project_source = next(
        (
            item
            for item in evidence
            if item["evidence_type"] == "project_material"
        ),
        None,
    )
    template_source = next(
        (
            item
            for item in evidence
            if item["evidence_type"] == "planning_template"
        ),
        None,
    )
    body_lines = ["# 项目策划报告", "", "## 项目概况"]
    claims = []
    citations = []
    if project_source and project_fact_source:
        project_claim = "项目建筑面积为12000平方米。[C1]"
        body_lines.append(project_claim)
        claims.append(
            {
                "section": "项目概况",
                "claim": project_claim,
                "claim_type": "project_fact",
                "citation_ids": ["C1"],
            },
        )
        citations.append(_citation("C1", project_source))
        template_citation_id = "C2"
    else:
        template_citation_id = "C1"

    if template_source:
        template_claim = f"依据 B-01，策划文本明确目标与验收条件。[{template_citation_id}]"
        body_lines.extend(["", "## 模板要求", template_claim])
        claims.append(
            {
                "section": "模板要求",
                "claim": template_claim,
                "claim_type": "template_rule",
                "citation_ids": [template_citation_id],
            },
        )
        citations.append(_citation(template_citation_id, template_source))
    if not project_source or not project_fact_source:
        body_lines.extend(
            [
                "",
                "## 待补资料",
                "项目面积、设备台账数量、预算及周期均待核实。",
            ],
        )
        pending_claim = "项目面积、设备台账数量、预算及周期均待核实。"
        unverified = [
            {
                "section": "待补资料",
                "claim": pending_claim,
                "reason": "本次运行没有可引用的项目委托原文。",
            }
        ]
    else:
        body_lines.extend(
            ["", "## 待补资料", "设备台账数量、预算及周期均待核实。"],
        )
        pending_claim = "设备台账数量、预算及周期均待核实。"
        unverified = [
            {
                "section": "待补资料",
                "claim": pending_claim,
                "reason": "这些事项未附对应项目来源摘录。",
            }
        ]
    body = "\n".join(body_lines)
    claims_block = (
        "\n\n```task-evidence-claims\n"
        + json.dumps(
            {"claims": claims, "unverified_items": unverified},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n```"
    )
    citation_block = (
        "\n\n```task-citations\n"
        + json.dumps(
            {"citations": citations},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n```"
    )
    return body + claims_block + citation_block


class EvidenceLedgerTest(TestCase):
    def test_server_ledger_distinguishes_source_checks_from_semantics(self):
        project_source = _evidence(
            "project_material",
            _PROJECT_DOC_ID,
            _PROJECT_SOURCE,
            _PROJECT_CHUNKS[0],
        )
        template_source = _evidence(
            "planning_template",
            _TEMPLATE_DOC_ID,
            _TEMPLATE_SOURCE,
            _TEMPLATE_QUOTE,
        )
        evidence = [project_source, template_source]
        document = _planning_report(evidence)

        source_check = verify_task_citations(document, evidence)
        self.assertTrue(source_check["passed"])
        self.assertEqual(
            source_check["verification_scope"]["citation_id_set"],
            "passed",
        )
        self.assertEqual(
            source_check["verification_scope"]["source_identity_and_quote"],
            "passed",
        )
        self.assertEqual(
            source_check["verification_scope"]["semantic_support"],
            "not_automatically_evaluated",
        )

        result = materialize_s02_project_planning_ledger(document, evidence)

        self.assertTrue(result["passed"], result["issues"])
        self.assertIn("### 来源与证据台账", result["document"])
        self.assertIn("已落条", result["document"])
        self.assertIn("待核实", result["document"])
        self.assertIn(_PROJECT_SOURCE, result["document"])
        self.assertIn(_PROJECT_CHUNKS[0], result["document"])
        self.assertIn("语义支持需人工复核", result["document"])
        self.assertNotIn("task-evidence-claims", result["document"])
        self.assertEqual(
            {item["citation_id"] for item in result["ledger"]},
            {"C1", "C2", "—"},
        )

    def test_missing_project_material_requires_pending_fact_categories(self):
        template_source = _evidence(
            "planning_template",
            _TEMPLATE_DOC_ID,
            _TEMPLATE_SOURCE,
            _TEMPLATE_QUOTE,
        )
        document = _planning_report([template_source])

        result = materialize_s02_project_planning_ledger(
            document, [template_source]
        )

        self.assertTrue(result["passed"], result["issues"])
        self.assertIn("未检索到匹配的项目委托资料", result["document"])
        self.assertTrue(
            any(item["status"] == "待核实" for item in result["ledger"])
        )

    def test_template_evidence_cannot_ground_project_facts(self):
        template_source = _evidence(
            "planning_template",
            _TEMPLATE_DOC_ID,
            _TEMPLATE_SOURCE,
            _TEMPLATE_QUOTE,
        )
        bad_claim = "项目建筑面积为12000平方米。[C1]"
        pending_claim = "项目面积、设备台账数量、预算及周期均待核实。"
        body = (
            "# 项目策划报告\n\n## 项目概况\n"
            + bad_claim
            + "\n\n## 待补资料\n"
            + pending_claim
        )
        payload = {
            "claims": [
                {
                    "section": "项目概况",
                    "claim": bad_claim,
                    # The body itself must prevent the model from bypassing
                    # project-source routing with a weaker self-declared type.
                    "claim_type": "other",
                    "citation_ids": ["C1"],
                }
            ],
            "unverified_items": [
                {
                    "section": "待补资料",
                    "claim": pending_claim,
                    "reason": "项目资料未命中。",
                }
            ],
        }
        citation = _citation("C1", template_source)
        bad_document = (
            body
            + "```task-evidence-claims\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n```\n\n```task-citations\n"
            + json.dumps(
                {"citations": [citation]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        result = materialize_s02_project_planning_ledger(
            bad_document,
            [template_source],
        )

        self.assertFalse(result["passed"])
        self.assertTrue(
            any("项目数值事实必须至少关联" in item for item in result["issues"])
        )

    def test_b_rule_requires_clause_text_and_one_source_per_fact(self):
        generic_template = _evidence(
            "planning_template",
            _TEMPLATE_DOC_ID,
            _TEMPLATE_SOURCE,
            "策划报告模板",
        )
        document = _planning_report([generic_template])
        invalid = materialize_s02_project_planning_ledger(
            document, [generic_template]
        )
        self.assertFalse(invalid["passed"])
        self.assertTrue(
            any("没有包含所标注 B 条款" in issue for issue in invalid["issues"])
        )

        source = _evidence(
            "planning_template",
            _TEMPLATE_DOC_ID,
            _TEMPLATE_SOURCE,
            _TEMPLATE_QUOTE,
        )
        repeated = _planning_report([source])
        repeated_body = repeated.split("```task-evidence-claims", 1)[0]
        repeated_body += "\n同一来源还用于另一结论。[C1]"
        repeated_result = materialize_s02_project_planning_ledger(
            repeated_body
            + repeated[repeated.index("```task-evidence-claims") :],
            [source],
        )
        self.assertFalse(repeated_result["passed"])
        self.assertTrue(
            any("多个正文位置" in issue for issue in repeated_result["issues"])
        )

    def test_citations_from_multiple_chunks_keep_exact_source_identity(self):
        first_chunk = _evidence(
            "project_material",
            _PROJECT_DOC_ID,
            _PROJECT_SOURCE,
            _PROJECT_CHUNKS[0],
            chunk_index=0,
        )
        second_chunk = _evidence(
            "project_material",
            _PROJECT_DOC_ID,
            _PROJECT_SOURCE,
            _PROJECT_CHUNKS[1],
            chunk_index=1,
        )
        claims = [
            {
                "section": "项目概况",
                "claim": "项目建筑面积为12000平方米。[C1]",
                "claim_type": "project_fact",
                "citation_ids": ["C1"],
            },
            {
                "section": "设备情况",
                "claim": "设备台账数量为64台。[C2]",
                "claim_type": "project_fact",
                "citation_ids": ["C2"],
            },
        ]
        body = (
            "# 项目策划报告\n\n## 项目概况\n"
            "项目建筑面积为12000平方米。[C1]\n\n"
            "## 设备情况\n设备台账数量为64台。[C2]"
        )
        document = (
            body
            + "\n\n```task-evidence-claims\n"
            + json.dumps(
                {"claims": claims, "unverified_items": []},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        _citation("C1", first_chunk),
                        _citation("C2", second_chunk),
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        result = materialize_s02_project_planning_ledger(
            document,
            [first_chunk, second_chunk],
        )

        self.assertTrue(result["passed"], result["issues"])
        citation_sources = {
            item["citation_id"]: (item["chunk_index"], item["quote"])
            for item in result["ledger"]
            if item["citation_id"] in {"C1", "C2"}
        }
        self.assertEqual(
            citation_sources,
            {
                "C1": (0, _PROJECT_CHUNKS[0]),
                "C2": (1, _PROJECT_CHUNKS[1]),
            },
        )

    def test_same_exact_evidence_cannot_cover_distinct_claims(self):
        source = _evidence(
            "project_material",
            _PROJECT_DOC_ID,
            _PROJECT_SOURCE,
            "建筑面积：12000平方米。设备台账数量：64台。",
        )
        claims = [
            {
                "section": "项目概况",
                "claim": "项目建筑面积为12000平方米。[C1]",
                "claim_type": "project_fact",
                "citation_ids": ["C1"],
            },
            {
                "section": "设备情况",
                "claim": "设备台账数量为64台。[C2]",
                "claim_type": "project_fact",
                "citation_ids": ["C2"],
            },
        ]
        body = (
            "# 项目策划报告\n\n## 项目概况\n"
            "项目建筑面积为12000平方米。[C1]\n\n"
            "## 设备情况\n设备台账数量为64台。[C2]"
        )
        document = (
            body
            + "\n\n```task-evidence-claims\n"
            + json.dumps(
                {"claims": claims, "unverified_items": []},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```\n\n```task-citations\n"
            + json.dumps(
                {
                    "citations": [
                        _citation("C1", source),
                        _citation("C2", source),
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n```"
        )

        result = materialize_s02_project_planning_ledger(document, [source])

        self.assertFalse(result["passed"])
        self.assertTrue(
            any("同一来源摘录被用于不同事实" in issue for issue in result["issues"])
        )


__all__ = ["EvidenceLedgerTest", "ProjectMaterialRetrievalTest"]
