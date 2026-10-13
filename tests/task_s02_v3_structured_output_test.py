"""S02 v3 structured output, evidence assembly, and SOP gate regressions."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from examples.agent_service.task._evidence_contract import (
    normalize_evidence_snapshot,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    RunStatus,
    StepVerdict,
    TaskContext,
    TaskInputReply,
    TaskRunRequest,
    TaskToolSchema,
    UpdateTaskRequest,
)
from examples.agent_service.task._output_validation import (
    validate_structured_result,
)
from examples.agent_service.task._s02_structured_output import (
    assemble_s02_v3_output,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_models import (
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService


OWNER = "s02-v3-test-owner"
KB_ID = "kb-s02-v3-fixture"
PROJECT_DOCUMENT_ID = "doc-s02-project-fixture"
TEMPLATE_DOCUMENT_ID = "doc-s02-template-fixture"
PROJECT_QUOTE = "项目名称：DEMO-P7-R3 绿色园区策划项目。"

RULE_TEXTS = {
    "B-01": "明确项目名称、编号和委托对象。",
    "B-02": "说明咨询范围和不包含事项。",
    "B-03": "说明能源数据的时间范围和计量单位。",
    "B-04": "说明表计边界、缺失数据和校准需求。",
    "B-05": "说明设备台账字段及其来源。",
    "B-06": "区分已收集资料、用户陈述和待取得资料。",
    "B-07": "说明访谈对象类别和记录方式，不虚构访谈结果。",
    "B-08": "说明现场核查步骤和安全前提。",
    "B-09": "说明分析假设、公式和限制。",
    "B-10": "区分节能目标建议值与实际节能量。",
    "B-11": "列出建议措施及其所需验证条件。",
    "B-12": "对预算数字说明来源和审批状态。",
    "B-13": "对工期说明关键依赖和不确定性。",
    "B-14": "列出风险、责任方和需补充证据。",
    "B-15": "列出交付物、复核人和草稿状态。",
}


def _schema() -> dict[str, Any]:
    template = TemplateRegistry().get_template_version(
        "s02_project_planning", "3.2.0"
    )
    assert template is not None
    contract = template.steps[-1].output_contract
    assert contract is not None and contract.json_schema is not None
    return contract.json_schema


def _raw_evidence(
    *,
    evidence_type: str,
    source_text: str,
    source: str,
    document_id: str,
    authorization_subject: str = OWNER,
) -> dict[str, Any]:
    return {
        "knowledge_base_id": KB_ID,
        "knowledge_base_name": "DEMO/SYNTHETIC Task v3 测试知识库",
        "document_id": document_id,
        "source": source,
        "chunk_index": 0,
        "content_sha256": hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest(),
        "source_text": source_text,
        "source_text_truncated": False,
        "source_type": "knowledge_base_chunk",
        "source_status": "ready",
        "authorization_subject": authorization_subject,
        "evidence_type": evidence_type,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def _evidence(*, duplicate_template: bool = False) -> list[dict[str, Any]]:
    template_lines = [
        "# 策划报告编制模板",
        "## 八章报告大纲与章节结构",
        "第一章 项目概况",
        "第二章 资料清单与可信度",
        "第三章 能源基线与计量口径",
        "第四章 设备台账现状",
        "第五章 分析方法与工作计划",
        "第六章 方案建议",
        "第七章 预算与进度边界",
        "第八章 风险、待核实事项与交付清单",
        "## B-01 至 B-15 编写要求",
        *(
            f"{rule_id} {description}"
            for rule_id, description in RULE_TEXTS.items()
        ),
        "策划模板属于演示材料，不是发布的企业制度、监管要求或正式标准。",
    ]
    template_text = "\n".join(template_lines)
    evidence = [
        _raw_evidence(
            evidence_type="project_material",
            source_text=(
                "# DEMO 项目委托资料\n"
                "资料类型：项目委托资料；数据标记：DEMO/SYNTHETIC。\n"
                f"{PROJECT_QUOTE}"
            ),
            source="DEMO/SYNTHETIC 项目委托资料.md",
            document_id=PROJECT_DOCUMENT_ID,
        ),
        _raw_evidence(
            evidence_type="planning_template",
            source_text=template_text,
            source="DEMO/SYNTHETIC 策划报告编制模板.md",
            document_id=TEMPLATE_DOCUMENT_ID,
        ),
    ]
    if duplicate_template:
        evidence.append(
            _raw_evidence(
                evidence_type="planning_template",
                source_text=template_text,
                source="DEMO/SYNTHETIC 策划报告编制模板副本.md",
                document_id="doc-s02-template-duplicate",
            ),
        )
    return normalize_evidence_snapshot(
        evidence,
        authorization_subject=None,
    )


def _business_payload(
    *,
    repeat_project_fact: bool = False,
    include_template_limitation: bool = False,
) -> dict[str, Any]:
    chapters: list[dict[str, Any]] = [
        {"number": number, "items": []} for number in range(1, 9)
    ]
    fact = {
        "kind": "fact",
        "text": "项目名称为 DEMO-P7-R3 绿色园区策划项目。",
        "source_quotes": [PROJECT_QUOTE],
        "rule_ids": [],
    }
    chapters[0]["items"].append(fact)
    if repeat_project_fact:
        chapters[1]["items"].append(dict(fact))

    for index, (rule_id, description) in enumerate(RULE_TEXTS.items()):
        quote = f"{rule_id} {description}"
        chapters[index % 8]["items"].append(
            {
                "kind": "rule",
                "text": quote,
                "source_quotes": [quote],
                "rule_ids": [rule_id],
            },
        )

    chapters[0]["items"].append(
        {
            "kind": "analysis",
            "text": "结合资料范围，后续仍需核对计量边界。",
            "source_quotes": [],
            "rule_ids": [],
        },
    )
    chapters[5]["items"].append(
        {
            "kind": "recommendation",
            "text": "建议先确认计量边界，再开展后续测算。",
            "source_quotes": [],
            "rule_ids": [],
        },
    )
    pending_items = (
        (
            "pending_verification",
            "项目计量边界尚未完成独立核实。",
            "边界原始资料尚未检索到，需要补充后核实。",
        ),
        (
            "pending_supplement",
            "项目校准记录尚未取得。",
            "本次授权资料未包含校准记录，需要补充原始记录。",
        ),
        (
            "pending_confirmation",
            "项目报告复核人尚待指定。",
            "责任人尚未确认，需要项目方指定复核人。",
        ),
    )
    for status, text, reason in pending_items:
        chapters[7]["items"].append(
            {
                "kind": "unverified",
                "text": text,
                "source_quotes": [],
                "rule_ids": [],
                "unverified_status": status,
                "reason": reason,
                "expected_evidence_types": ["project_material"],
            },
        )
    if include_template_limitation:
        limitation_quote = (
            "策划模板属于演示材料，不是发布的企业制度、监管要求或正式标准。"
        )
        chapters[7]["items"].append(
            {
                "kind": "template_limitation",
                "text": "策划模板属于演示材料，不是正式制度、监管要求或正式标准。",
                "source_quotes": [limitation_quote],
                "rule_ids": [],
            }
        )
    return {
        "title": "DEMO/SYNTHETIC 项目策划报告草稿",
        "chapters": chapters,
    }


def _real_run_unverified_shape_payload() -> dict[str, Any]:
    """Deidentified reproduction of the supplied 52-item Run shape."""

    payload = _business_payload()
    chapters = payload["chapters"]
    for index in range(7):
        chapters[index % 7]["items"].append(
            {
                "kind": "fact",
                "text": PROJECT_QUOTE,
                "source_quotes": [PROJECT_QUOTE],
                "rule_ids": [],
            }
        )
    for index in range(9):
        chapters[index % 8]["items"].append(
            {
                "kind": "analysis",
                "text": f"对演示资料范围进行分析 {index + 1}。",
                "source_quotes": [],
                "rule_ids": [],
            }
        )
    for index in range(10):
        chapters[index % 8]["items"].append(
            {
                "kind": "recommendation",
                "text": f"建议按演示步骤复核第 {index + 1} 项资料。",
                "source_quotes": [],
                "rule_ids": [],
            }
        )
    for index in range(5):
        chapters[7]["items"].append(
            {
                "kind": "unverified",
                "text": f"第 {index + 4} 项演示资料尚待核实。",
                "unverified_status": "pending_verification",
                "reason": "脱敏回归样本未提供相应原始资料。",
                "expected_evidence_types": ["project_material"],
            }
        )
    unverified_items = [
        item
        for chapter in chapters
        for item in chapter["items"]
        if item["kind"] == "unverified"
    ]
    assert len(unverified_items) == 8
    for item in unverified_items:
        item.pop("source_quotes", None)
        item.pop("rule_ids", None)
    assert sum(len(chapter["items"]) for chapter in chapters) == 52
    return payload


def _real_run_kind_conflict_payload() -> dict[str, Any]:
    """Deidentified 8-chapter/39-item shape from the supplied Run summary."""

    payload = _business_payload()
    chapters = payload["chapters"]
    for index in range(5):
        chapters[7]["items"].append(
            {
                "kind": "unverified",
                "text": f"补充的待核实事项 {index + 1}。",
                "source_quotes": [],
                "rule_ids": [],
                "unverified_status": "pending_verification",
                "reason": "授权资料不足，需人工复核。",
                "expected_evidence_types": ["project_material"],
            }
        )
    for index in range(13):
        if index == 0:
            item = {
                "kind": "rule",
                "text": "策划模板适用限制适用于 B-01 至 B-15 全部条款。",
                "source_quotes": [],
                "rule_ids": [],
            }
        else:
            item = {
                "kind": "analysis",
                "text": f"对演示业务内容作一般分析 {index}。",
                "source_quotes": [],
                "rule_ids": [],
            }
        item.update(
            {
                "unverified_status": "pending_verification",
                "reason": "来源语义支持需人工复核。",
                "expected_evidence_types": ["project_material"],
            }
        )
        chapters[index % 7]["items"].append(item)

    ordinary = [
        item
        for chapter in chapters
        for item in chapter["items"]
        if item["kind"] != "unverified"
    ]
    for item in ordinary:
        item.setdefault("unverified_status", "pending_verification")
        item.setdefault("reason", "来源语义支持需人工复核。")
        item.setdefault("expected_evidence_types", ["project_material"])
    unverified = [
        item
        for chapter in chapters
        for item in chapter["items"]
        if item["kind"] == "unverified"
    ]
    assert len(ordinary) == 31
    assert len(unverified) == 8
    assert sum(len(chapter["items"]) for chapter in chapters) == 39
    return payload


def _real_run_b_clause_reference_shape_payload() -> dict[str, Any]:
    """47-item deidentified reproduction of the supplied B-marker failure."""

    payload = _business_payload(include_template_limitation=True)
    chapters = payload["chapters"]
    limitation_quote = (
        "策划模板属于演示材料，不是发布的企业制度、监管要求或正式标准。"
    )

    for index in range(11):
        chapters[index % 8]["items"].append(
            {
                "kind": "analysis",
                "text": (
                    f"分析说明：B-{index + 1:02d} 是模板条款编号，"
                    "此处仅讨论条款引用，不新增规则。"
                ),
                "source_quotes": [],
                "rule_ids": [],
            }
        )

    pending_statuses = (
        "pending_verification",
        "pending_supplement",
        "pending_confirmation",
    )
    for index in range(12):
        chapters[index % 8]["items"].append(
            {
                "kind": "unverified",
                "text": (
                    f"B-{index + 1:02d} 对应事项所需的项目资料尚待补充。"
                ),
                "source_quotes": [],
                "rule_ids": [],
                "unverified_status": pending_statuses[index % 3],
                "reason": "当前授权资料不足，尚不能确认对应项目事实。",
                "expected_evidence_types": ["project_material"],
            }
        )

    for index in range(2):
        chapters[7]["items"].append(
            {
                "kind": "template_limitation",
                "text": (
                    "策划模板属于演示材料；B-01 至 B-15 的编号范围"
                    "仅用于说明模板覆盖边界，不代表正式制度。"
                ),
                "source_quotes": [limitation_quote],
                "rule_ids": [],
            }
        )

    assert sum(len(chapter["items"]) for chapter in chapters) == 47
    return payload


def _assemble(
    payload: dict[str, Any] | str | None = None,
    *,
    evidence: list[dict[str, Any]] | None = None,
    template_version: str = "3.2.0",
) -> dict[str, Any]:
    raw = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload or _business_payload(), ensure_ascii=False)
    )
    return assemble_s02_v3_output(
        raw,
        _schema(),
        _evidence() if evidence is None else evidence,
        authorization_subject=OWNER,
        template_version=template_version,
    )


def test_s02_v3_assembles_stable_claim_occurrence_and_binding_protocol() -> None:
    payload = _business_payload(repeat_project_fact=True)
    result = _assemble(payload)
    repeated_result = _assemble(payload)

    assert result["passed"] is True
    assert repeated_result["passed"] is True
    assert result["document"] == repeated_result["document"]
    assert result["claim_count"] < result["occurrence_count"]
    assert result["binding_count"] == 17
    assert result["unverified_count"] == 3
    assert result["citation_count"] == 16
    assert "```task-claim-evidence\n" in result["document"]
    assert "```task-citations\n" in result["document"]

    claims_payload = _protocol_block(result["document"], "task-claim-evidence")
    citations_payload = _protocol_block(result["document"], "task-citations")
    claims = claims_payload["claims"]
    assert len({claim["claim_id"] for claim in claims}) == len(claims)
    occurrences = [
        occurrence
        for claim in claims
        for occurrence in claim["occurrences"]
    ]
    assert len({item["occurrence_id"] for item in occurrences}) == len(
        occurrences
    )
    repeated = [
        claim
        for claim in claims
        if claim["text"] == "项目名称为 DEMO-P7-R3 绿色园区策划项目。"
    ]
    assert len(repeated) == 1
    assert len(repeated[0]["occurrences"]) == 2
    assert len(
        [
            binding
            for binding in claims_payload["bindings"]
            if binding["claim_id"] == repeated[0]["claim_id"]
        ]
    ) == 2
    assert all(
        citation["evidence_id"].startswith("EV-")
        for citation in citations_payload["citations"]
    )
    assert {
        item["status"] for item in claims_payload["unverified_items"]
    } == {
        "pending_verification",
        "pending_supplement",
        "pending_confirmation",
    }
    uncited = [
        claim
        for claim in claims
        if claim["kind"] in {"analysis", "recommendation"}
    ]
    assert len(uncited) == 2
    assert all(not claim["citation_ids"] for claim in uncited)


def test_s02_v32_template_limitation_requires_authorized_template_evidence() -> None:
    payload = _business_payload(include_template_limitation=True)
    result = _assemble(payload)

    assert result["passed"] is True
    protocol = _protocol_block(result["document"], "task-claim-evidence")
    limitation_claims = [
        item for item in protocol["claims"]
        if item["kind"] == "template_limitation"
    ]
    assert len(limitation_claims) == 1
    binding = next(
        item
        for item in protocol["bindings"]
        if item["claim_id"] == limitation_claims[0]["claim_id"]
    )
    assert binding["quote"] == (
        "策划模板属于演示材料，不是发布的企业制度、监管要求或正式标准。"
    )
    citation = next(
        item
        for item in _protocol_block(result["document"], "task-citations")[
            "citations"
        ]
        if item["citation_id"] == binding["citation_id"]
    )
    assert citation["evidence_id"] == binding["evidence_id"]

    duplicate = _assemble(
        payload,
        evidence=_evidence(duplicate_template=True),
    )
    assert duplicate["error_code"] == "TASK_S02_V3_SOURCE_MATCH_AMBIGUOUS"

    unauthorized = _evidence()
    template = next(
        item for item in unauthorized
        if item["evidence_type"] == "planning_template"
    )
    template["authorization_subject"] = "different-user"
    assert (
        _assemble(payload, evidence=unauthorized)["error_code"]
        == "TASK_S02_V3_SOURCE_PERMISSION_MISMATCH"
    )

    bad_hash = _evidence()
    template = next(
        item for item in bad_hash
        if item["evidence_type"] == "planning_template"
    )
    template["source_text"] += " tampered"
    assert (
        _assemble(payload, evidence=bad_hash)["error_code"]
        == "TASK_S02_V3_SOURCE_HASH_MISMATCH"
    )


def test_real_run_47_items_allow_b_clause_mentions_outside_rule_kind() -> None:
    payload = _real_run_b_clause_reference_shape_payload()
    items = [
        item
        for chapter in payload["chapters"]
        for item in chapter["items"]
    ]
    rules = [item for item in items if item["kind"] == "rule"]
    limitations = [
        item for item in items if item["kind"] == "template_limitation"
    ]

    assert len(items) == 47
    assert {item["rule_ids"][0] for item in rules} == set(RULE_TEXTS)
    assert len(rules) == 15
    assert len(limitations) == 3

    result = _assemble(payload)

    assert result["passed"] is True
    protocol = _protocol_block(result["document"], "task-claim-evidence")
    assembled_rules = [
        item for item in protocol["claims"] if item["kind"] == "rule"
    ]
    assert len(assembled_rules) == 15
    assert len(protocol["unverified_items"]) == 15


def _protocol_block(document: str, name: str) -> dict[str, Any]:
    marker = f"```{name}\n"
    assert document.count(marker) == 1
    start = document.index(marker) + len(marker)
    end = document.index("\n```", start)
    return json.loads(document[start:end])


@pytest.mark.parametrize(
    ("raw", "error_code"),
    [
        ('{"title":"A","title":"B"}', "TASK_S02_V3_JSON_DUPLICATE_KEY"),
        ('{"title":"incomplete"', "TASK_S02_V3_JSON_INVALID"),
        ('{"title":"missing chapters"}', "TASK_S02_V3_SCHEMA_INVALID"),
    ],
)
def test_s02_v3_rejects_duplicate_truncated_and_schema_invalid_json(
    raw: str,
    error_code: str,
) -> None:
    result = _assemble(raw)
    assert result["passed"] is False
    assert result["error_code"] == error_code


def test_real_run_shape_normalizes_only_omitted_unverified_empty_arrays() -> None:
    payload = _real_run_unverified_shape_payload()
    raw = json.dumps(payload, ensure_ascii=False)
    original_validation = validate_structured_result(_schema(), payload)
    assert original_validation.passed is False
    assert len(original_validation.issues) == 8
    assert all(issue.endswith(": oneOf") for issue in original_validation.issues)

    result = _assemble(raw)

    assert result["passed"] is True
    assert len(result["normalization_changes"]) == 16
    assert result["raw_model_output"] == raw
    raw_items = [
        item
        for chapter in json.loads(result["raw_model_output"])["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    ]
    assert all("source_quotes" not in item and "rule_ids" not in item for item in raw_items)
    normalized_items = [
        item
        for chapter in result["business_result"]["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    ]
    assert len(normalized_items) == 8
    assert all(item["source_quotes"] == [] for item in normalized_items)
    assert all(item["rule_ids"] == [] for item in normalized_items)
    assert all(
        change["operation"] == "insert_empty_array"
        for change in result["normalization_changes"]
    )


def test_real_run_kind_conflict_is_rejected_by_schema_with_field_diagnostics() -> None:
    payload = _real_run_kind_conflict_payload()
    result = _assemble(payload)

    assert result["passed"] is False
    assert result["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert result["validation_stage"] == "json_schema"
    pending_field_diagnostics = [
        item
        for item in result["diagnostics"]
        if item["error_code"] == "field_not_allowed_for_kind"
    ]
    assert len(pending_field_diagnostics) == 31 * 3
    assert all(
        item["field"]
        in {"unverified_status", "reason", "expected_evidence_types"}
        and item["kind"] != "unverified"
        and item["chapter_number"] in range(1, 9)
        and isinstance(item["item_index"], int)
        for item in pending_field_diagnostics
    )
    extra_rule = [
        item
        for item in result["diagnostics"]
        if item["kind"] == "rule" and item["field"] == "rule_ids"
    ]
    assert extra_rule
    assert all(
        item["error_code"] == "minimum_items_not_met"
        for item in extra_rule
    )
    assert result["normalization_changes"] == []


def test_s02_v3_preserves_existing_empty_arrays_and_reports_schema_paths() -> None:
    existing_empty = _assemble(_business_payload())
    assert existing_empty["passed"] is True
    assert existing_empty["normalization_changes"] == []

    missing_required = _business_payload()
    unverified = next(
        item
        for chapter in missing_required["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )
    unverified.pop("text")
    missing_result = _assemble(missing_required)
    assert missing_result["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert missing_result["validation_stage"] == "json_schema"
    assert any(
        "chapters[7].items[1].text: missing_required_field" in issue
        and "item.kind=unverified" in issue
        and "raw_error_type=required" in issue
        for issue in missing_result["issues"]
    )

    invalid_types = _business_payload()
    invalid_unverified = next(
        item
        for chapter in invalid_types["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )
    invalid_unverified["source_quotes"] = ""
    invalid_unverified["rule_ids"] = None
    invalid_result = _assemble(invalid_types)
    assert invalid_result["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert invalid_result["normalization_changes"] == []
    assert any(
        item["field"] == "source_quotes"
        and item["error_code"] == "field_type_invalid"
        for item in invalid_result["diagnostics"]
    )
    assert any(
        item["field"] == "rule_ids"
        and item["error_code"] == "field_type_invalid"
        for item in invalid_result["diagnostics"]
    )

    missing_reason = _business_payload()
    missing_reason_item = next(
        item
        for chapter in missing_reason["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )
    missing_reason_item.pop("reason")
    missing_reason_result = _assemble(missing_reason)
    assert missing_reason_result["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert missing_reason_result["validation_stage"] == "json_schema"
    assert any(
        item["field"] == "reason"
        and item["error_code"] == "missing_required_field"
        and item["kind"] == "unverified"
        for item in missing_reason_result["diagnostics"]
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_field"),
    [
        (
            "source_quotes",
            ["not an authorized source quote"],
            "source_quotes",
        ),
        ("rule_ids", ["B-01"], "rule_ids"),
    ],
)
def test_s02_v3_does_not_remove_nonempty_unverified_source_declarations(
    field: str,
    value: list[str],
    expected_field: str,
) -> None:
    payload = _business_payload()
    unverified = next(
        item
        for chapter in payload["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )
    unverified[field] = value
    result = _assemble(payload)
    assert result["passed"] is False
    assert result["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert "normalized_business_result" in result
    assert result["normalization_changes"] == []
    assert any(
        item["field"] == expected_field
        and item["error_code"] == "maximum_items_exceeded"
        for item in result["diagnostics"]
    )


@pytest.mark.parametrize(
    "field",
    ["unverified_status", "reason", "expected_evidence_types"],
)
def test_s02_v3_does_not_synthesize_unverified_business_fields(
    field: str,
) -> None:
    payload = _business_payload()
    unverified = next(
        item
        for chapter in payload["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )
    unverified.pop(field)

    result = _assemble(payload)

    assert result["passed"] is False
    assert result["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert result["validation_stage"] == "json_schema"
    assert any(
        item["field"] == field
        and item["error_code"] == "missing_required_field"
        for item in result["diagnostics"]
    )
    assert field not in result["normalized_business_result"]["chapters"][7][
        "items"
    ][1]


def test_s02_v3_fails_closed_for_missing_ambiguous_or_wrong_source() -> None:
    payload = _business_payload()
    missing = _business_payload()
    missing["chapters"][0]["items"][0]["source_quotes"] = [
        "不存在于授权资料中的原文"
    ]
    assert _assemble(missing)["error_code"] == "TASK_S02_V3_SOURCE_QUOTE_NOT_FOUND"

    assert (
        _assemble(
            payload,
            evidence=_evidence(duplicate_template=True),
        )["error_code"]
        == "TASK_S02_V3_SOURCE_MATCH_AMBIGUOUS"
    )

    wrong_kind = normalize_evidence_snapshot(
        [
            _raw_evidence(
                evidence_type="planning_template",
                source_text=(
                    "# DEMO/SYNTHETIC 策划报告编制模板\n"
                    + PROJECT_QUOTE
                    + "\n"
                    + "\n".join(
                        f"{rule_id} {description}"
                        for rule_id, description in RULE_TEXTS.items()
                    )
                ),
                source="DEMO/SYNTHETIC 策划报告编制模板.md",
                document_id=TEMPLATE_DOCUMENT_ID,
            )
        ],
        authorization_subject=None,
    )
    assert (
        _assemble(payload, evidence=wrong_kind)["error_code"]
        == "TASK_S02_V3_SOURCE_TYPE_NOT_ALLOWED"
    )


def test_s02_v3_rejects_unauthorized_and_tampered_evidence_snapshot() -> None:
    unauthorized = _evidence()
    unauthorized[0]["authorization_subject"] = "another-user"
    assert (
        _assemble(evidence=unauthorized)["error_code"]
        == "TASK_S02_V3_SOURCE_PERMISSION_MISMATCH"
    )

    tampered = _evidence()
    tampered[0]["source_text"] += " 篡改内容"
    assert (
        _assemble(evidence=tampered)["error_code"]
        == "TASK_S02_V3_SOURCE_HASH_MISMATCH"
    )


class _DemoKnowledgeGateway:
    """Deterministic authorized-snapshot fixture; not a live RAG service."""

    def __init__(self) -> None:
        all_evidence = _evidence()
        self.project_evidence = [
            item
            for item in all_evidence
            if item["evidence_type"] == "project_material"
        ]
        self.template_evidence = [
            item
            for item in all_evidence
            if item["evidence_type"] == "planning_template"
        ]
        self.knowledge_bases = [
            {"id": KB_ID, "name": "DEMO/SYNTHETIC Task v3 测试知识库"}
        ]
        self.template_matches = [
            {"source": self.template_evidence[0]["source"]}
        ]

    async def retrieve_planning_template_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        *,
        top_k: int,
    ) -> dict[str, Any]:
        assert user_id == OWNER
        assert knowledge_base_ids == [KB_ID]
        assert top_k == 12
        return {
            "status": "ready",
            "knowledge_bases": self.knowledge_bases,
            "evidence": self.template_evidence,
            "template_matches": self.template_matches,
            "queries": ["DEMO/SYNTHETIC planning template fixture"],
            "citation_protocol": "Use exact source quotes from this fixture.",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }

    async def retrieve_project_material_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        *,
        project_name: str,
        source_material_ids: list[str],
        top_k: int,
    ) -> dict[str, Any]:
        assert user_id == OWNER
        assert knowledge_base_ids == [KB_ID]
        assert project_name
        assert source_material_ids == [PROJECT_DOCUMENT_ID]
        assert top_k == 8
        return {
            "status": "ready",
            "knowledge_bases": self.knowledge_bases,
            "evidence": self.project_evidence,
            "queries": ["DEMO/SYNTHETIC project fixture"],
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def format_task_evidence(result: dict[str, Any]) -> str:
        return json.dumps(result, ensure_ascii=False, default=str)


class _NoPlanner:
    async def plan(self, **_: Any) -> Any:
        raise AssertionError("Template Task must not invoke Planner.")


def _redacted_legacy_protocol_output() -> str:
    """Redacted shape of the submitted Markdown/protocol S02 failure Run."""

    claims = [
        {
            "claim_id": f"CL-{index:02d}",
            "kind": "fact",
            "anchor": f"CL-{index:02d}",
            "text": "脱敏项目事实，与授权来源关系待校验。",
            "citation_ids": ["C1"],
        }
        for index in range(1, 15)
    ]
    claims.append(
        {
            "claim_id": "CL-15",
            "kind": "unverified",
            "anchor": "CL-15",
            "text": "多个独立事项待核实。",
            "citation_ids": [],
        }
    )
    bindings = [
        {
            "claim_id": f"CL-{index:02d}",
            "citation_id": "C1",
            "evidence_id": "EV-REDACTED-UNTRUSTED",
            "quote": "脱敏原文候选。",
        }
        for index in range(1, 15)
    ]
    unverified_items = [
        {
            "unverified_id": f"UV-{index:02d}",
            "claim_id": "CL-15",
            "reason": f"脱敏待核实事项 {index} 缺少证据。",
            "expected_evidence_types": ["project_material"],
        }
        for index in range(1, 6)
    ]
    body = "\n\n".join(
        [
            "# 脱敏失败 Run 输出（旧 v2 Markdown + 协议形状）",
            "\n".join("脱敏正文 [C1]" for _ in range(47)),
        ]
    )
    return (
        body
        + "\n\n```task-claim-evidence\n"
        + json.dumps(
            {
                "claims": claims,
                "bindings": bindings,
                "unverified_items": unverified_items,
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
                        "evidence_id": "EV-REDACTED-UNTRUSTED",
                        "quote": "脱敏原文候选。",
                    }
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n```"
    )


class _S02V3Executor:
    def __init__(
        self,
        *,
        malformed_final: bool = False,
        legacy_protocol_final: bool = False,
        invalid_s02_policy: bool = False,
        omit_unverified_empty_arrays: bool = False,
        missing_unverified_reason: bool = False,
        kind_schema_conflict_final: bool = False,
        template_version: str = "3.2.0",
    ) -> None:
        self.malformed_final = malformed_final
        self.legacy_protocol_final = legacy_protocol_final
        self.invalid_s02_policy = invalid_s02_policy
        self.omit_unverified_empty_arrays = omit_unverified_empty_arrays
        self.missing_unverified_reason = missing_unverified_reason
        self.kind_schema_conflict_final = kind_schema_conflict_final
        self.template_version = template_version
        self.final_calls = 0
        self.artifact_writes: list[tuple[str, bytes]] = []
        self.artifact_files: dict[str, bytes] = {}

    async def list_tools(self, _context: TaskContext) -> list[TaskToolSchema]:
        return []

    async def execute_node(
        self,
        *,
        node: Any,
        previous_output: str,
        context: Any,
    ) -> StepExecutionResult:
        del previous_output, context
        if node.name != "整理策划输出":
            return StepExecutionResult(
                text=(
                    "上游草稿信息已整理。\n\n"
                    "```task-citations\n{\"citations\":[]}\n```"
                ),
            )
        self.final_calls += 1
        if self.malformed_final:
            raw = '{"title":"DEMO 输出被截断"'
        elif self.legacy_protocol_final:
            raw = _redacted_legacy_protocol_output()
        else:
            payload = (
                _real_run_unverified_shape_payload()
                if self.omit_unverified_empty_arrays
                else (
                    _real_run_kind_conflict_payload()
                    if self.kind_schema_conflict_final
                    else _business_payload(
                        include_template_limitation=(
                            self.template_version == "3.2.0"
                        )
                    )
                )
            )
            if self.invalid_s02_policy:
                b01 = next(
                    item
                    for chapter in payload["chapters"]
                    for item in chapter["items"]
                    if item["kind"] == "rule"
                    and item["rule_ids"] == ["B-01"]
                )
                b01["text"] = (
                    "B-01 项目建筑面积为 1000 平方米，并明确项目名称、编号和委托对象。"
                )
            if self.missing_unverified_reason:
                unverified = next(
                    item
                    for chapter in payload["chapters"]
                    for item in chapter["items"]
                    if item["kind"] == "unverified"
                )
                unverified.pop("reason", None)
            raw = json.dumps(payload, ensure_ascii=False)
        return StepExecutionResult(
            text=raw,
            result={
                "status": "completed",
                "output": raw,
                "reason": "",
                "questions": [],
            },
        )

    async def verify_node(self, **_: Any) -> StepVerdict:
        return StepVerdict(passed=True)

    async def preflight_artifact_writer(self, *, context: Any) -> tuple[bool, str]:
        assert context.user_id == OWNER
        return True, "DEMO/SYNTHETIC fixture Workspace available"

    async def write_artifact(
        self,
        *,
        path: str,
        data: bytes,
        context: Any,
    ) -> int:
        assert context.user_id == OWNER
        self.artifact_writes.append((path, data))
        self.artifact_files[path] = data
        return len(data)

    async def read_artifact(self, *, path: str, context: Any) -> bytes:
        assert context.user_id == OWNER
        return self.artifact_files[path]


async def _run_s02_v3(
    *,
    malformed_final: bool,
    legacy_protocol_final: bool = False,
    invalid_s02_policy: bool = False,
    omit_unverified_empty_arrays: bool = False,
    missing_unverified_reason: bool = False,
    kind_schema_conflict_final: bool = False,
    template_version: str = "3.2.0",
) -> tuple[Any, _S02V3Executor]:
    executor = _S02V3Executor(
        malformed_final=malformed_final,
        legacy_protocol_final=legacy_protocol_final,
        invalid_s02_policy=invalid_s02_policy,
        omit_unverified_empty_arrays=omit_unverified_empty_arrays,
        missing_unverified_reason=missing_unverified_reason,
        kind_schema_conflict_final=kind_schema_conflict_final,
        template_version=template_version,
    )
    service = TaskService(
        TaskStore(),
        agentscope_executor=executor,  # type: ignore[arg-type]
        preview_executor=executor,  # type: ignore[arg-type]
        planner=_NoPlanner(),  # type: ignore[arg-type]
        artifact_writer=executor,  # type: ignore[arg-type]
    )
    gateway = _DemoKnowledgeGateway()
    try:
        task = await TaskTemplateService(
            TemplateRegistry(),
            service,
            compiler=TaskTemplateCompiler(),
        ).instantiate(
            OWNER,
            "s02_project_planning",
            TemplateInstantiateRequest(
                template_version=template_version,
                inputs={
                    "project_name": "DEMO-P7-R3 绿色园区策划项目",
                    "project_brief": "DEMO/SYNTHETIC 项目策划验收夹具。",
                    "professional_fields": ["能源管理"],
                    "source_material_ids": [PROJECT_DOCUMENT_ID],
                },
                source_context=TaskContext(
                    agent_id="demo-s02-agent",
                    session_id="demo-s02-session",
                ),
            ),
        )
        task = await service.update_task(
            OWNER,
            task.id,
            UpdateTaskRequest(knowledge_base_ids=[KB_ID]),
        )
        run = await service.create_run(
            OWNER,
            task.id,
            TaskRunRequest(),
            gateway,  # type: ignore[arg-type]
        )
        assert run is not None

        for _ in range(8):
            active = service._active_runs.get(run.id)
            if active is not None:
                await active
            run = await service.get_run(OWNER, run.id)
            assert run is not None
            if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
                return run, executor
            assert run.status is RunStatus.AWAITING_INPUT
            assert run.pending_input is not None
            answers: dict[str, str] = {}
            for question in run.pending_input.questions:
                if question.key == "project_name":
                    answers[question.key] = "DEMO-P7-R3 绿色园区策划项目"
                elif question.key == "project_brief":
                    answers[question.key] = "DEMO/SYNTHETIC 项目策划验收夹具。"
                elif question.key == "source_material_ids":
                    answers[question.key] = json.dumps(
                        [PROJECT_DOCUMENT_ID],
                        ensure_ascii=False,
                    )
                elif question.required:
                    answers[question.key] = "DEMO/SYNTHETIC 测试输入"
            if not any(answers.values()):
                answers["planning_feedback"] = "无补充"
            await service.answer_run(
                OWNER,
                run.id,
                TaskInputReply(
                    request_id=run.pending_input.id,
                    answers=answers,
                ),
                gateway,  # type: ignore[arg-type]
            )
        raise AssertionError("S02 v3 test Run did not reach a terminal state.")
    finally:
        await service.shutdown()


def test_s02_v3_taskservice_sop_verifier_and_artifact_gate() -> None:
    run, executor = asyncio.run(_run_s02_v3(malformed_final=False))

    assert run.status is RunStatus.SUCCEEDED
    assert run.source_template_version == "3.2.0"
    assert len(run.artifacts) == 1
    assert run.artifacts[0].run_id == run.id
    assert run.artifacts[0].task_id == run.task_id
    assert len(executor.artifact_writes) == 1
    assert executor.final_calls == 1
    assert executor.artifact_writes[0][1].startswith(b"PK")
    assert run.artifacts[0].format.value == "docx"
    final_node = run.node_runs[-1]
    assert final_node.metadata["s02_v3_structured_output"]["passed"] is True
    assert final_node.metadata["evidence_ledger"]["passed"] is True
    assert final_node.metadata["output_contract_validation"]["status"] == "passed"
    assert isinstance(final_node.result, dict)
    assert len(final_node.result["chapters"]) == 8
    assert final_node.metadata["s02_v3_structured_output"][
        "normalization_changes"
    ] == []
    assert final_node.metadata["s02_v3_structured_output"][
        "raw_model_output"
    ]


def test_real_run_missing_empty_arrays_passes_full_run_and_writes_docx() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            omit_unverified_empty_arrays=True,
        )
    )

    assert run.status is RunStatus.SUCCEEDED
    assert run.source_template_version == "3.2.0"
    assert len(run.artifacts) == 1
    assert len(executor.artifact_writes) == 1
    final_node = run.node_runs[-1]
    output_metadata = final_node.metadata["s02_v3_structured_output"]
    assert output_metadata["passed"] is True
    assert len(output_metadata["normalization_changes"]) == 16
    original_payload = json.loads(output_metadata["raw_model_output"])
    assert sum(
        len(chapter["items"]) for chapter in original_payload["chapters"]
    ) == 52
    assert all(
        "source_quotes" not in item and "rule_ids" not in item
        for chapter in original_payload["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )
    assert sum(
        1
        for chapter in final_node.result["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    ) == 8
    assert all(
        item["source_quotes"] == [] and item["rule_ids"] == []
        for chapter in final_node.result["chapters"]
        for item in chapter["items"]
        if item["kind"] == "unverified"
    )


def test_s02_v3_required_business_field_still_fails_and_blocks_docx() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            omit_unverified_empty_arrays=True,
            missing_unverified_reason=True,
        )
    )

    assert run.status is RunStatus.FAILED
    assert run.artifacts == []
    assert executor.artifact_writes == []
    final_node = run.node_runs[-1]
    structured = final_node.metadata["s02_v3_structured_output"]
    assert structured["passed"] is False
    assert structured["validation_stage"] == "json_schema"
    assert len(structured["normalization_changes"]) == 16
    assert any(
        item["field"] == "reason"
        and item["error_code"] == "missing_required_field"
        for item in structured["diagnostics"]
    )


def test_s02_v3_0_historical_version_remains_executable() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            template_version="3.0.0",
        )
    )

    assert run.status is RunStatus.SUCCEEDED
    assert run.source_template_version == "3.0.0"
    assert len(run.artifacts) == 1
    assert len(executor.artifact_writes) == 1


def test_s02_v31_historical_version_remains_executable() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            template_version="3.1.0",
        )
    )

    assert run.status is RunStatus.SUCCEEDED
    assert run.source_template_version == "3.1.0"
    assert len(run.artifacts) == 1
    assert len(executor.artifact_writes) == 1


def test_real_run_kind_conflict_fails_service_run_and_blocks_docx() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            kind_schema_conflict_final=True,
        )
    )

    assert run.status is RunStatus.FAILED
    assert run.source_template_version == "3.2.0"
    assert run.artifacts == []
    assert executor.artifact_writes == []
    final_node = run.node_runs[-1]
    structured = final_node.metadata["s02_v3_structured_output"]
    assert structured["error_code"] == "TASK_S02_V3_SCHEMA_INVALID"
    assert structured["validation_stage"] == "json_schema"
    pending = [
        item
        for item in structured["diagnostics"]
        if item["error_code"] == "field_not_allowed_for_kind"
    ]
    assert len(pending) == 31 * 3
    assert "诊断：" in final_node.error


def test_s02_v3_invalid_inner_json_stops_before_artifact_writer() -> None:
    run, executor = asyncio.run(_run_s02_v3(malformed_final=True))

    assert run.status is RunStatus.FAILED
    assert run.artifacts == []
    assert executor.artifact_writes == []
    assert executor.final_calls == 1
    final_node = run.node_runs[-1]
    assert final_node.metadata["s02_v3_structured_output"]["passed"] is False
    assert (
        final_node.metadata["s02_v3_structured_output"]["error_code"]
        == "TASK_S02_V3_JSON_INVALID"
    )


def test_s02_v3_rejects_redacted_legacy_run_protocol_before_artifact_writer() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            legacy_protocol_final=True,
        )
    )

    assert run.status is RunStatus.FAILED
    assert run.artifacts == []
    assert executor.artifact_writes == []
    assert executor.final_calls == 1
    final_node = run.node_runs[-1]
    assert final_node.metadata["s02_v3_structured_output"]["passed"] is False
    assert (
        final_node.metadata["s02_v3_structured_output"]["error_code"]
        == "TASK_S02_V3_JSON_INVALID"
    )


def test_s02_v3_policy_failure_does_not_retry_or_create_artifact() -> None:
    run, executor = asyncio.run(
        _run_s02_v3(
            malformed_final=False,
            invalid_s02_policy=True,
        )
    )

    assert run.status is RunStatus.FAILED
    assert executor.final_calls == 1
    assert run.artifacts == []
    assert executor.artifact_writes == []
