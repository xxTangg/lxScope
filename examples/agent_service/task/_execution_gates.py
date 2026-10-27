# -*- coding: utf-8 -*-
"""Deterministic Task node output gates applied before AI acceptance."""

from __future__ import annotations

from typing import Any

from ._capability_contracts import capability_contract, validate_capability_output
from ._executor import StepExecutionResult
from ._models import StepType, TaskNode
from ._output_validation import validate_structured_result


def enforce_json_output_contract(
    node: TaskNode,
    result: StepExecutionResult,
) -> StepExecutionResult:
    """Validate a frozen JSON output contract before a node can succeed.

    The model/tool result remains available for diagnostics on failure. A
    successful structured result is normalized into the persisted result so
    downstream ``validated_result`` bindings never parse display text.
    """

    if result.status != "completed":
        return result

    contract = node.effective_output_contract
    schema = None
    if contract is not None and contract.validation_mode == "json_schema":
        schema = contract.json_schema
    elif node.output_contract is None:
        # ``output_schema`` predates NodeOutputContract and remains a supported
        # frozen Task contract for old Runs and Tool adapters.
        schema = node.output_schema
    if schema is None:
        return result

    if node.type is StepType.TOOL:
        capability = capability_contract(node.capability_key)
        if (
            capability is not None
            and capability.output_schema == schema
        ):
            validation = validate_capability_output(
                node.capability_key,
                schema,
                result.result,
                result.text,
            )
        else:
            validation = validate_structured_result(
                schema,
                result.result,
                tool_output_text=result.text,
            )
    else:
        validation = validate_structured_result(
            schema,
            result.result if result.result is not None else result.text,
            agent_output_json=node.type is StepType.AGENT,
        )

    schema_record = {
        "status": "passed" if validation.passed else "failed",
        "target": validation.target,
        "issues": list(validation.issues),
    }
    metadata = dict(result.metadata or {})
    metadata["output_schema_validation"] = schema_record
    metadata["output_contract_validation"] = {
        "contract_id": (
            contract.contract_id
            if contract is not None
            else "task.output-schema"
        ),
        "contract_version": contract.contract_version if contract else 1,
        "validation_mode": "json_schema",
        **schema_record,
    }
    if not validation.passed:
        # Agent output can be regenerated safely by the existing bounded SOP
        # retry policy. Tool and Python outputs may follow external effects,
        # so their contract failures remain terminal in this attempt.
        if node.type is StepType.AGENT:
            return StepExecutionResult(
                text=result.text,
                result=result.result,
                metadata=metadata,
                status=result.status,
                questions=result.questions,
                reason="结构化结果校验失败：" + "；".join(validation.issues),
            )
        return StepExecutionResult(
            text=result.text,
            result=result.result,
            metadata=metadata,
            status="failed",
            questions=result.questions,
            reason="结构化结果校验失败：" + "；".join(validation.issues),
        )

    return StepExecutionResult(
        text=result.text,
        result=validation.value,
        metadata=metadata,
        status=result.status,
        questions=result.questions,
        reason=result.reason,
    )


def contract_validation_record(
    *,
    contract: Any,
    passed: bool,
    target: str,
    issues: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Create the stable audit representation for non-JSON contracts."""

    return {
        "contract_id": contract.contract_id,
        "contract_version": contract.contract_version,
        "validation_mode": contract.validation_mode,
        "status": "passed" if passed else "failed",
        "target": target,
        "issues": list(issues),
    }
