# -*- coding: utf-8 -*-
"""Bounded Task-owned adapters for scenario inputs and deterministic checks.

These adapters process data submitted to the current TaskRun. They do not
connect to expert, meeting, archive, finance, OCR, ASR, ERP, or notification
systems. Such boundaries remain explicit in each result.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from ._executor import StepExecutionResult
from ._evidence_contract import normalize_evidence_snapshot


_SCENARIO_NATIVE_CAPABILITIES = frozenset(
    {
        "project.similar_search",
        "project.rule_check",
        "expert.user_data_lookup",
        "expert.user_conflict_check",
        "expert.user_rank",
        "meeting.cost_calculate",
        "meeting.material_generate",
        "meeting.material_check",
        "meeting.transcript_import",
        "opinion.text_import",
        "opinion.classify",
        "archive.document_metadata",
        "archive.completeness_check",
        "expense.invoice_import",
        "expense.invoice_verify_dedupe",
        "expense.rule_check",
    }
)

_RAG_CAPABILITIES = frozenset(
    {
        "planning.template_retrieve",
        "project.similar_search",
        "project.rule_check",
        "report.rag_retrieve",
    }
)

_RULE_KEYWORDS = (
    "办法",
    "规定",
    "规范",
    "标准",
    "规则",
    "制度",
    "条例",
    "指南",
    "政策",
)

_ARCHIVE_FIELD_LABELS = {
    "file_name": "文件名",
    "category": "归档类别",
    "document_date": "文件日期",
    "owner": "责任人",
    "retention_period": "保管期限",
    "document_number": "文号",
}


class ScenarioInputError(ValueError):
    """Safe validation failure for explicit scenario input data."""


def is_scenario_native_capability(capability_key: str | None) -> bool:
    """Return whether Task has a local, bounded implementation for the key."""

    return capability_key in _SCENARIO_NATIVE_CAPABILITIES


def is_task_rag_capability(capability_key: str | None) -> bool:
    """Return whether the capability stores authorized RAG evidence."""

    return capability_key in _RAG_CAPABILITIES


def is_knowledge_native_capability(capability_key: str | None) -> bool:
    """Return whether the capability needs the authorized KB gateway."""

    return capability_key in {
        "planning.template_retrieve",
        "document.citation_verify",
        "project.similar_search",
        "project.rule_check",
        "report.rag_retrieve",
        "report.citation_check",
    }


def native_tool_schema(capability_key: str) -> dict[str, Any]:
    """Expose a bounded Task-only Tool shape to template validation."""

    known = {
        "planning.template_retrieve": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "document.citation_verify": {
            "type": "object",
            "properties": {"draft": {"type": "string"}},
            "required": ["draft"],
            "additionalProperties": False,
        },
        "report.citation_check": {
            "type": "object",
            "properties": {"draft": {"type": "string"}},
            "required": ["draft"],
            "additionalProperties": False,
        },
        "report.rag_retrieve": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        },
    }
    return known.get(
        capability_key,
        {"type": "object", "additionalProperties": True},
    )


async def retrieve_scenario_evidence(
    capability_key: str,
    *,
    gateway: Any,
    user_id: str,
    knowledge_base_ids: list[str],
    source_material_ids: list[str],
    input_values: dict[str, str],
    authorization_subject: str,
) -> StepExecutionResult:
    """Retrieve source chunks under the existing Task user/KB permission scope."""

    query = _retrieval_query(capability_key, input_values)
    if not query:
        return _failure(
            "Task 输入缺少可用于授权知识库检索的主题或资料范围。",
            code="TASK_SCENARIO_QUERY_MISSING",
        )
    try:
        result = await gateway.retrieve_task_evidence(
            user_id,
            knowledge_base_ids,
            query,
            source_material_ids=source_material_ids,
            top_k=8,
        )
    except Exception as error:  # noqa: BLE001 — fail closed at the KB boundary
        if (
            capability_key == "project.rule_check"
            and getattr(error, "code", None) == "no_matching_evidence"
        ):
            result = {
                "status": "not_found",
                "query": query,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "knowledge_bases": [],
                "evidence": [],
                "message": "未检索到规则类来源；适用规则仍需人工确认。",
                "automated_compliance_conclusion": False,
            }
            return StepExecutionResult(
                text=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                result=result,
                metadata={
                    "scenario_retrieval": {
                        "capability_key": capability_key,
                        "status": "not_found",
                        "query": query,
                        "evidence_count": 0,
                    },
                },
            )
        message = getattr(error, "message", None) or "授权知识库检索失败，Task 已停止。"
        code = getattr(error, "code", "retrieval_failed")
        return StepExecutionResult(
            text=str(message),
            status="failed",
            reason=str(message),
            metadata={
                "knowledge_error_code": str(code),
                "scenario_retrieval": {"capability_key": capability_key},
            },
        )

    evidence = result.get("evidence", [])
    if capability_key == "project.rule_check":
        evidence = [
            item
            for item in evidence
            if _looks_like_rule_source(item)
        ]
        if not evidence:
            result = {
                **result,
                "status": "not_found",
                "evidence": [],
                "message": "未检索到可识别的规则类来源；适用规则仍需人工确认。",
                "automated_compliance_conclusion": False,
            }
        else:
            result = {
                **result,
                "status": "candidates_for_review",
                "message": "以下为规则类来源候选，不代表已完成适用性或合规性判定。",
                "automated_compliance_conclusion": False,
            }
    elif not evidence:
        result = {
            **result,
            "status": "not_found",
            "message": "未检索到匹配的授权资料。",
        }
        if capability_key == "report.rag_retrieve":
            return StepExecutionResult(
                text=result["message"],
                result=result,
                status="failed",
                reason=result["message"],
                metadata={
                    "scenario_retrieval": {
                        "capability_key": capability_key,
                        "status": "not_found",
                        "query": query,
                        "evidence_count": 0,
                        "source_scope": "authorized_task_knowledge_bases",
                    },
                },
            )
    else:
        result = {**result, "status": "ready"}

    evidence_type = {
        "project.similar_search": "project_material",
        "project.rule_check": "project_rule",
        "report.rag_retrieve": "report_source",
    }.get(capability_key, "knowledge_base_chunk")
    tagged_evidence = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        tagged_evidence.append(
            {
                **item,
                "evidence_type": evidence_type,
                "authorization_subject": authorization_subject,
            },
        )
    tagged_evidence = normalize_evidence_snapshot(
        tagged_evidence,
        authorization_subject=authorization_subject,
        collected_at=result.get("retrieved_at"),
    )
    result["evidence"] = tagged_evidence
    result["query"] = query
    result["source_scope"] = "authorized_task_knowledge_bases"
    if capability_key == "project.similar_search":
        result["search_interpretation"] = (
            "授权知识库检索结果；相似性和项目事实语义仍需人工复核。"
        )
    elif capability_key == "report.rag_retrieve":
        result["search_interpretation"] = (
            "授权报告资料原文；引用校验不证明结论的语义充分性。"
        )
    agent_result = dict(result)
    agent_result["evidence"] = [
        {
            key: value
            for key, value in item.items()
            if key != "authorization_subject"
        }
        for item in tagged_evidence
    ]
    result_text = gateway.format_task_evidence(agent_result)
    return StepExecutionResult(
        text=result_text,
        result=result,
        metadata={
            "scenario_retrieval": {
                "capability_key": capability_key,
                "status": result["status"],
                "query": query,
                "evidence_count": len(tagged_evidence),
                "knowledge_bases": result.get("knowledge_bases", []),
                "source_scope": "authorized_task_knowledge_bases",
            },
        },
    )


def execute_scenario_capability(
    capability_key: str,
    *,
    input_values: dict[str, str],
    prior_results: dict[str, Any],
    run_id: str,
) -> StepExecutionResult:
    """Run a deterministic Task-local adapter over persisted user inputs."""

    try:
        if capability_key in {
            "expert.user_data_lookup",
            "expert.user_conflict_check",
            "expert.user_rank",
        }:
            return _expert_capability(capability_key, input_values, prior_results, run_id)
        if capability_key == "meeting.cost_calculate":
            return _meeting_costs(input_values)
        if capability_key == "meeting.material_generate":
            return _meeting_materials(input_values, prior_results)
        if capability_key == "meeting.material_check":
            return _meeting_material_check(input_values, prior_results)
        if capability_key == "meeting.transcript_import":
            return _meeting_transcript(input_values)
        if capability_key in {"opinion.text_import", "opinion.classify"}:
            return _opinion_capability(capability_key, input_values, prior_results)
        if capability_key in {
            "archive.document_metadata",
            "archive.completeness_check",
        }:
            return _archive_capability(capability_key, input_values, prior_results)
        if capability_key in {
            "expense.invoice_import",
            "expense.invoice_verify_dedupe",
            "expense.rule_check",
        }:
            return _expense_capability(capability_key, input_values, prior_results)
    except ScenarioInputError as error:
        return _failure(str(error), code="TASK_SCENARIO_INPUT_INVALID")
    except (InvalidOperation, OverflowError, RecursionError, TypeError, ValueError) as error:
        return _failure(
            f"场景输入无法安全处理：{str(error)[:300]}",
            code="TASK_SCENARIO_INPUT_INVALID",
        )
    return _failure(
        "Task 没有该场景能力的本地执行适配。",
        code="TASK_SCENARIO_CAPABILITY_UNSUPPORTED",
    )


def _retrieval_query(capability_key: str, values: dict[str, str]) -> str:
    if capability_key == "project.similar_search":
        parts = [values.get("project_name", ""), values.get("project_brief", "")]
        if values.get("industry"):
            parts.append(values["industry"])
        return " ".join(part.strip() for part in parts if part.strip())[:8_000]
    if capability_key == "project.rule_check":
        parts = [
            values.get("industry", ""),
            values.get("scope", ""),
            values.get("project_brief", ""),
            "立项评审适用规则 规定 办法 指南",
        ]
        return " ".join(part.strip() for part in parts if part.strip())[:8_000]
    if capability_key == "report.rag_retrieve":
        parts = [
            values.get("project_name", ""),
            values.get("required_chapters", ""),
            values.get("applicable_rules", ""),
            values.get("source_material_ids", ""),
        ]
        return " ".join(part.strip() for part in parts if part.strip())[:8_000]
    return ""


def _expert_capability(
    capability_key: str,
    values: dict[str, str],
    prior_results: dict[str, Any],
    run_id: str,
) -> StepExecutionResult:
    if capability_key == "expert.user_data_lookup":
        records = _json_list(values, "expert_records_json", max_items=100)
        required_fields = _professional_fields(values.get("professional_fields", ""))
        if not required_fields:
            raise ScenarioInputError("请至少填写一个所需专业领域。")
        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(records):
            if not isinstance(item, dict):
                raise ScenarioInputError(f"专家记录第 {index + 1} 项必须是对象。")
            expert_id = _text(item.get("expert_id"), f"专家记录第 {index + 1} 项缺少 expert_id。", 120)
            if expert_id in seen_ids:
                raise ScenarioInputError("用户提供的专家记录包含重复 expert_id。")
            seen_ids.add(expert_id)
            fields = _professional_field_values(
                item.get("professional_fields"), "professional_fields"
            )
            status = item.get("conflict_status", "unknown")
            if status not in {"clear", "conflict", "unknown"}:
                raise ScenarioInputError("专家回避状态仅接受 clear、conflict 或 unknown。")
            candidates.append(
                {
                    "expert_id": expert_id,
                    "name": _text(item.get("name", expert_id), "专家 name 无效。", 120),
                    "professional_fields": fields,
                    "conflict_status": status,
                    "source_reference": _optional_text(item.get("source_reference"), 240),
                    "matched_fields": sorted(set(required_fields) & set(fields)),
                },
            )
        if not candidates:
            raise ScenarioInputError("没有提供可匹配的结构化专家记录。")
        result = {
            "status": "loaded",
            "source_kind": "user_provided_task_input",
            "official_expert_database": False,
            "run_id": run_id,
            "required_fields": required_fields,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "verification_note": "记录来源由任务创建者提供，未连接或验证正式专家库。",
        }
        return _success(result)

    if capability_key == "expert.user_conflict_check":
        source = prior_results.get("expert.user_data_lookup")
        if not isinstance(source, dict) or not isinstance(source.get("candidates"), list):
            raise ScenarioInputError("缺少上游用户提供的专家候选记录。")
        candidates = []
        for item in source["candidates"]:
            status = item.get("conflict_status", "unknown")
            candidates.append(
                {
                    "expert_id": item["expert_id"],
                    "name": item["name"],
                    "declared_conflict_status": status,
                    "eligible_for_preliminary_ranking": status == "clear",
                    "source_reference": item.get("source_reference"),
                },
            )
        result = {
            "status": "declarations_checked",
            "source_kind": "user_provided_task_input",
            "official_conflict_check_performed": False,
            "candidates": candidates,
            "note": "仅整理用户提供的回避声明，不代表正式回避规则查询或冲突核验。",
        }
        return _success(result)

    conflicts = prior_results.get("expert.user_conflict_check")
    lookup = prior_results.get("expert.user_data_lookup")
    if not isinstance(conflicts, dict) or not isinstance(lookup, dict):
        raise ScenarioInputError("缺少上游结构化专家记录或回避声明。")
    required_fields = lookup.get("required_fields", [])
    statuses = {
        item.get("expert_id"): item.get("declared_conflict_status")
        for item in conflicts.get("candidates", [])
        if isinstance(item, dict)
    }
    ranked: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in lookup.get("candidates", []):
        status = statuses.get(item.get("expert_id"), "unknown")
        matched = sorted(set(item.get("professional_fields", [])) & set(required_fields))
        if status != "clear":
            excluded.append(
                {
                    "expert_id": item.get("expert_id"),
                    "declared_conflict_status": status,
                    "reason": "回避声明不是 clear，候选人不进入初步排序。",
                },
            )
            continue
        score = round(100 * len(matched) / len(required_fields), 2)
        ranked.append(
            {
                "expert_id": item["expert_id"],
                "name": item["name"],
                "rank": 0,
                "score": score,
                "score_components": {
                    "professional_field_overlap": {
                        "matched": matched,
                        "required": required_fields,
                        "formula": "重合专业领域数 / 所需专业领域数 × 100",
                    },
                },
                "declared_conflict_status": status,
                "source_reference": item.get("source_reference"),
            },
        )
    ranked.sort(key=lambda item: (-item["score"], item["expert_id"]))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    requested_count = values.get("required_expert_count", "").strip()
    if requested_count:
        limit = _positive_integer(requested_count, "期望候选人数")
        if limit > 20:
            raise ScenarioInputError("期望候选人数不能超过 20。")
        ranked = ranked[:limit]
    return _success(
        {
            "status": "preliminary_ranking",
            "source_kind": "user_provided_task_input",
            "official_expert_database": False,
            "official_conflict_check_performed": False,
            "ranking_algorithm_version": "task.user-data-field-overlap.v1",
            "candidates": ranked,
            "eligible_candidate_count": len(
                [item for item in statuses.values() if item == "clear"]
            ),
            "excluded_candidates": excluded,
            "requires_human_review": True,
            "note": "分值仅为透明的专业字段重合度建议，不是正式专家评价或聘任结论。",
        },
    )


def _meeting_costs(values: dict[str, str]) -> StepExecutionResult:
    participants = _positive_integer(values.get("participant_count"), "参会人数")
    topic = _text(values.get("meeting_topic"), "请填写会议主题。", 500)
    date = _text(values.get("meeting_date"), "请填写会议时间。", 200)
    location = _optional_text(values.get("location"), 500) or "待确认"
    rules = _json_object(values, "fee_rates_json")
    rule_id = _text(rules.get("rule_id"), "费用规则需要 rule_id。", 120)
    rates = rules.get("rates")
    if not isinstance(rates, dict) or not rates:
        raise ScenarioInputError("费用规则需要提供 rates 对象。")
    lines = []
    total = Decimal("0.00")
    for key, label, multiplier in (
        ("venue_fixed", "场地", 1),
        ("catering_per_person", "餐饮", participants),
        ("materials_per_person", "材料", participants),
        ("transport_per_person", "交通", participants),
    ):
        raw = rates.get(key, "0")
        rate = _money(raw, f"费用规则 {key}")
        if rate < 0:
            raise ScenarioInputError(f"费用规则 {key} 不能为负数。")
        amount = (rate * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total += amount
        lines.append(
            {
                "key": key,
                "label": label,
                "unit_rate": _decimal_text(rate),
                "quantity": multiplier,
                "amount": _decimal_text(amount),
            },
        )
    currency = _optional_text(rules.get("currency"), 12) or "CNY"
    return _success(
        {
            "status": "calculated_from_user_supplied_rates",
            "source_kind": "user_provided_fee_rule",
            "official_fee_policy_verified": False,
            "rule_id": rule_id,
            "meeting": {
                "topic": topic,
                "date": date,
                "location": location,
                "participant_count": participants,
            },
            "currency": currency,
            "lines": lines,
            "total": _decimal_text(total),
            "formula_note": "未配置的费率按 0 计算；请人工核实费率、税费及报销口径。",
        },
    )


def _meeting_materials(
    values: dict[str, str], prior_results: dict[str, Any]
) -> StepExecutionResult:
    costs = prior_results.get("meeting.cost_calculate")
    if not isinstance(costs, dict) or costs.get("status") != "calculated_from_user_supplied_rates":
        raise ScenarioInputError("会前材料草稿缺少上游费用核算结果。")
    meeting = costs["meeting"]
    agenda = _split_values(values.get("agenda", ""))
    if not agenda:
        agenda = ["会议主题与目标", "项目情况汇报", "讨论与意见收集", "后续安排"]
    result = {
        "status": "draft",
        "source_kind": "task_generated_draft",
        "meeting": meeting,
        "agenda": agenda,
        "preparation_checklist": [
            "确认参会人员及联系方式",
            "确认会议室或线上会议入口",
            "准备议程和项目资料",
            "确认签到、记录和意见收集人员",
        ],
        "cost_summary": {
            "currency": costs["currency"],
            "total": costs["total"],
            "rule_id": costs["rule_id"],
        },
        "notification": {"status": "draft_only", "sent": False},
    }
    return _success(result)


def _meeting_material_check(
    values: dict[str, str], prior_results: dict[str, Any]
) -> StepExecutionResult:
    del values
    materials = prior_results.get("meeting.material_generate")
    costs = prior_results.get("meeting.cost_calculate")
    if not isinstance(materials, dict) or not isinstance(costs, dict):
        raise ScenarioInputError("缺少可检查的会前材料或费用核算结果。")
    result = {
        "status": "ready_for_ai_review",
        "meeting_materials": materials,
        "cost_calculation": costs,
        "fee_policy_verified": False,
        "notification_sent": False,
        "review_instruction": (
            "Task 通用 task-verifier 将按本节点验收标准审阅会前材料与费用结果；"
            "本适配器不作完整性通过/失败判断。"
        ),
    }
    return _success(result)


def _meeting_transcript(values: dict[str, str]) -> StepExecutionResult:
    transcript = _text(values.get("transcript_text"), "请提供已转写的会议文本。", 20_000)
    meeting_id = _text(values.get("meeting_id"), "请填写会议记录标识。", 120)
    result = {
        "status": "imported_user_transcript",
        "meeting_id": meeting_id,
        "source_kind": "user_supplied_transcript",
        "transcription_performed": False,
        "transcript_text": transcript,
        "language": _optional_text(values.get("language"), 80),
        "agenda": _split_values(values.get("agenda", "")),
        "note": "Task 仅处理用户已转写文本；未调用 ASR，也未验证录音来源。",
    }
    return _success(result)


def _opinion_capability(
    capability_key: str,
    values: dict[str, str],
    prior_results: dict[str, Any],
) -> StepExecutionResult:
    if capability_key == "opinion.text_import":
        raw_items = _json_list(values, "opinion_records_json", max_items=200)
        opinions = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise ScenarioInputError(f"意见第 {index + 1} 项必须是对象。")
            opinion_id = _text(item.get("opinion_id"), f"意见第 {index + 1} 项缺少 opinion_id。", 120)
            text = _text(item.get("text"), f"意见 {opinion_id} 缺少已识别文本。", 4_000)
            opinions.append(
                {
                    "opinion_id": opinion_id,
                    "text": text,
                    "source_reference": _optional_text(item.get("source_reference"), 240),
                },
            )
        if not opinions:
            raise ScenarioInputError("请导入至少一条已识别的意见文本。")
        return _success(
            {
                "status": "imported_user_recognized_text",
                "source_kind": "user_supplied_recognized_text",
                "ocr_performed": False,
                "project_name": _optional_text(values.get("project_name"), 240),
                "opinions": opinions,
                "note": "Task 未读取图片，未执行 OCR；原始识别文本由用户提供。",
            },
        )

    imported = prior_results.get("opinion.text_import")
    if not isinstance(imported, dict):
        raise ScenarioInputError("缺少上游已识别意见文本。")
    raw_rules = _json_list(values, "classification_rules_json", max_items=50)
    rules: list[tuple[str, list[str]]] = []
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            raise ScenarioInputError(f"分类规则第 {index + 1} 项必须是对象。")
        category = _text(rule.get("category"), "分类规则缺少 category。", 120)
        keywords = _string_list(rule.get("keywords"), "classification keywords", 30)
        if not keywords:
            raise ScenarioInputError(f"分类 {category} 至少需要一个关键词。")
        rules.append((category, keywords))
    if not rules:
        raise ScenarioInputError("请提供至少一条由业务方确认的分类规则。")

    deduped: dict[str, dict[str, Any]] = {}
    for item in imported["opinions"]:
        normalized = _normalize_text(item["text"])
        key = normalized.casefold()
        target = deduped.get(key)
        if target is None:
            matches = [
                category
                for category, keywords in rules
                if any(keyword.casefold() in key for keyword in keywords)
            ]
            category = matches[0] if len(matches) == 1 else (
                "待复核" if len(matches) > 1 else "未分类"
            )
            deduped[key] = {
                **item,
                "text": normalized,
                "category": category,
                "category_matches": matches,
                    "opinion_ids": [item["opinion_id"]],
                "source_references": [item.get("source_reference")]
                if item.get("source_reference")
                else [],
                "duplicate_count": 1,
            }
        else:
            target["duplicate_count"] += 1
            target["opinion_ids"].append(item["opinion_id"])
            target["source_references"].extend(
                [item["source_reference"]]
                if item.get("source_reference")
                else [],
            )
    result = {
        "status": "classified_by_user_supplied_rules",
        "source_kind": "user_supplied_recognized_text",
        "ocr_performed": False,
        "rule_source_kind": "user_supplied_classification_rules",
        "original_count": len(imported["opinions"]),
        "unique_count": len(deduped),
        "duplicate_count": len(imported["opinions"]) - len(deduped),
        "items": list(deduped.values()),
        "requires_human_review": any(
            item["category"] in {"待复核", "未分类"}
            for item in deduped.values()
        ),
    }
    return _success(result)


def _archive_capability(
    capability_key: str,
    values: dict[str, str],
    prior_results: dict[str, Any],
) -> StepExecutionResult:
    if capability_key == "archive.document_metadata":
        raw_items = _json_list(values, "archive_metadata_json", max_items=200)
        items: list[dict[str, Any]] = []
        seen_names: dict[str, int] = {}
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise ScenarioInputError(f"归档元数据第 {index + 1} 项必须是对象。")
            name = _text(item.get("file_name"), "文件元数据缺少 file_name。", 240)
            if any(separator in name for separator in ("/", "\\")) or name in {".", ".."}:
                raise ScenarioInputError("文件名只接受单一名称，不接受路径。")
            key = name.casefold()
            duplicate_of = seen_names.get(key)
            if duplicate_of is None:
                seen_names[key] = index
            extension = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
            category = _extension_category(extension)
            items.append(
                {
                    "file_name": name,
                    "extension": extension,
                    "file_category": category,
                    "metadata": {
                        field: _text(
                            item[field],
                            f"文件元数据字段 {field} 必须是非空字符串。",
                            500,
                        )
                        for field in _ARCHIVE_FIELD_LABELS
                        if field in item and field != "file_name"
                    },
                    "source_kind": "user_provided_metadata",
                    "file_content_read": False,
                    "duplicate": duplicate_of is not None,
                    "duplicate_of_index": duplicate_of,
                },
            )
        if not items:
            raise ScenarioInputError("请至少提供一项文件元数据。")
        return _success(
            {
                "status": "metadata_normalized",
                "project_id": _optional_text(values.get("project_id"), 120),
                "source_kind": "user_provided_metadata",
                "file_content_read": False,
                "archive_system_write_performed": False,
                "items": items,
                "duplicate_count": sum(item["duplicate"] for item in items),
            },
        )

    manifest = prior_results.get("archive.document_metadata")
    if not isinstance(manifest, dict):
        raise ScenarioInputError("缺少上游文件元数据清单。")
    required_fields = _string_list(
        _parse_json_value(
            values.get("required_metadata_fields_json", "[]") or "[]",
            "required_metadata_fields_json",
        ),
        "required metadata fields",
        12,
    )
    if not required_fields:
        raise ScenarioInputError(
            "请提供至少一个完整性检查必需字段；空规则不能判定归档完整。",
        )
    if any(field not in _ARCHIVE_FIELD_LABELS for field in required_fields):
        raise ScenarioInputError("保管规则字段只接受 Task 支持的元数据字段。")
    items = []
    for item in manifest["items"]:
        combined = {"file_name": item["file_name"], **item.get("metadata", {})}
        missing = [field for field in required_fields if not combined.get(field)]
        items.append({**item, "missing_fields": missing, "complete": not missing})
    result = {
        "status": "checked",
        "source_kind": "user_provided_metadata",
        "required_fields": required_fields,
        "items": items,
        "incomplete_count": sum(not item["complete"] for item in items),
        "metadata_source_verified": False,
        "archive_system_write_performed": False,
        "note": "仅核对用户提交的元数据字段；未读取文件内容或写入档案系统。",
    }
    return _success(result)


def _expense_capability(
    capability_key: str,
    values: dict[str, str],
    prior_results: dict[str, Any],
) -> StepExecutionResult:
    if capability_key == "expense.invoice_import":
        raw_invoices = _json_list(values, "invoice_records_json", max_items=100)
        invoices = []
        for index, item in enumerate(raw_invoices):
            if not isinstance(item, dict):
                raise ScenarioInputError(f"票据第 {index + 1} 项必须是对象。")
            invoice_id = _text(item.get("invoice_id"), "票据缺少 invoice_id。", 120)
            code = _text(item.get("invoice_code"), f"票据 {invoice_id} 缺少发票代码。", 120)
            number = _text(item.get("invoice_number"), f"票据 {invoice_id} 缺少发票号码。", 120)
            amount = _money(item.get("amount"), f"票据 {invoice_id} 金额")
            if amount <= 0:
                raise ScenarioInputError("票据金额必须大于零。")
            tax = _money(item.get("tax", "0"), f"票据 {invoice_id} 税额")
            if tax < 0:
                raise ScenarioInputError("票据税额不能为负数。")
            total = _money(amount + tax, f"票据 {invoice_id} 含税金额")
            if item.get("total") is not None and _money(
                item["total"], f"票据 {invoice_id} 声明的含税金额"
            ) != total:
                raise ScenarioInputError(
                    f"票据 {invoice_id} 的含税金额与金额加税额不一致。",
                )
            if total <= 0:
                raise ScenarioInputError(f"票据 {invoice_id} 含税金额必须大于零。")
            invoices.append(
                {
                    "invoice_id": invoice_id,
                    "invoice_code": code,
                    "invoice_number": number,
                    "date": _optional_text(item.get("date"), 40),
                    "seller": _optional_text(item.get("seller"), 240),
                    "expense_category": _text(item.get("expense_category", values.get("expense_category", "")), "票据缺少费用类别。", 120),
                    "amount": _decimal_text(amount),
                    "tax": _decimal_text(tax),
                    "total": _decimal_text(total),
                    "source_kind": "user_provided_structured_invoice_data",
                    "invoice_authenticity_verified": False,
                },
            )
        if not invoices:
            raise ScenarioInputError("请提供至少一条结构化票据信息。")
        return _success(
            {
                "status": "structured_invoice_data_imported",
                "project_id": _optional_text(values.get("project_id"), 120),
                "source_kind": "user_provided_structured_invoice_data",
                "ocr_performed": False,
                "invoice_authenticity_verified": False,
                "invoices": invoices,
            },
        )

    if capability_key == "expense.invoice_verify_dedupe":
        imported = prior_results.get("expense.invoice_import")
        if not isinstance(imported, dict):
            raise ScenarioInputError("缺少上游结构化票据数据。")
        seen: dict[tuple[str, str], int] = {}
        invoices = []
        for invoice in imported["invoices"]:
            key = (invoice["invoice_code"].casefold(), invoice["invoice_number"].casefold())
            duplicate_of = seen.get(key)
            if duplicate_of is None:
                seen[key] = len(invoices)
            invoices.append(
                {
                    **invoice,
                    "duplicate": duplicate_of is not None,
                    "duplicate_of_index": duplicate_of,
                },
            )
        return _success(
            {
                "status": "deduplicated_within_task_input",
                "source_kind": imported["source_kind"],
                "official_invoice_verification_performed": False,
                "duplicate_count": sum(item["duplicate"] for item in invoices),
                "invoices": invoices,
            },
        )

    deduped = prior_results.get("expense.invoice_verify_dedupe")
    if not isinstance(deduped, dict):
        raise ScenarioInputError("缺少上游票据重复检查结果。")
    rules = _json_object(values, "expense_rules_json")
    rule_id = _text(rules.get("rule_id"), "费用规则需要 rule_id。", 120)
    allowed = _string_list(rules.get("allowed_categories", []), "allowed_categories", 50)
    if not allowed and not any(
        key in rules for key in ("max_per_invoice", "max_total")
    ):
        raise ScenarioInputError("费用规则至少需要一个类别或金额限制条件。")
    max_per_invoice = _money(rules.get("max_per_invoice", "999999999"), "单笔限额")
    if max_per_invoice < 0:
        raise ScenarioInputError("单笔限额不能为负数。")
    violations = []
    total = Decimal("0.00")
    for index, invoice in enumerate(deduped["invoices"]):
        amount = _money(invoice["total"], "票据含税金额")
        total += amount
        if allowed and invoice["expense_category"] not in allowed:
            violations.append({"invoice_id": invoice["invoice_id"], "code": "category_not_allowed"})
        if amount > max_per_invoice:
            violations.append({"invoice_id": invoice["invoice_id"], "code": "amount_over_limit"})
        if invoice["duplicate"]:
            violations.append({"invoice_id": invoice["invoice_id"], "code": "duplicate_in_input"})
    max_total_raw = rules.get("max_total")
    if max_total_raw is not None:
        max_total = _money(max_total_raw, "总额限额")
        if max_total < 0:
            raise ScenarioInputError("总额限额不能为负数。")
        if total > max_total:
            violations.append({"invoice_id": None, "code": "total_over_limit"})
    return _success(
        {
            "status": "draft_rule_check",
            "rule_id": rule_id,
            "source_kind": "user_provided_expense_rules",
            "invoices": deduped["invoices"],
            "total": _decimal_text(total),
            "currency": _optional_text(rules.get("currency"), 12) or "CNY",
            "violations": violations,
            "invoice_authenticity_verified": False,
            "formal_approval": False,
            "erp_submitted": False,
        },
    )


def _json_object(values: dict[str, str], key: str) -> dict[str, Any]:
    value = _parse_json_value(values.get(key, ""), key)
    if not isinstance(value, dict):
        raise ScenarioInputError(f"{key} 必须是 JSON 对象。")
    return value


def _json_list(values: dict[str, str], key: str, *, max_items: int) -> list[Any]:
    value = _parse_json_value(values.get(key, ""), key)
    if not isinstance(value, list) or len(value) > max_items:
        raise ScenarioInputError(f"{key} 必须是最多 {max_items} 项的 JSON 数组。")
    return value


def _parse_json_value(raw: str, key: str) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        raise ScenarioInputError(f"请填写 {key}。")
    if len(raw.encode("utf-8")) > 20_000:
        raise ScenarioInputError(f"{key} 超过 20 KB 限制。")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ScenarioInputError(f"{key} 不是有效且无重复键的 JSON。") from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 键")
        result[key] = value
    return result


def _text(value: Any, message: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ScenarioInputError(message)
    return value.strip()


def _optional_text(value: Any, limit: int) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ScenarioInputError("文本字段格式无效或过长。")
    return value.strip() or None


def _string_list(value: Any, label: str, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ScenarioInputError(f"{label} 必须是最多 {limit} 项的字符串数组。")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 240:
            raise ScenarioInputError(f"{label} 包含无效文本。")
        result.append(item.strip())
    return list(dict.fromkeys(result))


def _split_values(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("["):
        decoded = _parse_json_value(stripped, "列表输入")
        return _string_list(decoded, "列表输入", 200)
    return list(dict.fromkeys(item.strip() for item in re.split(r"[,，;；\r\n]+", value) if item.strip()))


def _professional_fields(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("["):
        decoded = _parse_json_value(stripped, "professional_fields")
        return _professional_field_values(decoded, "professional_fields")
    try:
        decoded = json.loads(f"[{stripped}]")
    except (json.JSONDecodeError, RecursionError):
        decoded = _split_values(stripped)
    return _professional_field_values(decoded, "professional_fields")


def _professional_field_values(value: Any, label: str) -> list[str]:
    """Normalize plain or quoted field names without changing field semantics."""

    values = _string_list(value, label, 32)
    normalized: list[str] = []
    quote_pairs = (
        ("\"", "\""),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
        ('\\"', '\\"'),
    )
    for item in values:
        field = item.strip()
        unwrapped = False
        for opening, closing in quote_pairs:
            if field.startswith(opening) and field.endswith(closing):
                field = field[len(opening) : -len(closing)].strip()
                unwrapped = True
                break
        if not unwrapped and (
            field.startswith(('"', "'", "“", "‘", '\\"'))
            or field.endswith(('"', "'", "”", "’", '\\"'))
        ):
            raise ScenarioInputError(f"{label} 包含未配对的引号。")
        if not field:
            raise ScenarioInputError(f"{label} 包含空专业领域。")
        normalized.append(field)
    return list(dict.fromkeys(normalized))


def _positive_integer(raw: Any, label: str) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as error:
        raise ScenarioInputError(f"{label}必须是正整数。") from error
    if value <= 0 or value > 100_000:
        raise ScenarioInputError(f"{label}超出允许范围。")
    return value


def _money(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ScenarioInputError(f"{label}必须是十进制金额。")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise ScenarioInputError(f"{label}不是有效金额。") from error
    if not amount.is_finite() or abs(amount) > Decimal("1000000000000"):
        raise ScenarioInputError(f"{label}超出安全金额范围。")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_rule_source(evidence: dict[str, Any]) -> bool:
    searchable = " ".join(
        str(evidence.get(key, ""))
        for key in ("source", "source_text")
    )
    return any(keyword in searchable for keyword in _RULE_KEYWORDS)


def _extension_category(extension: str) -> str:
    if extension in {"pdf", "doc", "docx", "rtf", "txt"}:
        return "文本材料"
    if extension in {"xls", "xlsx", "csv"}:
        return "表格材料"
    if extension in {"ppt", "pptx"}:
        return "演示材料"
    if extension in {"jpg", "jpeg", "png", "tif", "tiff"}:
        return "图像材料"
    return "其他材料"


def _success(result: dict[str, Any]) -> StepExecutionResult:
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return StepExecutionResult(text=text, result=result, status="completed")


def _failure(
    message: str,
    *,
    code: str,
    result: dict[str, Any] | None = None,
) -> StepExecutionResult:
    return StepExecutionResult(
        text=json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if result is not None
        else message,
        result=result,
        status="failed",
        reason=message,
        metadata={"scenario_capability_error": {"code": code, "message": message}},
    )


__all__ = [
    "ScenarioInputError",
    "execute_scenario_capability",
    "is_knowledge_native_capability",
    "is_scenario_native_capability",
    "is_task_rag_capability",
    "native_tool_schema",
    "retrieve_scenario_evidence",
]
