"""Task-owned runtime error classification and run/node correlation."""

from __future__ import annotations

from typing import Any

from ._models import (
    TaskErrorCategory,
    TaskErrorDetail,
    TaskNode,
    TaskRunRecord,
)


def classify_node_error(
    run: TaskRunRecord,
    node: TaskNode,
    metadata: dict[str, Any] | None,
    message: str,
    *,
    artifact_failure: bool = False,
) -> TaskErrorDetail:
    """Classify a failed SOP node without altering engine progression."""

    node_metadata = metadata or {}
    category: TaskErrorCategory = "execution_failed"
    code = "TASK_NODE_EXECUTION_FAILED"
    details: dict[str, Any] = {}

    if artifact_failure:
        category = "artifact_generation_failed"
        code = "TASK_ARTIFACT_GENERATION_FAILED"
    elif (
        isinstance(node_metadata.get("agent_outcome_protocol"), dict)
        and node_metadata["agent_outcome_protocol"].get("status") == "rejected"
    ):
        category = "schema_mismatch"
        code = "TASK_AGENT_OUTCOME_PROTOCOL_INVALID"
        protocol_code = node_metadata["agent_outcome_protocol"].get("code")
        if isinstance(protocol_code, str):
            details["protocol_code"] = protocol_code[:120]
        question_count = node_metadata["agent_outcome_protocol"].get(
            "question_count"
        )
        if isinstance(question_count, int) and not isinstance(question_count, bool):
            details["question_count"] = max(0, min(question_count, 16))
    elif node_metadata.get("knowledge_error_code"):
        source_error_code = str(node_metadata["knowledge_error_code"])
        if source_error_code == "knowledge_base_unavailable":
            category = "permission_denied"
            code = "TASK_KNOWLEDGE_PERMISSION_DENIED"
        elif source_error_code == "source_material_not_found":
            category = "data_source_not_ready"
            code = "TASK_KNOWLEDGE_SOURCE_NOT_FOUND"
        elif source_error_code == "source_material_not_ready":
            category = "data_source_not_ready"
            code = "TASK_KNOWLEDGE_DOCUMENT_NOT_READY"
        else:
            category = "data_source_not_ready"
            code = "TASK_DATA_SOURCE_NOT_READY"
        details["source_error_code"] = source_error_code[:120]
    else:
        schema_result = node_metadata.get("output_schema_validation") or (
            node_metadata.get("capability_output_validation")
        )
        contract_result = node_metadata.get("output_contract_validation")
        if (
            isinstance(contract_result, dict)
            and contract_result.get("status") == "failed"
        ):
            if contract_result.get("target") == "claim_evidence_document":
                category = "citation_verification_failed"
                code = "TASK_EVIDENCE_LEDGER_FAILED"
            else:
                category = "schema_mismatch"
                code = "TASK_OUTPUT_SCHEMA_MISMATCH"
        if isinstance(schema_result, dict) and schema_result.get("status") == "failed":
            category = "schema_mismatch"
            code = "TASK_OUTPUT_SCHEMA_MISMATCH"
        citation_result = node_metadata.get("citation_verification")
        if (
            isinstance(citation_result, dict)
            and citation_result.get("passed") is False
        ):
            category = "citation_verification_failed"
            code = "TASK_CITATION_VERIFICATION_FAILED"
        ledger_result = node_metadata.get("evidence_ledger")
        if (
            isinstance(ledger_result, dict)
            and ledger_result.get("passed") is False
        ):
            category = "citation_verification_failed"
            code = "TASK_EVIDENCE_LEDGER_FAILED"
            diagnostics = ledger_result.get("diagnostics")
            if isinstance(diagnostics, dict):
                details["diagnostic_groups"] = {
                    str(key): len(value)
                    for key, value in diagnostics.items()
                    if isinstance(value, list) and value
                }
        if code == "TASK_NODE_EXECUTION_FAILED":
            normalized = message.casefold()
            if any(
                marker in normalized
                for marker in (
                    "permission denied",
                    "forbidden",
                    "permission ",
                    "权限不足",
                    "无权",
                    "拒绝访问",
                )
            ):
                category = "permission_denied"
                code = "TASK_PERMISSION_DENIED"
            elif any(
                marker in normalized
                for marker in (
                    "invalid arguments",
                    "schema",
                    "契约校验失败",
                    "参数校验失败",
                )
            ):
                category = "schema_mismatch"
                code = "TASK_INPUT_SCHEMA_MISMATCH"
            elif any(
                marker in normalized
                for marker in (
                    "tool ",
                    "tool '",
                    "工具 ",
                )
            ) and any(
                marker in normalized
                for marker in (
                    "not available",
                    "not found",
                    "缺少",
                    "不存在",
                    "未注册",
                )
            ):
                category = "missing_tool"
                code = "TASK_TOOL_MISSING"
            elif any(
                marker in normalized
                for marker in ("required field", "必填输入", "缺少输入")
            ):
                category = "input_incomplete"
                code = "TASK_INPUT_INCOMPLETE"
            elif node.type.value == "tool":
                category = "external_interface_failed"
                code = "TASK_TOOL_EXECUTION_FAILED"
            capability = node_metadata.get("capability_key")
            if isinstance(capability, str):
                details["capability_key"] = capability[:120]

    return TaskErrorDetail(
        category=category,
        code=code,
        message=(message or "Task 节点执行失败。")[:4_000],
        task_id=run.task_id,
        run_id=run.id,
        node_id=node.id,
        details=details,
    )


__all__ = ["classify_node_error"]
