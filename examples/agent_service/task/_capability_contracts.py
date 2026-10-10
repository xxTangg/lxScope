# -*- coding: utf-8 -*-
"""Task-owned business capability contracts, never a Tool registry.

The contracts describe stable logical requirements. Actual Tool/MCP lookup and
binding remain owned by ``TaskTemplateCompiler`` and the AgentScope Toolkit.
The expert contracts are deliberately unverified until a business system is
integrated and accepted by a later stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from ._output_validation import (
    OutputSchemaValidation,
    validate_output_schema_definition,
    validate_structured_result,
)


_EXPERT_EVIDENCE_REF = {
    "type": "object",
    "required": ["source_id", "record_version", "field"],
    "properties": {
        "source_id": {"type": "string", "minLength": 1, "maxLength": 240},
        "record_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
        "field": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "additionalProperties": False,
}
_EXPERT_SOURCE = {
    "type": "object",
    "required": ["system_id", "dataset_id", "data_version", "queried_at"],
    "properties": {
        "system_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "dataset_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "data_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
        "queried_at": {"type": "string", "minLength": 1, "maxLength": 80},
    },
    "additionalProperties": False,
}
_PROJECT_PROFILE = {
    "type": "object",
    "required": ["project_name", "project_brief", "professional_fields"],
    "properties": {
        "project_name": {"type": "string", "minLength": 1, "maxLength": 500},
        "project_brief": {"type": "string", "minLength": 1, "maxLength": 8000},
        "professional_fields": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
            "minItems": 1,
            "maxItems": 32,
        },
        "exclusion_rules": {"type": "string", "maxLength": 2000},
    },
    "additionalProperties": False,
}
_PROJECT_QUERY = {
    "type": "object",
    "required": ["project_profile", "candidate_limit"],
    "properties": {
        "project_profile": _PROJECT_PROFILE,
        "candidate_limit": {"type": "integer", "minimum": 1, "maximum": 20},
        "filters": {
            "type": "object",
            "properties": {
                "regions": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 120},
                    "maxItems": 32,
                },
                "organization_ids": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 120},
                    "maxItems": 64,
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
_CANDIDATE = {
    "type": "object",
    "required": ["expert_id", "authorized_fields", "evidence_refs"],
    "properties": {
        "expert_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "authorized_fields": {"type": "object", "additionalProperties": True},
        "evidence_refs": {
            "type": "array",
            "items": _EXPERT_EVIDENCE_REF,
            "maxItems": 64,
        },
    },
    "additionalProperties": False,
}
_LOOKUP_OUTPUT = {
    "type": "object",
    "required": ["contract_version", "source", "query_context", "candidates"],
    "properties": {
        "contract_version": {
            "type": "string",
            "enum": ["task.expert-capability.v1"],
        },
        "source": _EXPERT_SOURCE,
        "query_context": _PROJECT_QUERY,
        "candidates": {"type": "array", "items": _CANDIDATE, "maxItems": 100},
    },
    "additionalProperties": False,
}
_CONFLICT_CANDIDATE = {
    "type": "object",
    "required": ["expert_id", "conflict_status", "evidence_refs"],
    "properties": {
        "expert_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "conflict_status": {
            "type": "string",
            "enum": ["clear", "conflict", "unknown"],
        },
        "evidence_refs": {
            "type": "array",
            "items": _EXPERT_EVIDENCE_REF,
            "maxItems": 64,
        },
    },
    "additionalProperties": False,
}
_CONFLICT_OUTPUT = {
    "type": "object",
    "required": ["contract_version", "source", "rule_version", "checked_at", "candidates"],
    "properties": {
        "contract_version": {
            "type": "string",
            "enum": ["task.expert-capability.v1"],
        },
        "source": _EXPERT_SOURCE,
        "rule_version": {"type": "string", "minLength": 1, "maxLength": 120},
        "checked_at": {"type": "string", "minLength": 1, "maxLength": 80},
        "candidates": {
            "type": "array",
            "items": _CONFLICT_CANDIDATE,
            "maxItems": 100,
        },
    },
    "additionalProperties": False,
}
_RANK_CANDIDATE = {
    "type": "object",
    "required": [
        "expert_id",
        "conflict_status",
        "rank",
        "score",
        "score_components",
        "evidence_refs",
    ],
    "properties": {
        "expert_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "conflict_status": {"type": "string", "enum": ["clear"]},
        "rank": {"type": "integer", "minimum": 1, "maximum": 100},
        "score": {
            "type": "number",
            "minimum": 0,
            "description": "分值区间和权重须由业务方确认。",
        },
        "score_components": {"type": "object", "additionalProperties": True},
        "evidence_refs": {
            "type": "array",
            "items": _EXPERT_EVIDENCE_REF,
            "maxItems": 64,
        },
    },
    "additionalProperties": False,
}
_RANK_OUTPUT = {
    "type": "object",
    "required": [
        "contract_version",
        "source",
        "ranking_algorithm_version",
        "scored_at",
        "candidates",
    ],
    "properties": {
        "contract_version": {
            "type": "string",
            "enum": ["task.expert-capability.v1"],
        },
        "source": _EXPERT_SOURCE,
        "ranking_algorithm_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
        "scored_at": {"type": "string", "minLength": 1, "maxLength": 80},
        "candidates": {
            "type": "array",
            "items": _RANK_CANDIDATE,
            "maxItems": 100,
        },
    },
    "additionalProperties": False,
}
_CONFLICT_INPUT = {
    "type": "object",
    "required": ["contract_version", "source", "query_context", "candidates"],
    "properties": {
        "contract_version": _LOOKUP_OUTPUT["properties"]["contract_version"],
        "source": _EXPERT_SOURCE,
        "query_context": _PROJECT_QUERY,
        "candidates": {"type": "array", "items": _CANDIDATE, "maxItems": 100},
    },
    "additionalProperties": False,
}
_RANK_INPUT = {
    "type": "object",
    "required": ["contract_version", "source", "rule_version", "checked_at", "candidates"],
    "properties": {
        "contract_version": _CONFLICT_OUTPUT["properties"]["contract_version"],
        "source": _EXPERT_SOURCE,
        "rule_version": {"type": "string", "minLength": 1, "maxLength": 120},
        "checked_at": {"type": "string", "minLength": 1, "maxLength": 80},
        "candidates": {
            "type": "array",
            "items": _CONFLICT_CANDIDATE,
            "maxItems": 100,
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class BusinessCapabilityContract:
    """Logical capability contract independent of any concrete Tool name."""

    capability_key: str
    category: str
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    required_permissions: tuple[str, ...]
    is_read_only: bool
    external_side_effect: bool
    business_data_verified: bool = False


EXPERT_CAPABILITY_CONTRACTS: dict[str, BusinessCapabilityContract] = {
    "expert.lookup": BusinessCapabilityContract(
        capability_key="expert.lookup",
        category="数据查询",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": _PROJECT_QUERY},
            "additionalProperties": False,
        },
        output_schema=_LOOKUP_OUTPUT,
        required_permissions=("专家目录查询权限",),
        is_read_only=True,
        external_side_effect=False,
    ),
    "expert.conflict_check": BusinessCapabilityContract(
        capability_key="expert.conflict_check",
        category="规则校验",
        input_schema={
            "type": "object",
            "required": ["candidates"],
            "properties": {"candidates": _CONFLICT_INPUT},
            "additionalProperties": False,
        },
        output_schema=_CONFLICT_OUTPUT,
        required_permissions=("专家回避规则查询权限",),
        is_read_only=True,
        external_side_effect=False,
    ),
    "expert.rank": BusinessCapabilityContract(
        capability_key="expert.rank",
        category="确定性计算",
        input_schema={
            "type": "object",
            "required": ["candidates"],
            "properties": {"candidates": _RANK_INPUT},
            "additionalProperties": False,
        },
        output_schema=_RANK_OUTPUT,
        required_permissions=("专家评分规则查询权限",),
        is_read_only=True,
        external_side_effect=False,
    ),
}

_CATEGORY_BY_PREFIX = {
    "archive.document_metadata": "文件处理",
    "archive.completeness_check": "规则校验",
    "archive.write": "业务系统写入",
    "document.citation_verify": "规则校验",
    "document.render": "文件处理",
    "expense.erp_submit": "业务系统写入",
    "expense.invoice_ocr": "OCR/ASR",
    "expense.invoice_verify_dedupe": "规则校验",
    "expense.rule_check": "规则校验",
    "expert.conflict_check": "规则校验",
    "expert.lookup": "数据查询",
    "expert.rank": "确定性计算",
    "meeting.audio_transcribe": "OCR/ASR",
    "meeting.cost_calculate": "确定性计算",
    "meeting.material_check": "规则校验",
    "meeting.material_generate": "文件处理",
    "meeting.minutes_export": "文件处理",
    "notification.send": "消息通知",
    "opinion.classify": "规则校验",
    "opinion.export": "文件处理",
    "opinion.image_ocr": "OCR/ASR",
    "planning.template_retrieve": "数据查询",
    "project.authorization.read": "数据查询",
    "project.rule_check": "规则校验",
    "project.similar_search": "数据查询",
    "report.citation_check": "规则校验",
    "report.compliance_check": "规则校验",
    "report.consistency_check": "规则校验",
    "report.rag_retrieve": "数据查询",
}

_UNVERIFIED_BUSINESS_CAPABILITIES = frozenset(_CATEGORY_BY_PREFIX)
_SIDE_EFFECT_CAPABILITIES = frozenset(
    {
        "archive.write",
        "expense.erp_submit",
        "notification.send",
    }
)
_TASK_ARTIFACT_CAPABILITIES = frozenset(
    {"document.render", "meeting.minutes_export"}
)
_TASK_NATIVE_CAPABILITIES = frozenset(
    {"planning.template_retrieve", "document.citation_verify"}
)


def capability_category(capability_key: str) -> str:
    """Return the Task catalog category, or an explicit unknown category."""

    return _CATEGORY_BY_PREFIX.get(capability_key, "unsupported")


def capability_contract(
    capability_key: str | None,
) -> BusinessCapabilityContract | None:
    """Get a strict candidate contract where one has been defined."""

    if capability_key is None:
        return None
    return EXPERT_CAPABILITY_CONTRACTS.get(capability_key)


def capability_business_verified(capability_key: str | None) -> bool | None:
    """Return false for unverified business data sources; None is nonbusiness."""

    if capability_key in _UNVERIFIED_BUSINESS_CAPABILITIES:
        return False
    return None


def capability_external_side_effect(capability_key: str | None) -> bool:
    """Return only the declared Task catalog side-effect classification."""

    return capability_key in _SIDE_EFFECT_CAPABILITIES


def is_task_artifact_capability(capability_key: str) -> bool:
    """Whether an unbound logical capability is provided by Task artifacts."""

    return capability_key in _TASK_ARTIFACT_CAPABILITIES


def is_task_native_capability(capability_key: str | None) -> bool:
    """Whether Task itself provides a scoped implementation.

    Native capabilities are confined to the Task execution boundary.
    """

    return capability_key in _TASK_NATIVE_CAPABILITIES


def validate_contract_tool_schema(
    capability_key: str,
    actual_schema: dict[str, Any],
) -> tuple[str, ...]:
    """Check whether the registered Tool input schema accepts the contract."""

    contract = capability_contract(capability_key)
    if contract is None or contract.input_schema is None:
        return ()
    try:
        Draft202012Validator.check_schema(actual_schema)
        Draft202012Validator.check_schema(contract.input_schema)
        validate_output_schema_definition(contract.input_schema)
    except SchemaError:
        return ("Tool 输入 Schema 无效，无法与业务能力契约比对。",)
    except ValueError:
        return ("业务能力输入契约无效，无法与 Tool Schema 比对。",)
    return _schema_acceptance_issues(
        actual_schema,
        contract.input_schema,
        path="input_schema",
    )


def validate_capability_input(
    capability_key: str | None,
    arguments: Any,
) -> tuple[str, ...]:
    """Validate structured S03 Tool arguments before invoking the Tool."""

    contract = capability_contract(capability_key)
    if contract is None or contract.input_schema is None:
        return ()
    try:
        errors = list(
            Draft202012Validator(contract.input_schema).iter_errors(arguments)
        )
    except (TypeError, ValueError):
        return ("能力输入不是有效 JSON 数据。",)
    issues = tuple(
        f"{_path(error.absolute_path)}: {error.validator or 'schema'}"
        for error in errors[:8]
    )
    if issues:
        return issues
    if capability_key == "expert.rank":
        payload = arguments.get("candidates", {})
        candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
        if any(
            candidate.get("conflict_status") == "unknown"
            for candidate in candidates
            if isinstance(candidate, dict)
        ):
            return ("存在回避状态 unknown 的候选人，评分步骤已拒绝。",)
    return ()


def validate_capability_output(
    capability_key: str | None,
    schema: dict[str, Any],
    result: Any,
    output_text: str,
) -> OutputSchemaValidation:
    """Validate strict Tool JSON output and fail closed on unknown conflicts."""

    validation = validate_structured_result(
        schema,
        result,
        tool_output_text=output_text,
    )
    if not validation.passed or capability_key != "expert.conflict_check":
        return validation

    import json

    try:
        payload = json.loads(output_text)
    except (json.JSONDecodeError, RecursionError):  # already checked above
        return OutputSchemaValidation(
            False,
            ("Tool 输出不是合法 JSON。",),
            target="tool_output_json",
        )
    candidates = payload.get("candidates", [])
    if any(
        item.get("conflict_status") == "unknown"
        for item in candidates
        if isinstance(item, dict)
    ):
        return OutputSchemaValidation(
            False,
            ("回避状态存在 unknown，不能继续生成推荐。",),
            target="tool_output_json",
        )
    return validation


def _schema_acceptance_issues(
    actual: dict[str, Any],
    contract: dict[str, Any],
    *,
    path: str,
) -> tuple[str, ...]:
    """Conservatively prove contract inputs are accepted by a Tool schema."""

    issues: list[str] = []
    allowed_keywords = {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "enum",
    }
    unsupported = set(actual) - allowed_keywords
    if unsupported:
        issues.append(
            f"{path} 包含无法安全比对的 Schema 限制：{sorted(unsupported)[0]}。"
        )

    contract_type = contract.get("type")
    actual_type = actual.get("type")
    if contract_type is not None and actual_type is None:
        issues.append(f"{path}.type 未声明，不能确认契约类型兼容。")
    elif contract_type is not None and actual_type is not None:
        actual_types = set(actual_type) if isinstance(actual_type, list) else {actual_type}
        if contract_type not in actual_types:
            issues.append(f"{path}.type 不接受契约类型 {contract_type}。")

    contract_required = set(contract.get("required", []))
    actual_required = set(actual.get("required", []))
    if actual_required - contract_required:
        issues.append(f"{path}.required 要求契约未保证的字段。")

    contract_properties = contract.get("properties", {})
    actual_properties = actual.get("properties", {})
    if not isinstance(contract_properties, dict) or not isinstance(actual_properties, dict):
        return tuple(issues + [f"{path}.properties 结构无效。"])
    if (
        contract.get("additionalProperties", True) is not False
        and actual.get("additionalProperties") is False
    ):
        issues.append(f"{path}.additionalProperties 限制了契约允许的输入字段。")
    if actual.get("additionalProperties") is False and set(contract_properties) - set(actual_properties):
        issues.append(f"{path}.additionalProperties 拒绝契约声明的字段。")
    for name, contract_child in contract_properties.items():
        actual_child = actual_properties.get(name)
        if actual_child is None:
            issues.append(f"{path}.properties.{name} 未在 Tool Schema 中声明。")
            continue
        if not isinstance(contract_child, dict) or not isinstance(actual_child, dict):
            issues.append(f"{path}.properties.{name} Schema 结构无效。")
            continue
        issues.extend(
            _schema_acceptance_issues(
                actual_child,
                contract_child,
                path=f"{path}.properties.{name}",
            )
        )

    contract_items = contract.get("items")
    actual_items = actual.get("items")
    if contract_items is not None:
        if not isinstance(contract_items, dict) or not isinstance(actual_items, dict):
            issues.append(f"{path}.items 无法确认接受契约数组项目。")
        else:
            issues.extend(
                _schema_acceptance_issues(
                    actual_items,
                    contract_items,
                    path=f"{path}.items",
                )
            )

    for keyword, stricter_direction in (
        ("minimum", "lower"),
        ("minLength", "lower"),
        ("minItems", "lower"),
        ("maximum", "upper"),
        ("maxLength", "upper"),
        ("maxItems", "upper"),
    ):
        actual_bound = actual.get(keyword)
        contract_bound = contract.get(keyword)
        if actual_bound is None:
            continue
        if contract_bound is None:
            issues.append(f"{path}.{keyword} 比契约额外收窄输入范围。")
        elif stricter_direction == "lower" and actual_bound > contract_bound:
            issues.append(f"{path}.{keyword} 比契约额外收窄输入范围。")
        elif stricter_direction == "upper" and actual_bound < contract_bound:
            issues.append(f"{path}.{keyword} 比契约额外收窄输入范围。")

    actual_enum = actual.get("enum")
    contract_enum = contract.get("enum")
    if actual_enum is not None and (
        contract_enum is None or not set(contract_enum) <= set(actual_enum)
    ):
        issues.append(f"{path}.enum 限制了契约允许的值。")
    return tuple(issues)


def _path(parts: Any) -> str:
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in parts
    )
