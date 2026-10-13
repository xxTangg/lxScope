# -*- coding: utf-8 -*-
"""Deterministic S02 v3 business-output and evidence-protocol adapter."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from jsonschema import Draft202012Validator

from ._citation_verifier import DuplicateJSONKeyError, unique_json_object
from ._evidence_contract import (
    _lexically_associated,
    normalize_evidence_snapshot,
)
from ._output_validation import validate_structured_result
from ._scenario_evidence_policies import get_scenario_evidence_policy


_MAX_OUTPUT_BYTES = 64_000
_MAX_REPORT_BYTES = 100_000
_MAX_CLAIMS = 128
_MAX_CITATIONS = 128
S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS = frozenset(
    {"3.0.0", "3.1.0", "3.2.0", "4.0.0", "4.1.0"}
)
_S02_V4_TEMPLATE_VERSIONS = frozenset({"4.0.0", "4.1.0"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RULE_MARKER = re.compile(r"(?<![A-Za-z0-9])B-(0[1-9]|1[0-5])(?!\d)")
_TEMPLATE_RULE_ENTRY = re.compile(
    r"^\s*(?:[-*+•]\s*)?(?:\|\s*)?B\s*[-‐‑‒–—~～]\s*"
    r"(0[1-9]|1[0-5])(?:\s*[：:、.．)）]\s*|\s+)"
    r"(?P<description>\S[^\r\n]*?)\s*(?:\|\s*)?$",
    re.IGNORECASE,
)
_CITATION_MARKER = re.compile(r"\[C[1-9][0-9]{0,2}\]")
_RESERVED_CONTENT = re.compile(
    r"<!--\s*task-claim:|```\s*task-(?:claim-evidence|citations)|"
    r"task-citations|task-claim-evidence|task-evidence-claims",
    re.IGNORECASE,
)

_CHAPTER_TITLES = {
    1: "第一章 项目概况",
    2: "第二章 资料清单与可信度",
    3: "第三章 能源基线与计量口径",
    4: "第四章 设备台账现状",
    5: "第五章 分析方法与工作计划",
    6: "第六章 方案建议",
    7: "第七章 预算与进度边界",
    8: "第八章 风险、待核实事项与交付清单",
}

_PENDING_LABELS = {
    "pending_verification": "待核实",
    "pending_supplement": "待补充",
    "pending_confirmation": "待确认",
}

_KIND_LABELS = {
    "analysis": "【分析】",
    "recommendation": "【建议】",
    "template_limitation": "【模板适用性限制】",
    "unverified": "",
}


def assemble_s02_v3_output(
    raw_output: str,
    schema: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str,
    template_version: str = "3.0.0",
    delivery_mode: str = "strict_verified",
) -> dict[str, Any]:
    """Validate inner JSON, then create server-owned Claim/Evidence data.

    This function does not prove semantic entailment. It only resolves exact
    quote candidates to the current authorized TaskRun snapshot and emits the
    existing generic claim/citation protocol for its established validators.
    """

    if not isinstance(raw_output, str):
        return _failure(
            "TASK_S02_V3_JSON_INVALID",
            "S02 v3 输出必须是 JSON 文本。",
        )
    if delivery_mode not in {"strict_verified", "demo_draft"}:
        return _failure(
            "TASK_S02_V3_DELIVERY_MODE_INVALID",
            "S02 交付模式无效。",
            validation_stage="delivery_mode",
        )
    if delivery_mode == "demo_draft" and template_version not in _S02_V4_TEMPLATE_VERSIONS:
        return _failure(
            "TASK_S02_DEMO_MODE_UNSUPPORTED",
            "演示草稿模式仅适用于 S02 v4.0.0。",
            validation_stage="delivery_mode",
        )
    if len(raw_output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        return _failure(
            "TASK_S02_V3_JSON_TOO_LARGE",
            "S02 v3 结构化输出超过 64 KB 限制。",
        )
    try:
        business_data = json.loads(
            raw_output,
            object_pairs_hook=unique_json_object,
        )
    except DuplicateJSONKeyError:
        return _failure(
            "TASK_S02_V3_JSON_DUPLICATE_KEY",
            "S02 v3 结构化 JSON 包含重复字段。",
            raw_model_output=raw_output,
        )
    except json.JSONDecodeError as error:
        return _failure(
            "TASK_S02_V3_JSON_INVALID",
            "S02 v3 JSON 语法错误位于第 "
            f"{error.lineno} 行第 {error.colno} 列。",
            raw_model_output=raw_output,
        )
    except (RecursionError, ValueError):
        return _failure(
            "TASK_S02_V3_JSON_INVALID",
            "S02 v3 JSON 超出解析深度或包含无效值。",
            raw_model_output=raw_output,
        )

    normalization_changes = _normalize_unverified_empty_arrays(
        business_data
    )

    def fail(
        error_code: str,
        message: str,
        *,
        issues: list[str] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
        validation_stage: str | None = None,
    ) -> dict[str, Any]:
        return _failure(
            error_code,
            message,
            issues=issues,
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            normalization_changes=normalization_changes,
            diagnostics=diagnostics,
            validation_stage=validation_stage,
        )

    validation = validate_structured_result(schema, business_data)
    if not validation.passed:
        schema_diagnostics = _detailed_schema_diagnostics(
            schema,
            business_data,
            fallback=list(validation.issues),
        )
        schema_issues = [item["message"] for item in schema_diagnostics]
        return fail(
            "TASK_S02_V3_SCHEMA_INVALID",
            "S02 v3 结构化结果未通过 JSON Schema 校验。",
            issues=schema_issues,
            diagnostics=schema_diagnostics,
            validation_stage="json_schema",
        )
    business_data = validation.value

    semantic_issues = _validate_business_shape(
        business_data,
        template_version=template_version,
        delivery_mode=delivery_mode,
    )
    if semantic_issues:
        business_diagnostics = _business_shape_diagnostics(
            business_data,
            template_version=template_version,
        )
        return fail(
            "TASK_S02_V3_BUSINESS_SHAPE_INVALID",
            "S02 v3 业务内容与策划输出约束不一致。",
            issues=semantic_issues,
            diagnostics=business_diagnostics,
            validation_stage="s02_business",
        )

    policy = get_scenario_evidence_policy(
        "s02_project_planning",
        template_version,
    )
    if policy is None:  # pragma: no cover - registry policy is static
        return fail(
            "TASK_S02_V3_POLICY_UNAVAILABLE",
            "S02 v3 EvidencePolicy 未配置。",
            validation_stage="evidence_policy",
        )

    normalized_evidence = normalize_evidence_snapshot(
        evidence,
        authorization_subject=None,
    )
    citation_cache: dict[tuple[str, str], str] = {}
    citations_by_id: dict[str, dict[str, str]] = {}
    claims_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    bindings: list[dict[str, str]] = []
    unverified_items: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    body: list[str] = [
        f"# {business_data['title']}",
        "",
        (
            "> DEMO/SYNTHETIC · 演示草稿 · 未完成完整证据核验。"
            "来源语义支持仍需人工复核。"
            if delivery_mode == "demo_draft"
            else "> 文档状态：草稿；未签发。语义支持仍需人工复核。"
        ),
        "",
    ]

    claim_number = 0
    occurrence_number = 0
    unverified_number = 0
    for chapter in business_data["chapters"]:
        chapter_number = chapter["number"]
        body.extend([f"## {_CHAPTER_TITLES[chapter_number]}", ""])
        for item_index, source_item in enumerate(chapter["items"]):
            item = dict(source_item)
            kind = item["kind"]
            resolved_quotes: list[tuple[str, str]] = []
            unresolved_reason: str | None = None
            unresolved_code: str | None = None
            unresolved_message: str | None = None
            for quote in item["source_quotes"]:
                resolved = _resolve_quote(
                    quote,
                    kind,
                    normalized_evidence,
                    policy.allowed_evidence_types_by_claim_kind.get(
                        kind, []
                    ),
                    authorization_subject=authorization_subject,
                )
                if isinstance(resolved, dict):
                    error_code = str(resolved.get("error_code", ""))
                    if (
                        delivery_mode == "demo_draft"
                        and template_version == "4.0.0"
                        and error_code
                        in {
                            "TASK_S02_V3_SOURCE_QUOTE_NOT_FOUND",
                            "TASK_S02_V3_SOURCE_TYPE_NOT_ALLOWED",
                            "TASK_S02_V3_SOURCE_MATCH_AMBIGUOUS",
                        }
                    ):
                        unresolved_code = error_code
                        unresolved_message = str(
                            resolved.get("error")
                            or "来源候选无法唯一匹配。"
                        )
                        unresolved_reason = (
                            "本次运行未能从授权来源唯一确认该内容："
                            + unresolved_message
                        )
                        break
                    return _with_output_trace(
                        resolved,
                        raw_output,
                        business_data,
                        normalization_changes,
                        validation_stage="evidence_resolution",
                    )
                resolved_quotes.append(resolved)

            if (
                not item["source_quotes"]
                and kind in policy.require_citation_claim_kinds
                and delivery_mode == "demo_draft"
                and template_version == "4.0.0"
            ):
                unresolved_code = "TASK_S02_V4_SOURCE_CANDIDATE_MISSING"
                unresolved_message = "Agent 未提供可核验的原文候选。"
                unresolved_reason = (
                    "本次运行没有为该来源事实提供可匹配的授权原文候选。"
                )

            if unresolved_reason is not None:
                if kind in policy.require_citation_claim_kinds:
                    expected_types = list(
                        policy.allowed_evidence_types_by_claim_kind.get(
                            kind, []
                        )
                    )
                    item = {
                        "kind": "unverified",
                        "text": item["text"],
                        "source_quotes": [],
                        "rule_ids": [],
                        "unverified_status": "pending_verification",
                        "reason": unresolved_reason,
                        "expected_evidence_types": expected_types
                        or ["project_material"],
                    }
                    chapter["items"][item_index] = item
                    kind = "unverified"
                    resolved_quotes = []
                    warnings.append(
                        {
                            "warning_code": unresolved_code,
                            "chapter_number": chapter_number,
                            "item_index": item_index,
                            "kind": source_item["kind"],
                            "message": unresolved_message,
                            "disposition": "marked_pending_verification",
                        }
                    )
                else:
                    resolved_quotes = []
                    warnings.append(
                        {
                            "warning_code": unresolved_code,
                            "chapter_number": chapter_number,
                            "item_index": item_index,
                            "kind": kind,
                            "message": unresolved_message,
                            "disposition": "source_candidate_not_verified",
                        }
                    )

            visible_text = _visible_text(item)

            citation_ids: list[str] = []
            for evidence_id, quote in resolved_quotes:
                key = (evidence_id, quote)
                citation_id = citation_cache.get(key)
                if citation_id is None:
                    if len(citations_by_id) >= _MAX_CITATIONS:
                        return fail(
                            "TASK_S02_V3_CITATION_LIMIT",
                            "S02 v3 引用数量超过 Task 协议上限。",
                            validation_stage="protocol_assembly",
                        )
                    citation_id = f"C{len(citations_by_id) + 1}"
                    citation_cache[key] = citation_id
                    citations_by_id[citation_id] = {
                        "citation_id": citation_id,
                        "evidence_id": evidence_id,
                        "quote": quote,
                    }
                if citation_id not in citation_ids:
                    citation_ids.append(citation_id)

            if (
                kind in policy.require_citation_claim_kinds
                and not citation_ids
            ):
                return fail(
                    "TASK_S02_V3_REQUIRED_SOURCE_MISSING",
                    "事实、模板规则和历史案例必须提供可匹配的授权原文候选。",
                    validation_stage="evidence_policy",
                )

            group_key = _claim_group_key(item, visible_text)
            claim = claims_by_key.get(group_key)
            if claim is None:
                claim_number += 1
                claim_id = f"CL-{claim_number:04d}"
                claim = {
                    "claim_id": claim_id,
                    "kind": kind,
                    "anchor": claim_id,
                    "text": visible_text,
                    "citation_ids": [],
                    "occurrences": [],
                }
                claims_by_key[group_key] = claim
                claims.append(claim)

            occurrence_number += 1
            occurrence_id = f"O-{occurrence_number:04d}"
            occurrence = {
                "occurrence_id": occurrence_id,
                "text": visible_text,
                "citation_ids": citation_ids,
            }
            claim["occurrences"].append(occurrence)
            for citation_id in citation_ids:
                if citation_id not in claim["citation_ids"]:
                    claim["citation_ids"].append(citation_id)
            body_line = visible_text
            if citation_ids:
                body_line += " " + " ".join(
                    f"[{citation_id}]" for citation_id in citation_ids
                )
            body_line += (
                f"<!-- task-claim:{claim['claim_id']}:{occurrence_id} -->"
            )
            body.append(body_line)
            body.append("")

            for evidence_id, quote in resolved_quotes:
                citation_id = citation_cache[(evidence_id, quote)]
                bindings.append(
                    {
                        "claim_id": claim["claim_id"],
                        "occurrence_id": occurrence_id,
                        "citation_id": citation_id,
                        "evidence_id": evidence_id,
                        "quote": quote,
                    }
                )

            if kind == "unverified":
                unverified_number += 1
                unverified_items.append(
                    {
                        "unverified_id": f"UV-{unverified_number:04d}",
                        "claim_id": claim["claim_id"],
                        "occurrence_id": occurrence_id,
                        "status": item["unverified_status"],
                        "reason": item["reason"],
                        "expected_evidence_types": item[
                            "expected_evidence_types"
                        ],
                    }
                )

    if len(claims) > _MAX_CLAIMS:
        return fail(
            "TASK_S02_V3_CLAIM_LIMIT",
            "S02 v3 声明数量超过 Task 协议上限。",
            validation_stage="protocol_assembly",
        )

    protocol = {
        "claims": claims,
        "bindings": bindings,
        "unverified_items": unverified_items,
    }
    citations_protocol = {"citations": list(citations_by_id.values())}
    try:
        encoded_claims = json.dumps(
            protocol,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded_citations = json.dumps(
            citations_protocol,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):  # pragma: no cover
        return fail(
            "TASK_S02_V3_PROTOCOL_ASSEMBLY_FAILED",
            "Task 无法序列化 S02 v3 证据协议。",
            validation_stage="protocol_assembly",
        )

    document = (
        "\n".join(body).rstrip()
        + "\n\n```task-claim-evidence\n"
        + encoded_claims
        + "\n```\n\n```task-citations\n"
        + encoded_citations
        + "\n```"
    )
    if len(document.encode("utf-8")) > _MAX_REPORT_BYTES:
        return fail(
            "TASK_S02_V3_REPORT_TOO_LARGE",
            "Task 组装后的 S02 v3 报告超过 100 KB 限制。",
            validation_stage="report_size",
        )
    return {
        "passed": True,
        "document": document,
        "business_result": business_data,
        "raw_model_output": raw_output,
        "normalization_changes": normalization_changes,
        "claim_count": len(claims),
        "occurrence_count": occurrence_number,
        "binding_count": len(bindings),
        "unverified_count": len(unverified_items),
        "citation_count": len(citations_by_id),
        "warnings": warnings,
        "delivery_report": {
            "mode": delivery_mode,
            "status": (
                "draft_unverified"
                if delivery_mode == "demo_draft"
                else "strict_verified"
            ),
            "warnings": warnings,
            "unverified_items": unverified_items,
            "semantic_support": "requires_human_review",
            "human_review_required": True,
        },
    }


def assemble_s02_v4_output(
    raw_output: str,
    schema: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str,
    delivery_mode: str = "strict_verified",
    template_version: str = "4.0.0",
) -> dict[str, Any]:
    """Validate the small S02 v4 business shape and reuse the v3 protocol path.

    Agent output contains only chapter content and literal source candidates.
    The existing Task-owned assembler resolves those candidates against the
    current Run snapshot and creates the established Claim/Occurrence,
    Citation, Binding, and Unverified records.
    """

    if not isinstance(raw_output, str):
        return _failure(
            "TASK_S02_V4_JSON_INVALID",
            "S02 v4 输出必须是 JSON 文本。",
            validation_stage="json_parse",
        )
    if delivery_mode not in {"strict_verified", "demo_draft"}:
        return _failure(
            "TASK_S02_V4_DELIVERY_MODE_INVALID",
            "S02 v4 交付模式无效。",
            validation_stage="delivery_mode",
        )
    if template_version not in _S02_V4_TEMPLATE_VERSIONS:
        return _failure(
            "TASK_S02_V4_TEMPLATE_VERSION_UNSUPPORTED",
            "S02 v4 结构化适配器不支持该模板版本。",
            validation_stage="template_version",
        )
    if len(raw_output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        return _failure(
            "TASK_S02_V4_JSON_TOO_LARGE",
            "S02 v4 结构化输出超过 64 KB 限制。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )
    try:
        business_data = json.loads(
            raw_output,
            object_pairs_hook=unique_json_object,
        )
    except DuplicateJSONKeyError:
        return _failure(
            "TASK_S02_V4_JSON_DUPLICATE_KEY",
            "S02 v4 结构化 JSON 包含重复字段。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )
    except json.JSONDecodeError as error:
        return _failure(
            "TASK_S02_V4_JSON_INVALID",
            "S02 v4 JSON 语法错误位于第 "
            f"{error.lineno} 行第 {error.colno} 列。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )
    except (RecursionError, ValueError):
        return _failure(
            "TASK_S02_V4_JSON_INVALID",
            "S02 v4 JSON 超出解析深度或包含无效值。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )

    validation = validate_structured_result(schema, business_data)
    if not validation.passed:
        diagnostics = _detailed_schema_diagnostics(
            schema,
            business_data,
            fallback=list(validation.issues),
        )
        return _failure(
            "TASK_S02_V4_SCHEMA_INVALID",
            "S02 v4 结构化结果未通过 JSON Schema 校验。",
            issues=[item["message"] for item in diagnostics],
            diagnostics=diagnostics,
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            validation_stage="json_schema",
        )
    business_data = validation.value
    business_issues = _validate_s02_v4_business_shape(
        business_data,
        template_version=template_version,
    )
    if business_issues:
        diagnostics = _s02_v4_business_diagnostics(business_data)
        return _failure(
            "TASK_S02_V4_BUSINESS_SHAPE_INVALID",
            "S02 v4 业务内容与报告结构约束不一致。",
            issues=business_issues,
            diagnostics=diagnostics,
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            validation_stage="s02_v4_business",
        )

    normalized_data = {
        "title": business_data["title"],
        "chapters": [],
    }
    for chapter in business_data["chapters"]:
        normalized_chapter = {
            "number": chapter["number"],
            "items": [],
        }
        for item in chapter["items"]:
            normalized_item: dict[str, Any] = {
                "kind": item["kind"],
                "text": item["text"],
                "source_quotes": list(item["source_candidates"]),
                "rule_ids": [],
            }
            if item["kind"] == "unverified":
                normalized_item.update(
                    {
                        "unverified_status": item["pending_status"],
                        "reason": item["reason"],
                        "expected_evidence_types": list(
                            item["expected_evidence_types"]
                        ),
                    }
                )
            normalized_chapter["items"].append(normalized_item)
        normalized_data["chapters"].append(normalized_chapter)

    # The v4 Schema above is the authoritative contract. This internal pass
    # reuses the existing deterministic protocol builder and evidence policy.
    assembled = assemble_s02_v3_output(
        json.dumps(normalized_data, ensure_ascii=False, separators=(",", ":")),
        {"type": "object"},
        evidence,
        authorization_subject=authorization_subject,
        template_version=template_version,
        delivery_mode=delivery_mode,
    )
    if not assembled.get("passed"):
        assembled["raw_model_output"] = raw_output
        assembled["normalized_business_result"] = business_data
        assembled["business_result"] = business_data
        assembled["validation_stage"] = assembled.get(
            "validation_stage", "s02_v4_protocol_assembly"
        )
        return assembled

    normalized_evidence = normalize_evidence_snapshot(
        evidence,
        authorization_subject=None,
    )
    template_rule_status = _s02_v4_template_rule_status(
        normalized_evidence,
        authorization_subject=authorization_subject,
    )
    missing_template_rules = [
        item["rule_id"]
        for item in template_rule_status
        if not item["source_available"]
    ]
    if missing_template_rules and delivery_mode == "strict_verified":
        return _failure(
            "TASK_S02_V4_TEMPLATE_RULE_SOURCE_INCOMPLETE",
            "授权策划模板证据快照未覆盖全部 B-01 至 B-15 写作要求。",
            issues=["缺少模板来源条目：" + ", ".join(missing_template_rules)],
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            validation_stage="template_rule_coverage",
        )

    warnings = list(assembled.get("warnings", []))
    if missing_template_rules:
        warnings.append(
            {
                "warning_code": "template_rule_source_incomplete",
                "message": (
                    "授权模板证据快照未覆盖："
                    + ", ".join(missing_template_rules)
                    + "；报告未将其标记为已应用。"
                ),
                "disposition": "requires_human_review",
            }
        )
    report = dict(assembled.get("delivery_report", {}))
    report.update(
        {
            "template_rule_requirements": template_rule_status,
            "chapter_count": len(business_data["chapters"]),
            "completed_chapters": [
                f"第{chapter['number']}章"
                for chapter in business_data["chapters"]
            ],
            "warnings": warnings,
            "status": (
                "draft_unverified"
                if delivery_mode == "demo_draft"
                else "strict_verified"
            ),
            "mode": delivery_mode,
            "semantic_support": "requires_human_review",
            "human_review_required": True,
        }
    )
    return {
        **assembled,
        "raw_model_output": raw_output,
        "business_result": assembled["business_result"],
        "normalized_business_result": assembled["business_result"],
        "warnings": warnings,
        "delivery_report": report,
    }


def assemble_s02_v4_1_output(
    raw_output: str,
    schema: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str,
    delivery_mode: str = "strict_verified",
) -> dict[str, Any]:
    """Assemble S02 v4.1 quote-to-local-claim mappings deterministically.

    v4.1 makes the model identify the exact text span each source quote is
    intended to support. The adapter splits compound items into those literal
    spans before invoking the existing Claim/Evidence assembler and ledger.
    It never creates or changes an evidence source, quote, identity, or hash.
    """

    if not isinstance(raw_output, str):
        return _failure(
            "TASK_S02_V4_JSON_INVALID",
            "S02 v4.1 输出必须是 JSON 文本。",
            validation_stage="json_parse",
        )
    if delivery_mode not in {"strict_verified", "demo_draft"}:
        return _failure(
            "TASK_S02_V4_DELIVERY_MODE_INVALID",
            "S02 v4.1 交付模式无效。",
            validation_stage="delivery_mode",
        )
    if len(raw_output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        return _failure(
            "TASK_S02_V4_JSON_TOO_LARGE",
            "S02 v4.1 结构化输出超过 64 KB 限制。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )
    try:
        business_data = json.loads(
            raw_output,
            object_pairs_hook=unique_json_object,
        )
    except DuplicateJSONKeyError:
        return _failure(
            "TASK_S02_V4_JSON_DUPLICATE_KEY",
            "S02 v4.1 结构化 JSON 包含重复字段。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )
    except json.JSONDecodeError as error:
        return _failure(
            "TASK_S02_V4_JSON_INVALID",
            f"S02 v4.1 JSON 语法错误位于第 {error.lineno} 行第 {error.colno} 列。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )
    except (RecursionError, ValueError):
        return _failure(
            "TASK_S02_V4_JSON_INVALID",
            "S02 v4.1 JSON 超出解析深度或包含无效值。",
            raw_model_output=raw_output,
            validation_stage="json_parse",
        )

    validation = validate_structured_result(schema, business_data)
    if not validation.passed:
        diagnostics = _detailed_schema_diagnostics(
            schema,
            business_data,
            fallback=list(validation.issues),
        )
        return _failure(
            "TASK_S02_V4_SCHEMA_INVALID",
            "S02 v4.1 结构化结果未通过 JSON Schema 校验。",
            issues=[item["message"] for item in diagnostics],
            diagnostics=diagnostics,
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            validation_stage="json_schema",
        )
    business_data = validation.value
    shape_issues = _validate_s02_v4_business_shape(
        business_data,
        template_version="4.1.0",
    )
    if shape_issues:
        diagnostics = _s02_v4_business_diagnostics(
            business_data,
            template_version="4.1.0",
        )
        return _failure(
            "TASK_S02_V4_BUSINESS_SHAPE_INVALID",
            "S02 v4.1 业务内容与报告结构约束不一致。",
            issues=shape_issues,
            diagnostics=diagnostics,
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            validation_stage="s02_v4_business",
        )

    policy = get_scenario_evidence_policy(
        "s02_project_planning", "4.1.0"
    )
    if policy is None:  # pragma: no cover - static policy catalog
        return _failure(
            "TASK_S02_V4_POLICY_UNAVAILABLE",
            "S02 v4.1 EvidencePolicy 未配置。",
            raw_model_output=raw_output,
            validation_stage="evidence_policy",
        )
    normalized_evidence = normalize_evidence_snapshot(
        evidence,
        authorization_subject=None,
    )
    required_kinds = set(policy.require_citation_claim_kinds)
    normalized_data: dict[str, Any] = {
        "title": business_data["title"],
        "chapters": [],
    }
    warnings: list[dict[str, Any]] = []
    normalization_changes: list[dict[str, Any]] = []

    def pending_item(
        source_item: dict[str, Any],
        text: str,
        reason: str,
        expected_types: list[str],
    ) -> dict[str, Any]:
        return {
            "kind": "unverified",
            "text": text,
            "source_quotes": [],
            "rule_ids": [],
            "unverified_status": "pending_verification",
            "reason": reason,
            "expected_evidence_types": expected_types or ["project_material"],
        }

    def add_warning(
        *,
        code: str,
        chapter_number: int,
        item_index: int,
        kind: str,
        text: str,
        message: str,
        disposition: str,
    ) -> None:
        warnings.append(
            {
                "warning_code": code,
                "chapter_number": chapter_number,
                "item_index": item_index,
                "kind": kind,
                "support_text": text,
                "message": message,
                "disposition": disposition,
            }
        )

    for chapter in business_data["chapters"]:
        normalized_chapter = {"number": chapter["number"], "items": []}
        normalized_data["chapters"].append(normalized_chapter)
        for item_index, source_item in enumerate(chapter["items"]):
            kind = source_item["kind"]
            if kind == "unverified":
                normalized_chapter["items"].append(
                    {
                        "kind": kind,
                        "text": source_item["text"],
                        "source_quotes": [],
                        "rule_ids": [],
                        "unverified_status": source_item["pending_status"],
                        "reason": source_item["reason"],
                        "expected_evidence_types": list(
                            source_item["expected_evidence_types"]
                        ),
                    }
                )
                continue

            candidates = source_item["source_candidates"]
            allowed_types = policy.allowed_evidence_types_by_claim_kind.get(
                kind, []
            )
            if not candidates:
                if kind in required_kinds and delivery_mode == "strict_verified":
                    return _s02_v41_failure(
                        "TASK_S02_V4_SOURCE_CANDIDATE_MISSING",
                        "來源必引內容缺少 quote-to-claim 原文候選。",
                        raw_output,
                        business_data,
                        normalization_changes,
                        chapter_number=chapter["number"],
                        item_index=item_index,
                        kind=kind,
                        field="source_candidates",
                        validation_stage="quote_resolution",
                    )
                if kind in required_kinds:
                    reason = "本次 Run 沒有可映射到該來源事實的授權原文候選。"
                    normalized_chapter["items"].append(
                        pending_item(
                            source_item,
                            source_item["text"],
                            reason,
                            list(allowed_types),
                        )
                    )
                    add_warning(
                        code="source_candidate_missing",
                        chapter_number=chapter["number"],
                        item_index=item_index,
                        kind=kind,
                        text=source_item["text"],
                        message=reason,
                        disposition="marked_pending_verification",
                    )
                    normalization_changes.append(
                        {
                            "path": f"chapters[{chapter['number'] - 1}].items[{item_index}]",
                            "change": "required_source_missing_to_unverified",
                        }
                    )
                else:
                    normalized_chapter["items"].append(
                        {
                            "kind": kind,
                            "text": source_item["text"],
                            "source_quotes": [],
                            "rule_ids": [],
                        }
                    )
                continue

            groups: dict[str, list[str]] = {}
            for candidate in candidates:
                groups.setdefault(candidate["supports"], []).append(
                    candidate["quote"]
                )
            ranges = _s02_v41_support_ranges(source_item["text"], groups)
            if isinstance(ranges, str):
                if delivery_mode == "strict_verified":
                    return _s02_v41_failure(
                        "TASK_S02_V4_SUPPORT_MAPPING_INVALID",
                        "来源候选的 supports 无法无歧义覆盖正文声明。",
                        raw_output,
                        business_data,
                        normalization_changes,
                        chapter_number=chapter["number"],
                        item_index=item_index,
                        kind=kind,
                        field="source_candidates.supports",
                        validation_stage="claim_occurrence_mapping",
                        detail=ranges,
                    )
                if kind in required_kinds:
                    reason = (
                        "模型提供的局部声明映射不能无歧义覆盖正文，"
                        "该条完整声明已保留为待核验内容。"
                    )
                    normalized_chapter["items"].append(
                        pending_item(
                            source_item,
                            source_item["text"],
                            reason,
                            list(allowed_types),
                        )
                    )
                    disposition = "whole_claim_marked_pending_verification"
                else:
                    normalized_chapter["items"].append(
                        {
                            "kind": kind,
                            "text": source_item["text"],
                            "source_quotes": [],
                            "rule_ids": [],
                        }
                    )
                    disposition = "candidate_mapping_discarded"
                add_warning(
                    code="support_mapping_invalid",
                    chapter_number=chapter["number"],
                    item_index=item_index,
                    kind=kind,
                    text=source_item["text"],
                    message=ranges,
                    disposition=disposition,
                )
                normalization_changes.append(
                    {
                        "path": f"chapters[{chapter['number'] - 1}].items[{item_index}]",
                        "change": disposition,
                    }
                )
                continue

            supports_in_order = [support for _, _, support in ranges]
            if len(supports_in_order) > 1:
                normalization_changes.append(
                    {
                        "path": f"chapters[{chapter['number'] - 1}].items[{item_index}]",
                        "change": "split_compound_claim_by_exact_support_spans",
                    }
                )
            for support in supports_in_order:
                resolved_quotes: list[str] = []
                for quote in groups[support]:
                    resolved = _resolve_quote(
                        quote,
                        kind,
                        normalized_evidence,
                        allowed_types,
                        authorization_subject=authorization_subject,
                    )
                    if isinstance(resolved, dict):
                        error_code = str(resolved.get("error_code", ""))
                        if (
                            delivery_mode == "demo_draft"
                            and error_code
                            in {
                                "TASK_S02_V3_SOURCE_QUOTE_NOT_FOUND",
                                "TASK_S02_V3_SOURCE_TYPE_NOT_ALLOWED",
                                "TASK_S02_V3_SOURCE_MATCH_AMBIGUOUS",
                            }
                        ):
                            add_warning(
                                code=error_code,
                                chapter_number=chapter["number"],
                                item_index=item_index,
                                kind=kind,
                                text=support,
                                message=str(resolved.get("error") or error_code),
                                disposition="source_candidate_not_verified",
                            )
                            continue
                        return _s02_v41_failure(
                            error_code or "TASK_S02_V4_SOURCE_RESOLUTION_FAILED",
                            str(resolved.get("error") or "来源身份或授权校验失败。"),
                            raw_output,
                            business_data,
                            normalization_changes,
                            chapter_number=chapter["number"],
                            item_index=item_index,
                            kind=kind,
                            field="source_candidates.quote",
                            validation_stage="evidence_resolution",
                        )
                    if not _lexically_associated(support, quote):
                        if delivery_mode == "strict_verified":
                            return _s02_v41_failure(
                                "TASK_S02_V4_CLAIM_QUOTE_ASSOCIATION_FAILED",
                                "来源原文与声明局部片段无法建立确定性关联。",
                                raw_output,
                                business_data,
                                normalization_changes,
                                chapter_number=chapter["number"],
                                item_index=item_index,
                                kind=kind,
                                field="source_candidates.supports",
                                validation_stage="claim_quote_association",
                            )
                        add_warning(
                            code="quote_unrelated_to_claim",
                            chapter_number=chapter["number"],
                            item_index=item_index,
                            kind=kind,
                            text=support,
                            message="授权原文与声明局部片段未建立确定性关联；未作为引用展示。",
                            disposition=(
                                "marked_pending_verification"
                                if kind in required_kinds
                                else "candidate_discarded"
                            ),
                        )
                        continue
                    resolved_quotes.append(quote)

                if resolved_quotes:
                    normalized_chapter["items"].append(
                        {
                            "kind": kind,
                            "text": support,
                            "source_quotes": resolved_quotes,
                            "rule_ids": [],
                        }
                    )
                elif kind in required_kinds:
                    if delivery_mode == "strict_verified":
                        return _s02_v41_failure(
                            "TASK_S02_V4_REQUIRED_SOURCE_MISSING",
                            "来源必引的局部声明没有通过原文关联检查。",
                            raw_output,
                            business_data,
                            normalization_changes,
                            chapter_number=chapter["number"],
                            item_index=item_index,
                            kind=kind,
                            field="source_candidates",
                            validation_stage="claim_quote_association",
                        )
                    reason = (
                        "本次 Run 没有可与该局部声明建立确定性关联的授权原文。"
                    )
                    normalized_chapter["items"].append(
                        pending_item(
                            source_item,
                            support,
                            reason,
                            list(allowed_types),
                        )
                    )
                    add_warning(
                        code="claim_quote_association_unverified",
                        chapter_number=chapter["number"],
                        item_index=item_index,
                        kind=kind,
                        text=support,
                        message=reason,
                        disposition="marked_pending_verification",
                    )
                else:
                    normalized_chapter["items"].append(
                        {
                            "kind": kind,
                            "text": support,
                            "source_quotes": [],
                            "rule_ids": [],
                        }
                    )

    normalized_json = json.dumps(
        normalized_data,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    assembled = assemble_s02_v3_output(
        normalized_json,
        {"type": "object"},
        evidence,
        authorization_subject=authorization_subject,
        template_version="4.1.0",
        delivery_mode=delivery_mode,
    )
    if not assembled.get("passed"):
        assembled["raw_model_output"] = raw_output
        assembled["normalized_business_result"] = normalized_data
        assembled["business_result"] = assembled.get("business_result")
        assembled["normalization_changes"] = [
            *normalization_changes,
            *assembled.get("normalization_changes", []),
        ]
        assembled["validation_stage"] = assembled.get(
            "validation_stage", "s02_v4_1_protocol_assembly"
        )
        return assembled

    template_rule_status = _s02_v4_template_rule_status(
        normalized_evidence,
        authorization_subject=authorization_subject,
    )
    missing_template_rules = [
        item["rule_id"]
        for item in template_rule_status
        if not item["source_available"]
    ]
    if missing_template_rules and delivery_mode == "strict_verified":
        return _failure(
            "TASK_S02_V4_TEMPLATE_RULE_SOURCE_INCOMPLETE",
            "授权策划模板证据快照未覆盖全部 B-01 至 B-15 写作要求。",
            issues=["缺少模板来源条目：" + ", ".join(missing_template_rules)],
            raw_model_output=raw_output,
            normalized_business_result=business_data,
            normalization_changes=normalization_changes,
            validation_stage="template_rule_coverage",
        )

    combined_warnings = [*warnings, *assembled.get("warnings", [])]
    if missing_template_rules:
        combined_warnings.append(
            {
                "warning_code": "template_rule_source_incomplete",
                "message": (
                    "授权模板证据快照未覆盖："
                    + ", ".join(missing_template_rules)
                    + "；报告未将其标记为已应用。"
                ),
                "disposition": "requires_human_review",
            }
        )
    report = dict(assembled.get("delivery_report", {}))
    report.update(
        {
            "mode": delivery_mode,
            "status": (
                "draft_unverified"
                if delivery_mode == "demo_draft"
                else "strict_verified"
            ),
            "warnings": combined_warnings,
            "template_rule_requirements": template_rule_status,
            "claim_quote_association": "local_exact_support_spans",
            "adapter_verification_scope": "schema_business_shape_and_source_resolution",
            "evidence_ledger_passed": False,
            "semantic_support": "requires_human_review",
            "human_review_required": True,
        }
    )
    return {
        **assembled,
        "passed": True,
        "raw_model_output": raw_output,
        "normalized_business_result": business_data,
        "business_result": assembled["business_result"],
        "normalization_changes": [
            *normalization_changes,
            *assembled.get("normalization_changes", []),
        ],
        "warnings": combined_warnings,
        "delivery_report": report,
        "adapter_verification_scope": "schema_business_shape_and_source_resolution",
    }


def _s02_v41_support_ranges(
    text: str,
    supports: dict[str, list[str]],
) -> list[tuple[int, int, str]] | str:
    ranges: list[tuple[int, int, str]] = []
    for support in supports:
        first = text.find(support)
        if first < 0:
            return "supports 必须是正文 text 的连续逐字子串。"
        if text.find(support, first + 1) >= 0:
            return "supports 在正文中出现多次，无法唯一定位该 Claim Occurrence。"
        ranges.append((first, first + len(support), support))
    ranges.sort(key=lambda item: item[0])
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            return "不同 supports 片段发生重叠，无法无歧义拆分 Claim。"
    covered = {
        index
        for start, end, _ in ranges
        for index in range(start, end)
    }
    for index, character in enumerate(text):
        if _s02_v41_significant_character(character) and index not in covered:
            return "supports 片段没有覆盖正文中的全部事实性字符。"
    return ranges


def _s02_v41_significant_character(character: str) -> bool:
    if character.isspace():
        return False
    if character in {"%", "％", "‰", "°", "℃", "¥", "￥", "$", "€"}:
        return True
    category = unicodedata.category(character)
    return category.startswith(("L", "N", "S"))


def _s02_v41_failure(
    error_code: str,
    message: str,
    raw_output: str,
    business_data: Any,
    normalization_changes: list[dict[str, Any]],
    *,
    chapter_number: int,
    item_index: int,
    kind: str,
    field: str,
    validation_stage: str,
    detail: str | None = None,
) -> dict[str, Any]:
    diagnostic = {
        "error_code": error_code,
        "chapter_number": chapter_number,
        "item_index": item_index,
        "kind": kind,
        "field": field,
        "validation_stage": validation_stage,
        "message": detail or message,
    }
    return _failure(
        error_code,
        message,
        issues=[detail or message],
        diagnostics=[diagnostic],
        raw_model_output=raw_output,
        normalized_business_result=business_data,
        normalization_changes=normalization_changes,
        validation_stage=validation_stage,
    )


def _validate_s02_v4_business_shape(
    data: Any,
    *,
    template_version: str = "4.0.0",
) -> list[str]:
    """Reject unsafe or incoherent business items after JSON Schema checks."""

    if not isinstance(data, dict) or not _safe_single_line(data.get("title", "")):
        return ["title 必须是安全的单行报告标题。"]
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or len(chapters) != 8:
        return ["chapters 必须恰好包含八章。"]
    if [item.get("number") if isinstance(item, dict) else None for item in chapters] != list(range(1, 9)):
        return ["chapters 必须按 1 至 8 的顺序出现。"]
    allowed_kinds = {
        "fact",
        "historical_case",
        "analysis",
        "recommendation",
        "template_limitation",
        "unverified",
    }
    count = 0
    issues: list[str] = []
    for chapter_index, chapter in enumerate(chapters):
        items = chapter.get("items") if isinstance(chapter, dict) else None
        if not isinstance(items, list):
            issues.append(f"chapters[{chapter_index}].items 必须是数组。")
            continue
        for item_index, item in enumerate(items):
            count += 1
            prefix = f"chapters[{chapter_index}].items[{item_index}]"
            if not isinstance(item, dict):
                issues.append(f"{prefix} 必须是对象。")
                continue
            kind = item.get("kind")
            text = item.get("text")
            candidates = item.get("source_candidates")
            if kind not in allowed_kinds:
                issues.append(f"{prefix}.kind 不受 S02 v4 支持。")
            if not isinstance(text, str) or not _safe_single_line(text):
                issues.append(f"{prefix}.text 必须是安全的单行文本。")
            if not isinstance(candidates, list) or len(candidates) > 6:
                issues.append(f"{prefix}.source_candidates 必须是最多六项的数组。")
            elif template_version == "4.1.0":
                candidate_keys: list[tuple[str, str]] = []
                for candidate_index, candidate in enumerate(candidates):
                    candidate_prefix = (
                        f"{prefix}.source_candidates[{candidate_index}]"
                    )
                    if not isinstance(candidate, dict):
                        issues.append(f"{candidate_prefix} 必须是对象。")
                        continue
                    if set(candidate) != {"quote", "supports"}:
                        issues.append(
                            f"{candidate_prefix} 只能包含 quote 与 supports。"
                        )
                        continue
                    quote = candidate.get("quote")
                    supports = candidate.get("supports")
                    if (
                        not isinstance(quote, str)
                        or not quote.strip()
                        or quote.strip() != quote
                    ):
                        issues.append(
                            f"{candidate_prefix}.quote 必须是原样连续摘录。"
                        )
                    if (
                        not isinstance(supports, str)
                        or not supports.strip()
                        or supports.strip() != supports
                        or isinstance(text, str) and supports not in text
                    ):
                        issues.append(
                            f"{candidate_prefix}.supports 必须是 text 的连续原文片段。"
                        )
                    if isinstance(quote, str) and isinstance(supports, str):
                        candidate_keys.append((quote, supports))
                if len(set(candidate_keys)) != len(candidate_keys):
                    issues.append(
                        f"{prefix}.source_candidates 不得重复 quote/supports 配对。"
                    )
            elif (
                any(
                    not isinstance(quote, str)
                    or not quote.strip()
                    or quote.strip() != quote
                    for quote in candidates
                )
                or len(set(candidates)) != len(candidates)
            ):
                issues.append(
                    f"{prefix}.source_candidates 必须是互不重复的原样连续摘录。"
                )
            if kind == "unverified":
                if candidates != []:
                    issues.append(f"{prefix}.unverified 不得声明来源候选。")
                if item.get("pending_status") not in _PENDING_LABELS:
                    issues.append(f"{prefix}.pending_status 无效。")
                if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                    issues.append(f"{prefix}.reason 不能为空。")
                expected = item.get("expected_evidence_types")
                if (
                    not isinstance(expected, list)
                    or not expected
                    or any(
                        value not in {"project_material", "planning_template", "historical_case"}
                        for value in expected
                    )
                ):
                    issues.append(f"{prefix}.expected_evidence_types 无效。")
            elif any(
                field in item
                for field in ("pending_status", "reason", "expected_evidence_types")
            ):
                issues.append(f"{prefix} 普通业务条目不能携带待处理字段。")
    if count > _MAX_CLAIMS:
        issues.append("S02 v4 条目总数不能超过 128 项。")
    return issues


def _s02_v4_business_diagnostics(
    data: Any,
    *,
    template_version: str = "4.0.0",
) -> list[dict[str, Any]]:
    issues = _validate_s02_v4_business_shape(
        data,
        template_version=template_version,
    )
    diagnostics: list[dict[str, Any]] = []
    for issue in issues:
        match = re.search(
            r"chapters\[(\d+)\]\.items\[(\d+)\](?:\.([\w]+))?",
            issue,
        )
        chapter_index = int(match.group(1)) if match else None
        item_index = int(match.group(2)) if match else None
        chapter = (
            data["chapters"][chapter_index]
            if chapter_index is not None
            and isinstance(data, dict)
            and isinstance(data.get("chapters"), list)
            and chapter_index < len(data["chapters"])
            else None
        )
        item = (
            chapter["items"][item_index]
            if item_index is not None
            and isinstance(chapter, dict)
            and isinstance(chapter.get("items"), list)
            and item_index < len(chapter["items"])
            else None
        )
        diagnostics.append(
            {
                "error_code": "s02_v4_business_shape_invalid",
                "chapter_number": (
                    chapter.get("number")
                    if isinstance(chapter, dict)
                    else None
                ),
                "item_index": item_index,
                "kind": item.get("kind") if isinstance(item, dict) else None,
                "field": match.group(3) if match else None,
                "validation_stage": "s02_v4_business",
                "message": issue,
            }
        )
    return diagnostics


def _s02_v4_template_rule_status(
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str,
) -> list[dict[str, Any]]:
    present: set[str] = set()
    for item in evidence:
        if (
            not isinstance(item, dict)
            or item.get("evidence_type") != "planning_template"
            or item.get("authorization_subject") != authorization_subject
            or item.get("source_status", "ready") != "ready"
            or not isinstance(item.get("source_text"), str)
        ):
            continue
        source_text = item["source_text"]
        content_hash = item.get("content_sha256")
        if not isinstance(content_hash, str) or not _SHA256.fullmatch(
            content_hash
        ):
            continue
        if item.get("source_text_truncated") is False and hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest() != content_hash:
            continue
        for line in source_text.splitlines():
            match = _TEMPLATE_RULE_ENTRY.fullmatch(line)
            if match is None:
                continue
            description = match.group("description").strip()
            if re.match(r"^(?:至|到|[-~～])\s*B?\s*\d", description):
                continue
            if len(description) >= 8:
                present.add(f"B-{int(match.group(1)):02d}")
    return [
        {
            "rule_id": f"B-{number:02d}",
            "source_available": f"B-{number:02d}" in present,
            "report_application": (
                "available_requires_human_review"
                if f"B-{number:02d}" in present
                else "not_applied_source_missing"
            ),
        }
        for number in range(1, 16)
    ]


def _normalize_unverified_empty_arrays(
    business_data: Any,
) -> list[dict[str, str]]:
    """Insert only the two semantically empty arrays omitted by unverified items."""

    changes: list[dict[str, str]] = []
    if not isinstance(business_data, dict):
        return changes
    chapters = business_data.get("chapters")
    if not isinstance(chapters, list):
        return changes
    for chapter_index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            continue
        items = chapter.get("items")
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("kind") != "unverified":
                continue
            for field in ("source_quotes", "rule_ids"):
                if field in item:
                    continue
                item[field] = []
                changes.append(
                    {
                        "path": (
                            f"chapters[{chapter_index}].items[{item_index}]"
                            f".{field}"
                        ),
                        "operation": "insert_empty_array",
                    }
                )
    return changes


def _detailed_schema_diagnostics(
    schema: dict[str, Any],
    value: Any,
    *,
    fallback: list[str],
) -> list[dict[str, Any]]:
    """Report discriminated item errors with safe paths and kind context."""

    try:
        errors = list(Draft202012Validator(schema).iter_errors(value))
    except (TypeError, RecursionError, ValueError):
        return [
            _schema_diagnostic(
                "schema_validation_error",
                "schema",
                None,
                None,
                None,
                "JSON Schema 无法完成校验。",
                validation_stage="json_schema",
                raw_error_type="schema",
            )
        ]
    if not errors:
        return [
            _schema_diagnostic(
                "schema_validation_error",
                "schema",
                None,
                None,
                None,
                message,
                validation_stage="json_schema",
                raw_error_type="schema",
            )
            for message in fallback
        ]

    diagnostics: list[dict[str, Any]] = []
    for error in errors:
        path_parts = list(error.absolute_path)
        if error.validator == "oneOf" and isinstance(error.instance, dict):
            variants = error.schema.get("oneOf", [])
            branch = _schema_variant_for_kind(variants, error.instance)
            if branch is None:
                diagnostics.append(
                    _schema_diagnostic(
                        "no_matching_kind_schema",
                        "kind",
                        *_schema_location(value, path_parts),
                        "kind 没有对应的允许结构。",
                        path_parts=path_parts + ["kind"],
                        raw_error_type="oneOf",
                    )
                )
                continue
            for branch_error in Draft202012Validator(branch).iter_errors(
                error.instance
            ):
                branch_path = path_parts + list(branch_error.absolute_path)
                chapter_number, item_index, item_kind = _schema_location(
                    value, path_parts
                )
                if (
                    branch_error.validator == "additionalProperties"
                    and isinstance(branch_error.instance, dict)
                ):
                    allowed = set(
                        branch_error.schema.get("properties", {})
                    )
                    for field in sorted(set(branch_error.instance) - allowed):
                        diagnostics.append(
                            _schema_diagnostic(
                                "field_not_allowed_for_kind",
                                field,
                                chapter_number,
                                item_index,
                                item_kind,
                                f"{field} 不属于 kind={item_kind} 的 Schema 字段。",
                                path_parts=path_parts + [field],
                                raw_error_type="additionalProperties",
                            )
                        )
                    continue
                if (
                    branch_error.validator == "required"
                    and isinstance(branch_error.instance, dict)
                ):
                    missing = [
                        field
                        for field in branch_error.validator_value
                        if field not in branch_error.instance
                    ]
                    for field in missing:
                        diagnostics.append(
                            _schema_diagnostic(
                                "missing_required_field",
                                field,
                                chapter_number,
                                item_index,
                                item_kind,
                                f"缺少必填字段 {field}。",
                                path_parts=path_parts + [field],
                                raw_error_type="required",
                            )
                        )
                    continue
                field = (
                    str(branch_path[-1])
                    if branch_path
                    and isinstance(branch_path[-1], str)
                    else "item"
                )
                error_code = {
                    "minItems": "minimum_items_not_met",
                    "maxItems": "maximum_items_exceeded",
                    "type": "field_type_invalid",
                    "enum": "field_value_not_allowed",
                    "minLength": "minimum_length_not_met",
                }.get(branch_error.validator, "schema_constraint_failed")
                diagnostics.append(
                    _schema_diagnostic(
                        error_code,
                        field,
                        chapter_number,
                        item_index,
                        item_kind,
                        f"{field} 不符合当前 kind 的 Schema 约束。",
                        path_parts=branch_path,
                        raw_error_type=str(branch_error.validator or "schema"),
                    )
                )
            continue

        if error.validator == "required" and isinstance(error.instance, dict):
            missing = [
                field
                for field in error.validator_value
                if field not in error.instance
            ]
            for field in missing:
                diagnostics.append(
                    _schema_diagnostic(
                        "missing_required_field",
                        field,
                        *_schema_location(value, path_parts),
                        f"缺少必填字段 {field}。",
                        path_parts=path_parts + [field],
                        raw_error_type="required",
                    )
                )
        else:
            field = (
                str(path_parts[-1])
                if path_parts and isinstance(path_parts[-1], str)
                else "schema"
            )
            diagnostics.append(
                _schema_diagnostic(
                    str(error.validator or "schema_constraint_failed"),
                    field,
                    *_schema_location(value, path_parts),
                    f"{field} 不符合 JSON Schema 约束。",
                    path_parts=path_parts,
                    raw_error_type=str(error.validator or "schema"),
                )
            )
    return diagnostics or [
        _schema_diagnostic(
            "schema_validation_error",
            "schema",
            None,
            None,
            None,
            message,
            validation_stage="json_schema",
            raw_error_type="schema",
        )
        for message in fallback
    ]


def _schema_variant_for_kind(
    variants: Any,
    value: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(variants, list):
        return None
    kind = value.get("kind")
    matches = [
        item
        for item in variants
        if isinstance(item, dict)
        and isinstance(item.get("properties"), dict)
        and isinstance(item["properties"].get("kind"), dict)
        and kind in item["properties"]["kind"].get("enum", [])
    ]
    return matches[0] if len(matches) == 1 else None


def _schema_location(
    value: Any,
    path_parts: list[Any],
) -> tuple[Any, int | None, Any]:
    chapter_index: int | None = None
    item_index: int | None = None
    for index, part in enumerate(path_parts[:-1]):
        if part == "chapters" and isinstance(path_parts[index + 1], int):
            chapter_index = path_parts[index + 1]
        if part == "items" and isinstance(path_parts[index + 1], int):
            item_index = path_parts[index + 1]
    chapters = value.get("chapters") if isinstance(value, dict) else None
    chapter = (
        chapters[chapter_index]
        if isinstance(chapters, list)
        and chapter_index is not None
        and chapter_index < len(chapters)
        else None
    )
    items = chapter.get("items") if isinstance(chapter, dict) else None
    item = (
        items[item_index]
        if isinstance(items, list)
        and item_index is not None
        and item_index < len(items)
        else None
    )
    chapter_number = (
        chapter.get("number")
        if isinstance(chapter, dict)
        else (chapter_index + 1 if chapter_index is not None else None)
    )
    item_kind = item.get("kind") if isinstance(item, dict) else None
    return chapter_number, item_index, item_kind


def _schema_diagnostic(
    error_code: str,
    field: str,
    chapter_number: Any,
    item_index: int | None,
    kind: Any,
    detail: str,
    *,
    path_parts: list[Any] | None = None,
    validation_stage: str = "json_schema",
    raw_error_type: str,
) -> dict[str, Any]:
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in (path_parts or [])
    )
    context = (
        f"chapter={chapter_number}, item_index={item_index}, item.kind={kind}"
        if chapter_number is not None and item_index is not None
        else "root"
    )
    return {
        "error_code": error_code,
        "path": path,
        "chapter_number": chapter_number,
        "item_index": item_index,
        "kind": kind,
        "field": field,
        "raw_error_type": raw_error_type,
        "validation_stage": validation_stage,
        "message": (
            f"{path}: {error_code} (raw_error_type={raw_error_type}, "
            f"stage={validation_stage}, {context})；{detail}"
        ),
    }


def _with_output_trace(
    result: dict[str, Any],
    raw_output: str,
    normalized_business_data: Any,
    normalization_changes: list[dict[str, str]],
    *,
    validation_stage: str,
) -> dict[str, Any]:
    result.update(
        {
            "raw_model_output": raw_output,
            "normalized_business_result": normalized_business_data,
            "normalization_changes": list(normalization_changes),
            "validation_stage": validation_stage,
        }
    )
    return result


def _validate_business_shape(
    data: Any,
    *,
    template_version: str = "3.0.0",
    delivery_mode: str = "strict_verified",
) -> list[str]:
    if not isinstance(data, dict):
        return ["根结果必须是 JSON 对象。"]
    issues: list[str] = []
    title = data.get("title")
    if not isinstance(title, str) or not _safe_single_line(title):
        issues.append("title 必须是安全的单行报告标题。")
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or len(chapters) != 8:
        issues.append("chapters 必须恰好包含八章。")
        return issues
    observed_numbers = [
        item.get("number") if isinstance(item, dict) else None
        for item in chapters
    ]
    if observed_numbers != list(range(1, 9)):
        issues.append("chapters 必须按 1 至 8 的顺序出现。")

    seen_rules: list[str] = []
    total_items = 0
    policy = get_scenario_evidence_policy(
        "s02_project_planning", template_version
    )
    allowed_expected = {
        value
        for values in (
            policy.allowed_evidence_types_by_claim_kind.values()
            if policy is not None
            else []
        )
        for value in values
    }
    for chapter_index, chapter in enumerate(chapters, start=1):
        items = chapter.get("items") if isinstance(chapter, dict) else None
        if not isinstance(items, list):
            issues.append(f"chapters[{chapter_index}].items 必须是数组。")
            continue
        total_items += len(items)
        for item_index, item in enumerate(items):
            prefix = f"chapters[{chapter_index}].items[{item_index}]"
            if not isinstance(item, dict):
                issues.append(f"{prefix} 必须是对象。")
                continue
            kind = item.get("kind")
            text = item.get("text")
            if not isinstance(text, str) or not _safe_single_line(text):
                issues.append(f"{prefix}.text 必须是安全的单行文本。")
                continue
            if template_version in _S02_V4_TEMPLATE_VERSIONS and kind not in {
                "fact",
                "historical_case",
                "analysis",
                "recommendation",
                "template_limitation",
                "unverified",
            }:
                issues.append(f"{prefix}.kind 不是 S02 v4 支持的内容类型。")
                continue
            quote_candidates = item.get("source_quotes")
            if not isinstance(quote_candidates, list):
                issues.append(f"{prefix}.source_quotes 必须是数组。")
                continue
            if any(
                not isinstance(quote, str)
                or not quote.strip()
                or quote.strip() != quote
                for quote in quote_candidates
            ):
                issues.append(
                    f"{prefix}.source_quotes 只能包含非空、原样的连续原文。"
                )
            if len(set(quote_candidates)) != len(quote_candidates):
                issues.append(f"{prefix}.source_quotes 不能重复。")

            rule_ids = item.get("rule_ids")
            text_rules = [
                f"B-{number}" for number in _RULE_MARKER.findall(text)
            ]
            if kind == "rule":
                if (
                    not isinstance(rule_ids, list)
                    or len(rule_ids) != 1
                    or rule_ids[0] not in text_rules
                ):
                    issues.append(
                        f"{prefix} 的 rule 必须只声明一个正文中实际出现的 B 条款。"
                    )
                elif not any(
                    rule_ids[0]
                    == f"B-{number}"
                    and len(_RULE_MARKER.sub("", quote).strip()) >= 8
                    for quote in quote_candidates
                    for number in _RULE_MARKER.findall(quote)
                ):
                    issues.append(
                        f"{prefix} 的 B 条款来源摘录必须包含条款编号和具体内容。"
                    )
                else:
                    seen_rules.append(rule_ids[0])
            elif rule_ids:
                issues.append(
                    f"{prefix} 仅 kind=rule 可以声明 rule_ids。"
                )

            if (
                template_version == "3.2.0"
                and kind == "rule"
                and len(text_rules) != 1
            ):
                issues.append(
                    f"{prefix}.text 必须只表达一个 B-01 至 B-15 条款。"
                )

            if kind == "template_limitation":
                if template_version not in {"3.2.0", *_S02_V4_TEMPLATE_VERSIONS}:
                    issues.append(
                        f"{prefix}.kind template_limitation 仅适用于 S02 v3.2.0。"
                    )
                if not re.search(r"策划模板|模板", text, re.IGNORECASE):
                    issues.append(
                        f"{prefix}.text 必须明确描述模板适用性限制。"
                    )
                if not quote_candidates and delivery_mode != "demo_draft":
                    issues.append(
                        f"{prefix}.source_quotes 必须提供授权模板原文候选。"
                    )

            if kind == "unverified":
                if quote_candidates:
                    issues.append(
                        f"{prefix} 的 unverified 项不能提供来源候选摘录。"
                    )
                if item.get("unverified_status") not in _PENDING_LABELS:
                    issues.append(
                        f"{prefix} 必须声明有效的 unverified_status。"
                    )
                if not isinstance(item.get("reason"), str) or not item[
                    "reason"
                ].strip():
                    issues.append(f"{prefix}.reason 不能为空。")
                expected = item.get("expected_evidence_types")
                if (
                    not isinstance(expected, list)
                    or not expected
                    or any(value not in allowed_expected for value in expected)
                ):
                    issues.append(
                        f"{prefix}.expected_evidence_types 必须列出 S02 支持的证据类型。"
                    )
            elif any(
                key in item
                for key in (
                    "unverified_status",
                    "reason",
                    "expected_evidence_types",
                )
            ):
                issues.append(
                    f"{prefix} 非 unverified 内容不能携带待处理字段。"
                )
            if kind == "recommendation" and not any(
                marker in text for marker in ("建议", "拟定目标", "推断")
            ):
                issues.append(
                    f"{prefix} 的 recommendation 必须在正文中明确标为建议。"
                )
    if total_items > _MAX_CLAIMS:
        issues.append("内容项总数不能超过 Task 协议的 128 项上限。")
    expected_rules = [f"B-{number:02d}" for number in range(1, 16)]
    if template_version not in _S02_V4_TEMPLATE_VERSIONS and sorted(seen_rules) != expected_rules:
        issues.append("S02 v3 必须各自完整覆盖 B-01 至 B-15。")
    return issues


def _business_shape_diagnostics(
    data: dict[str, Any],
    *,
    template_version: str,
) -> list[dict[str, Any]]:
    """Return locatable diagnostics for S02 kind-specific business rules."""

    diagnostics: list[dict[str, Any]] = []
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        return [
            {
                "error_code": "chapter_array_invalid",
                "chapter_number": None,
                "item_index": None,
                "kind": None,
                "field": "chapters",
                "validation_stage": "s02_business",
                "message": "chapters 必须是八章数组。",
            }
        ]

    pending_fields = {
        "unverified_status",
        "reason",
        "expected_evidence_types",
    }
    ordinary_kinds = {
        "fact",
        "rule",
        "historical_case",
        "analysis",
        "recommendation",
        "template_limitation",
    }

    def add(
        error_code: str,
        chapter_number: Any,
        item_index: int,
        kind: Any,
        field: str,
        message: str,
    ) -> None:
        diagnostics.append(
            {
                "error_code": error_code,
                "chapter_number": chapter_number,
                "item_index": item_index,
                "kind": kind,
                "field": field,
                "validation_stage": "s02_business",
                "message": message,
            }
        )

    for chapter_index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            continue
        chapter_number = chapter.get("number", chapter_index + 1)
        items = chapter.get("items")
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            text = item.get("text")
            if kind in ordinary_kinds:
                for field in sorted(pending_fields.intersection(item)):
                    add(
                        "field_not_allowed_for_kind",
                        chapter_number,
                        item_index,
                        kind,
                        field,
                        f"{field} 仅允许 unverified 条目使用。",
                    )
            if not isinstance(text, str):
                continue
            text_rule_ids = _RULE_MARKER.findall(text)
            declared_rule_ids = item.get("rule_ids")
            if kind == "rule":
                if template_version == "3.2.0" and (
                    not isinstance(declared_rule_ids, list)
                    or len(declared_rule_ids) != 1
                    or len(text_rule_ids) != 1
                    or declared_rule_ids[0] != f"B-{text_rule_ids[0]}"
                ):
                    add(
                        "single_b_clause_required",
                        chapter_number,
                        item_index,
                        kind,
                        "text/rule_ids",
                        "rule 必须只关联一个正文中出现的 B-01 至 B-15 条款。",
                    )
                quotes = item.get("source_quotes")
                if (
                    not isinstance(quotes, list)
                    or not any(
                        isinstance(quote, str)
                        and declared_rule_ids
                        and declared_rule_ids[0]
                        in {
                            f"B-{number}"
                            for number in _RULE_MARKER.findall(quote)
                        }
                        and len(_RULE_MARKER.sub("", quote).strip()) >= 8
                        for quote in quotes
                    )
                ):
                    add(
                        "b_clause_source_quote_insufficient",
                        chapter_number,
                        item_index,
                        kind,
                        "source_quotes",
                        "B 条款来源摘录必须包含该条款编号及具体内容。",
                    )
            if kind == "template_limitation":
                quotes = item.get("source_quotes")
                if not isinstance(quotes, list) or not quotes:
                    add(
                        "template_limitation_source_required",
                        chapter_number,
                        item_index,
                        kind,
                        "source_quotes",
                        "模板适用性限制必须关联授权策划模板原文。",
                    )
            if kind == "recommendation" and not any(
                marker in text for marker in ("建议", "拟定目标", "推断")
            ):
                add(
                    "recommendation_not_marked",
                    chapter_number,
                    item_index,
                    kind,
                    "text",
                    "建议内容必须在正文中明确标为建议、拟定目标或推断。",
                )

    if diagnostics:
        return diagnostics
    return [
        {
            "error_code": "s02_business_shape_invalid",
            "chapter_number": None,
            "item_index": None,
            "kind": None,
            "field": None,
            "validation_stage": "s02_business",
            "message": "业务内容未满足 S02 章节或 B-01 至 B-15 覆盖规则。",
        }
    ]


def _resolve_quote(
    quote: str,
    claim_kind: str,
    evidence: list[dict[str, Any]],
    allowed_types: list[str],
    *,
    authorization_subject: str,
) -> tuple[str, str] | dict[str, Any]:
    matching = [
        item
        for item in evidence
        if isinstance(item.get("source_text"), str)
        and quote in item["source_text"]
    ]
    if not matching:
        return _failure(
            "TASK_S02_V3_SOURCE_QUOTE_NOT_FOUND",
            "来源候选摘录未出现在本次 TaskRun 的授权证据快照中。",
        )

    valid_matches: list[dict[str, Any]] = []
    for item in matching:
        if item.get("authorization_subject") != authorization_subject:
            return _failure(
                "TASK_S02_V3_SOURCE_PERMISSION_MISMATCH",
                "来源候选的授权主体与当前 TaskRun 所有者不一致。",
            )
        if item.get("source_status", "ready") != "ready":
            return _failure(
                "TASK_S02_V3_SOURCE_NOT_READY",
                "来源候选对应的证据快照状态不是 ready。",
            )
        content_hash = item.get("content_sha256")
        source_text = item.get("source_text")
        if (
            not isinstance(content_hash, str)
            or not _SHA256.fullmatch(content_hash)
            or not isinstance(source_text, str)
            or not isinstance(item.get("evidence_id"), str)
        ):
            return _failure(
                "TASK_S02_V3_SOURCE_IDENTITY_INVALID",
                "来源候选缺少有效的服务端 Evidence 身份或内容哈希。",
            )
        if item.get("source_text_truncated") is False:
            actual_hash = hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest()
            if actual_hash != content_hash:
                return _failure(
                    "TASK_S02_V3_SOURCE_HASH_MISMATCH",
                    "来源候选的内容哈希与本次运行保存的原文不一致。",
                )
        if not allowed_types or item.get("evidence_type") in allowed_types:
            valid_matches.append(item)

    if not valid_matches:
        return _failure(
            "TASK_S02_V3_SOURCE_TYPE_NOT_ALLOWED",
            f"{claim_kind} 声明没有匹配 EvidencePolicy 允许的来源类型。",
        )
    if len(valid_matches) != 1:
        return _failure(
            "TASK_S02_V3_SOURCE_MATCH_AMBIGUOUS",
            "来源候选摘录匹配到多个授权 Evidence，Task 无法唯一确定来源。",
        )
    selected = valid_matches[0]
    return str(selected["evidence_id"]), quote


def _claim_group_key(item: dict[str, Any], visible_text: str) -> tuple[Any, ...]:
    kind = item["kind"]
    if kind != "unverified":
        return (kind, visible_text)
    return (
        kind,
        visible_text,
        item["unverified_status"],
        item["reason"],
        tuple(item["expected_evidence_types"]),
    )


def _visible_text(item: dict[str, Any]) -> str:
    kind = item["kind"]
    if kind == "unverified":
        label = _PENDING_LABELS[item["unverified_status"]]
        return f"【{label}】{item['text']}"
    label = _KIND_LABELS.get(kind, "")
    return f"{label}{item['text']}" if label else item["text"]


def _safe_single_line(value: str) -> bool:
    return bool(value.strip()) and not any(
        marker in value
        for marker in ("\r", "\n", "\x00")
    ) and _CITATION_MARKER.search(value) is None and _RESERVED_CONTENT.search(
        value
    ) is None


def _failure(
    error_code: str,
    message: str,
    *,
    issues: list[str] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    raw_model_output: str | None = None,
    normalized_business_result: Any = None,
    normalization_changes: list[dict[str, str]] | None = None,
    validation_stage: str | None = None,
) -> dict[str, Any]:
    result = {
        "passed": False,
        "error_code": error_code,
        "error": message,
        "issues": issues or [message],
    }
    if raw_model_output is not None:
        result["raw_model_output"] = raw_model_output
    if diagnostics is not None:
        result["diagnostics"] = diagnostics
    if normalized_business_result is not None:
        result["normalized_business_result"] = normalized_business_result
    if normalization_changes is not None:
        result["normalization_changes"] = list(normalization_changes)
    if validation_stage is not None:
        result["validation_stage"] = validation_stage
    return result


__all__ = [
    "assemble_s02_v3_output",
    "assemble_s02_v4_output",
    "assemble_s02_v4_1_output",
]
