"""S02 citation block syntax and generation-to-verifier contract tests."""

import hashlib
import json
from types import SimpleNamespace
from unittest import TestCase

from examples.agent_service.task._agentscope_executor import _render_task_value
from examples.agent_service.task._citation_verifier import (
    _task_evidence_id,
    verify_task_citations,
)
from examples.agent_service.task._models import (
    AgentStepConfig,
    ExecutionContext,
    StepType,
    TaskNode,
    ToolStepConfig,
)
from examples.agent_service.task._service import _with_citation_prompt


_OWNER = "s02-citation-owner"
_SOURCE_TEXT = "策划报告应明确项目目标，并列出验收条件。"


def _evidence():
    return {
        "source_type": "knowledge_base_chunk",
        "authorization_subject": _OWNER,
        "knowledge_base_id": "authorized-kb",
        "knowledge_base_name": "授权策划资料",
        "document_id": "planning-template-doc",
        "source": "策划报告编制模板.docx",
        "chunk_index": 0,
        "content_sha256": hashlib.sha256(
            _SOURCE_TEXT.encode("utf-8")
        ).hexdigest(),
        "source_text": _SOURCE_TEXT,
        "source_text_truncated": False,
        "source_status": "ready",
        "evidence_type": "planning_template",
    }


def _citation_document(
    *,
    body_marker="[C1]",
    citation_id="C1",
    evidence_id=None,
    quote=_SOURCE_TEXT,
):
    evidence_id = evidence_id or "EV-" + "1" * 24
    citation_json = json.dumps(
        {
            "citations": [
                {
                    "citation_id": citation_id,
                    "evidence_id": evidence_id,
                    "quote": quote,
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"# 项目策划初稿\n\n依据授权模板整理策划要求。{body_marker}\n\n"
        "```task-citations\n"
        f"{citation_json}\n"
        "```"
    )


def _s02_draft_and_verifier_nodes():
    retrieval = TaskNode(
        id="retrieve-template",
        name="检索策划模板",
        type=StepType.TOOL,
        config=ToolStepConfig(
            tool_name="planning.template_retrieve",
            arguments={"query": "{{previous_output}}"},
        ),
        order=1,
        capability_key="planning.template_retrieve",
    )
    draft = TaskNode(
        id="draft-document",
        name="生成策划初稿",
        type=StepType.AGENT,
        config=AgentStepConfig(prompt="按已确认的大纲生成策划初稿。"),
        order=3,
    )
    verifier = TaskNode(
        id="verify-citations",
        name="核对引用",
        type=StepType.TOOL,
        config=ToolStepConfig(
            tool_name="document.citation_verify",
            arguments={"draft": "{{previous_output}}"},
        ),
        order=4,
        capability_key="document.citation_verify",
    )
    final_output = TaskNode(
        id="produce-final-output",
        name="整理策划输出",
        type=StepType.AGENT,
        config=AgentStepConfig(prompt="整理经过核验的策划报告。"),
        order=6,
    )
    run = SimpleNamespace(
        source_template_id="s02_project_planning",
        source_template_version="2.0.0",
        nodes=[retrieval, draft, verifier, final_output],
    )
    return run, draft, verifier, final_output


class S02CitationBlockProtocolTest(TestCase):
    def setUp(self):
        self.source = _evidence()
        # The verifier derives this ID from the run-owned source identity.
        self.evidence_id = _task_evidence_id(self.source, _OWNER)

    def test_one_exact_task_citations_block_passes(self):
        result = verify_task_citations(
            _citation_document(evidence_id=self.evidence_id),
            [self.source],
            authorization_subject=_OWNER,
        )

        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(
            result["verification_scope"]["citation_id_set"], "passed"
        )
        self.assertEqual(
            result["verification_scope"]["source_identity_and_quote"],
            "passed",
        )

    def test_plain_title_followed_by_json_fence_is_not_a_valid_block(self):
        malformed = (
            "# 项目策划初稿\n\n要求来自模板。[C1]\n\n"
            "task-citations\n"
            "```json\n"
            + json.dumps(
                {
                    "citations": [
                        {
                            "citation_id": "C1",
                            "evidence_id": self.evidence_id,
                            "quote": _SOURCE_TEXT,
                        }
                    ]
                },
                ensure_ascii=False,
            )
            + "\n```"
        )

        result = verify_task_citations(malformed, [self.source])

        self.assertFalse(result["passed"])
        self.assertIn("且仅包含一个 task-citations", result["issues"][0])

    def test_missing_block_fails(self):
        result = verify_task_citations("正文要求。[C1]", [self.source])
        self.assertFalse(result["passed"])
        self.assertIn("且仅包含一个 task-citations", result["issues"][0])

    def test_multiple_blocks_fail(self):
        block = _citation_document(evidence_id=self.evidence_id).split(
            "\n\n```task-citations", 1
        )[1]
        citation_block = "\n\n```task-citations" + block
        result = verify_task_citations(
            _citation_document(evidence_id=self.evidence_id) + citation_block,
            [self.source],
        )
        self.assertFalse(result["passed"])
        self.assertIn("且仅包含一个 task-citations", result["issues"][0])

    def test_invalid_json_fails(self):
        result = verify_task_citations(
            "正文要求。[C1]\n\n```task-citations\n{bad json}\n```",
            [self.source],
        )
        self.assertFalse(result["passed"])
        self.assertIn("不是合法 JSON", result["issues"][0])

    def test_body_marker_and_citation_id_must_match(self):
        result = verify_task_citations(
            _citation_document(
                body_marker="[C2]",
                citation_id="C1",
                evidence_id=self.evidence_id,
            ),
            [self.source],
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["verification_scope"]["citation_id_set"], "failed"
        )

    def test_evidence_id_must_belong_to_this_run(self):
        result = verify_task_citations(
            _citation_document(evidence_id="EV-" + "0" * 24),
            [self.source],
            authorization_subject=_OWNER,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("不属于本次运行" in issue for issue in result["issues"])
        )

    def test_quote_must_be_a_contiguous_source_excerpt(self):
        result = verify_task_citations(
            _citation_document(
                evidence_id=self.evidence_id,
                quote="项目目标验收条件",
            ),
            [self.source],
            authorization_subject=_OWNER,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("摘录与保存的原文片段不一致" in issue for issue in result["issues"])
        )

    def test_s02_generation_prompt_and_previous_output_reach_verifier(self):
        run, draft_node, verifier_node, final_node = (
            _s02_draft_and_verifier_nodes()
        )
        prompted_draft = _with_citation_prompt(run, draft_node)
        self.assertIn("```task-citations\n", prompted_draft.config.prompt)
        self.assertIn(
            "禁止在代码区块前另写 `task-citations` 标题",
            prompted_draft.config.prompt,
        )
        self.assertIn(
            "禁止使用 `json` 作为围栏\n语言标识",
            prompted_draft.config.prompt,
        )
        prompted_final = _with_citation_prompt(run, final_node)
        self.assertIn("```task-citations\n", prompted_final.config.prompt)

        document = _citation_document(evidence_id=self.evidence_id)
        context = ExecutionContext(
            user_id=_OWNER,
            task_id="s02-task",
            run_id="s02-run",
            task_revision=1,
        )
        tool_input = _render_task_value(
            verifier_node.config.arguments,
            document,
            context,
            typed=True,
        )

        self.assertEqual(tool_input["draft"], document)
        result = verify_task_citations(
            tool_input["draft"],
            [self.source],
            authorization_subject=_OWNER,
        )
        self.assertTrue(result["passed"], result["issues"])

    def test_exact_format_addition_is_s02_specific(self):
        run, draft_node, _, _ = _s02_draft_and_verifier_nodes()
        for template_id, version in (
            ("s01_project_init", "2.0.0"),
            ("s07_report_generation", "3.0.0"),
        ):
            with self.subTest(template_id=template_id):
                other_run = SimpleNamespace(
                    source_template_id=template_id,
                    source_template_version=version,
                    nodes=run.nodes,
                )
                prompted = _with_citation_prompt(other_run, draft_node)
                self.assertNotIn(
                    "S02 引用区块的格式必须严格遵循",
                    prompted.config.prompt,
                )
