# -*- coding: utf-8 -*-
"""Bounded JSON Schema validation for Task structured results."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, SchemaError


_MAX_SCHEMA_BYTES = 16_000
_MAX_SCHEMA_DEPTH = 8
_MAX_SCHEMA_NODES = 128
_MAX_RESULT_BYTES = 64_000
_MAX_RESULT_DEPTH = 12
_MAX_RESULT_ITEMS = 4_096
_MAX_ERRORS = 8
_SUPPORTED_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_SUPPORTED_KEYWORDS = {
    "type",
    "title",
    "description",
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


class OutputSchemaDefinitionError(ValueError):
    """Raised for a schema outside Task's bounded supported subset."""


@dataclass(frozen=True, slots=True)
class OutputSchemaValidation:
    """Deterministic result of validating one adapter result."""

    passed: bool
    issues: tuple[str, ...] = ()
    target: str = "result"


def validate_output_schema_definition(schema: Any) -> None:
    """Validate a small, safe JSON Schema subset without running user code."""

    if not isinstance(schema, dict):
        raise OutputSchemaDefinitionError("Schema 根节点必须是 JSON 对象。")
    try:
        encoded = json.dumps(
            schema,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise OutputSchemaDefinitionError("Schema 必须是有限 JSON 数据。") from error
    if len(encoded.encode("utf-8")) > _MAX_SCHEMA_BYTES:
        raise OutputSchemaDefinitionError("Schema 超过 16 KB 限制。")

    node_count = [0]
    _validate_schema_node(schema, path="$", depth=1, node_count=node_count)
    if schema.get("type") != "object":
        raise OutputSchemaDefinitionError("结构化输出 Schema 根类型必须为 object。")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise OutputSchemaDefinitionError("Schema 未通过 JSON Schema 校验。") from error


def validate_structured_result(
    schema: dict[str, Any],
    result: Any,
    *,
    agent_output_json: bool = False,
    tool_output_text: str | None = None,
) -> OutputSchemaValidation:
    """Check a Task result object or the JSON text returned by a ToolStep."""

    target = "result"
    if tool_output_text is not None:
        target = "tool_output_json"
        if len(tool_output_text.encode("utf-8")) > _MAX_RESULT_BYTES:
            return OutputSchemaValidation(
                False,
                ("Tool 输出超过 64 KB 限制。",),
                target=target,
            )
        try:
            result = json.loads(tool_output_text)
        except (json.JSONDecodeError, RecursionError):
            return OutputSchemaValidation(
                False,
                ("Tool 输出必须是合法 JSON。",),
                target=target,
            )
    elif (
        agent_output_json
        and isinstance(result, dict)
        and set(result) == {"status", "output", "reason", "questions"}
        and result.get("status") in {"completed", "needs_input", "failed"}
        and isinstance(result.get("output"), str)
    ):
        try:
            result = json.loads(result["output"])
        except (json.JSONDecodeError, RecursionError):
            return OutputSchemaValidation(
                False,
                ("result.output: Agent 结构化输出必须是合法 JSON。",),
                target="agent_output_json",
            )
        target = "agent_output_json"

    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        return OutputSchemaValidation(
            False,
            ("result: 结果不是可持久化的 JSON 数据。",),
            target=target,
        )
    if len(encoded.encode("utf-8")) > _MAX_RESULT_BYTES:
        return OutputSchemaValidation(
            False,
            ("result: 结构化结果超过 64 KB 限制。",),
            target=target,
        )

    try:
        normalized = json.loads(encoded)
    except json.JSONDecodeError:  # pragma: no cover - json.dumps output is valid
        return OutputSchemaValidation(
            False,
            ("result: 结果不是有效 JSON。",),
            target=target,
        )
    reason = _result_limit_issue(normalized)
    if reason:
        return OutputSchemaValidation(
            False,
            (f"result: {reason}",),
            target=target,
        )

    errors: list[str] = []
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(normalized):
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        errors.append(f"{path}: {error.validator or 'schema'}")
        if len(errors) >= _MAX_ERRORS:
            break
    if errors:
        return OutputSchemaValidation(False, tuple(errors), target=target)
    return OutputSchemaValidation(True, target=target)


def describe_output_schema(schema: dict[str, Any]) -> str:
    """Render the supported schema subset as prompt-safe prose."""

    lines = ["根结果必须是 JSON 对象。"]
    _describe_schema_node(schema, "根对象", lines, depth=0)
    return "\n".join(lines)


def _validate_schema_node(
    schema: Any,
    *,
    path: str,
    depth: int,
    node_count: list[int],
) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise OutputSchemaDefinitionError("Schema 嵌套深度不能超过 8 层。")
    node_count[0] += 1
    if node_count[0] > _MAX_SCHEMA_NODES:
        raise OutputSchemaDefinitionError("Schema 字段总数不能超过 128 个。")
    if not isinstance(schema, dict):
        raise OutputSchemaDefinitionError(f"{path} 必须是 Schema 对象。")
    unsupported = set(schema) - _SUPPORTED_KEYWORDS
    if unsupported:
        keyword = sorted(unsupported)[0]
        raise OutputSchemaDefinitionError(
            f"{path}.{keyword} 不属于当前支持的 Schema 特性。"
        )
    if schema.get("type") not in _SUPPORTED_TYPES:
        raise OutputSchemaDefinitionError(f"{path}.type 不是支持的 JSON 类型。")
    for label in ("title", "description"):
        if label in schema and (
            not isinstance(schema[label], str) or len(schema[label]) > 500
        ):
            raise OutputSchemaDefinitionError(
                f"{path}.{label} 必须是 500 字符以内的文本。"
            )

    schema_type = schema["type"]
    allowed_by_type = {
        "object": {"properties", "required", "additionalProperties"},
        "array": {"items", "minItems", "maxItems"},
        "string": {"minLength", "maxLength"},
        "integer": {"minimum", "maximum"},
        "number": {"minimum", "maximum"},
        "boolean": set(),
        "null": set(),
    }
    invalid = set(schema) - {"type", "title", "description", "enum"} - allowed_by_type[schema_type]
    if invalid:
        keyword = sorted(invalid)[0]
        raise OutputSchemaDefinitionError(
            f"{path}.{keyword} 与类型 {schema_type} 不兼容。"
        )

    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 32:
            raise OutputSchemaDefinitionError(
                f"{path}.properties 最多支持 32 个字段。"
            )
        for key, child in properties.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 120
                or "{" in key
                or "}" in key
            ):
                raise OutputSchemaDefinitionError(
                    f"{path}.properties 包含无效字段名。"
                )
            _validate_schema_node(
                child,
                path=f"{path}.properties.{key}",
                depth=depth + 1,
                node_count=node_count,
            )
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or len(required) > 32
            or any(not isinstance(key, str) for key in required)
            or len(required) != len(set(required))
            or not set(required) <= set(properties)
        ):
            raise OutputSchemaDefinitionError(
                f"{path}.required 必须引用已声明且不重复的字段。"
            )
        if "additionalProperties" in schema and not isinstance(
            schema["additionalProperties"], bool
        ):
            raise OutputSchemaDefinitionError(
                f"{path}.additionalProperties 仅支持布尔值。"
            )
    elif schema_type == "array":
        if "items" not in schema:
            raise OutputSchemaDefinitionError(f"{path}.items 是必需字段。")
        _validate_schema_node(
            schema["items"],
            path=f"{path}.items",
            depth=depth + 1,
            node_count=node_count,
        )
        _validate_integer_bound(schema, path, "minItems")
        _validate_integer_bound(schema, path, "maxItems")
        _validate_ordered_bounds(schema, path, "minItems", "maxItems")
    elif schema_type == "string":
        _validate_integer_bound(schema, path, "minLength")
        _validate_integer_bound(schema, path, "maxLength")
        _validate_ordered_bounds(schema, path, "minLength", "maxLength")
    elif schema_type in {"integer", "number"}:
        for keyword in ("minimum", "maximum"):
            if keyword in schema and (
                isinstance(schema[keyword], bool)
                or not isinstance(schema[keyword], (int, float))
                or not math.isfinite(schema[keyword])
            ):
                raise OutputSchemaDefinitionError(
                    f"{path}.{keyword} 必须是有限数字。"
                )
        _validate_ordered_bounds(schema, path, "minimum", "maximum")

    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise OutputSchemaDefinitionError(
                f"{path}.enum 必须包含 1 至 32 个 JSON 标量值。"
            )
        for value in values:
            if isinstance(value, (dict, list)) or not (
                value is None
                or isinstance(value, (str, int, float, bool))
            ):
                raise OutputSchemaDefinitionError(
                    f"{path}.enum 仅支持 JSON 标量值。"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise OutputSchemaDefinitionError(
                    f"{path}.enum 不支持非有限数字。"
                )
            if isinstance(value, str) and len(value) > 1_000:
                raise OutputSchemaDefinitionError(
                    f"{path}.enum 文本不能超过 1000 字符。"
                )


def _validate_integer_bound(schema: dict[str, Any], path: str, key: str) -> None:
    if key in schema and (
        isinstance(schema[key], bool)
        or not isinstance(schema[key], int)
        or schema[key] < 0
    ):
        raise OutputSchemaDefinitionError(
            f"{path}.{key} 必须是非负整数。"
        )


def _validate_ordered_bounds(
    schema: dict[str, Any], path: str, lower: str, upper: str
) -> None:
    if lower in schema and upper in schema and schema[lower] > schema[upper]:
        raise OutputSchemaDefinitionError(
            f"{path}.{lower} 不能大于 {upper}。"
        )


def _result_limit_issue(value: Any) -> str | None:
    item_count = [0]

    def visit(item: Any, depth: int) -> bool:
        if depth > _MAX_RESULT_DEPTH:
            return False
        if isinstance(item, dict):
            item_count[0] += len(item)
            return item_count[0] <= _MAX_RESULT_ITEMS and all(
                visit(child, depth + 1) for child in item.values()
            )
        if isinstance(item, list):
            item_count[0] += len(item)
            return item_count[0] <= _MAX_RESULT_ITEMS and all(
                visit(child, depth + 1) for child in item
            )
        return True

    return None if visit(value, 1) else "超过 12 层嵌套或 4096 个字段/数组项。"


def _describe_schema_node(
    schema: dict[str, Any],
    label: str,
    lines: list[str],
    *,
    depth: int,
) -> None:
    schema_type = schema["type"]
    constraints: list[str] = []
    if "enum" in schema:
        constraints.append(
            "允许值："
            + "、".join(_safe_prompt_value(value) for value in schema["enum"])
        )
    for key, display in (
        ("minimum", "最小值"),
        ("maximum", "最大值"),
        ("minLength", "最短字符数"),
        ("maxLength", "最长字符数"),
        ("minItems", "最少项目数"),
        ("maxItems", "最多项目数"),
    ):
        if key in schema:
            constraints.append(f"{display} {schema[key]}")
    lines.append(
        f"{'  ' * depth}- {label}：{schema_type}"
        + (f"（{'；'.join(constraints)}）" if constraints else "")
    )
    if schema_type == "object":
        required = set(schema.get("required", []))
        for name, child in schema.get("properties", {}).items():
            _describe_schema_node(child, name, lines, depth=depth + 1)
            if name in required:
                lines[-1] += "（必填字段）"
        if schema.get("additionalProperties") is False:
            lines.append(f"{'  ' * (depth + 1)}- 不允许未声明字段")
    elif schema_type == "array" and "items" in schema:
        _describe_schema_node(
            schema["items"],
            "每个数组项目",
            lines,
            depth=depth + 1,
        )


def _safe_prompt_value(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return encoded.replace("{", "\\u007b").replace("}", "\\u007d")
