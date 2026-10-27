# -*- coding: utf-8 -*-
"""Application service for reusable linear Tasks."""

import asyncio
import hashlib
import json
import logging
import os
import re
from uuid import uuid4
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any, Literal

from agentscope.event import CustomEvent
from agentscope.message import UserMsg
from agentscope.sop import SOP, SOPEngine, SOPPhase, SOPRunState
from datetime import datetime, timedelta, timezone
from pydantic import ValidationError

from ._artifact_spec import (
    ArtifactSpec,
    artifact_spec_prompt,
    parse_agent_spec,
    spec_from_markdown,
)
from ._artifacts import (
    GeneratedArtifact,
    artifact_renderer_available,
    generate_artifact,
)
from ._executor import (
    PreviewTaskExecutor,
    StepExecutionResult,
    TaskArtifactWriter,
    TaskExecutor,
)
from ._models import (
    AgentStepConfig,
    CreateTaskRequest,
    ExecutionContext,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    TaskArtifactRecord,
    TaskCapabilityCheck,
    TaskContext,
    TaskEventRecord,
    TaskEventDraft,
    TaskErrorCategory,
    TaskErrorDetail,
    TaskGenerationStatus,
    TaskGraph,
    TaskGraphEdge,
    TaskDagCapabilitiesResponse,
    TaskNode,
    TaskRecord,
    TaskWorkflowMode,
    TaskToolSchema,
    TaskRunRecord,
    TaskRunRequest,
    TaskRunControlRequest,
    TaskInputReply,
    TaskNodeAttemptRecord,
    TaskPreflightRequest,
    TaskPlanningAuditRecord,
    ToolStepConfig,
    PythonStepConfig,
    StepVerdict,
    StepType,
    TaskStatus,
    UpdateTaskRequest,
    TaskValidationIssue,
    TaskValidationResponse,
)
from ._planner import PreviewTaskPlanner, TaskPlanner
from ._planning_contract import (
    TaskPlanningCandidate,
    TaskPlanningConfirmRequest,
    TaskPlanningConfirmResponse,
    TaskPlanningDecision,
    TaskPlanningRequest,
    TaskPlanningResponse,
    TaskPlanningRuntimePolicy,
    TaskPlanningTemplateContract,
    TaskPlanningNode,
)
from ._planning_decision import decide_task_plan
from ._plan_validation import format_plan_issues, validate_task_plan
from ._store import TaskStoreConflictError, TaskStoreProtocol
from ._knowledge import TaskKnowledgeError, TaskKnowledgeGateway
from ._citation_verifier import TASK_CITATION_PROMPT, verify_task_citations
from ._evidence_contract import (
    TASK_CLAIM_EVIDENCE_PROMPT,
    _evidence_records,
    materialize_task_citations_from_bindings,
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
)
from ._information_contract import build_information_contract
from ._s02_project_planning_evidence import (
    materialize_s02_project_planning_ledger,
)
from ._s02_structured_output import (
    S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS,
    assemble_s02_v3_output,
    assemble_s02_v4_1_output,
    assemble_s02_v4_output,
)
from ._s02_lightweight_output import (
    S02_LIGHTWEIGHT_TEMPLATE_VERSIONS,
    validate_s02_lightweight_markdown,
)
from ._errors import classify_node_error
from ._dag import (
    TaskGraphValidationError,
    ancestors,
    build_inputs_by_node_id,
    build_named_input_values,
    descendants,
    remap_task_graph,
    ready_node_ids,
    resources_fit,
    terminal_node_ids,
    topological_node_ids,
    validate_task_graph,
)
from ._capability_contracts import (
    capability_business_verified,
    capability_category,
    capability_contract,
    capability_external_side_effect,
    is_task_artifact_capability,
    is_task_native_capability,
    task_tool_has_external_side_effect,
    validate_contract_tool_schema,
)
from ._execution_gates import (
    contract_validation_record,
    enforce_json_output_contract,
)
from ._sop import TaskSOPRunState, TaskSOPStep, TaskStepState
from ._template_validation import (
    TaskTemplateValidator,
    TaskValidationFailure,
)
from ._template_compiler import TaskTemplateCompiler
from ._template_registry import CURRENT_TEMPLATE_VERSIONS
from ._template_models import TemplateArtifactConfig
from ._scenario_runtime import (
    execute_scenario_capability,
    is_knowledge_native_capability,
    is_scenario_native_capability,
    is_task_rag_capability,
    native_tool_schema,
    retrieve_scenario_evidence,
)
from ._scenario_evidence_policies import get_scenario_evidence_policy


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _node_snapshot_fingerprint(nodes: list[TaskNode]) -> str:
    """Hash the frozen Task node definitions used by a Run."""

    encoded = json.dumps(
        [_node_fingerprint_payload(node) for node in nodes],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_plan_snapshot_fingerprint(
    nodes: list[TaskNode],
    graph: TaskGraph | None,
) -> str:
    """Fingerprint the exact Task definition accepted by plan confirmation."""

    encoded = json.dumps(
        {
            "nodes": [_node_fingerprint_payload(node) for node in nodes],
            "graph": graph.model_dump(mode="json") if graph is not None else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _node_fingerprint_payload(node: TaskNode) -> dict[str, Any]:
    """Keep additive empty contract defaults compatible with old Run hashes."""

    payload = node.model_dump(mode="json")
    if not payload.get("information_requirements"):
        # TaskNode snapshots predating information contracts did not include
        # this property; omit the empty default so their fingerprints survive.
        payload.pop("information_requirements", None)
    return payload


def _authoritative_failure_header(
    error_code: str,
    message: str,
    *,
    artifact_generated: bool,
) -> str:
    """Make server-owned failure state visible beside Agent-authored text."""

    artifact_status = (
        "TaskRun 未记录可用文件产物；下载状态以服务端产物记录为准。"
        if not artifact_generated
        else "文件产物状态见 TaskRun 的服务端记录。"
    )
    safe_message = (message or "Task 服务端校验失败。")[:2_000]
    return (
        "> **Task 服务端验收：失败**  \n"
        f"> 错误码：`{error_code}`  \n"
        f"> {safe_message}  \n"
        "> 模型文本中的校验通过、无失败项或产物状态声明不构成验收。"
        f"{artifact_status}\n\n---\n\n"
    )


_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELED,
    RunStatus.TIMED_OUT,
}

# The nine built-in business scenarios collect user supplements through
# InputStep. Those answers are input data and use the Task form's deterministic
# field checks; generated Agent/Tool results still use task-verifier.
_BUILTIN_SCENARIO_TEMPLATE_IDS = frozenset(
    {
        "s01_project_init",
        "s02_project_planning",
        "s03_expert_matching",
        "s04_meeting_preparation",
        "s05_meeting_minutes",
        "s06_opinion_processing",
        "s07_report_generation",
        "s08_archive_transfer",
        "s09_expense_settlement",
    }
)


logger = logging.getLogger(__name__)

def _with_task_native_tool_schemas(
    nodes: list[TaskNode],
    tool_schemas: list[TaskToolSchema],
    *,
    enabled: bool,
) -> list[TaskToolSchema]:
    """Expose Task-owned capabilities to validation, not AgentScope."""

    native_nodes = [
        node
        for node in nodes
        if node.type is StepType.TOOL
        and isinstance(node.config, ToolStepConfig)
        and is_task_native_capability(node.capability_key)
    ]
    native_names = {node.config.tool_name for node in native_nodes}
    schemas = [
        schema for schema in tool_schemas if schema.name not in native_names
    ]
    existing = {schema.name for schema in schemas}
    for node in native_nodes:
        if node.config.tool_name in existing:
            continue
        if (
            is_knowledge_native_capability(node.capability_key)
            and not enabled
        ):
            continue
        input_schema = native_tool_schema(node.capability_key or "")
        schemas.append(
            TaskToolSchema(
                name=node.config.tool_name,
                description="由 Task 模块边界内的受控能力适配器执行。",
                input_schema=input_schema,
                is_read_only=True,
            ),
        )
        existing.add(node.config.tool_name)
    return schemas


def _needs_external_tool_discovery(task: TaskRecord) -> bool:
    """Whether the Task has a Tool that must come from the session Toolkit."""

    return any(
        isinstance(node.config, ToolStepConfig)
        and not is_task_native_capability(node.capability_key)
        for node in task.nodes
    )


def _task_source_material_ids(
    template_inputs: dict[str, Any] | None,
) -> list[str]:
    """Read the declared source scope without widening it."""

    if not isinstance(template_inputs, dict):
        return []
    raw_ids = template_inputs.get("source_material_ids", [])
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, list) or any(
        not isinstance(item, str) for item in raw_ids
    ):
        raise TaskKnowledgeError(
            "source_material_invalid",
            "指定资料标识格式无效，Task 未扩大到其他知识库资料。",
        )
    source_ids = list(
        dict.fromkeys(item.strip() for item in raw_ids if item.strip())
    )
    if len(source_ids) > 128:
        raise TaskKnowledgeError(
            "source_material_limit",
            "单个 Task 最多指定 128 份资料。",
        )
    return source_ids


def _task_run_input_values(run: TaskRunRecord) -> dict[str, str]:
    """Collect user answers persisted in the current SOP run snapshot."""

    values: dict[str, str] = {}
    for step in _task_sop_step_payloads(run):
        if not isinstance(step, dict):
            continue
        for answer in step.get("answers", []):
            try:
                reply = TaskInputReply.model_validate(answer)
            except (TypeError, ValueError):
                continue
            values.update(reply.answers)
    return values


def _task_sop_step_payloads(run: TaskRunRecord) -> list[dict[str, Any]]:
    """Read linear SOP steps or per-node SOP checkpoints from a Run."""

    state = run.sop_state or {}
    if run.engine == "dag":
        dag_states = state.get("dag_node_states", {})
        if not isinstance(dag_states, dict):
            return []
        return [
            item
            for item in dag_states.values()
            if isinstance(item, dict)
        ]
    steps = state.get("steps", [])
    return [step for step in steps if isinstance(step, dict)]


def _task_run_source_material_ids(run: TaskRunRecord) -> list[str]:
    """Merge declared and InputStep-selected document IDs for this run."""

    source_ids = list(run.source_material_ids)
    raw_value = _task_run_input_values(run).get("source_material_ids")
    if raw_value:
        stripped = raw_value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except (json.JSONDecodeError, RecursionError) as error:
                raise TaskKnowledgeError(
                    "source_material_invalid",
                    "指定资料标识格式无效，Task 未扩大到其他知识库资料。",
                ) from error
            if not isinstance(decoded, list) or any(
                not isinstance(item, str) for item in decoded
            ):
                raise TaskKnowledgeError(
                    "source_material_invalid",
                    "指定资料标识格式无效，Task 未扩大到其他知识库资料。",
                )
            source_ids.extend(decoded)
        else:
            source_ids.extend(
                re.split(r"[,，;；\r\n]+", stripped)
            )
    normalized = list(
        dict.fromkeys(item.strip() for item in source_ids if item.strip())
    )
    if len(normalized) > 128:
        raise TaskKnowledgeError(
            "source_material_limit",
            "单个 Task 最多指定 128 份资料。",
        )
    return normalized


def _retrieval_result_for_run(
    run: TaskRunRecord,
    current: TaskRunRecord,
    before_order: int | str,
) -> dict[str, Any] | None:
    """Return persisted, owner-scoped evidence from earlier RAG nodes.

    The S02 retrieval result remains structurally intact. Other scenarios may
    have multiple Task-owned retrieval nodes; their actual snapshots are
    combined without inventing evidence or widening the current Task scope.
    """

    retrievals: list[tuple[TaskNode, dict[str, Any]]] = []
    if run.engine == "dag" and isinstance(before_order, str) and run.graph:
        eligible_ids = ancestors(run.graph, before_order)
        indexes = [
            index
            for index, node in enumerate(run.nodes)
            if node.id in eligible_ids
        ]
    else:
        limit = min(int(before_order), len(run.nodes), len(current.node_runs))
        indexes = list(range(limit))
    for index in indexes:
        if index >= len(current.node_runs):
            continue
        node = run.nodes[index]
        if not is_task_rag_capability(node.capability_key):
            continue
        result = current.node_runs[index].result
        if (
            not isinstance(result, dict)
            or result.get("status") not in {
                "ready",
                "candidates_for_review",
                "not_found",
            }
            or not isinstance(result.get("evidence"), list)
        ):
            continue
        retrievals.append((node, result))
    if not retrievals:
        return None

    if run.source_template_id == "s02_project_planning":
        result = dict(retrievals[-1][1])
    else:
        retrieval_results = [item for _, item in retrievals]
        knowledge_bases = list(
            {
                str(base.get("id")): base
                for retrieval in retrieval_results
                for base in retrieval.get("knowledge_bases", [])
                if isinstance(base, dict) and base.get("id")
            }.values()
        )
        result = {
            "status": (
                "ready"
                if any(
                    item.get("status") in {"ready", "candidates_for_review"}
                    for item in retrieval_results
                )
                else "not_found"
            ),
            "retrieved_at": retrieval_results[-1].get("retrieved_at"),
            "knowledge_bases": knowledge_bases,
            "scenario_retrievals": [
                {
                    "capability_key": node.capability_key,
                    "query": item.get("query"),
                    "status": item.get("status"),
                    "message": item.get("message"),
                }
                for node, item in retrievals
            ],
        }
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for _, retrieval in retrievals:
        for item in retrieval.get("evidence", []):
            if not isinstance(item, dict):
                continue
            normalized = normalize_evidence_snapshot(
                [item],
                authorization_subject=run.user_id,
                collected_at=retrieval.get("retrieved_at"),
            )
            if normalized:
                evidence_by_id[normalized[0]["evidence_id"]] = normalized[0]
    result["evidence"] = list(evidence_by_id.values())
    if not result["evidence"]:
        result["status"] = "not_found"
    return result


def _is_s02_lightweight_run(run: TaskRunRecord) -> bool:
    """Identify S02 v5 tasks, including user-owned copies of those templates."""

    return bool(
        run.source_template_id == "s02_project_planning"
        and run.source_template_version in S02_LIGHTWEIGHT_TEMPLATE_VERSIONS
    )


def _is_s02_lightweight_final_node(
    run: TaskRunRecord,
    node: TaskNode,
) -> bool:
    """Match the S02 v5 final Agent without affecting copies or older versions."""

    if not _is_s02_lightweight_run(run) or node.type is not StepType.AGENT:
        return False
    if run.engine == "dag" and run.graph is not None:
        return node.id in terminal_node_ids(run.graph, run.nodes)
    return bool(run.nodes and node.id == run.nodes[-1].id)


def _s02_lightweight_retrieval_report(
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    """Project the current Run's authorized retrieval snapshot for draft UI."""

    evidence = retrieval.get("evidence", [])
    evidence = evidence if isinstance(evidence, list) else []
    project_retrieval = retrieval.get("project_material_retrieval")
    if not isinstance(project_retrieval, dict):
        project_retrieval = {}
    sources = [
        {
            key: item.get(key)
            for key in (
                "knowledge_base_name",
                "document_id",
                "source",
                "chunk_index",
                "content_sha256",
                "evidence_type",
            )
            if item.get(key) is not None
        }
        for item in evidence
        if isinstance(item, dict)
    ]
    return {
        "status": retrieval.get("status", "not_found"),
        "template_status": retrieval.get("status", "not_found"),
        "project_material_status": project_retrieval.get(
            "status", "not_found"
        ),
        "retrieved_at": retrieval.get("retrieved_at"),
        "matched_source_count": len(sources),
        "knowledge_bases": [
            {
                "id": base.get("id"),
                "name": base.get("name"),
            }
            for base in retrieval.get("knowledge_bases", [])
            if isinstance(base, dict)
        ],
        "sources": sources,
    }


def _s02_delivery_finalization(
    run: TaskRunRecord,
    metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Build the versioned S02 delivery record from already-passed SOP gates.

    The SOP and DAG paths share this finalizer. It deliberately consumes the
    frozen final-node audit rather than inferring business acceptance from a
    successful node status.
    """

    if run.source_template_id != "s02_project_planning":
        raise RuntimeError("S02 delivery finalization requires an S02 Run.")
    is_lightweight = _is_s02_lightweight_run(run)
    ai_verification = metadata.get("task_ai_verification")
    if (
        not isinstance(ai_verification, dict)
        or ai_verification.get("method") != "task-verifier"
        or ai_verification.get("passed") is not True
    ):
        raise RuntimeError("S02 缺少本次 TaskRun 的通用 AI 验收记录。")
    evidence_ledger = metadata.get("evidence_ledger")
    citation_verification = metadata.get(
        "final_citation_verification",
        metadata.get("citation_verification"),
    )
    if not is_lightweight and (
        not isinstance(evidence_ledger, dict)
        or evidence_ledger.get("passed") is not True
        or not isinstance(citation_verification, dict)
        or citation_verification.get("passed") is not True
    ):
        raise RuntimeError(
            "S02 缺少本次 TaskRun 的来源引用与 Claim–Evidence 验收记录。"
        )
    structured_report = metadata.get("s02_delivery_report")
    if not isinstance(structured_report, dict):
        structured_report = metadata.get("s02_v4_delivery")
    if not isinstance(structured_report, dict) and is_lightweight:
        structured_report = metadata.get("s02_lightweight_output_validation")
    if not isinstance(structured_report, dict):
        structured_report = {}
    ledger_passed = (
        evidence_ledger.get("passed") is True
        if isinstance(evidence_ledger, dict)
        else False
    )
    delivery_status = "draft_unverified"
    verification_report: dict[str, Any] = {
        "delivery_profile": (
            "s02_lightweight_draft" if is_lightweight else "ai_verified_draft"
        ),
        "mode": run.delivery_mode,
        "status": delivery_status,
        "acceptance": {
            "method": "task-verifier",
            "status": "passed",
            "message": ai_verification.get("message", ""),
            "retryable": ai_verification.get("retryable"),
        },
        "retrieval": metadata.get("s02_lightweight_retrieval"),
        "source_integrity": metadata.get("s02_lightweight_source_validation"),
        "evidence_ledger_status": (
            "not_performed"
            if is_lightweight
            else ("passed" if ledger_passed else "failed")
        ),
        "source_ledger": (
            evidence_ledger.get("ledger", [])
            if isinstance(evidence_ledger, dict)
            else []
        ),
        "citation_verification": citation_verification,
        "chapter_count": structured_report.get("chapter_count"),
        "completed_chapters": structured_report.get("completed_chapters", []),
        "warnings": structured_report.get("warnings", []),
        "semantic_support": "requires_human_review",
        "human_review_required": True,
        "acceptance_warnings": (
            [
                {
                    "warning_code": "TASK_EVIDENCE_LEDGER_NOT_PERFORMED",
                    "message": (
                        "业务内容由通用 Task task-verifier 按步骤验收标准评估；"
                        "未执行逐条 Claim–Evidence 台账核验，来源语义仍需人工复核。"
                    ),
                    "disposition": "manual_review_required",
                }
            ]
            if is_lightweight
            else []
        ),
    }
    if not is_lightweight:
        verification_report["evidence_ledger_passed"] = ledger_passed
    return delivery_status, verification_report


def _validate_s02_lightweight_sources(
    run: TaskRunRecord,
    current: TaskRunRecord,
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    """Apply existing deterministic source checks without a claim ledger.

    Lightweight delivery skips per-claim association only. It still validates
    every source snapshot against the current Run owner, selected KB scope,
    ready status, identity shape, and content hash where the complete source
    text is available.
    """

    evidence = retrieval.get("evidence", [])
    if not isinstance(evidence, list):
        return {
            "passed": False,
            "error_code": "TASK_S02_LIGHTWEIGHT_SOURCE_SNAPSHOT_INVALID",
            "issues": ["授权检索 Evidence 快照格式无效。"],
            "evidence_count": 0,
        }
    _, record_issues = _evidence_records(
        evidence,
        authorization_subject=run.user_id,
    )
    issues = [
        item.get("message", "Evidence 来源记录无效。")
        for item in record_issues
    ]
    allowed_knowledge_bases = set(current.knowledge_base_ids)
    allowed_project_sources = set(current.source_material_ids)
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            issues.append(f"evidence[{index}] 不是对象。")
            continue
        if (
            item.get("source_type", "knowledge_base_chunk")
            != "knowledge_base_chunk"
        ):
            issues.append(f"evidence[{index}] 不是受支持的知识库来源类型。")
        knowledge_base_id = item.get("knowledge_base_id")
        if knowledge_base_id not in allowed_knowledge_bases:
            issues.append(
                f"evidence[{index}] 不属于当前 TaskRun 选择的知识库。"
            )
        evidence_type = item.get("evidence_type")
        if evidence_type not in {"planning_template", "project_material"}:
            issues.append(f"evidence[{index}] 不属于 S02 支持的资料类型。")
        if (
            evidence_type == "project_material"
            and allowed_project_sources
            and item.get("document_id") not in allowed_project_sources
        ):
            issues.append(
                f"evidence[{index}] 不属于当前 TaskRun 选择的项目资料。"
            )
    return {
        "passed": not issues,
        "error_code": (
            None if not issues else "TASK_S02_LIGHTWEIGHT_SOURCE_INVALID"
        ),
        "issues": issues,
        "evidence_count": len(evidence),
    }


def _annotate_s02_lightweight_draft(
    output: str,
    retrieval_report: dict[str, Any],
) -> str:
    """Add a server-owned draft notice based only on the current Run snapshot."""

    status = str(retrieval_report.get("status", "not_found"))
    status_labels = {
        "ready": "授权检索已返回资料",
        "candidates_for_review": "授权检索已返回候选资料，需人工检查",
        "not_found": "未检索到匹配资料",
    }
    source_count = int(retrieval_report.get("matched_source_count", 0) or 0)
    notice = (
        "> **文档状态：项目策划初稿；尚未完成正式核验。**\n"
        f"> 本次 TaskRun 知识库检索：{status_labels.get(status, '状态待确认')}；"
        f"授权检索快照记录 {source_count} 个来源。\n"
        "> 本轻量版本不执行逐条 Claim–Evidence 审计；检索命中不代表报告中的每项事实均已核实，"
        "项目事实、分析与建议仍需人工复核。\n\n"
    )
    heading = re.search(r"(?m)^\s*#\s+.+$", output)
    if heading is None:
        return notice + output.lstrip()
    return output[: heading.end()] + "\n\n" + notice + output[heading.end() :].lstrip(
        "\r\n"
    )


_S02_CITATION_FORMAT_PROMPT = """\
S02 引用区块的格式必须严格遵循以下语法：正文后恰好一个代码区块，
开围栏的语言标识必须是 `task-citations`，围栏内是仅含 `citations`
数组的 JSON 对象。请按下面的结构输出：
```task-citations
{"citations":[
  {
    "citation_id":"<正文中实际使用的 C 编号>",
    "evidence_id":"<本次运行授权证据中的实际 evidence_id>",
    "quote":"<该 Evidence 原文中的连续逐字摘录>"
  }
]}
```
尖括号内容只是格式占位符，实际输出不得保留占位符，必须替换为
本次运行的真实引用编号、真实 evidence_id 和来源原文。
禁止在代码区块前另写 `task-citations` 标题，禁止使用 `json` 作为围栏
语言标识，禁止补造证据 ID 或摘录。引用编号必须与正文标记完全一致；
证据不存在或没有连续原文时，不得声称该引用已核验。
代码区块之后不得追加内容。
"""


_S02_FINAL_OUTPUT_PROMPT = """\
最终输出请整理成一份紧凑、完整、供人工编辑的策划报告：八章正文各写一次，
把大纲要求合并到对应章节；不要分别重印完整大纲、完整初稿、摘要、人工引用表
或重复的待核实清单。保留必要的项目事实、模板规则、建议和待核实事项，只删重复表达。
Task 通用 task-verifier 会结合本节点验收标准与本次运行的授权检索结果评估报告。
每个独立事实或规则使用独立 Claim；每个正文段落或表格行最多一个 occurrence。
锚点必须内联在它标记的正文同一物理行末尾，不得另起一行，也不得在同一段落后连续放多个锚点。
例如：`项目编号为 DEMO-001 [C1]。<!-- task-claim:CL-01:O-01-1 -->`。
如果一个段落含多个声明或同时含有来源事实与待核实内容，先拆分成各自独立的段落/表格行，分别设置锚点。
同一内容尽量只出现一次；若确需重复，每处使用独立 occurrence 和对应 binding。
每条来源事实在句末标注 [C1] 这类编号，并使正文引用、锚点和 occurrence.text 一致；
分析、建议和待核实内容要明确标识。同一 Claim 的全部 occurrence 必须使用同一种 kind；
`kind=unverified` 的正文不得带任何 [C#] 引用标记，每个 unverified occurrence 必须有且仅有一条完全匹配 claim_id/occurrence_id 的登记。
保留上游已核验引用，不添加无来源支持的新事实。
项目事实只能由本次授权项目资料支持，模板规则只能由本次授权策划模板支持；
用户输入数值只有在当前授权项目资料的逐字引用原文包含该具体数值时才可标注为资料事实并引用；否则只能作为用户输入/待核实内容，且不得带知识库引用。
引用 B-01～B-15 时，连续原文摘录必须同时包含相应条款编号及具体内容；若本次 TaskRun 的授权证据快照没有该条款原文，不得声称该条款已被模板来源支持，也不得用八章标题替代条款原文。
无可用来源的事项应在正文明确标为待核实，并为每个事项建立独立 Claim occurrence 与 unverified_id。
Claim–Evidence 区块使用紧凑、合法的 JSON，不缩进或重复正文；不要手工重复生成来源台账。
每个 binding 的 quote 必须是本次运行授权证据中的连续逐字原文；
不得编造 evidence_id、来源、哈希、摘录或校验结论。
`task-citations` 必须按上方格式随协议输出，并由 task-verifier 对照本次运行实际提供的来源评估；
不能把模型生成的引用编号或摘录当成已完成服务端校验。
正文后必须有一个完整的 `task-claim-evidence` 区块，JSON 与围栏必须闭合。
不要在正文声称服务端校验通过、文件已生成或审批已通过。
知识库访问边界仍由 Task 服务强制执行；引用语义支持仍需人工复核。
"""


def _claim_evidence_policy_prompt(run: TaskRunRecord) -> str:
    """Expose the active Task EvidencePolicy to the claim-producing Agent."""

    policy = get_scenario_evidence_policy(
        run.source_template_id,
        run.source_template_version,
    )
    if policy is None:
        return ""
    lines = [
        "当前 Task 的服务端 EvidencePolicy（必须遵守，不可通过改分类绕过）："
    ]
    for claim_kind, source_types in sorted(
        policy.allowed_evidence_types_by_claim_kind.items()
    ):
        lines.append(
            f"- {claim_kind} 仅可关联来源类型：{', '.join(source_types)}。"
        )
    required = ", ".join(policy.require_citation_claim_kinds) or "无"
    lines.append(f"必须有来源引用的 Claim 类型：{required}。")
    lines.append(
        "若来源类型与 Claim 类型不匹配，不得伪装分类或复用该证据；"
        "来源规则/限制应使用策略允许的类别。纯分析或建议可无引用，"
        "但必须清楚标注，且不得冒充来源事实；缺少支持的事实须标为待核实。"
    )
    return "\n".join(lines)


def _with_citation_prompt(run: TaskRunRecord, node: TaskNode) -> TaskNode:
    """Add the citation contract only to this run's Agent snapshot."""

    has_prior_rag = any(
        is_task_rag_capability(item.capability_key)
        and item.order < node.order
        for item in run.nodes
    )
    is_s02_v2_final_output = bool(
        run.source_template_id == "s02_project_planning"
        and run.source_template_version == "2.0.0"
        and run.nodes
        and node.id == run.nodes[-1].id
    )
    is_s02_v3_final_output = bool(
        run.source_template_id == "s02_project_planning"
        and run.source_template_version
        in S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS
        and run.nodes
        and node.id == run.nodes[-1].id
    )
    is_s02_lightweight_output = _is_s02_lightweight_run(run)
    has_explicit_citation_protocol = any(
        item.capability_key
        in {"document.citation_verify", "report.citation_check"}
        or (
            item.effective_output_contract is not None
            and item.effective_output_contract.validation_mode
            in {"citations", "claim_evidence"}
        )
        for item in run.nodes
    )
    is_s07_v3_final_output = bool(
        run.source_template_id == "s07_report_generation"
        and run.source_template_version == "3.0.0"
        and run.nodes
        and node.id == run.nodes[-1].id
    )
    output_contract = node.effective_output_contract
    requires_claim_evidence = bool(
        output_contract is not None
        and output_contract.validation_mode == "claim_evidence"
    ) or is_s02_v2_final_output or is_s07_v3_final_output
    if (
        node.type is not StepType.AGENT
        or not (has_prior_rag or requires_claim_evidence)
    ):
        return node
    assert isinstance(node.config, AgentStepConfig)
    prompt = node.config.prompt.rstrip()
    if (
        has_prior_rag
        and not requires_claim_evidence
        and not is_s02_v3_final_output
        and not is_s02_lightweight_output
        and has_explicit_citation_protocol
    ):
        prompt += f"\n\n{TASK_CITATION_PROMPT}"
    if requires_claim_evidence:
        prompt += f"\n\n{TASK_CLAIM_EVIDENCE_PROMPT}"
        policy_prompt = _claim_evidence_policy_prompt(run)
        if policy_prompt:
            prompt += f"\n\n{policy_prompt}"
    if (
        run.source_template_id == "s02_project_planning"
        and run.source_template_version == "2.0.0"
        and run.nodes
    ):
        next_node = min(
            (item for item in run.nodes if item.order > node.order),
            key=lambda item: item.order,
            default=None,
        )
        is_citation_checked_draft = (
            next_node is not None
            and next_node.capability_key == "document.citation_verify"
        )
        is_final_output = node.id == run.nodes[-1].id
        if is_citation_checked_draft:
            prompt += f"\n\n{_S02_CITATION_FORMAT_PROMPT}"
        elif is_final_output:
            prompt += f"\n\n{_S02_CITATION_FORMAT_PROMPT}"
            prompt += f"\n\n{_S02_FINAL_OUTPUT_PROMPT}"
    elif is_s07_v3_final_output:
        prompt += """

报告保持草稿状态，整合章节内容与人工意见，不重复输出完整大纲、初稿和引用台账。
不要输出 `passed=true`、`issues=[]`、`失败项：无`、文件生成成功或审批通过等服务端验收结论；这些状态只由
Task 服务端确认。业务结论及适用规则仍需人工复核。
"""
    if len(prompt) > 20_000:
        raise TaskKnowledgeError(
            "citation_prompt_too_large",
            "节点提示词过长，无法安全追加引用核验协议。",
        )
    config = node.config.model_copy(update={"prompt": prompt})
    return node.model_copy(update={"prompt": prompt, "config": config})

_DEFAULT_TASK_GENERATION_TIMEOUT_SECONDS = 45.0
_TASK_VARIABLE_PATTERN = re.compile(
    r"\{\{([\w.-]+)\}\}|\{(previous_output|user_input)\}"
)


class TaskWorkflowConflictError(RuntimeError):
    """A workflow operation is not valid for the Task's editing mode."""


def _configured_generation_timeout() -> float:
    """Read the planner deadline without allowing an invalid env to disable it."""

    value = os.getenv(
        "LXSCOPE_TASK_GENERATION_TIMEOUT_SECONDS",
        str(_DEFAULT_TASK_GENERATION_TIMEOUT_SECONDS),
    )
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return _DEFAULT_TASK_GENERATION_TIMEOUT_SECONDS


def _configured_dag_scheduler_enabled() -> bool:
    """Require an explicit Task-local runtime opt-in for DAG scheduling."""

    value = os.getenv("LXSCOPE_TASK_DAG_ENABLED", "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _configured_scenario_dag_execution_enabled() -> bool:
    """Require an explicit Task-local opt-in for versioned scenario DAGs."""

    value = os.getenv(
        "LXSCOPE_TASK_SCENARIO_DAG_EXECUTION_ENABLED",
        "",
    ).strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _configured_scenario_dag_allowed_templates() -> frozenset[tuple[str, str]]:
    """Load only exact current template versions from the Task-local registry."""

    raw = os.getenv("LXSCOPE_TASK_SCENARIO_DAG_ALLOWED_TEMPLATES", "")
    if not raw.strip():
        return frozenset()

    current_versions = frozenset(CURRENT_TEMPLATE_VERSIONS)
    parsed: set[tuple[str, str]] = set()
    invalid_entry = False
    for entry in raw.split(","):
        template_id, separator, template_version = entry.strip().partition("@")
        scope = (template_id.strip(), template_version.strip())
        if not separator or not all(scope) or scope not in current_versions:
            invalid_entry = True
            continue
        parsed.add(scope)

    if invalid_entry:
        logger.warning(
            "Ignoring LXSCOPE_TASK_SCENARIO_DAG_ALLOWED_TEMPLATES because it "
            "contains a malformed or non-current template scope."
        )
        return frozenset()
    return frozenset(parsed)


def _configured_dag_editing_enabled() -> bool:
    """Allow authenticated owners to author custom DAGs independently of execution."""

    value = os.getenv("LXSCOPE_TASK_DAG_EDITING_ENABLED", "true").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _configured_planner_max_concurrency() -> int:
    """Read the Task planner cap and keep it inside the existing graph schema."""

    try:
        value = int(os.getenv("LXSCOPE_TASK_PLANNER_MAX_CONCURRENCY", "8"))
    except (TypeError, ValueError):
        return 8
    return min(8, max(1, value))


def _configured_task_resource_limits() -> dict[str, int]:
    """Read Task-local resource capacities without trusting model output."""

    raw = os.getenv("LXSCOPE_TASK_RESOURCE_LIMITS_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("resource limits must be a JSON object")
        if any(
            not isinstance(name, str)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            for name, amount in value.items()
        ):
            raise ValueError("resource capacities must be integers")
        normalized = dict(value)
        return TaskPlanningRuntimePolicy(resource_limits=normalized).resource_limits
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning(
            "Ignoring invalid LXSCOPE_TASK_RESOURCE_LIMITS_JSON Task planner configuration."
        )
        return {}


def _knowledge_error_category(code: str) -> TaskErrorCategory:
    """Map the Task knowledge boundary's safe error code to a UI category."""

    normalized = code.casefold()
    if normalized == "knowledge_base_unavailable":
        return "permission_denied"
    if normalized == "knowledge_base_required":
        return "input_incomplete"
    if normalized in {
        "source_material_invalid",
        "source_material_limit",
        "knowledge_base_limit",
    }:
        return "configuration_error"
    return "data_source_not_ready"


def _knowledge_preflight_status(code: str) -> str:
    """Keep data/source failures distinct from a missing executable Tool."""

    if code == "knowledge_base_unavailable":
        return "permission_denied"
    if code in {"source_material_not_found", "source_material_not_ready"}:
        return "unverified"
    if code in {
        "knowledge_base_required",
        "no_indexed_material",
        "source_material_limit",
        "source_material_invalid",
        "no_matching_evidence",
        "no_matching_template",
    }:
        # Preserve established S01/S02 content-preflight semantics. These
        # checks do not mean an executable Tool is missing.
        return "missing"
    return "unverified"


def _knowledge_preflight_error_code(code: str) -> str:
    """Return a precise public Task preflight code for a safe gateway error."""

    return {
        "knowledge_base_unavailable": "TASK_KNOWLEDGE_PERMISSION_DENIED",
        "knowledge_base_required": "TASK_KNOWLEDGE_BASE_REQUIRED",
        "source_material_not_found": "TASK_KNOWLEDGE_SOURCE_NOT_FOUND",
        "source_material_not_ready": "TASK_KNOWLEDGE_DOCUMENT_NOT_READY",
        "source_material_invalid": "TASK_KNOWLEDGE_INPUT_INVALID",
        "source_material_limit": "TASK_KNOWLEDGE_INPUT_INVALID",
        "knowledge_base_limit": "TASK_KNOWLEDGE_INPUT_INVALID",
    }.get(code, "TASK_KNOWLEDGE_DATA_SOURCE_NOT_READY")


def _looks_like_permission_failure(message: str | None) -> bool:
    """Recognize only explicit permission wording from the Workspace adapter."""

    normalized = (message or "").casefold()
    return any(
        marker in normalized
        for marker in ("permission", "forbidden", "denied", "权限", "无权")
    )


def _preflight_arguments(
    value: Any,
    run_input: str,
) -> tuple[dict[str, Any], bool]:
    """Resolve only preflight-known Tool arguments without inventing outputs."""

    resolved = True

    def visit(item: Any) -> Any:
        nonlocal resolved
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if not isinstance(item, str):
            return item
        matches = list(_TASK_VARIABLE_PATTERN.finditer(item))
        if not matches:
            return item

        def replace(match: re.Match[str]) -> str:
            nonlocal resolved
            key = match.group(1) or match.group(2)
            if key in {"user_input"}:
                return run_input
            resolved = False
            return match.group(0)

        rendered = _TASK_VARIABLE_PATTERN.sub(replace, item)
        if resolved and len(matches) == 1 and matches[0].span() == (0, len(item)):
            try:
                return json.loads(rendered)
            except (ValueError, TypeError, RecursionError):
                return rendered
        return rendered

    arguments = visit(value)
    return (arguments if isinstance(arguments, dict) else {}, resolved)


class TaskService:
    """Own Task lifecycle while keeping execution behind an adapter port."""

    def __init__(
        self,
        store: TaskStoreProtocol,
        *,
        agentscope_executor: TaskExecutor | None = None,
        preview_executor: TaskExecutor | None = None,
        planner: TaskPlanner | None = None,
        artifact_writer: TaskArtifactWriter | None = None,
        generation_timeout_seconds: float | None = None,
        message_bus: Any | None = None,
        enable_dag_scheduler: bool | None = None,
        enable_dag_editing: bool | None = None,
        dag_scenario_test_allowlist: set[tuple[str, str]] | None = None,
    ) -> None:
        """Bind repository and execution adapters.

        ``dag_scenario_test_allowlist`` remains an explicit in-process test
        seam. Normal application instances use the separate Task-local
        ``LXSCOPE_TASK_SCENARIO_DAG_*`` settings; both formal flags default to
        off and the allowed-template list defaults to empty.
        """

        self._store = store
        self._dag_scheduler_enabled = (
            enable_dag_scheduler
            if enable_dag_scheduler is not None
            else _configured_dag_scheduler_enabled()
        )
        self._dag_editing_enabled = (
            enable_dag_editing
            if enable_dag_editing is not None
            else _configured_dag_editing_enabled()
        )
        self._dag_scenario_test_allowlist = frozenset(
            dag_scenario_test_allowlist or ()
        )
        self._dag_scenario_execution_enabled = (
            _configured_scenario_dag_execution_enabled()
        )
        self._dag_scenario_allowed_templates = (
            _configured_scenario_dag_allowed_templates()
        )
        self._template_validator = TaskTemplateValidator()
        self._message_bus = message_bus
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._dag_transition_locks: dict[str, asyncio.Lock] = {}
        self._agentscope_executor = agentscope_executor
        candidate = artifact_writer or agentscope_executor
        self._artifact_writer = (
            candidate
            if callable(getattr(candidate, "write_artifact", None))
            and callable(getattr(candidate, "read_artifact", None))
            else None
        )
        self._preview_executor = preview_executor or PreviewTaskExecutor()
        self._planner = planner or PreviewTaskPlanner()
        self._generation_timeout_seconds = max(
            1.0,
            (
                generation_timeout_seconds
                if generation_timeout_seconds is not None
                else _configured_generation_timeout()
            ),
        )
        self._active_runs: dict[str, asyncio.Task[None]] = {}
        self._run_users: dict[str, str] = {}
        # Some adapters turn ``CancelledError`` into a normal error/result.
        # Retain the cancellation request until the worker exits so such an
        # adapter cannot later overwrite the user's terminal cancellation.
        self._cancel_requested_run_ids: set[str] = set()

    async def create_task(
        self,
        user_id: str,
        body: CreateTaskRequest,
        *,
        source_template_id: str | None = None,
        source_template_version: str | None = None,
        template_step_bindings: dict[str, str] | None = None,
        template_inputs: dict[str, Any] | None = None,
    ) -> TaskRecord:
        """Create a draft, manual task, or trusted template-derived copy."""

        if body.graph is not None:
            self._ensure_dag_editing_enabled()
            if source_template_id is not None:
                raise TaskWorkflowConflictError(
                    "DAG editing is available only for custom Tasks without a scenario template."
                )

        has_nodes = bool(body.nodes)
        record = TaskRecord(
            user_id=user_id,
            title=(body.title or "").strip() or "未命名任务",
            goal=body.goal.strip(),
            nodes=body.nodes,
            title_source=(
                "user" if body.title and body.title.strip() else "auto"
            ),
            status=TaskStatus.ACTIVE if has_nodes else TaskStatus.DRAFT,
            knowledge_base_ids=body.knowledge_base_ids,
            generation_status=(
                TaskGenerationStatus.SUCCEEDED
                if has_nodes
                else TaskGenerationStatus.IDLE
            ),
            source_context=body.source_context,
            workflow_mode=(
                TaskWorkflowMode.TEMPLATE_INSTANCE
                if source_template_id
                else TaskWorkflowMode.FREE_AI_PLANNER
            ),
            source_template_id=source_template_id,
            source_template_version=source_template_version,
            template_step_bindings=template_step_bindings or {},
            template_inputs=template_inputs,
            graph=body.graph,
        )
        if record.graph is not None:
            self._validate_graph_or_raise(record.graph, record.nodes, record.id)
        return await self._store.save_task(record)

    async def generate_task(
        self,
        user_id: str,
        task_id: str,
        context: TaskContext | None,
    ) -> TaskRecord | None:
        """Generate and persist editable nodes for one task draft."""

        current = await self._store.get_task(user_id, task_id)
        if current is None or current.status == TaskStatus.ARCHIVED:
            return None
        if (
            current.workflow_mode
            in {
                TaskWorkflowMode.TEMPLATE_INSTANCE,
                TaskWorkflowMode.CUSTOM_FLOW,
            }
            or current.source_template_id is not None
            or current.graph is not None
        ):
            raise TaskWorkflowConflictError(
                "模板来源任务使用固定流程，不支持 AI Planner 重新生成。",
            )
        merged_context = (
            context if context is not None else current.source_context
        )
        await self._store.save_task(
            current.model_copy(
                update={
                    "source_context": merged_context,
                    "generation_status": TaskGenerationStatus.GENERATING,
                    "generation_error": None,
                },
            ),
        )
        try:
            tool_schemas: list[TaskToolSchema] = []
            try:
                tool_schemas = await asyncio.wait_for(
                    self.list_tools(user_id, merged_context),
                    timeout=self._generation_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Task tool discovery timed out after %.1fs for task=%s; "
                    "continuing without tool schemas",
                    self._generation_timeout_seconds,
                    task_id,
                )
                tool_schemas = []
            except Exception:  # pylint: disable=broad-except
                # Tool discovery is an enhancement for planning. A broken
                # optional MCP must not prevent Agent/Python planning.
                tool_schemas = []
            try:
                draft = await asyncio.wait_for(
                    self._planner.plan(
                        user_id=user_id,
                        goal=current.goal,
                        context=merged_context,
                        current_title=(
                            current.title
                            if current.title_source == "user"
                            else None
                        ),
                        tool_schemas=tool_schemas,
                    ),
                    timeout=self._generation_timeout_seconds,
                )
            except asyncio.TimeoutError:
                # A planner may be waiting on a model or a stateful MCP.  The
                # editor must never leave a newly created task in "generating"
                # forever; a deterministic editable plan keeps the UI usable
                # while still allowing the user to refine or regenerate it.
                logger.warning(
                    "Task planner timed out after %.1fs for task=%s; "
                    "using the editable fallback plan",
                    self._generation_timeout_seconds,
                    task_id,
                )
                draft = await PreviewTaskPlanner().plan(
                    user_id=user_id,
                    goal=current.goal,
                    context=merged_context,
                    current_title=(
                        current.title
                        if current.title_source == "user"
                        else None
                    ),
                    tool_schemas=tool_schemas,
                )
            plan_issues = validate_task_plan(draft, tool_schemas)
            if plan_issues:
                raise ValueError(
                    "Generated task plan is invalid:\n"
                    + format_plan_issues(plan_issues),
                )
            nodes = [
                TaskNode(
                    name=node.name.strip(),
                    prompt=node.prompt.strip(),
                    type=node.type,
                    config=node.config,
                    order=index,
                    artifact=node.artifact,
                    output_contract=node.output_contract,
                    acceptance_criteria=node.acceptance_criteria,
                    max_attempts=node.max_attempts,
                )
                for index, node in enumerate(draft.nodes)
            ]
            title = (
                current.title
                if current.title_source == "user"
                else draft.suggested_title.strip()
            )
            updated = current.model_copy(
                update={
                    "title": title or "未命名任务",
                    "goal": current.goal.strip(),
                    "intent": draft.intent.strip(),
                    "nodes": nodes,
                    "status": TaskStatus.ACTIVE,
                    "revision": current.revision + 1,
                    "generation_status": TaskGenerationStatus.SUCCEEDED,
                    "generation_error": None,
                    "source_context": merged_context,
                    "planning_decision": None,
                    "planning_goal_fingerprint": None,
                    "planning_goal_snapshot": None,
                    "planning_confirmed_id": None,
                    "planning_confirmed_version": None,
                    "planning_confirmed_fingerprint": None,
                    "planning_confirmed_at": None,
                },
            )
            return await self._store.save_task(updated)
        except Exception as error:  # pylint: disable=broad-except
            message = str(error) or "任务节点生成失败。"
            await self._store.save_task(
                current.model_copy(
                    update={
                        "source_context": merged_context,
                        "generation_status": TaskGenerationStatus.FAILED,
                        "generation_error": message,
                    },
                ),
            )
            raise

    def _planning_runtime_policy(
        self,
        *,
        authorized_tool_node_ids: list[str] | None = None,
    ) -> TaskPlanningRuntimePolicy:
        """Build planner authorization only from this TaskService instance."""

        capabilities = self.get_dag_capabilities()
        return TaskPlanningRuntimePolicy(
            sop_execution_enabled=True,
            dag_execution_enabled=self._dag_scheduler_enabled,
            authorized_tool_node_ids=authorized_tool_node_ids or [],
            scenario_execution_allowlist=capabilities.scenario_execution_allowlist,
            max_concurrency_limit=_configured_planner_max_concurrency(),
            resource_limits=_configured_task_resource_limits(),
        )

    def _planning_template_contract(
        self,
        task: TaskRecord,
    ) -> TaskPlanningTemplateContract | None:
        """Resolve the exact frozen source contract for a versioned Task."""

        if not task.source_template_id and not task.source_template_version:
            return None
        template = self._template_validator.template_for_task(task)
        if template is None:
            raise TaskWorkflowConflictError(
                "来源模板版本无法解析；Planner 不能清除或改写模板业务契约。",
            )
        if not task.nodes:
            raise TaskWorkflowConflictError(
                "版本化模板 Task 缺少冻结节点；不能基于空节点重新规划。",
            )
        return TaskPlanningTemplateContract(
            template=template,
            step_bindings=dict(task.template_step_bindings),
            frozen_nodes=[node.model_copy(deep=True) for node in task.nodes],
        )

    async def _revalidate_planning_candidate(
        self,
        user_id: str,
        task: TaskRecord,
        candidate: TaskPlanningCandidate,
        *,
        context: TaskContext,
        tool_schemas: list[TaskToolSchema],
        tool_discovery_error: bool,
        template_contract: TaskPlanningTemplateContract | None,
        knowledge_gateway: TaskKnowledgeGateway | None,
        planning_id: str | None = None,
        planning_created_at: datetime | None = None,
    ) -> TaskPlanningDecision:
        """Recompute a plan, then add only live server permission results."""

        policy = self._planning_runtime_policy()
        preliminary = decide_task_plan(
            candidate,
            tool_schemas=tool_schemas,
            runtime_policy=policy,
            template_contract=template_contract,
        )
        if preliminary.validation_status == "invalid":
            return preliminary.model_copy(
                update={
                    "planning_id": planning_id or preliminary.planning_id,
                    "created_at": planning_created_at or preliminary.created_at,
                },
            )

        graph = (
            preliminary.as_task_graph()
            if preliminary.recommended_mode == "dag"
            else None
        )
        candidate_task = task.model_copy(
            deep=True,
            update={
                "nodes": [node.model_copy(deep=True) for node in preliminary.nodes],
                "graph": graph,
                "status": TaskStatus.ACTIVE,
                "source_context": context,
            },
        )
        permission_result = await self._preflight_task_record(
            user_id,
            candidate_task,
            TaskPreflightRequest(input=task.goal, context=context),
            tool_schemas=tool_schemas,
            tool_discovery_error=tool_discovery_error,
            knowledge_gateway=knowledge_gateway,
        )
        authorized_node_ids = sorted(
            {
                check.step_id
                for check in permission_result.capability_checks
                if check.step_id
                and check.status == "available"
                and check.permission_status == "allowed"
                and not check.external_side_effect
            },
        )
        final = decide_task_plan(
            candidate,
            tool_schemas=tool_schemas,
            runtime_policy=policy.model_copy(
                update={"authorized_tool_node_ids": authorized_node_ids},
            ),
            template_contract=template_contract,
        )
        return final.model_copy(
            update={
                "planning_id": planning_id or final.planning_id,
                "created_at": planning_created_at or final.created_at,
            },
        )

    @staticmethod
    def _candidate_from_decision(
        decision: TaskPlanningDecision,
    ) -> TaskPlanningCandidate:
        """Recover only model-editable fields from a server-reviewed decision."""

        return TaskPlanningCandidate(
            suggested_title=decision.suggested_title,
            intent=decision.intent,
            recommended_mode=decision.planner_suggested_mode,
            reason=decision.planner_reason or decision.reason,
            nodes=[
                TaskPlanningNode.model_validate(node.model_dump(mode="python"))
                for node in decision.nodes
            ],
            edges=[edge.model_copy(deep=True) for edge in decision.edges],
            input_bindings={
                node_id: {
                    name: binding.model_copy(deep=True)
                    for name, binding in named.items()
                }
                for node_id, named in decision.input_bindings.items()
            },
        )

    async def plan_task_with_decision(
        self,
        user_id: str,
        task_id: str,
        body: TaskPlanningRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskPlanningResponse | None:
        """Persist a reviewable plan proposal without changing the workflow."""

        task = await self._store.get_task(user_id, task_id)
        if task is None or task.status == TaskStatus.ARCHIVED:
            return None
        if task.generation_status == TaskGenerationStatus.GENERATING:
            raise TaskWorkflowConflictError(
                "任务节点仍在生成；完成或取消当前生成后再进行智能规划。",
            )
        planner = getattr(self._planner, "plan_with_decision", None)
        if not callable(planner):
            raise TaskWorkflowConflictError(
                "当前 TaskService 未配置支持模式决策的 AgentScope Planner。",
            )

        context = body.context or task.source_context
        template_contract = self._planning_template_contract(task)
        tool_discovery_error = False
        try:
            tool_schemas = await asyncio.wait_for(
                self.list_tools(user_id, context, strict=True),
                timeout=self._generation_timeout_seconds,
            )
        except asyncio.TimeoutError:
            tool_schemas = []
            tool_discovery_error = True
        except Exception as error:  # noqa: BLE001 — planning remains reviewable
            logger.warning(
                "Task planner tool discovery failed for task=%s: %s",
                task_id,
                error,
            )
            tool_schemas = []
            tool_discovery_error = True

        policy = self._planning_runtime_policy()
        decision = await asyncio.wait_for(
            planner(
                user_id=user_id,
                goal=task.goal,
                context=context,
                runtime_policy=policy,
                current_title=task.title if task.title_source == "user" else None,
                tool_schemas=tool_schemas,
                template_contract=template_contract,
            ),
            timeout=self._generation_timeout_seconds,
        )
        decision = TaskPlanningDecision.model_validate(decision)
        if decision.validation_status != "invalid":
            candidate = self._candidate_from_decision(decision)
            decision = await self._revalidate_planning_candidate(
                user_id,
                task,
                candidate,
                context=context,
                tool_schemas=tool_schemas,
                tool_discovery_error=tool_discovery_error,
                template_contract=template_contract,
                knowledge_gateway=knowledge_gateway,
                planning_id=decision.planning_id,
                planning_created_at=decision.created_at,
            )

        plan_version = task.planning_version + 1
        decision_payload = decision.model_dump(mode="json")
        audit = [
            *task.planning_audit,
            TaskPlanningAuditRecord(
                planning_id=decision.planning_id,
                plan_version=plan_version,
                action="proposed",
                actor_user_id=user_id,
                recommended_mode=decision.recommended_mode,
                validation_status=decision.validation_status,
            ),
        ][-128:]
        history = [
            *task.planning_history,
            {"plan_version": plan_version, "decision": decision_payload},
        ][-64:]
        saved = await self._store.save_task(
            task.model_copy(
                update={
                    "source_context": context,
                    "planning_decision": decision_payload,
                    "planning_version": plan_version,
                    "planning_history": history,
                    "planning_audit": audit,
                    "planning_goal_fingerprint": hashlib.sha256(
                        task.goal.strip().encode("utf-8"),
                    ).hexdigest(),
                    "planning_goal_snapshot": task.goal.strip(),
                    "planning_confirmed_id": None,
                    "planning_confirmed_version": None,
                    "planning_confirmed_fingerprint": None,
                    "planning_confirmed_at": None,
                    "revision": task.revision + 1,
                    "updated_at": _utc_now(),
                },
            ),
        )
        return TaskPlanningResponse(
            task=saved,
            plan=decision,
            plan_version=plan_version,
        )

    async def confirm_task_plan(
        self,
        user_id: str,
        task_id: str,
        body: TaskPlanningConfirmRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskPlanningConfirmResponse | None:
        """Save a confirmed SOP/DAG definition, then run read-only Preflight."""

        task = await self._store.get_task(user_id, task_id)
        if task is None or task.status == TaskStatus.ARCHIVED:
            return None
        if task.generation_status == TaskGenerationStatus.GENERATING:
            raise TaskWorkflowConflictError(
                "任务节点仍在生成；不能确认尚未完成的规划。",
            )
        if task.planning_decision is None:
            raise TaskWorkflowConflictError("当前 Task 没有待确认的智能规划。")
        if body.planning_id != task.planning_decision.get("planning_id"):
            raise TaskWorkflowConflictError("规划 ID 已变化；请重新规划后再确认。")
        if body.plan_version != task.planning_version:
            raise TaskWorkflowConflictError("规划版本已变化；请重新规划后再确认。")
        expected_goal_fingerprint = hashlib.sha256(
            task.goal.strip().encode("utf-8"),
        ).hexdigest()
        if task.planning_goal_fingerprint != expected_goal_fingerprint:
            raise TaskWorkflowConflictError("任务目标在规划后已修改；请重新规划。")
        if task.planning_goal_snapshot != task.goal.strip():
            raise TaskWorkflowConflictError("任务目标在规划后已修改；请重新规划。")

        stored_decision = TaskPlanningDecision.model_validate(
            task.planning_decision,
        )
        if stored_decision.validation_status == "invalid":
            raise TaskWorkflowConflictError(
                "当前候选计划未通过确定性校验，不能确认或创建 Run。",
            )
        context = task.source_context.model_copy(
            update=body.context.model_dump(exclude_none=True),
        )
        template_contract = self._planning_template_contract(task)
        tool_discovery_error = False
        try:
            tool_schemas = await self.list_tools(user_id, context, strict=True)
        except Exception as error:  # noqa: BLE001 — fail closed on confirmation
            logger.warning(
                "Task confirmation tool discovery failed for task=%s: %s",
                task_id,
                error,
            )
            tool_schemas = []
            tool_discovery_error = True

        nodes = (
            [node.model_copy(deep=True) for node in body.nodes]
            if "nodes" in body.model_fields_set and body.nodes is not None
            else [node.model_copy(deep=True) for node in stored_decision.nodes]
        )
        if "graph" in body.model_fields_set:
            if body.graph is None:
                edges = [
                    {"source_node_id": left.id, "target_node_id": right.id}
                    for left, right in zip(nodes, nodes[1:])
                ]
                input_bindings = {}
            else:
                edges = [edge.model_dump(mode="python") for edge in body.graph.edges]
                input_bindings = {
                    node_id: {
                        name: binding.model_dump(mode="python")
                        for name, binding in named.items()
                    }
                    for node_id, named in body.graph.input_bindings.items()
                }
        else:
            edges = [edge.model_dump(mode="python") for edge in stored_decision.edges]
            input_bindings = {
                node_id: {
                    name: binding.model_dump(mode="python")
                    for name, binding in named.items()
                }
                for node_id, named in stored_decision.input_bindings.items()
            }
        candidate = TaskPlanningCandidate(
            suggested_title=stored_decision.suggested_title,
            intent=stored_decision.intent,
            recommended_mode=stored_decision.planner_suggested_mode,
            reason=stored_decision.planner_reason or stored_decision.reason,
            nodes=[
                TaskPlanningNode.model_validate(node.model_dump(mode="python"))
                for node in nodes
            ],
            edges=edges,
            input_bindings=input_bindings,
        )
        decision = await self._revalidate_planning_candidate(
            user_id,
            task,
            candidate,
            context=context,
            tool_schemas=tool_schemas,
            tool_discovery_error=tool_discovery_error,
            template_contract=template_contract,
            knowledge_gateway=knowledge_gateway,
            planning_id=stored_decision.planning_id,
            planning_created_at=stored_decision.created_at,
        )
        if decision.validation_status == "invalid":
            raise TaskWorkflowConflictError(
                "确认后的编辑未通过确定性校验："
                + "; ".join(
                    f"{issue.path}: {issue.message}"
                    for issue in decision.validation_issues
                ),
            )

        graph = (
            decision.as_task_graph()
            if decision.recommended_mode == "dag"
            else None
        )
        if graph is not None:
            await self.ensure_dag_graph_editable(user_id, task)
        validation = await self.validate_task_definition(
            user_id,
            task,
            decision.nodes,
            context=context,
            graph=graph,
        )
        if not validation.valid:
            raise TaskValidationFailure(validation)

        ordered_nodes = decision.nodes
        if graph is None:
            ordering_graph = TaskGraph(
                edges=[edge.model_copy(deep=True) for edge in decision.edges],
                input_bindings={},
            )
            try:
                ordered_ids = topological_node_ids(ordering_graph, decision.nodes)
            except TaskGraphValidationError as error:
                raise TaskWorkflowConflictError(
                    f"SOP 节点依赖无法排序：{error.message}",
                ) from error
            by_id = {node.id: node for node in decision.nodes}
            ordered_nodes = [by_id[node_id] for node_id in ordered_ids]
        ordered_nodes = [
            node.model_copy(update={"order": index}, deep=True)
            for index, node in enumerate(ordered_nodes)
        ]

        edited = bool(
            [node.model_dump(mode="json") for node in ordered_nodes]
            != [node.model_dump(mode="json") for node in stored_decision.nodes]
            or [edge.model_dump(mode="json") for edge in decision.edges]
            != [edge.model_dump(mode="json") for edge in stored_decision.edges]
            or decision.input_bindings != stored_decision.input_bindings
        )
        plan_version = task.planning_version + (1 if edited else 0)
        decision_payload = decision.model_dump(mode="json")
        history = list(task.planning_history)
        audit = list(task.planning_audit)
        if edited:
            history.append({"plan_version": plan_version, "decision": decision_payload})
            audit.append(
                TaskPlanningAuditRecord(
                    planning_id=decision.planning_id,
                    plan_version=plan_version,
                    action="edited_and_confirmed",
                    actor_user_id=user_id,
                    recommended_mode=decision.recommended_mode,
                    validation_status=decision.validation_status,
                ),
            )
        audit.append(
            TaskPlanningAuditRecord(
                planning_id=decision.planning_id,
                plan_version=plan_version,
                action="confirmed",
                actor_user_id=user_id,
                recommended_mode=decision.recommended_mode,
                validation_status=decision.validation_status,
            ),
        )
        updated = task.model_copy(
            deep=True,
            update={
                "title": (
                    (body.title.strip() if body.title and body.title.strip() else task.title)
                    if task.title_source == "user"
                    else (body.title.strip() if body.title and body.title.strip() else decision.suggested_title)
                ),
                "intent": decision.intent,
                "nodes": ordered_nodes,
                "graph": graph,
                "source_context": context,
                "status": TaskStatus.ACTIVE,
                "generation_status": TaskGenerationStatus.SUCCEEDED,
                "generation_error": None,
                "planning_decision": decision_payload,
                "planning_version": plan_version,
                "planning_history": history[-64:],
                "planning_audit": audit[-128:],
                "planning_confirmed_id": decision.planning_id,
                "planning_confirmed_version": plan_version,
                "planning_confirmed_fingerprint": _task_plan_snapshot_fingerprint(
                    ordered_nodes,
                    graph,
                ),
                "planning_confirmed_at": _utc_now(),
                "revision": task.revision + 1,
                "updated_at": _utc_now(),
            },
        )
        saved = await self._store.save_task(updated)
        preflight = await self._preflight_task_record(
            user_id,
            saved,
            TaskPreflightRequest(input=body.input, context=context),
            tool_schemas=tool_schemas,
            tool_discovery_error=tool_discovery_error,
            knowledge_gateway=knowledge_gateway,
        )
        return TaskPlanningConfirmResponse(
            task=saved,
            plan=decision,
            plan_version=plan_version,
            preflight=preflight,
        )

    async def list_tasks(self, user_id: str) -> list[TaskRecord]:
        """List the caller's active tasks."""

        return await self._store.list_tasks(user_id)

    async def list_tools(
        self,
        user_id: str,
        context: TaskContext,
        *,
        strict: bool = False,
    ) -> list[TaskToolSchema]:
        """List AgentScope tools available to the selected Task context."""

        executor = self._agentscope_executor
        list_tools = getattr(executor, "list_tools", None)
        if (
            list_tools is None
            or not context.agent_id
            or not context.session_id
        ):
            return []
        execution_context = ExecutionContext(
            **context.model_dump(),
            user_id=user_id,
            task_id="task-tool-catalog",
            run_id="task-tool-catalog",
            task_revision=0,
        )
        try:
            return await list_tools(execution_context)
        except Exception as error:  # noqa: BLE001 — tool discovery is optional
            if strict:
                raise
            # Tool discovery runs while the Task editor is loading.  A stale
            # stateful MCP must not make the whole page look unreachable; the
            # planner and editor can safely continue with no optional tools.
            logger.warning(
                "Task tool discovery degraded for agent=%s session=%s; "
                "continuing with no optional tools: %s",
                context.agent_id,
                context.session_id,
                error,
            )
            return []

    async def get_task(self, user_id: str, task_id: str) -> TaskRecord | None:
        """Get one task owned by the caller."""

        return await self._store.get_task(user_id, task_id)

    async def create_custom_flow_copy(
        self,
        user_id: str,
        task_id: str,
        *,
        mode: Literal["sop", "dag"] = "sop",
    ) -> TaskRecord | None:
        """Create a provenance-preserving, user-owned template copy."""

        source = await self._store.get_task(user_id, task_id)
        if source is None or source.status == TaskStatus.ARCHIVED:
            return None
        if (
            source.workflow_mode == TaskWorkflowMode.CUSTOM_FLOW
            or not source.source_template_id
            or not source.source_template_version
        ):
            raise TaskWorkflowConflictError(
                "只能从带有有效来源模板的标准模板任务创建自定义流程。",
            )

        validation = await self.validate_task_definition(
            user_id,
            source,
            source.nodes,
            context=source.source_context,
        )
        if not validation.valid:
            raise TaskWorkflowConflictError(
                "来源模板任务当前无效，不能复制为自定义流程。",
            )
        if mode == "dag":
            self._ensure_dag_editing_enabled()
            if source.graph is not None:
                raise TaskWorkflowConflictError(
                    "来源模板已经包含图定义；为避免静默覆盖依赖，只能从 SOP 模板实例转换为 DAG。",
                )

        copied_ids: dict[str, str] = {}

        def copied_id(source_node_id: str) -> str:
            if source_node_id not in copied_ids:
                copied_ids[source_node_id] = uuid4().hex
            return copied_ids[source_node_id]

        copied_nodes = [
            node.model_copy(
                deep=True,
                update={"id": copied_id(node.id), "order": index},
            )
            for index, node in enumerate(source.nodes)
        ]
        copied_bindings = {
            step_key: copied_id(source_node_id)
            for step_key, source_node_id in source.template_step_bindings.items()
        }
        copied_graph = remap_task_graph(source.graph, copied_ids)
        if mode == "dag":
            resource_limits: dict[str, int] = {}
            for node in copied_nodes:
                for resource, amount in node.resource_requirements.items():
                    resource_limits[resource] = max(
                        amount,
                        resource_limits.get(resource, 0),
                    )
            copied_graph = TaskGraph(
                schema_version=1,
                edges=[
                    TaskGraphEdge(
                        source_node_id=left.id,
                        target_node_id=right.id,
                    )
                    for left, right in zip(copied_nodes, copied_nodes[1:])
                ],
                input_bindings={},
                max_concurrency=1,
                resource_limits=resource_limits,
            )
        title_suffix = "（自定义）"
        copied_title = source.title[: 160 - len(title_suffix)] + title_suffix
        copied = source.model_copy(
            deep=True,
            update={
                "id": uuid4().hex,
                "title": copied_title,
                "nodes": copied_nodes,
                "graph": copied_graph,
                "workflow_mode": TaskWorkflowMode.CUSTOM_FLOW,
                "source_task_id": source.id,
                "source_task_revision": source.revision,
                "template_step_bindings": copied_bindings,
                "revision": 1,
                "status": TaskStatus.ACTIVE if copied_nodes else TaskStatus.DRAFT,
                "generation_status": (
                    TaskGenerationStatus.SUCCEEDED
                    if copied_nodes
                    else TaskGenerationStatus.IDLE
                ),
                "generation_error": None,
                "last_run_id": None,
                "last_run_status": None,
                "planning_decision": None,
                "planning_version": 0,
                "planning_history": [],
                "planning_audit": [],
                "planning_goal_fingerprint": None,
                "planning_goal_snapshot": None,
                "planning_confirmed_id": None,
                "planning_confirmed_version": None,
                "planning_confirmed_fingerprint": None,
                "planning_confirmed_at": None,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            },
        )
        if mode == "dag":
            copied_validation = await self.validate_task_definition(
                user_id,
                copied,
                copied.nodes,
                context=copied.source_context,
                graph=copied.graph,
            )
            if not copied_validation.valid:
                raise TaskValidationFailure(copied_validation)
        return await self._store.save_task(copied)

    async def update_task(
        self,
        user_id: str,
        task_id: str,
        body: UpdateTaskRequest,
    ) -> TaskRecord | None:
        """Update a task definition and bump its reusable revision."""

        current = await self._store.get_task(user_id, task_id)
        if current is None:
            return None
        if current.status == TaskStatus.ARCHIVED:
            return None
        graph_was_submitted = "graph" in body.model_fields_set
        if graph_was_submitted or (current.graph is not None and body.nodes is not None):
            await self.ensure_dag_graph_editable(user_id, current)
        updates: dict[str, object] = {
            "revision": current.revision + 1,
            "generation_error": None,
        }
        if body.title is not None:
            updates["title"] = body.title.strip()
            updates["title_source"] = "user"
        if body.goal is not None:
            updates["goal"] = body.goal.strip()
        if body.source_context is not None:
            updates["source_context"] = body.source_context
        if body.knowledge_base_ids is not None:
            updates["knowledge_base_ids"] = body.knowledge_base_ids
        if "graph" in body.model_fields_set:
            updates["graph"] = body.graph
        if body.nodes is not None:
            updates["nodes"] = [
                node.model_copy(update={"order": index})
                for index, node in enumerate(body.nodes)
            ]
            updates["status"] = (
                TaskStatus.ACTIVE if body.nodes else TaskStatus.DRAFT
            )
            updates["generation_status"] = (
                TaskGenerationStatus.SUCCEEDED
                if body.nodes
                else TaskGenerationStatus.IDLE
            )
        if any(
            field_name in body.model_fields_set
            for field_name in (
                "goal",
                "nodes",
                "graph",
                "source_context",
                "knowledge_base_ids",
            )
        ):
            updates.update(
                {
                    "planning_confirmed_id": None,
                    "planning_confirmed_version": None,
                    "planning_confirmed_fingerprint": None,
                    "planning_confirmed_at": None,
                },
            )
        candidate = current.model_copy(update=updates)
        if (
            candidate.source_template_id is not None
            or candidate.source_template_version is not None
            or body.nodes is not None
            or "graph" in body.model_fields_set
        ):
            validation = await self.validate_task_definition(
                user_id,
                candidate,
                candidate.nodes,
                context=candidate.source_context,
            )
            if not validation.valid:
                raise TaskValidationFailure(validation)
        return await self._store.save_task(candidate)

    def get_dag_capabilities(self) -> TaskDagCapabilitiesResponse:
        """Return the independent Task-local authoring and execution switches."""

        return TaskDagCapabilitiesResponse(
            editing_enabled=self._dag_editing_enabled,
            execution_enabled=self._dag_scheduler_enabled,
            sop_control_enabled=True,
            sop_node_retry_enabled=True,
            scenario_execution_allowlist=[
                {
                    "template_id": template_id,
                    "template_version": template_version,
                }
                for template_id, template_version in sorted(
                    self._dag_scenario_test_allowlist
                    | (
                        self._dag_scenario_allowed_templates
                        if self._dag_scenario_execution_enabled
                        else frozenset()
                    ),
                )
            ],
        )

    def _ensure_dag_editing_enabled(self) -> None:
        if not self._dag_editing_enabled:
            raise TaskWorkflowConflictError(
                "Custom DAG editing is disabled by the Task service configuration."
            )

    async def ensure_dag_graph_editable(
        self,
        user_id: str,
        task: TaskRecord | None,
    ) -> None:
        """Check Task authoring permission and state before graph validation or writes."""

        self._ensure_dag_editing_enabled()
        if task is None:
            return
        if task.status is TaskStatus.ARCHIVED:
            raise TaskWorkflowConflictError(
                "An archived Task cannot change its DAG definition."
            )
        if task.generation_status is TaskGenerationStatus.GENERATING:
            raise TaskWorkflowConflictError(
                "A Task cannot change its DAG definition while its nodes are being generated."
            )
        has_template_provenance = bool(
            task.source_template_id or task.source_template_version
        )
        traceable_custom_copy = bool(
            task.workflow_mode is TaskWorkflowMode.CUSTOM_FLOW
            and task.source_template_id
            and task.source_template_version
            and task.source_task_id
            and task.source_task_revision is not None
            and self._template_validator.template_for_task(task) is not None
        )
        if (
            task.workflow_mode is TaskWorkflowMode.TEMPLATE_INSTANCE
            or (has_template_provenance and not traceable_custom_copy)
            or (
                task.workflow_mode is TaskWorkflowMode.CUSTOM_FLOW
                and not traceable_custom_copy
            )
        ):
            raise TaskWorkflowConflictError(
                "DAG editing requires a user-owned custom copy with a verifiable source template and source Task revision."
            )
        runs = await self._store.list_runs(user_id, task.id)
        if any(run.status not in _TERMINAL_RUN_STATUSES for run in runs):
            raise TaskWorkflowConflictError(
                "A Task with an active Run cannot change its DAG definition."
            )

    async def validate_task_definition(
        self,
        user_id: str,
        task: TaskRecord | None,
        nodes: list[TaskNode],
        *,
        context: TaskContext | None = None,
        run_input: str | None = None,
        graph: TaskGraph | None = None,
    ) -> TaskValidationResponse:
        """Validate structure and template rules; runtime readiness is preflight-only."""

        del user_id, context
        result = self._template_validator.validate(
            task,
            nodes,
            run_input=run_input,
        )
        graph_to_validate = graph if graph is not None else (
            task.graph if task is not None else None
        )
        if graph_to_validate is None:
            return result
        try:
            validate_task_graph(graph_to_validate, nodes)
        except TaskGraphValidationError as error:
            issue = TaskValidationIssue(path=error.path, message=error.message)
            return result.model_copy(
                update={
                    "valid": False,
                    "ready": False if result.ready is not None else None,
                    "issues": [*result.issues, issue],
                }
            )
        if task is not None and task.source_template_id is not None:
            dependency_issues = self._validate_template_dag_dependencies(
                task,
                graph_to_validate,
                nodes,
            )
            if dependency_issues:
                return result.model_copy(
                    update={
                        "valid": False,
                        "ready": False if result.ready is not None else None,
                        "issues": [*result.issues, *dependency_issues],
                    }
                )
        return result

    def _validate_template_dag_dependencies(
        self,
        task: TaskRecord,
        graph: TaskGraph,
        nodes: list[TaskNode],
    ) -> list[TaskValidationIssue]:
        """Keep direct data handoffs between retained source-template steps."""

        template = self._template_validator.template_for_task(task)
        if template is None:
            return [
                TaskValidationIssue(
                    path="source_template_id",
                    message="DAG 的来源模板版本无法解析；为避免丢失冻结业务契约，不能保存图结构。",
                )
            ]
        node_ids = {node.id for node in nodes}
        retained_source_nodes = [
            task.template_step_bindings.get(step.step_key)
            for step in template.steps
            if task.template_step_bindings.get(step.step_key) in node_ids
        ]
        direct_edges = {
            (edge.source_node_id, edge.target_node_id)
            for edge in graph.edges
        }
        missing_handoffs = [
            (source_id, target_id)
            for source_id, target_id in zip(
                retained_source_nodes,
                retained_source_nodes[1:],
            )
            if (source_id, target_id) not in direct_edges
        ]
        if not missing_handoffs:
            return []
        if self._is_s01_parallel_pilot_graph(
            (task.source_template_id, task.source_template_version),
            retained_source_nodes,
            graph,
        ):
            return []
        return [
            TaskValidationIssue(
                path="graph.edges",
                message=(
                    "来源模板保留节点之间必须维持模板顺序的直接数据依赖；"
                    "可在原有节点之间增加自定义分支并汇合，但不能断开相邻来源步骤的输入传递。"
                ),
            )
        ]

    def _scenario_dag_execution_blocker(
        self,
        task: TaskRecord,
        graph: TaskGraph,
    ) -> str | None:
        """Authorize exact scenario scopes and retain frozen-graph safeguards.

        Formal runtime scopes come only from the Task-local opt-in settings.
        The separate in-process allowlist remains available to isolated tests.
        Both paths still require the frozen source copy and either its exact
        linear graph or the already-reviewed S01 pilot graph.
        """

        source = (task.source_template_id, task.source_template_version)
        if None in source:
            return "版本化场景 DAG 缺少完整模板来源，已拒绝执行。"
        formally_authorized = (
            self._dag_scheduler_enabled
            and self._dag_scenario_execution_enabled
            and source in self._dag_scenario_allowed_templates
        )
        test_authorized = source in self._dag_scenario_test_allowlist
        if not formally_authorized and not test_authorized:
            return (
                "版本化场景 DAG 仅允许精确模板 ID/版本通过 Task 内正式执行配置"
                "或隔离测试授权执行；当前实例未获授权。"
                " DAG execution for versioned scenario Tasks remains blocked."
            )
        if (
            task.workflow_mode is not TaskWorkflowMode.CUSTOM_FLOW
            or not task.source_task_id
            or task.source_task_revision is None
        ):
            return "仅可测试执行保留可验证来源的用户自定义模板副本。"
        template = self._template_validator.template_for_task(task)
        if template is None:
            return "冻结来源模板版本无法解析，已拒绝场景 DAG 执行。"

        expected_keys = {step.step_key for step in template.steps}
        if set(task.template_step_bindings) != expected_keys:
            return "来源模板步骤绑定不完整，已拒绝场景 DAG 执行。"
        source_node_ids = [
            task.template_step_bindings[step.step_key]
            for step in template.steps
        ]
        ordered_nodes = sorted(task.nodes, key=lambda item: (item.order, item.id))
        if (
            len(ordered_nodes) != len(template.steps)
            or [node.id for node in ordered_nodes] != source_node_ids
        ):
            return (
                "场景 DAG 执行验收当前仅覆盖模板原节点的线性副本；"
                "新增、删除或重排节点仍可保存，但不能启动 Run。"
            )

        try:
            canonical_nodes = TaskTemplateCompiler().compile_nodes(
                template,
                [],
                require_tool_bindings=False,
            ).nodes
        except Exception:  # noqa: BLE001 — unknown source compilation fails closed
            return "冻结来源模板无法重新编译为规范节点，已拒绝场景 DAG 执行。"

        for step, node, canonical in zip(
            template.steps,
            ordered_nodes,
            canonical_nodes,
        ):
            if node.type.value != step.kind:
                return f"来源步骤 {step.step_key} 的节点类型与冻结模板不一致。"
            if node.capability_key != step.capability_key:
                return f"来源步骤 {step.step_key} 的能力绑定与冻结模板不一致。"
            if node.output_schema != step.output_schema:
                return f"来源步骤 {step.step_key} 的输出 Schema 与冻结模板不一致。"
            actual_contract = (
                node.output_contract.model_dump(mode="json")
                if node.output_contract is not None
                else None
            )
            expected_contract = (
                step.output_contract.model_dump(mode="json")
                if step.output_contract is not None
                else None
            )
            if actual_contract != expected_contract:
                return f"来源步骤 {step.step_key} 的输出契约与冻结模板不一致。"
            actual_node = node.model_dump(mode="json")
            expected_node = canonical.model_dump(mode="json")
            actual_node.pop("id", None)
            expected_node.pop("id", None)
            if actual_node != expected_node:
                return (
                    f"来源步骤 {step.step_key} 的冻结节点配置已改变，"
                    "拒绝场景 DAG 执行。"
                )
            if (
                (node.artifact.format.value, node.artifact.filename)
                if node.artifact is not None
                else None
            ) != (
                (step.artifact.format, step.artifact.filename)
                if step.artifact is not None
                else None
            ):
                return f"来源步骤 {step.step_key} 的 Artifact 配置与冻结模板不一致。"

        expected_edges = {
            (left, right)
            for left, right in zip(source_node_ids, source_node_ids[1:])
        }
        actual_edges = {
            (edge.source_node_id, edge.target_node_id)
            for edge in graph.edges
        }
        if (
            len(graph.edges) != len(expected_edges)
            or actual_edges != expected_edges
        ):
            if self._is_s01_parallel_pilot_graph(
                source,
                source_node_ids,
                graph,
            ):
                return None
            return (
                "当前版本化场景 DAG 仅允许已验收的冻结线性图；"
                "未列入精确测试授权的非线性图仍被阻断。"
            )
        if graph.input_bindings:
            return (
                "当前版本化场景 DAG 的线性兼容图不允许额外输入绑定。"
            )
        if graph.max_concurrency != 1:
            return "并发度为 1 是版本化线性兼容图的执行前提。"
        return None

    @staticmethod
    def _is_s01_parallel_pilot_graph(
        source: tuple[str | None, str | None],
        source_node_ids: list[str],
        graph: TaskGraph,
    ) -> bool:
        """Match the sole E2 test-authorized non-linear scenario graph."""

        if source != ("s01_project_init", "3.0.0") or len(source_node_ids) != 7:
            return False
        (
            collect_id,
            profile_id,
            similar_id,
            rules_id,
            draft_id,
            review_id,
            output_id,
        ) = source_node_ids
        expected_edges = {
            (collect_id, profile_id),
            (collect_id, similar_id),
            (collect_id, rules_id),
            (profile_id, draft_id),
            (similar_id, draft_id),
            (rules_id, draft_id),
            (draft_id, review_id),
            (review_id, output_id),
        }
        actual_edges = {
            (edge.source_node_id, edge.target_node_id)
            for edge in graph.edges
        }

        def bind(source_node_id: str, source_value: str) -> dict[str, str]:
            return {
                "source_node_id": source_node_id,
                "source_value": source_value,
            }

        expected_bindings = {
            similar_id: {
                field: bind(collect_id, "output")
                for field in ("project_name", "project_brief", "industry")
            },
            rules_id: {
                field: bind(collect_id, "output")
                for field in ("project_brief", "industry", "scope")
            },
            draft_id: {
                "project_profile": bind(profile_id, "output"),
                "similar_projects": bind(similar_id, "result"),
                "rule_candidates": bind(rules_id, "result"),
            },
        }
        actual_bindings = {
            target_id: {
                name: binding.model_dump(mode="json")
                for name, binding in bindings.items()
            }
            for target_id, bindings in graph.input_bindings.items()
        }
        return bool(
            graph.max_concurrency == 3
            and not graph.resource_limits
            and len(graph.edges) == len(expected_edges)
            and actual_edges == expected_edges
            and actual_bindings == expected_bindings
        )

    @staticmethod
    def _is_linear_source_template_run(run: TaskRunRecord) -> bool:
        """Return whether a frozen source Run has the SOP handoff shape."""

        if run.source_template_id is None or run.graph is None:
            return False
        ordered_ids = [
            node.id for node in sorted(run.nodes, key=lambda item: item.order)
        ]
        expected_edges = {
            (left, right) for left, right in zip(ordered_ids, ordered_ids[1:])
        }
        actual_edges = {
            (edge.source_node_id, edge.target_node_id)
            for edge in run.graph.edges
        }
        return bool(
            len(run.nodes) == len(ordered_ids)
            and run.graph.max_concurrency == 1
            and not run.graph.input_bindings
            and len(run.graph.edges) == len(expected_edges)
            and actual_edges == expected_edges
        )

    @staticmethod
    def _validate_graph_or_raise(
        graph: TaskGraph,
        nodes: list[TaskNode],
        task_id: str,
    ) -> None:
        """Keep invalid opt-in graphs out of Task persistence."""

        try:
            validate_task_graph(graph, nodes)
        except TaskGraphValidationError as error:
            raise TaskValidationFailure(
                TaskValidationResponse(
                    task_id=task_id,
                    valid=False,
                    ready=False,
                    issues=[
                        TaskValidationIssue(
                            path=error.path,
                            message=error.message,
                        )
                    ],
                )
            ) from error

    async def preflight_task(
        self,
        user_id: str,
        task_id: str,
        body: TaskPreflightRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskValidationResponse | None:
        """Check ownership, workflow and current capabilities without writes."""

        task = await self._store.get_task(user_id, task_id)
        if task is None:
            return None
        return await self._preflight_task_record(
            user_id,
            task,
            body,
            knowledge_gateway=knowledge_gateway,
        )

    async def _preflight_task_record(
        self,
        user_id: str,
        task: TaskRecord,
        body: TaskPreflightRequest,
        *,
        tool_schemas: list[TaskToolSchema] | None = None,
        tool_discovery_error: bool = False,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskValidationResponse:
        """Inspect a fixed Task snapshot without creating a Run or calling Tools."""

        context = task.source_context.model_copy(
            update=body.context.model_dump(exclude_none=True),
        )
        run_input = body.input.strip() or task.goal.strip()
        needs_tools = _needs_external_tool_discovery(task)
        if tool_schemas is None:
            if needs_tools:
                try:
                    tool_schemas = await self.list_tools(
                        user_id,
                        context,
                        strict=True,
                    )
                except Exception as error:  # noqa: BLE001 — report unavailable
                    logger.warning(
                        "Task preflight could not inspect tools for task=%s: %s",
                        task.id,
                        error,
                    )
                    tool_schemas = []
                    tool_discovery_error = True
        tool_schemas = _with_task_native_tool_schemas(
            task.nodes,
            tool_schemas or [],
            enabled=knowledge_gateway is not None,
        )
        validation_tool_schemas = None if tool_discovery_error else tool_schemas
        validation = self._template_validator.validate(
            task,
            task.nodes,
            tool_schemas=validation_tool_schemas,
            run_input=run_input,
        )
        issues = list(validation.issues)
        if tool_discovery_error:
            issues.append(
                TaskValidationIssue(
                    path="tools",
                    message="当前会话工具目录无法访问，不能确认 Tool 是否存在。",
                ),
            )
        if task.status != TaskStatus.ACTIVE:
            issues.append(
                TaskValidationIssue(
                    path="status",
                    message="只有已保存的有效 Task 才能运行。",
                ),
            )
        if not task.nodes:
            issues.append(
                TaskValidationIssue(
                    path="nodes",
                    message="Task 至少需要一个可运行节点。",
                ),
            )
        if task.graph is not None:
            try:
                validate_task_graph(task.graph, task.nodes)
            except TaskGraphValidationError as error:
                issues.append(
                    TaskValidationIssue(
                        path=error.path,
                        message=error.message,
                    ),
                )
            if not self._dag_scheduler_enabled:
                issues.append(
                    TaskValidationIssue(
                        path="graph.execution_enabled",
                        message=(
                            "DAG 执行开关当前关闭；图定义可以编辑和保存，"
                            "但服务端不会创建 DAG Run。"
                        ),
                    ),
                )
            if (
                task.source_template_id is not None
                or task.source_template_version is not None
            ):
                scenario_dag_blocker = self._scenario_dag_execution_blocker(
                    task,
                    task.graph,
                )
                if scenario_dag_blocker:
                    issues.append(
                        TaskValidationIssue(
                            path="graph.scenario_execution",
                            message=(
                                "版本化业务模板 DAG 当前通过 Task 内场景执行安全门禁："
                                + scenario_dag_blocker
                                + " 编辑和保存仍可继续；未授权来源和非线性图继续 fail closed。"
                            ),
                        ),
                    )
        capability_checks = await self._build_capability_checks(
            user_id=user_id,
            task=task,
            nodes=task.nodes,
            context=context,
            run_input=run_input,
            tool_schemas=tool_schemas or [],
            tool_discovery_error=tool_discovery_error,
            missing_capabilities=validation.missing_capabilities,
            knowledge_gateway=knowledge_gateway,
        )
        satisfied_artifact_capabilities = {
            check.capability_key
            for check in capability_checks
            if is_task_artifact_capability(check.capability_key)
            and check.status == "available"
        }
        template = self._template_validator.template_for_task(task)
        required_template_capabilities = (
            set(template.required_capabilities) if template is not None else set()
        )
        missing_capabilities = [
            capability
            for capability in validation.missing_capabilities
            if capability not in satisfied_artifact_capabilities
        ]
        missing_capabilities = sorted(
            {
                *missing_capabilities,
                *(
                    check.capability_key
                    for check in capability_checks
                    if check.status == "missing"
                    and check.capability_key in required_template_capabilities
                ),
            },
            key=str.casefold,
        )
        for check in capability_checks:
            if check.status != "available":
                issues.append(
                    TaskValidationIssue(
                        path=f"capabilities.{check.capability_key}",
                        message=check.message,
                    ),
                )
        ready = (
            not issues
            and not missing_capabilities
            and not validation.blocked_capabilities
            and all(check.status == "available" for check in capability_checks)
        )
        return validation.model_copy(
            update={
                "issues": issues,
                "valid": ready,
                "ready": ready,
                "capability_checks": capability_checks,
                "missing_capabilities": missing_capabilities,
            },
        )

    async def _build_capability_checks(
        self,
        *,
        user_id: str,
        task: TaskRecord,
        nodes: list[TaskNode],
        context: TaskContext,
        run_input: str,
        tool_schemas: list[TaskToolSchema],
        missing_capabilities: list[str],
        tool_discovery_error: bool = False,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> list[TaskCapabilityCheck]:
        """Report tool, schema, permission, and source verification separately."""

        template = self._template_validator.template_for_task(task)
        steps_by_node_id = {
            task.template_step_bindings[step.step_key]: step
            for step in (template.steps if template is not None else [])
            if step.step_key in task.template_step_bindings
        }
        tools_by_name: dict[str, list[TaskToolSchema]] = {}
        for tool in tool_schemas:
            tools_by_name.setdefault(tool.name, []).append(tool)
        plan_issues = validate_task_plan(
            {"nodes": nodes},
            tool_schemas,
            require_final_agent=False,
        )
        run_context = ExecutionContext(
            **context.model_dump(),
            user_id=user_id,
            task_id=task.id,
            run_id="task-preflight",
            task_revision=task.revision,
            initial_input=run_input,
        )
        permission_check = getattr(
            self._agentscope_executor,
            "preflight_tool_permission",
            None,
        )
        checks: list[TaskCapabilityCheck] = []
        found_capabilities: set[str] = set()

        for index, node in enumerate(nodes):
            if not isinstance(node.config, ToolStepConfig):
                continue
            template_step = steps_by_node_id.get(node.id)
            capability_key = (
                template_step.capability_key
                if template_step is not None
                else node.capability_key
            ) or node.config.tool_name
            found_capabilities.add(capability_key)
            if is_task_native_capability(capability_key):
                checks.append(
                    await self._preflight_task_native_capability(
                        user_id=user_id,
                        task=task,
                        nodes=nodes,
                        node=node,
                        capability_key=capability_key,
                        knowledge_gateway=knowledge_gateway,
                    ),
                )
                continue
            contract = capability_contract(capability_key)
            candidates = tools_by_name.get(node.config.tool_name, [])
            tool = candidates[0] if len(candidates) == 1 else None
            tool_available = bool(candidates)
            interface_compatible: bool | None = None
            permission_status = "unavailable"
            permission_message = "当前 Task 执行器未提供权限预检接口。"
            side_effect = capability_external_side_effect(capability_key)

            if tool_discovery_error:
                tool_available = None
                interface_compatible = None
                message = "当前会话工具目录无法访问，不能确认工具绑定状态。"
                status = "unverified"
            elif not candidates:
                message = f"当前会话缺少工具 {node.config.tool_name}。"
                status = "missing"
            elif len(candidates) > 1:
                message = f"工具名称 {node.config.tool_name} 存在重复注册，无法唯一绑定。"
                interface_compatible = False
                status = "incompatible"
            else:
                assert tool is not None
                side_effect = task_tool_has_external_side_effect(
                    tool,
                    capability_key=capability_key,
                )
                if not tool.input_schema:
                    interface_compatible = False
                    message = "工具未提供可校验的 JSON Schema。"
                elif contract is not None:
                    contract_issues = validate_contract_tool_schema(
                        capability_key,
                        tool.input_schema,
                    )
                    interface_compatible = not contract_issues
                    message = "；".join(contract_issues)
                else:
                    target = f"nodes[{index}].config.arguments"
                    node_issues = [
                        issue
                        for issue in plan_issues
                        if issue.path == target
                        or issue.path.startswith(target + ".")
                    ]
                    interface_compatible = not node_issues
                    message = "；".join(issue.message for issue in node_issues)

                arguments, resolved = _preflight_arguments(
                    node.config.arguments,
                    run_input,
                )
                if callable(permission_check):
                    try:
                        permission_status, permission_message = (
                            await permission_check(
                                tool_name=node.config.tool_name,
                                arguments=arguments,
                                context=run_context,
                                arguments_resolved=resolved,
                            )
                        )
                    except Exception as error:  # noqa: BLE001 — fail closed
                        logger.warning(
                            "Task permission preflight failed for task=%s node=%s: %s",
                            task.id,
                            node.id,
                            error,
                        )
                        permission_status = "unavailable"
                        permission_message = "当前会话权限无法完成预检。"

                business_verified = self._business_data_verified(capability_key)
                if side_effect:
                    status = "unsupported"
                    if capability_external_side_effect(capability_key):
                        message = "该 Task 能力明确包含外部副作用，当前 Task 阶段不支持运行。"
                    else:
                        message = (
                            "当前工具未被只读 MCP 元数据或 Task 能力契约确认为只读；"
                            "为避免误执行外部写入，当前 Task 阶段按副作用风险阻止运行。"
                        )
                elif interface_compatible is False:
                    status = "incompatible"
                    message = message or "工具接口与 Task 节点参数不兼容。"
                elif permission_status in {"denied", "requires_confirmation"}:
                    status = "permission_denied"
                    message = permission_message
                elif permission_status == "unavailable":
                    status = "unverified"
                    message = permission_message
                elif business_verified is False:
                    status = "unverified"
                    message = "工具已绑定，但真实业务数据源尚未完成验证。"
                elif permission_status == "requires_runtime_check":
                    status = "unverified"
                    message = permission_message
                else:
                    status = "available"
                    message = permission_message

            business_verified = self._business_data_verified(capability_key)
            if status == "missing":
                message = message or "当前会话没有绑定可用工具。"
            checks.append(
                TaskCapabilityCheck(
                    step_id=node.id,
                    step_name=node.name,
                    capability_key=capability_key,
                    category=capability_category(capability_key),
                    tool_name=node.config.tool_name,
                    tool_available=tool_available,
                    interface_compatible=interface_compatible,
                    permission_status=permission_status,
                    business_data_verified=business_verified,
                    external_side_effect=side_effect,
                    status=status,
                    message=message,
                ),
            )

        required_capabilities = set(missing_capabilities)
        if template is not None:
            required_capabilities.update(
                set(template.required_capabilities) - found_capabilities
            )
        for capability_key in sorted(required_capabilities - found_capabilities):
            if (
                capability_key == "project.authorization.read"
                and any(
                    node.capability_key in {
                        "project.similar_search",
                        "project.rule_check",
                    }
                    for node in nodes
                )
            ):
                # The Task-native RAG preflight below checks this user's exact
                # selected KB scope through the existing authorization API.
                continue
            if is_task_artifact_capability(capability_key):
                checks.append(
                    await self._preflight_task_artifact_capability(
                        capability_key=capability_key,
                        task=task,
                        nodes=nodes,
                        context=run_context,
                    ),
                )
                continue
            business_verified = self._business_data_verified(capability_key)
            checks.append(
                TaskCapabilityCheck(
                    step_name=capability_key,
                    capability_key=capability_key,
                    category=capability_category(capability_key),
                    tool_available=False,
                    interface_compatible=None,
                    permission_status="not_applicable",
                    business_data_verified=business_verified,
                    external_side_effect=capability_external_side_effect(
                        capability_key
                    ),
                    status="missing",
                    message=(
                        "模板声明了此必需能力，但没有对应可绑定的 Tool 节点。"
                    ),
                ),
            )
        if any(node.artifact is not None for node in nodes) and not any(
            is_task_artifact_capability(check.capability_key)
            for check in checks
        ):
            checks.append(
                await self._preflight_task_artifact_capability(
                    capability_key="document.render",
                    task=task,
                    nodes=nodes,
                    context=run_context,
                ),
            )
        return checks

    async def _preflight_task_native_capability(
        self,
        *,
        user_id: str,
        task: TaskRecord,
        nodes: list[TaskNode],
        node: TaskNode,
        capability_key: str,
        knowledge_gateway: TaskKnowledgeGateway | None,
    ) -> TaskCapabilityCheck:
        """Check Task RAG and citation adapters against the user's KB scope."""

        base = {
            "step_id": node.id,
            "step_name": node.name,
            "capability_key": capability_key,
            "category": capability_category(capability_key),
            "tool_name": (
                node.config.tool_name
                if isinstance(node.config, ToolStepConfig)
                else None
            ),
            "tool_available": (
                knowledge_gateway is not None
                if is_knowledge_native_capability(capability_key)
                else is_scenario_native_capability(capability_key)
            ),
            "interface_compatible": True,
            "external_side_effect": False,
        }
        if (
            capability_key in {"document.citation_verify", "report.citation_check"}
            and not any(
                item.type is StepType.TOOL
                and is_task_rag_capability(item.capability_key)
                and item.order < node.order
                for item in nodes
            )
        ):
            return TaskCapabilityCheck(
                **base,
                permission_status="not_applicable",
                business_data_verified=False,
                status="missing",
                message="引用核验节点缺少前置的 Task 授权知识库检索节点。",
                error_category="configuration_error",
                error_code="TASK_CITATION_SOURCE_NODE_MISSING",
            )
        if capability_key == "project.rule_check" and not any(
            item.type is StepType.TOOL
            and item.capability_key == "project.similar_search"
            and item.order < node.order
            for item in nodes
        ):
            return TaskCapabilityCheck(
                **base,
                permission_status="not_applicable",
                business_data_verified=False,
                status="missing",
                message="项目规则候选检索缺少前置的授权知识库检索节点。",
                error_category="configuration_error",
                error_code="TASK_PROJECT_RULE_SOURCE_NODE_MISSING",
            )
        if (
            is_scenario_native_capability(capability_key)
            and not is_knowledge_native_capability(capability_key)
        ):
            return TaskCapabilityCheck(
                **base,
                permission_status="not_applicable",
                business_data_verified=False,
                status="available",
                message=(
                    "Task 内数据处理适配可用；步骤结果由通用 Task AI verifier "
                    "按节点验收标准评估。适配器仅处理本次 Task 提交的数据，"
                    "不表示已连接或验证对应外部业务系统。"
                ),
            )
        if knowledge_gateway is None:
            return TaskCapabilityCheck(
                **base,
                permission_status="unavailable",
                business_data_verified=False,
                status="missing",
                message="Task 知识库服务未配置，不能确认授权检索和引用证据。",
                error_category="data_source_not_ready",
                error_code="TASK_KNOWLEDGE_SERVICE_UNAVAILABLE",
            )
        if capability_key in {
            "document.citation_verify",
            "report.citation_check",
        }:
            return TaskCapabilityCheck(
                **base,
                permission_status="not_applicable",
                business_data_verified=False,
                status="available",
                message=(
                    "运行时将把本次 TaskRun 前序授权检索节点保存的 Evidence "
                    "快照提供给通用 Task AI verifier 评估；不会重新检索或接受"
                    "用户声明的来源。知识库访问范围仍由 Task 强制执行，语义支持"
                    "仍需人工复核。"
                ),
            )
        try:
            if capability_key == "planning.template_retrieve":
                retrieve_template = (
                    knowledge_gateway.retrieve_planning_template_evidence
                )
                scope = await retrieve_template(
                    user_id,
                    task.knowledge_base_ids,
                    top_k=12,
                )
                selected = scope["knowledge_bases"]
            else:
                source_material_ids = _task_source_material_ids(
                    task.template_inputs
                )
                scope = await knowledge_gateway.inspect_task_scope(
                    user_id,
                    task.knowledge_base_ids,
                    source_material_ids,
                )
                selected = scope["knowledge_bases"]
        except TaskKnowledgeError as error:
            status = _knowledge_preflight_status(error.code)
            if capability_key == "planning.template_retrieve":
                logger.info(
                    "Task planning-template preflight did not match "
                    "for task=%s: %s",
                    task.id,
                    error.details,
                )
            return TaskCapabilityCheck(
                **base,
                permission_status=(
                    "denied"
                    if error.code == "knowledge_base_unavailable"
                    else "allowed"
                    if error.code
                    in {
                        "source_material_not_found",
                        "source_material_not_ready",
                    }
                    else "unavailable"
                ),
                business_data_verified=False,
                status=status,
                message=error.message,
                error_category=_knowledge_error_category(error.code),
                error_code=_knowledge_preflight_error_code(error.code),
            )
        except Exception as error:  # noqa: BLE001 — fail closed
            logger.info(
                "Task knowledge scope preflight failed for task=%s: %s",
                task.id,
                error,
            )
            return TaskCapabilityCheck(
                **base,
                permission_status="unavailable",
                business_data_verified=False,
                status="unverified",
                message="Task 无法确认当前用户的知识库访问范围。",
                error_category="data_source_not_ready",
                error_code="TASK_KNOWLEDGE_PREFLIGHT_FAILED",
            )

        source_label = "、".join(item["name"] for item in selected[:4])
        if capability_key == "planning.template_retrieve":
            template_names = "、".join(
                match["source"] for match in scope["template_matches"]
            )
            evidence_note = (
                f"，已匹配策划模板“{template_names}”，并核对八章结构、"
                "B-01～B-15 原文和来源字段；项目事实来源将在输入节点后"
                "单独检索，未命中时须标记待核实"
            )
        else:
            evidence_note = (
                "；步骤输出由通用 Task AI verifier 结合本次授权检索结果评估，"
                "引用语义支持仍需人工复核"
            )
        return TaskCapabilityCheck(
            **base,
            permission_status="allowed",
            business_data_verified=False,
            status="available",
            message=(
                f"已按当前用户权限确认知识库“{source_label}”及已索引资料"
                f"{evidence_note}。"
            ),
        )

    async def _preflight_task_artifact_capability(
        self,
        *,
        capability_key: str,
        task: TaskRecord,
        nodes: list[TaskNode],
        context: ExecutionContext,
    ) -> TaskCapabilityCheck:
        """Check an artifact capability against real config and I/O adapters."""

        configured = [node for node in nodes if node.artifact is not None]
        artifact_node = (
            configured[0]
            if len(configured) == 1
            and nodes
            and configured[0] is nodes[-1]
            and nodes[-1].type is not StepType.INPUT
            else None
        )
        base = {
            "step_id": artifact_node.id if artifact_node is not None else None,
            "step_name": (
                artifact_node.name if artifact_node is not None else capability_key
            ),
            "capability_key": capability_key,
            "category": capability_category(capability_key),
            "tool_available": None,
            "permission_status": "not_applicable",
            "business_data_verified": None,
            "external_side_effect": False,
        }
        if artifact_node is None or artifact_node.artifact is None:
            return TaskCapabilityCheck(
                **base,
                interface_compatible=None,
                status="missing",
                message=(
                    "模板要求文件产物能力；请在最终输出节点配置受支持的 Task 产物。"
                ),
                error_category="configuration_error",
                error_code="TASK_ARTIFACT_CONFIG_MISSING",
            )

        artifact_format = artifact_node.artifact.format
        try:
            TemplateArtifactConfig.model_validate(
                {
                    "format": artifact_format.value,
                    "filename": artifact_node.artifact.filename,
                },
            )
        except ValidationError:
            return TaskCapabilityCheck(
                **base,
                interface_compatible=False,
                status="incompatible",
                message="最终输出节点的文件产物配置无效。",
                error_category="configuration_error",
                error_code="TASK_ARTIFACT_CONFIG_INVALID",
            )
        if not artifact_renderer_available(artifact_format):
            return TaskCapabilityCheck(
                **base,
                interface_compatible=False,
                status="unverified",
                message=(
                    f"当前服务未安装 {artifact_format.value.upper()} 产物渲染组件。"
                ),
                error_category="artifact_generation_failed",
                error_code="TASK_ARTIFACT_RENDERER_UNAVAILABLE",
            )
        if self._artifact_writer is None:
            return TaskCapabilityCheck(
                **base,
                interface_compatible=True,
                status="unverified",
                message="当前 Task 执行器未绑定 Workspace Artifact Writer。",
                error_category="artifact_generation_failed",
                error_code="TASK_ARTIFACT_WRITER_UNAVAILABLE",
            )

        preflight_writer = getattr(
            self._artifact_writer,
            "preflight_artifact_writer",
            None,
        )
        if not callable(preflight_writer):
            return TaskCapabilityCheck(
                **base,
                interface_compatible=True,
                status="unverified",
                message="当前 Artifact Writer 无法预检用户 Workspace 读写边界。",
                error_category="artifact_generation_failed",
                error_code="TASK_ARTIFACT_PREFLIGHT_UNAVAILABLE",
            )
        try:
            available, message = await preflight_writer(context=context)
        except Exception as error:  # noqa: BLE001 — fail closed
            logger.info(
                "Task artifact writer preflight failed for task=%s: %s",
                task.id,
                error,
            )
            available = False
            message = "当前用户的 Task Workspace 无法完成文件产物预检。"
        return TaskCapabilityCheck(
            **base,
            interface_compatible=True,
            status="available" if available else "unverified",
            message=message
            or (
                "Task Workspace Artifact Writer 可用。"
                if available
                else "Task Workspace Artifact Writer 尚未通过预检。"
            ),
            error_category=(
                None
                if available
                else (
                    "permission_denied"
                    if _looks_like_permission_failure(message)
                    else "artifact_generation_failed"
                )
            ),
            error_code=(
                None
                if available
                else (
                    "TASK_ARTIFACT_PERMISSION_DENIED"
                    if _looks_like_permission_failure(message)
                    else "TASK_ARTIFACT_PREFLIGHT_FAILED"
                )
            ),
        )

    def _business_data_verified(self, capability_key: str) -> bool | None:
        """Ask the server-side executor whether the business source is verified."""

        checker = getattr(self._agentscope_executor, "business_data_verified", None)
        if not callable(checker):
            return capability_business_verified(capability_key)
        try:
            result = checker(capability_key)
        except Exception as error:  # noqa: BLE001 — fail closed
            logger.warning(
                "Task business capability verification failed for %s: %s",
                capability_key,
                error,
            )
            return False if capability_business_verified(capability_key) is False else None
        if result is True or result is False or result is None:
            return result
        return False if capability_business_verified(capability_key) is False else None

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        """Archive a task while keeping its runs inspectable."""

        return await self._store.delete_task(user_id, task_id)

    async def create_run(
        self,
        user_id: str,
        task_id: str,
        body: TaskRunRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Snapshot a task and schedule one asynchronous execution."""

        task = await self._store.get_task(user_id, task_id)
        if task is None or task.status != TaskStatus.ACTIVE or not task.nodes:
            return None
        planning_decision: TaskPlanningDecision | None = None
        if task.planning_decision is not None:
            planning_decision = TaskPlanningDecision.model_validate(
                task.planning_decision,
            )
            if (
                task.planning_confirmed_id != planning_decision.planning_id
                or task.planning_confirmed_version != task.planning_version
                or task.planning_confirmed_fingerprint
                != _task_plan_snapshot_fingerprint(task.nodes, task.graph)
            ):
                raise TaskWorkflowConflictError(
                    "智能规划尚未由用户确认，或 Task 内容已在确认后修改；请重新确认规划。",
                )
            if planning_decision.validation_status != "valid":
                raise TaskWorkflowConflictError(
                    "智能规划仍处于待审查或无效状态，不能创建 Run。",
                )
        if task.graph is not None:
            self._validate_graph_or_raise(task.graph, task.nodes, task.id)
            if not self._dag_scheduler_enabled:
                raise TaskWorkflowConflictError(
                    "DAG execution is disabled; enable the Task DAG scheduler explicitly after backend safety validation."
                )
            if (
                task.source_template_id is not None
                or task.source_template_version is not None
            ):
                scenario_dag_blocker = self._scenario_dag_execution_blocker(
                    task,
                    task.graph,
                )
                if scenario_dag_blocker:
                    raise TaskWorkflowConflictError(scenario_dag_blocker)
        is_s02_v4_task = bool(
            task.workflow_mode == TaskWorkflowMode.TEMPLATE_INSTANCE
            and task.source_template_id == "s02_project_planning"
            and task.source_template_version in {"4.0.0", "4.1.0"}
        )
        is_s02_v5_task = bool(
            task.source_template_id == "s02_project_planning"
            and task.source_template_version
            in S02_LIGHTWEIGHT_TEMPLATE_VERSIONS
        )
        if body.delivery_mode == "demo_draft" and not (
            is_s02_v4_task
        ):
            raise TaskValidationFailure(
                TaskValidationResponse(
                    task_id=task.id,
                    valid=False,
                    ready=False,
                    issues=[
                        TaskValidationIssue(
                            path="delivery_mode",
                            message=(
                                "demo_draft 仅适用于标准 S02 v4.0.0 或 v4.1.0 模板 Task；"
                                "其他模板、自定义流程和自由 Planner 任务继续使用严格流程。"
                            ),
                        )
                    ],
                )
            )
        if body.delivery_mode == "lightweight_draft" and not is_s02_v5_task:
            raise TaskValidationFailure(
                TaskValidationResponse(
                    task_id=task.id,
                    valid=False,
                    ready=False,
                    issues=[
                        TaskValidationIssue(
                            path="delivery_mode",
                            message=(
                                "lightweight_draft 仅适用于标准 S02 v5.x 轻量模板 Task。"
                            ),
                        )
                    ],
                )
            )
        run_delivery_mode = (
            "lightweight_draft" if is_s02_v5_task else body.delivery_mode
        )
        preflight_body = TaskPreflightRequest(
            input=body.input,
            context=body.context,
        )
        context = task.source_context.model_copy(
            update=body.context.model_dump(exclude_none=True),
        )
        needs_tools = _needs_external_tool_discovery(task)
        tool_discovery_error = False
        if needs_tools:
            try:
                tool_schemas = await self.list_tools(
                    user_id,
                    context,
                    strict=True,
                )
            except Exception as error:  # noqa: BLE001 — fail closed
                logger.warning(
                    "Task run could not inspect tools for task=%s: %s",
                    task.id,
                    error,
                )
                tool_schemas = []
                tool_discovery_error = True
        else:
            tool_schemas = []
        tool_schemas = _with_task_native_tool_schemas(
            task.nodes,
            tool_schemas,
            enabled=knowledge_gateway is not None,
        )
        validation = await self._preflight_task_record(
            user_id,
            task,
            preflight_body,
            tool_schemas=tool_schemas,
            tool_discovery_error=tool_discovery_error,
            knowledge_gateway=knowledge_gateway,
        )
        if not validation.ready:
            raise TaskValidationFailure(validation)
        if planning_decision is not None and not planning_decision.execution_allowed:
            raise TaskWorkflowConflictError(
                "Planner 服务端授权策略尚未允许执行；请查看规划原因并重新规划或调整合法权限。"
            )

        latest = await self._store.get_task(user_id, task_id)
        if latest is None or latest.status != TaskStatus.ACTIVE or not latest.nodes:
            return None
        if latest.revision != task.revision or latest.nodes != task.nodes:
            task = latest
            context = task.source_context.model_copy(
                update=body.context.model_dump(exclude_none=True),
            )
            needs_tools = _needs_external_tool_discovery(task)
            tool_discovery_error = False
            if needs_tools:
                try:
                    tool_schemas = await self.list_tools(
                        user_id,
                        context,
                        strict=True,
                    )
                except Exception as error:  # noqa: BLE001 — fail closed
                    logger.warning(
                        "Task run could not inspect updated tools for task=%s: %s",
                        task.id,
                        error,
                    )
                    tool_schemas = []
                    tool_discovery_error = True
            else:
                tool_schemas = []
            tool_schemas = _with_task_native_tool_schemas(
                task.nodes,
                tool_schemas,
                enabled=knowledge_gateway is not None,
            )
            validation = await self._preflight_task_record(
                user_id,
                task,
                preflight_body,
                tool_schemas=tool_schemas,
                tool_discovery_error=tool_discovery_error,
                knowledge_gateway=knowledge_gateway,
            )
            if not validation.ready:
                raise TaskValidationFailure(validation)

        run_input = body.input.strip() or task.goal.strip()
        run_nodes = self._template_validator.runtime_nodes(
            task,
            task.nodes,
            tool_schemas,
        )
        run_id = uuid4().hex
        security_reviews_by_node: dict[str, list[dict[str, Any]]] = {}
        preflight_checked_at = _utc_now().isoformat()
        run_node_ids = {node.id for node in run_nodes}
        for check in validation.capability_checks:
            # A check without a concrete node binding remains a Run-level
            # result. Do not imply that it reviewed every node.
            if check.step_id is None or check.step_id not in run_node_ids:
                continue
            security_reviews_by_node.setdefault(check.step_id, []).append(
                {
                    "source": "task_preflight",
                    "checked_at": preflight_checked_at,
                    **check.model_dump(mode="json"),
                },
            )
        initial_sop_state = None
        if task.graph is None:
            initial_sop_state = TaskSOPRunState(
                id=run_id,
                inputs=[UserMsg(name="user", content=run_input)],
                steps=[TaskStepState() for _ in run_nodes],
                task_node_ids=[node.id for node in run_nodes],
            ).model_dump(mode="json")
        run = TaskRunRecord(
            id=run_id,
            task_id=task.id,
            user_id=user_id,
            task_revision=task.revision,
            engine="dag" if task.graph is not None else "sop",
            graph=task.graph,
            workflow_mode=task.workflow_mode,
            source_template_id=task.source_template_id,
            source_template_version=task.source_template_version,
            source_task_id=task.source_task_id,
            source_task_revision=task.source_task_revision,
            planning_id=(
                task.planning_confirmed_id
                if task.planning_decision is not None
                else None
            ),
            planning_version=(
                task.planning_confirmed_version
                if task.planning_decision is not None
                else None
            ),
            planning_snapshot=(
                json.loads(json.dumps(task.planning_decision))
                if task.planning_decision is not None
                else None
            ),
            node_snapshot_fingerprint=_node_snapshot_fingerprint(run_nodes),
            delivery_mode=run_delivery_mode,
            nodes=run_nodes,
            node_runs=[
                NodeRunRecord(
                    node_id=node.id,
                    name=node.name,
                    prompt=node.prompt,
                    type=node.type,
                    order=node.order,
                    metadata=(
                        {"capability_key": node.capability_key}
                        if node.capability_key
                        else {}
                    ),
                    security_reviews=security_reviews_by_node.get(
                        node.id,
                        [],
                    ),
                )
                for node in run_nodes
            ],
            knowledge_base_ids=task.knowledge_base_ids,
            source_material_ids=_task_source_material_ids(
                task.template_inputs
            ),
            input=run_input,
            context=context,
            sop_state=initial_sop_state,
        )
        await self._store.save_run(run)
        await self._store.save_task(
            task.model_copy(
                update={
                    "last_run_id": run.id,
                    "last_run_status": RunStatus.QUEUED,
                },
            ),
        )
        self._run_users[run.id] = user_id
        self._schedule_run(run, knowledge_gateway)
        return await self._store.get_run(user_id, run.id)

    async def get_run(
        self,
        user_id: str,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Get one run owned by the caller."""

        run = await self._store.get_run(user_id, run_id)
        self._recover_dag_run_if_needed(run, knowledge_gateway)
        return run

    async def list_runs(
        self,
        user_id: str,
        task_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> list[TaskRunRecord]:
        """List all runs for one task."""

        if await self._store.get_task(user_id, task_id) is None:
            return []
        runs = await self._store.list_runs(user_id, task_id)
        for run in runs:
            self._recover_dag_run_if_needed(run, knowledge_gateway)
        return runs

    def _recover_dag_run_if_needed(
        self,
        run: TaskRunRecord | None,
        knowledge_gateway: TaskKnowledgeGateway | None,
    ) -> None:
        """Lazily schedule recoverable durable Task Runs from read paths."""

        if (
            run is None
            or run.engine not in {"dag", "sop"}
            or run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}
            or run.control_mode in {"paused", "stop_requested", "stopped"}
        ):
            return
        if run.engine == "dag" and not self._dag_scheduler_enabled:
            return
        active = self._active_runs.get(run.id)
        if active is not None and not active.done():
            return
        self._run_users[run.id] = run.user_id
        self._schedule_run(run, knowledge_gateway)

    async def cancel_run(
        self,
        user_id: str,
        run_id: str,
        request: TaskRunControlRequest | None = None,
    ) -> TaskRunRecord | None:
        """Persist cancellation and notify the worker that owns execution."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.status in _TERMINAL_RUN_STATUSES:
            if request is not None:
                raise TaskWorkflowConflictError(
                    "A terminal Task Run cannot be stopped again."
                )
            return run
        command_id = (
            f"run.stop:{request.request_id}"
            if request is not None
            else None
        )
        if request is not None:
            if command_id in run.processed_command_ids:
                return run
            if run.run_version != request.expected_run_version:
                raise TaskStoreConflictError("Task Run version conflict.")
        # Record intent before yielding to storage or cancellation.  A task
        # executor may catch CancelledError internally and otherwise continue
        # to write a later failed/succeeded state.
        self._cancel_requested_run_ids.add(run_id)
        active = self._active_runs.get(run_id)
        if active is not None and request is None:
            active.cancel()
        if run.engine in {"dag", "sop"}:
            for _ in range(16):
                if run is None:
                    return None
                if run.status in _TERMINAL_RUN_STATUSES:
                    if active is None:
                        self._cancel_requested_run_ids.discard(run_id)
                    return run
                node_patches = {
                    node_run.node_id: {
                        "status": NodeRunStatus.CANCELED,
                        "finished_at": _utc_now(),
                    }
                    for node_run in run.node_runs
                    if node_run.status
                    in {
                        NodeRunStatus.PENDING,
                        NodeRunStatus.RUNNING,
                        NodeRunStatus.AWAITING_INPUT,
                    }
                }
                try:
                    updated = await self._store.transition_run(
                        user_id,
                        run_id,
                        expected_version=(
                            request.expected_run_version
                            if request is not None
                            else run.run_version
                        ),
                        run_patch={
                            "status": RunStatus.CANCELED,
                            "control_mode": "stopped",
                            "finished_at": _utc_now(),
                            "error": "Run canceled by the user.",
                            "pending_input": None,
                        },
                        node_patches=node_patches,
                        audit_events=[
                            TaskEventDraft(
                                type="run.canceled",
                                payload={
                                    "side_effects_rolled_back": False,
                                    **(
                                        {"request_id": request.request_id}
                                        if request is not None
                                        else {}
                                    ),
                                },
                            )
                        ],
                        command_id=command_id,
                    )
                    await self._mark_task_run_status(updated)
                    if active is not None:
                        active.cancel()
                    if self._message_bus is not None:
                        try:
                            await self._message_bus.publish(
                                f"lxscope:task:run:{run_id}:cancel",
                                {"run_id": run_id},
                            )
                        except Exception:
                            logger.exception(
                                "Task cancellation persisted, but worker notification failed: %s",
                                run_id,
                            )
                    if active is None:
                        self._cancel_requested_run_ids.discard(run_id)
                    return updated
                except TaskStoreConflictError:
                    run = await self._store.get_run(user_id, run_id)
                    if run is None or run.status in _TERMINAL_RUN_STATUSES:
                        if active is None:
                            self._cancel_requested_run_ids.discard(run_id)
                        return run
                    if request is not None:
                        if command_id in run.processed_command_ids:
                            return run
                        self._cancel_requested_run_ids.discard(run_id)
                        raise
            raise TaskStoreConflictError(
                "Task Run changed too often to persist cancellation."
            )
        node_runs = [
            (
                node_run.model_copy(
                    update={"status": NodeRunStatus.CANCELED},
                )
                if node_run.status
                in {
                    NodeRunStatus.PENDING,
                    NodeRunStatus.RUNNING,
                    NodeRunStatus.AWAITING_INPUT,
                }
                else node_run
            )
            for node_run in run.node_runs
        ]
        updated = await self._store.update_run(
            user_id,
            run_id,
            status=RunStatus.CANCELED,
            node_runs=node_runs,
            finished_at=_utc_now(),
            error="Run canceled by the user.",
            pending_input=None,
        )
        # The task list renders ``last_run_status`` rather than loading every
        # Run.  Keep that summary in sync for cancellation just as we do for
        # successful and failed executions; otherwise a stopped Run can stay
        # labelled queued/running in the sidebar.
        await self._mark_task_run_status(updated)
        await self._emit(updated, "run.canceled", payload={})
        if self._message_bus is not None:
            try:
                await self._message_bus.publish(
                    f"lxscope:task:run:{run_id}:cancel",
                    {"run_id": run_id},
                )
            except Exception:
                logger.exception(
                    "Task cancellation persisted, but worker notification failed: %s",
                    run_id,
                )
        if active is None:
            self._cancel_requested_run_ids.discard(run_id)
        return updated

    async def retry_run(
        self,
        user_id: str,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Create a fresh Run from a previous Run's input and context."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        return await self.create_run(
            user_id,
            run.task_id,
            TaskRunRequest(
                input=run.input,
                context=run.context,
                delivery_mode=run.delivery_mode,
            ),
            knowledge_gateway,
        )

    async def pause_run(
        self,
        user_id: str,
        run_id: str,
        request: TaskRunControlRequest,
    ) -> TaskRunRecord | None:
        """Request a pause at the next safe node checkpoint."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.engine not in {"dag", "sop"}:
            raise TaskWorkflowConflictError(
                "Run controls require a Task DAG or SOP Run."
            )
        command_id = f"run.pause:{request.request_id}"
        if command_id in run.processed_command_ids:
            return run
        if run.run_version != request.expected_run_version:
            raise TaskStoreConflictError("Task Run version conflict.")
        if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
            raise TaskWorkflowConflictError(
                "Only a queued or running Task Run can be paused."
            )
        if run.control_mode == "paused":
            raise TaskWorkflowConflictError("Task Run is already paused.")
        immediate = run.status is RunStatus.QUEUED and run.owner_token is None
        return await self._store.transition_run(
            user_id,
            run_id,
            expected_version=request.expected_run_version,
            run_patch={
                "control_mode": "paused" if immediate else "pause_requested",
            },
            audit_events=[
                TaskEventDraft(
                    type="run.paused" if immediate else "run.pause_requested",
                    payload={"request_id": request.request_id},
                )
            ],
            command_id=command_id,
        )

    async def continue_run(
        self,
        user_id: str,
        run_id: str,
        request: TaskRunControlRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Resume a paused DAG from successful persisted nodes."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.engine not in {"dag", "sop"}:
            raise TaskWorkflowConflictError(
                "Run controls require a Task DAG or SOP Run."
            )
        command_id = f"run.continue:{request.request_id}"
        if command_id in run.processed_command_ids:
            return run
        if run.engine == "dag" and not self._dag_scheduler_enabled:
            raise TaskWorkflowConflictError(
                "DAG execution is disabled; this Run cannot be continued."
            )
        if run.run_version != request.expected_run_version:
            raise TaskStoreConflictError("Task Run version conflict.")
        if (
            run.status in _TERMINAL_RUN_STATUSES
            or run.status is RunStatus.AWAITING_INPUT
        ):
            raise TaskWorkflowConflictError(
                "A terminal or input-waiting Task Run cannot be continued."
            )
        if run.control_mode != "paused":
            raise TaskWorkflowConflictError("Task Run is not paused.")
        if any(item.status is NodeRunStatus.FAILED for item in run.node_runs):
            raise TaskWorkflowConflictError(
                "Retry failed nodes before continuing the Task Run."
            )
        updated = await self._store.transition_run(
            user_id,
            run_id,
            expected_version=request.expected_run_version,
            run_patch={
                "status": RunStatus.QUEUED,
                "control_mode": "continuous",
            },
            audit_events=[
                TaskEventDraft(
                    type="run.continued",
                    payload={"request_id": request.request_id},
                )
            ],
            command_id=command_id,
        )
        self._schedule_run(updated, knowledge_gateway)
        return updated

    async def step_run(
        self,
        user_id: str,
        run_id: str,
        request: TaskRunControlRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Run one stable ready node, then pause at its checkpoint."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.engine not in {"dag", "sop"}:
            raise TaskWorkflowConflictError(
                "Run controls require a Task DAG or SOP Run."
            )
        command_id = f"run.step:{request.request_id}"
        if command_id in run.processed_command_ids:
            return run
        if run.engine == "dag" and not self._dag_scheduler_enabled:
            raise TaskWorkflowConflictError(
                "DAG execution is disabled; this Run cannot be single-stepped."
            )
        if run.run_version != request.expected_run_version:
            raise TaskStoreConflictError("Task Run version conflict.")
        if (
            run.status in _TERMINAL_RUN_STATUSES
            or run.status is RunStatus.AWAITING_INPUT
        ):
            raise TaskWorkflowConflictError(
                "A terminal or input-waiting Task Run cannot be stepped."
            )
        if run.control_mode != "paused":
            raise TaskWorkflowConflictError("Task Run must be paused before stepping.")
        if any(
            item.status
            in {NodeRunStatus.RUNNING, NodeRunStatus.AWAITING_INPUT}
            for item in run.node_runs
        ):
            raise TaskWorkflowConflictError(
                "A Task node is active; single-step cannot start yet."
            )
        if any(item.status is NodeRunStatus.FAILED for item in run.node_runs):
            raise TaskWorkflowConflictError(
                "Retry failed nodes before single-stepping the Task Run."
            )
        updated = await self._store.transition_run(
            user_id,
            run_id,
            expected_version=request.expected_run_version,
            run_patch={
                "status": RunStatus.QUEUED,
                "control_mode": "single_step",
            },
            audit_events=[
                TaskEventDraft(
                    type="run.step_requested",
                    payload={"request_id": request.request_id},
                )
            ],
            command_id=command_id,
        )
        self._schedule_run(updated, knowledge_gateway)
        return updated

    async def stop_run(
        self,
        user_id: str,
        run_id: str,
        request: TaskRunControlRequest,
    ) -> TaskRunRecord | None:
        """Stop a DAG Run using the existing cancellation semantics."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.engine not in {"dag", "sop"}:
            raise TaskWorkflowConflictError(
                "Run controls require a Task DAG or SOP Run."
            )
        command_id = f"run.stop:{request.request_id}"
        if command_id in run.processed_command_ids:
            return run
        if run.status in _TERMINAL_RUN_STATUSES:
            raise TaskWorkflowConflictError("A terminal Task Run cannot be stopped.")
        return await self.cancel_run(user_id, run_id, request)

    async def retry_node(
        self,
        user_id: str,
        run_id: str,
        node_id: str,
        request: TaskRunControlRequest,
    ) -> TaskRunRecord | None:
        """Reset one failed or successful DAG node and its descendants."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.engine not in {"dag", "sop"} or (
            run.engine == "dag" and run.graph is None
        ):
            raise TaskWorkflowConflictError(
                "Failed-node retry is available only for Task DAG or SOP Runs."
            )
        command_id = f"node_retry:{node_id}:{request.request_id}"
        if command_id in run.processed_command_ids:
            return run
        if run.engine == "dag" and not self._dag_scheduler_enabled:
            raise TaskWorkflowConflictError(
                "DAG execution is disabled; failed nodes cannot be retried."
            )
        if run.run_version != request.expected_run_version:
            raise TaskStoreConflictError("Task Run version conflict.")
        if any(
            item.status
            in {NodeRunStatus.RUNNING, NodeRunStatus.AWAITING_INPUT}
            for item in run.node_runs
        ):
            raise TaskWorkflowConflictError(
                "A node is still active; wait for its checkpoint before retrying."
            )
        if run.status is not RunStatus.FAILED:
            raise TaskWorkflowConflictError(
                "Only a FAILED Task Run can retry one of its nodes."
            )
        selected = next(
            (item for item in run.node_runs if item.node_id == node_id),
            None,
        )
        definition = next(
            (item for item in run.nodes if item.id == node_id),
            None,
        )
        if (
            selected is None
            or definition is None
            or selected.status
            not in {NodeRunStatus.FAILED, NodeRunStatus.SUCCEEDED}
        ):
            raise TaskWorkflowConflictError(
                "Only a FAILED or SUCCEEDED node in this Run can be retried."
            )
        if (
            isinstance(definition.config, (ToolStepConfig, PythonStepConfig))
            or capability_external_side_effect(definition.capability_key)
            or selected.metadata.get("recovery_required") is True
        ):
            if not request.side_effects_verified:
                raise TaskWorkflowConflictError(
                    "This node may have external or workspace side effects. "
                    "Verify their state, then retry with side_effects_verified=true."
                )
        if int(selected.metadata.get("manual_retry_count", 0)) >= 10:
            raise TaskWorkflowConflictError(
                "This node reached the manual retry limit of 10."
            )

        invalidated_ids = (
            descendants(run.graph, node_id)
            if run.graph is not None
            else {
                item.id
                for item in run.nodes
                if item.order > definition.order
            }
        )
        reset_ids = invalidated_ids | {node_id}
        node_patches: dict[str, dict[str, Any]] = {}
        now = _utc_now()
        for item in run.node_runs:
            if item.node_id not in reset_ids:
                continue
            history = list(item.attempt_history)
            if (
                item.status
                in {NodeRunStatus.SUCCEEDED, NodeRunStatus.FAILED}
                and item.attempt > 0
                and (
                    not history
                    or history[-1].attempt < item.attempt
                    or history[-1].status
                    != (
                        "succeeded"
                        if item.status is NodeRunStatus.SUCCEEDED
                        else "failed"
                    )
                )
            ):
                if len(history) >= 128:
                    raise TaskWorkflowConflictError(
                        "This node reached the attempt history limit of 128."
                    )
                history.append(
                    TaskNodeAttemptRecord(
                        attempt=item.attempt,
                        source="automatic",
                        status=(
                            "succeeded"
                            if item.status is NodeRunStatus.SUCCEEDED
                            else "failed"
                        ),
                        input=item.input,
                        output=item.output,
                        result=item.result,
                        metadata=item.metadata,
                        error=item.error,
                        error_detail=item.error_detail,
                        started_at=item.started_at,
                        finished_at=item.finished_at or now,
                    )
                )
            metadata = {
                key: value
                for key, value in item.metadata.items()
                if key in {"capability_key", "tool_name"}
            }
            if item.node_id == node_id:
                metadata["manual_retry_count"] = int(
                    item.metadata.get("manual_retry_count", 0)
                ) + 1
                metadata["next_attempt_source"] = "manual"
                if request.side_effects_verified:
                    metadata["side_effects_verified"] = {
                        "confirmed_by": user_id,
                        "confirmed_at": now.isoformat(),
                    }
            node_patches[item.node_id] = {
                "status": NodeRunStatus.PENDING,
                "attempt_history": history,
                "metadata": metadata,
                "input": "",
                "output": "",
                "display_summary": "",
                "result": None,
                "error": None,
                "error_detail": None,
                "started_at": None,
                "finished_at": None,
            }

        state = dict(run.sop_state or {})
        if run.engine == "dag":
            node_states = dict(state.get("dag_node_states", {}))
            old_state = node_states.get(node_id)
            if isinstance(old_state, dict):
                answers = [
                    TaskInputReply.model_validate(item)
                    for item in old_state.get("answers", [])
                    if isinstance(item, dict)
                ]
                node_states[node_id] = TaskStepState(
                    answers=answers,
                ).model_dump(mode="json")
            for invalidated_id in invalidated_ids:
                node_states.pop(invalidated_id, None)
            state["dag_node_states"] = node_states
        else:
            sop_state = self._task_sop_state(run)
            for reset_id in reset_ids:
                reset_index = sop_state.task_node_ids.index(reset_id)
                old_step = TaskStepState.model_validate(
                    sop_state.steps[reset_index].model_dump()
                )
                sop_state.steps[reset_index] = TaskStepState(
                    answers=(old_step.answers if reset_id == node_id else []),
                    knowledge_context=(
                        old_step.knowledge_context if reset_id == node_id else ""
                    ),
                )
            state = sop_state.model_dump(mode="json")
        remaining_failures = [
            item
            for item in run.node_runs
            if item.status is NodeRunStatus.FAILED
            and item.node_id not in reset_ids
        ]
        next_status = (
            RunStatus.FAILED if remaining_failures else RunStatus.QUEUED
        )
        audit = TaskEventDraft(
            type="node.retry_requested",
            node_id=node_id,
            payload={
                "request_id": request.request_id,
                "manual_attempt": int(
                    selected.metadata.get("manual_retry_count", 0)
                )
                + 1,
                "invalidated_node_ids": sorted(invalidated_ids),
                "side_effects_verified": request.side_effects_verified,
            },
        )
        try:
            updated = await self._store.transition_run(
                user_id,
                run_id,
                expected_version=request.expected_run_version,
                run_patch={
                    "status": next_status,
                    "control_mode": "paused",
                    "sop_state": state,
                    "pending_input": None,
                    "error": (
                        remaining_failures[0].error
                        if remaining_failures
                        else None
                    ),
                    "error_detail": (
                        remaining_failures[0].error_detail
                        if remaining_failures
                        else None
                    ),
                    "finished_at": now if remaining_failures else None,
                    "final_output": "",
                    "final_summary": "",
                    "artifacts": [],
                    "delivery_status": None,
                    "verification_report": {},
                },
                node_patches=node_patches,
                audit_events=[audit],
                command_id=command_id,
            )
        except TaskStoreConflictError:
            latest = await self._store.get_run(user_id, run_id)
            if latest is not None and command_id in latest.processed_command_ids:
                return latest
            raise
        await self._mark_task_run_status(updated)
        return updated

    async def list_artifacts(
        self,
        user_id: str,
        run_id: str,
    ) -> list[TaskArtifactRecord] | None:
        """Return artifact metadata for one owned run."""

        run = await self._store.get_run(user_id, run_id)
        return None if run is None else run.artifacts

    async def read_artifact(
        self,
        user_id: str,
        run_id: str,
        artifact_id: str,
    ) -> tuple[TaskArtifactRecord, bytes] | None:
        """Read one owned artifact through the Task storage adapter."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        artifact = next(
            (item for item in run.artifacts if item.id == artifact_id),
            None,
        )
        if artifact is None:
            return None
        if self._artifact_writer is None:
            raise RuntimeError(
                "Task artifact Workspace storage is unavailable."
            )
        context = ExecutionContext(
            **run.context.model_dump(),
            user_id=run.user_id,
            task_id=run.task_id,
            run_id=run.id,
            task_revision=run.task_revision,
        )
        data = await self._artifact_writer.read_artifact(
            path=artifact.path,
            context=context,
        )
        return artifact, data

    async def list_events(
        self, user_id: str, run_id: str
    ) -> list[TaskEventRecord]:
        """Replay lifecycle events for a run."""

        return await self._store.list_events(user_id, run_id)

    async def shutdown(self) -> None:
        """Cancel task-module workers during application shutdown."""

        active_runs = list(self._active_runs.values())
        for active in active_runs:
            active.cancel()
        if active_runs:
            await asyncio.gather(*active_runs, return_exceptions=True)
        self._active_runs.clear()

    async def _execute_run(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> None:
        """Advance the official engine under a per-run execution lock."""
        run = await self._find_run(run_id)
        if run is not None and run.engine in {"sop", "legacy"}:
            await self._execute_sop_run(run_id, knowledge_gateway)
            return
        owner = asyncio.current_task()
        listener: asyncio.Task[None] | None = None
        try:
            if self._message_bus is not None:
                ready = asyncio.Event()

                async def watch_cancel() -> None:
                    async for _ in self._message_bus.subscribe(
                        f"lxscope:task:run:{run_id}:cancel",
                        on_ready=ready.set,
                    ):
                        self._cancel_requested_run_ids.add(run_id)
                        if owner is not None:
                            owner.cancel()
                        return

                listener = asyncio.create_task(watch_cancel())
                await asyncio.wait_for(ready.wait(), timeout=10)
            async with self._run_guard(run_id, ttl_secs=45):
                run = await self._find_run(run_id)
                tool_nodes = (
                    [
                        node
                        for node in run.nodes
                        if isinstance(node.config, ToolStepConfig)
                    ]
                    if run is not None
                    else []
                )
                should_advance = (
                    run is not None
                    and run.status not in _TERMINAL_RUN_STATUSES
                    and not self._is_cancel_requested(run_id)
                )
                executor = (
                    self._select_executor(run.context)
                    if should_advance and run is not None
                    else None
                )
                run_scope = getattr(executor, "run_scope", None)
                if (
                    tool_nodes
                    and callable(run_scope)
                    and should_advance
                    and run is not None
                ):
                    context = ExecutionContext(
                        **run.context.model_dump(),
                        user_id=run.user_id,
                        task_id=run.task_id,
                        run_id=run.id,
                        task_revision=run.task_revision,
                        initial_input=run.input,
                    )
                    tool_names = {
                        node.config.tool_name
                        for node in tool_nodes
                        if isinstance(node.config, ToolStepConfig)
                        and not is_task_native_capability(node.capability_key)
                    }
                    if tool_names:
                        async with run_scope(context, tool_names=tool_names):
                            await self._advance_sop(run_id, knowledge_gateway)
                    else:
                        await self._advance_sop(run_id, knowledge_gateway)
                else:
                    await self._advance_sop(run_id, knowledge_gateway)
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.exception("Task worker could not start: %s", run_id)
            run = await self._find_run(run_id)
            if run is not None and run.status not in _TERMINAL_RUN_STATUSES:
                worker_error_detail = TaskErrorDetail(
                    category="execution_failed",
                    code="TASK_WORKER_START_FAILED",
                    message=(
                        str(error) or "Task worker could not start."
                    )[:4_000],
                    task_id=run.task_id,
                    run_id=run.id,
                )
                run = await self._store.update_run(
                    run.user_id,
                    run.id,
                    status=RunStatus.FAILED,
                    error=str(error) or "Task worker could not start.",
                    error_detail=worker_error_detail,
                    finished_at=_utc_now(),
                )
                await self._mark_task_run_status(run)
                await self._emit(
                    run,
                    "run.failed",
                    payload={
                        "message": run.error,
                        "error_detail": worker_error_detail.model_dump(
                            mode="json"
                        ),
                    },
                )
        finally:
            if listener is not None:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)

    async def _dag_transition(
        self,
        run_id: str,
        *,
        owner_token: str,
        fence_epoch: int,
        run_patch: dict[str, Any] | None = None,
        node_patches: dict[str, dict[str, Any]] | None = None,
        audit_events: list[TaskEventDraft] | None = None,
        checkpoint_state: tuple[str, dict[str, Any]] | None = None,
        release_owner: bool = False,
    ) -> TaskRunRecord:
        """Apply a fenced Task transition, merging its engine checkpoint."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            raise TaskStoreConflictError("Task Run owner is unavailable.")
        lock = self._dag_transition_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            for _ in range(16):
                current = await self._store.get_run(user_id, run_id)
                if current is None:
                    raise KeyError(f"Unknown task run {run_id!r}.")
                if current.status in _TERMINAL_RUN_STATUSES:
                    raise asyncio.CancelledError
                if (
                    current.owner_token != owner_token
                    or current.fence_epoch != fence_epoch
                    or current.lease_expires_at is None
                    or current.lease_expires_at <= _utc_now()
                ):
                    raise TaskStoreConflictError(
                        "Task Run owner lease or fencing epoch is stale."
                    )
                updates = dict(run_patch or {})
                if checkpoint_state is not None:
                    node_id, node_state = checkpoint_state
                    if current.engine == "dag":
                        state = dict(current.sop_state or {})
                        node_states = dict(state.get("dag_node_states", {}))
                        node_states[node_id] = node_state
                        state["dag_node_states"] = node_states
                        updates["sop_state"] = state
                    elif current.engine == "sop":
                        state = self._task_sop_state(current)
                        index = state.task_node_ids.index(node_id)
                        state.steps[index] = TaskStepState.model_validate(
                            node_state
                        )
                        updates["sop_state"] = state.model_dump(mode="json")
                    else:
                        raise TaskStoreConflictError(
                            "This Task Run engine has no durable checkpoint adapter."
                        )
                try:
                    return await self._store.transition_run(
                        user_id,
                        run_id,
                        expected_version=current.run_version,
                        run_patch=updates,
                        node_patches=node_patches,
                        audit_events=audit_events,
                        owner_token=owner_token,
                        expected_fence_epoch=fence_epoch,
                        release_owner=release_owner,
                    )
                except TaskStoreConflictError:
                    latest = await self._store.get_run(user_id, run_id)
                    if latest is None or latest.owner_token != owner_token:
                        raise
            raise TaskStoreConflictError("Task Run CAS retry limit was exceeded.")

    async def _renew_dag_lease(
        self,
        run_id: str,
        owner_token: str,
        fence_epoch: int,
        worker: asyncio.Task[None] | None,
    ) -> None:
        """Renew a scheduler lease while a node adapter is awaiting work."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            return
        lock = self._dag_transition_locks.setdefault(run_id, asyncio.Lock())
        try:
            while True:
                await asyncio.sleep(15)
                async with lock:
                    current = await self._store.get_run(user_id, run_id)
                    if current is None or current.status in _TERMINAL_RUN_STATUSES:
                        return
                    if (
                        current.owner_token != owner_token
                        or current.fence_epoch != fence_epoch
                    ):
                        raise TaskStoreConflictError(
                            "Task Run lease was taken by another worker."
                        )
                    await self._store.claim_run(
                        user_id,
                        run_id,
                        owner_token=owner_token,
                        lease_seconds=45,
                        audit_events=[
                            TaskEventDraft(
                                type="run.lease_renewed",
                                payload={"fence_epoch": fence_epoch},
                            )
                        ],
                    )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Task DAG worker lost its lease: %s", run_id)
            if worker is not None and not worker.done():
                worker.cancel()

    async def _execute_sop_run(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> None:
        """Run a linear SOP under the same durable claim and fencing as DAG."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            return
        owner_token = uuid4().hex
        fence_epoch: int | None = None
        heartbeat: asyncio.Task[None] | None = None
        worker = asyncio.current_task()
        listener: asyncio.Task[None] | None = None
        try:
            if self._message_bus is not None:
                ready = asyncio.Event()

                async def watch_cancel() -> None:
                    async for _ in self._message_bus.subscribe(
                        f"lxscope:task:run:{run_id}:cancel",
                        on_ready=ready.set,
                    ):
                        self._cancel_requested_run_ids.add(run_id)
                        if worker is not None:
                            worker.cancel()
                        return

                listener = asyncio.create_task(watch_cancel())
                await asyncio.wait_for(ready.wait(), timeout=10)
            async with self._run_guard(run_id):
                run = await self._store.get_run(user_id, run_id)
                if (
                    run is None
                    or run.status in _TERMINAL_RUN_STATUSES
                    or run.engine not in {"sop", "legacy"}
                ):
                    return
                try:
                    claimed = await self._store.claim_run(
                        user_id,
                        run_id,
                        owner_token=owner_token,
                        lease_seconds=45,
                        audit_events=[
                            TaskEventDraft(
                                type="run.claimed",
                                payload={"worker": "task-sop"},
                            )
                        ],
                    )
                except TaskStoreConflictError:
                    logger.info("Task SOP Run already has an owner: %s", run_id)
                    return
                fence_epoch = claimed.fence_epoch
                heartbeat = asyncio.create_task(
                    self._renew_dag_lease(
                        run_id,
                        owner_token,
                        fence_epoch,
                        worker,
                    )
                )
                if claimed.control_mode == "paused":
                    await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        run_patch={"status": RunStatus.QUEUED},
                        release_owner=True,
                    )
                    return
                current = await self._dag_transition(
                    run_id,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                    run_patch={
                        "status": RunStatus.RUNNING,
                        "started_at": claimed.started_at or _utc_now(),
                    },
                    audit_events=[TaskEventDraft(type="run.started")],
                )
                await self._mark_task_run_status(current)

                interrupted = next(
                    (
                        item
                        for item in current.node_runs
                        if item.status is NodeRunStatus.RUNNING
                    ),
                    None,
                )
                if interrupted is not None:
                    definition = next(
                        node
                        for node in current.nodes
                        if node.id == interrupted.node_id
                    )
                    now = _utc_now()
                    message = (
                        "Worker stopped while this SOP node was running; "
                        "verify possible external side effects before retrying."
                    )
                    metadata = {
                        **interrupted.metadata,
                        "recovery_required": True,
                        "recovery_policy": "manual_verification",
                    }
                    detail = classify_node_error(
                        current,
                        definition,
                        metadata,
                        message,
                    )
                    attempt_number = max(
                        interrupted.attempt,
                        len(interrupted.attempt_history) + 1,
                        1,
                    )
                    history = [
                        *interrupted.attempt_history,
                        TaskNodeAttemptRecord(
                            attempt=attempt_number,
                            source="automatic",
                            status="failed",
                            input=interrupted.input,
                            output=interrupted.output,
                            result=interrupted.result,
                            metadata=metadata,
                            error=message,
                            error_detail=detail,
                            started_at=interrupted.started_at,
                            finished_at=now,
                        ),
                    ]
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        node_patches={
                            interrupted.node_id: {
                                "status": NodeRunStatus.FAILED,
                                "attempt": attempt_number,
                                "attempt_history": history,
                                "metadata": metadata,
                                "error": message,
                                "error_detail": detail,
                                "finished_at": now,
                            }
                        },
                        run_patch={
                            "status": RunStatus.FAILED,
                            "error": message,
                            "error_detail": detail,
                            "finished_at": now,
                        },
                        audit_events=[
                            TaskEventDraft(
                                type="step.recovery_required",
                                node_id=interrupted.node_id,
                                payload={
                                    "message": message,
                                    "error_detail": detail.model_dump(
                                        mode="json"
                                    ),
                                },
                            ),
                            TaskEventDraft(
                                type="run.failed",
                                node_id=interrupted.node_id,
                                payload={"message": message},
                            ),
                        ],
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                    return

                tool_nodes = [
                    node
                    for node in current.nodes
                    if isinstance(node.config, ToolStepConfig)
                ]
                executor = self._select_executor(current.context)
                run_scope = getattr(executor, "run_scope", None)
                tool_names = {
                    node.config.tool_name
                    for node in tool_nodes
                    if isinstance(node.config, ToolStepConfig)
                    and not is_task_native_capability(node.capability_key)
                }
                if callable(run_scope) and tool_names:
                    context = ExecutionContext(
                        **current.context.model_dump(),
                        user_id=current.user_id,
                        task_id=current.task_id,
                        run_id=current.id,
                        task_revision=current.task_revision,
                        initial_input=current.input,
                    )
                    async with run_scope(context, tool_names=tool_names):
                        await self._advance_sop(
                            run_id,
                            knowledge_gateway,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                        )
                else:
                    await self._advance_sop(
                        run_id,
                        knowledge_gateway,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                    )
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.exception("Task SOP worker failed: %s", run_id)
            current = await self._store.get_run(user_id, run_id)
            if (
                current is not None
                and current.status not in _TERMINAL_RUN_STATUSES
                and fence_epoch is not None
                and current.owner_token == owner_token
            ):
                message = str(error) or "Task SOP worker failed."
                detail = TaskErrorDetail(
                    category="execution_failed",
                    code="TASK_SOP_WORKER_FAILED",
                    message=message[:4_000],
                    task_id=current.task_id,
                    run_id=current.id,
                )
                try:
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        run_patch={
                            "status": RunStatus.FAILED,
                            "error": message[:4_000],
                            "error_detail": detail,
                            "finished_at": _utc_now(),
                        },
                        audit_events=[
                            TaskEventDraft(
                                type="run.failed",
                                payload={
                                    "message": message[:4_000],
                                    "error_detail": detail.model_dump(
                                        mode="json"
                                    ),
                                },
                            )
                        ],
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                except (TaskStoreConflictError, asyncio.CancelledError):
                    logger.info("Task SOP failure write was fenced: %s", run_id)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if listener is not None:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)

    async def _execute_dag_run(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> None:
        """Run ready graph nodes under a durable claim and per-node SOP adapter."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            return
        owner_token = uuid4().hex
        fence_epoch: int | None = None
        heartbeat: asyncio.Task[None] | None = None
        worker = asyncio.current_task()
        try:
            async with self._run_guard(run_id):
                run = await self._store.get_run(user_id, run_id)
                if (
                    run is None
                    or run.status in _TERMINAL_RUN_STATUSES
                    or run.engine != "dag"
                    or run.graph is None
                ):
                    return
                if not self._dag_scheduler_enabled:
                    return
                try:
                    claimed = await self._store.claim_run(
                        user_id,
                        run_id,
                        owner_token=owner_token,
                        lease_seconds=45,
                        audit_events=[
                            TaskEventDraft(
                                type="run.claimed",
                                payload={"worker": "task-dag"},
                            )
                        ],
                    )
                except TaskStoreConflictError:
                    logger.info("Task DAG Run already has an owner: %s", run_id)
                    return
                fence_epoch = claimed.fence_epoch
                heartbeat = asyncio.create_task(
                    self._renew_dag_lease(
                        run_id,
                        owner_token,
                        fence_epoch,
                        worker,
                    )
                )
                current = await self._dag_transition(
                    run_id,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                    run_patch={
                        "status": RunStatus.RUNNING,
                        "started_at": claimed.started_at or _utc_now(),
                    },
                    audit_events=[TaskEventDraft(type="run.started")],
                )
                await self._mark_task_run_status(current)

                interrupted = [
                    item
                    for item in current.node_runs
                    if item.status is NodeRunStatus.RUNNING
                ]
                if interrupted:
                    message = (
                        "Worker interrupted while this node was running; "
                        "external side effects require manual verification."
                    )
                    node_defs = {node.id: node for node in current.nodes}
                    patches: dict[str, dict[str, Any]] = {}
                    drafts: list[TaskEventDraft] = []
                    now = _utc_now()
                    for item in interrupted:
                        definition = node_defs[item.node_id]
                        metadata = {
                            **item.metadata,
                            "recovery_required": True,
                            "recovery_policy": "manual_verification",
                        }
                        detail = classify_node_error(
                            current,
                            definition,
                            metadata,
                            message,
                        )
                        history = list(item.attempt_history)
                        attempt_number = max(item.attempt, len(history) + 1, 1)
                        history.append(
                            TaskNodeAttemptRecord(
                                attempt=attempt_number,
                                source="automatic",
                                status="failed",
                                input=item.input,
                                output=item.output,
                                result=item.result,
                                metadata=metadata,
                                error=message,
                                error_detail=detail,
                                started_at=item.started_at,
                                finished_at=now,
                            )
                        )
                        patches[item.node_id] = {
                            "status": NodeRunStatus.FAILED,
                            "metadata": metadata,
                            "attempt": attempt_number,
                            "attempt_history": history,
                            "error": message,
                            "error_detail": detail,
                            "finished_at": now,
                        }
                        drafts.append(
                            TaskEventDraft(
                                type="step.recovery_required",
                                node_id=item.node_id,
                                payload={
                                    "message": message,
                                    "error_detail": detail.model_dump(
                                        mode="json"
                                    ),
                                },
                            )
                        )
                    first_detail = patches[interrupted[0].node_id][
                        "error_detail"
                    ]
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        node_patches=patches,
                        run_patch={
                            "status": RunStatus.FAILED,
                            "error": message,
                            "error_detail": first_detail,
                            "finished_at": now,
                        },
                        audit_events=[
                            *drafts,
                            TaskEventDraft(
                                type="run.failed",
                                payload={
                                    "message": message,
                                    "recovery_required": True,
                                },
                            ),
                        ],
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                    return

                while True:
                    if self._is_cancel_requested(run_id):
                        return
                    current = await self._store.get_run(user_id, run_id)
                    if current is None or current.status in _TERMINAL_RUN_STATUSES:
                        return
                    if current.graph is None:
                        raise TaskGraphValidationError(
                            "graph", "DAG Run snapshot is missing its graph."
                        )
                    failed = next(
                        (
                            item
                            for item in current.node_runs
                            if item.status is NodeRunStatus.FAILED
                        ),
                        None,
                    )
                    if failed is not None:
                        detail = failed.error_detail
                        message = failed.error or "Task DAG node failed."
                        current = await self._dag_transition(
                            run_id,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                            run_patch={
                                "status": RunStatus.FAILED,
                                "error": message,
                                "error_detail": detail,
                                "finished_at": _utc_now(),
                                "pending_input": None,
                            },
                            audit_events=[
                                TaskEventDraft(
                                    type="run.failed",
                                    node_id=failed.node_id,
                                    payload={
                                        "message": message,
                                        "error_detail": (
                                            detail.model_dump(mode="json")
                                            if detail is not None
                                            else None
                                        ),
                                    },
                                )
                            ],
                            release_owner=True,
                        )
                        await self._mark_task_run_status(current)
                        return

                    awaiting = next(
                        (
                            item
                            for item in current.node_runs
                            if item.status is NodeRunStatus.AWAITING_INPUT
                        ),
                        None,
                    )
                    if awaiting is not None:
                        current = await self._dag_transition(
                            run_id,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                            run_patch={
                                "status": RunStatus.AWAITING_INPUT,
                            },
                            audit_events=[
                                TaskEventDraft(
                                    type="run.awaiting_input",
                                    node_id=(
                                        current.pending_input.node_id
                                        if current.pending_input
                                        else awaiting.node_id
                                    ),
                                    payload={
                                        "request_id": (
                                            current.pending_input.id
                                            if current.pending_input
                                            else None
                                        )
                                    },
                                )
                            ],
                            release_owner=True,
                        )
                        await self._mark_task_run_status(current)
                        return

                    if current.control_mode in {"pause_requested", "paused"}:
                        current = await self._dag_transition(
                            run_id,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                            run_patch={
                                "status": RunStatus.QUEUED,
                                "control_mode": "paused",
                            },
                            audit_events=[
                                TaskEventDraft(type="run.paused")
                            ],
                            release_owner=True,
                        )
                        await self._mark_task_run_status(current)
                        return

                    succeeded = {
                        item.node_id
                        for item in current.node_runs
                        if item.status is NodeRunStatus.SUCCEEDED
                    }
                    running = {
                        item.node_id
                        for item in current.node_runs
                        if item.status
                        in {
                            NodeRunStatus.RUNNING,
                            NodeRunStatus.AWAITING_INPUT,
                        }
                    }
                    ready_ids = ready_node_ids(
                        current.graph,
                        current.nodes,
                        succeeded_node_ids=succeeded,
                        running_node_ids=running,
                    )
                    if not ready_ids:
                        if len(succeeded) == len(current.nodes):
                            await self._finalize_dag_run(
                                current,
                                owner_token=owner_token,
                                fence_epoch=fence_epoch,
                            )
                            return
                        message = (
                            "No ready Task node remains although the DAG is incomplete."
                        )
                        detail = TaskErrorDetail(
                            category="execution_failed",
                            code="TASK_DAG_NO_READY_NODE",
                            message=message,
                            task_id=current.task_id,
                            run_id=current.id,
                        )
                        current = await self._dag_transition(
                            run_id,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                            run_patch={
                                "status": RunStatus.FAILED,
                                "error": message,
                                "error_detail": detail,
                                "finished_at": _utc_now(),
                            },
                            audit_events=[
                                TaskEventDraft(
                                    type="run.failed",
                                    payload={
                                        "message": message,
                                        "error_detail": detail.model_dump(
                                            mode="json"
                                        ),
                                    },
                                )
                            ],
                            release_owner=True,
                        )
                        await self._mark_task_run_status(current)
                        return

                    batch: list[str] = []
                    concurrency_limit = (
                        1
                        if current.control_mode == "single_step"
                        else current.graph.max_concurrency
                    )
                    for node_id in ready_ids:
                        if len(batch) >= concurrency_limit:
                            break
                        candidate = [*batch, node_id]
                        if resources_fit(
                            current.graph,
                            current.nodes,
                            candidate,
                        ):
                            batch.append(node_id)
                    if not batch:
                        batch = [ready_ids[0]]
                    outcomes = await asyncio.gather(
                        *(
                            self._execute_dag_node(
                                run_id,
                                node_id,
                                owner_token=owner_token,
                                fence_epoch=fence_epoch,
                                knowledge_gateway=knowledge_gateway,
                            )
                            for node_id in batch
                        ),
                        return_exceptions=True,
                    )
                    if any(
                        isinstance(outcome, asyncio.CancelledError)
                        for outcome in outcomes
                    ):
                        return
                    latest = await self._store.get_run(user_id, run_id)
                    if (
                        latest is not None
                        and latest.control_mode == "single_step"
                        and latest.status not in _TERMINAL_RUN_STATUSES
                        and not any(
                            item.status
                            in {
                                NodeRunStatus.FAILED,
                                NodeRunStatus.AWAITING_INPUT,
                            }
                            for item in latest.node_runs
                        )
                        and not all(
                            item.status is NodeRunStatus.SUCCEEDED
                            for item in latest.node_runs
                        )
                    ):
                        current = await self._dag_transition(
                            run_id,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                            run_patch={
                                "status": RunStatus.QUEUED,
                                "control_mode": "paused",
                            },
                            audit_events=[
                                TaskEventDraft(
                                    type="run.step_completed",
                                    payload={
                                        "node_ids": batch,
                                        "next_mode": "paused",
                                    },
                                )
                            ],
                            release_owner=True,
                        )
                        await self._mark_task_run_status(current)
                        return
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.exception("Task DAG worker failed: %s", run_id)
            current = await self._store.get_run(user_id, run_id)
            if current is not None and current.status not in _TERMINAL_RUN_STATUSES:
                message = str(error) or "Task DAG worker failed."
                detail = TaskErrorDetail(
                    category="execution_failed",
                    code="TASK_DAG_WORKER_FAILED",
                    message=message[:4_000],
                    task_id=current.task_id,
                    run_id=current.id,
                )
                try:
                    if fence_epoch is not None and current.owner_token == owner_token:
                        current = await self._dag_transition(
                            run_id,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                            run_patch={
                                "status": RunStatus.FAILED,
                                "error": message[:4_000],
                                "error_detail": detail,
                                "finished_at": _utc_now(),
                            },
                            audit_events=[
                                TaskEventDraft(
                                    type="run.failed",
                                    payload={
                                        "message": message[:4_000],
                                        "error_detail": detail.model_dump(
                                            mode="json"
                                        ),
                                    },
                                )
                            ],
                            release_owner=True,
                        )
                    await self._mark_task_run_status(current)
                except (TaskStoreConflictError, asyncio.CancelledError):
                    logger.info(
                        "Task DAG failure checkpoint was fenced: %s",
                        run_id,
                    )
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _execute_dag_node(
        self,
        run_id: str,
        node_id: str,
        *,
        owner_token: str,
        fence_epoch: int,
        knowledge_gateway: TaskKnowledgeGateway | None,
    ) -> bool:
        """Execute exactly one ready Task node with its own persisted SOP state."""

        run = await self._find_run(run_id)
        if run is None or run.graph is None:
            return False
        node = next((item for item in run.nodes if item.id == node_id), None)
        if node is None:
            return False
        stored = next(
            (item for item in run.node_runs if item.node_id == node_id),
            None,
        )
        if stored is None:
            return False
        if stored.status is NodeRunStatus.SUCCEEDED:
            return True
        state_payload = (run.sop_state or {}).get("dag_node_states", {}).get(
            node_id
        )
        state = SOPRunState(
            id=f"{run.id}:{node.id}",
            inputs=[UserMsg(name="user", content=run.input)],
            steps=(
                [TaskStepState.model_validate(state_payload)]
                if isinstance(state_payload, dict)
                else [TaskStepState()]
            ),
        )
        engine = self._build_sop_engine(
            run,
            knowledge_gateway,
            selected_nodes=[node],
            initial_state=state,
            owner_token=owner_token,
            fence_epoch=fence_epoch,
        )
        try:
            current = await self._store.get_run(run.user_id, run.id)
            if current is None or current.status in _TERMINAL_RUN_STATUSES:
                return False
            node_input = self._dag_previous_output(current, node)
            executor = self._select_executor(self._step_context(run, node))
            run_scope = getattr(executor, "run_scope", None)
            tool_names = (
                {
                    node.config.tool_name
                }
                if isinstance(node.config, ToolStepConfig)
                and not is_task_native_capability(node.capability_key)
                else set()
            )

            async def drive() -> None:
                async for event in engine.reply_stream():
                    if self._is_cancel_requested(run_id):
                        raise asyncio.CancelledError
                    if (
                        isinstance(event, CustomEvent)
                        and event.name in {"TASK_STEP_STARTED", "TASK_STEP_UPDATED"}
                    ):
                        await self._persist_dag_step_checkpoint(
                            current_run_id=run_id,
                            node=node,
                            node_input=node_input,
                            engine=engine,
                            event=event,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                        )
                await self._persist_dag_step_checkpoint(
                    current_run_id=run_id,
                    node=node,
                    node_input=node_input,
                    engine=engine,
                    event=None,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                )

            if callable(run_scope) and tool_names:
                context = ExecutionContext(
                    **self._step_context(run, node).model_dump(),
                    user_id=run.user_id,
                    task_id=run.task_id,
                    run_id=run.id,
                    task_revision=run.task_revision,
                    initial_input=run.input,
                )
                async with run_scope(context, tool_names=tool_names):
                    await drive()
            else:
                await drive()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            current = await self._store.get_run(run.user_id, run.id)
            if current is None or current.status in _TERMINAL_RUN_STATUSES:
                return False
            current_stored = next(
                item
                for item in current.node_runs
                if item.node_id == node_id
            )
            message = str(error) or "Task DAG node execution failed."
            metadata = {
                **current_stored.metadata,
                "error": message[:2_000],
                "recovery_required": bool(
                    isinstance(
                        node.config,
                        (ToolStepConfig, PythonStepConfig),
                    )
                    or capability_external_side_effect(node.capability_key)
                ),
            }
            detail = classify_node_error(
                run,
                node,
                metadata,
                message,
            )
            step_state = engine.state.steps[0]
            node_output = step_state.output
            if node.type is StepType.AGENT:
                metadata["authoritative_output_status"] = {
                    "status": "failed",
                    "error_code": detail.code,
                    "message": message[:2_000],
                    "artifact_generated": False,
                }
                node_output = _authoritative_failure_header(
                    detail.code,
                    message,
                    artifact_generated=False,
                ) + node_output
            attempt_number = max(
                current_stored.attempt,
                len(current_stored.attempt_history) + 1,
                1,
            )
            history = list(current_stored.attempt_history)
            if not history or history[-1].attempt < attempt_number:
                history.append(
                    TaskNodeAttemptRecord(
                        attempt=attempt_number,
                        source=current_stored.metadata.get(
                            "next_attempt_source",
                            "automatic",
                        ),
                        status="failed",
                        input=node_input,
                        output=step_state.output,
                        result=step_state.result,
                        metadata=metadata,
                        error=message[:2_000],
                        error_detail=detail,
                        started_at=current_stored.started_at or _utc_now(),
                        finished_at=_utc_now(),
                    )
                )
            try:
                await self._dag_transition(
                    run_id,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                    node_patches={
                        node_id: {
                            "status": NodeRunStatus.FAILED,
                            "attempt": attempt_number,
                            "attempt_history": history,
                            "input": node_input,
                            "output": node_output,
                            "display_summary": _compact_text(node_output),
                            "result": step_state.result,
                            "metadata": metadata,
                            "error": message[:2_000],
                            "error_detail": detail,
                            "finished_at": _utc_now(),
                        }
                    },
                    checkpoint_state=(
                        node_id,
                        step_state.model_dump(mode="json"),
                    ),
                    audit_events=[
                        TaskEventDraft(
                            type="step.failed",
                            node_id=node_id,
                            payload={
                                "message": message[:2_000],
                                "error_detail": detail.model_dump(mode="json"),
                            },
                        )
                    ],
                )
            except (TaskStoreConflictError, asyncio.CancelledError):
                return False
        current = await self._store.get_run(run.user_id, run.id)
        latest = next(
            (
                item
                for item in (current.node_runs if current else [])
                if item.node_id == node_id
            ),
            None,
        )
        return latest is not None and latest.status is NodeRunStatus.SUCCEEDED

    async def _persist_dag_step_checkpoint(
        self,
        *,
        current_run_id: str,
        node: TaskNode,
        node_input: str,
        engine: SOPEngine,
        event: CustomEvent | None,
        owner_token: str,
        fence_epoch: int,
    ) -> TaskRunRecord:
        """Persist one node SOP transition and its lifecycle event atomically."""

        current = await self._find_run(current_run_id)
        if current is None:
            raise TaskStoreConflictError("Task Run no longer exists.")
        stored = next(
            item for item in current.node_runs if item.node_id == node.id
        )
        step = engine.state.steps[0]
        event_attempt = (
            int(event.value.get("attempt", 1))
            if event is not None and event.name == "TASK_STEP_STARTED"
            else None
        )
        new_execution_epoch = bool(
            event is not None
            and event.name == "TASK_STEP_STARTED"
            and stored.status
            not in {NodeRunStatus.RUNNING, NodeRunStatus.AWAITING_INPUT}
            and (event_attempt or 1) == 1
        )
        if event is not None and event.name == "TASK_STEP_STARTED":
            attempt_number = max(
                stored.attempt,
                len(stored.attempt_history) + 1,
                event_attempt or 1,
            )
        else:
            attempt_number = max(
                stored.attempt,
                len(stored.attempt_history),
                1,
            )
        status = {
            SOPPhase.PENDING: NodeRunStatus.PENDING,
            SOPPhase.RUNNING: NodeRunStatus.RUNNING,
            SOPPhase.AWAITING: NodeRunStatus.AWAITING_INPUT,
            SOPPhase.COMPLETED: NodeRunStatus.SUCCEEDED,
            SOPPhase.FAILED: NodeRunStatus.FAILED,
        }[step.phase]
        now = _utc_now()
        inherited_metadata = {
            key: value
            for key, value in stored.metadata.items()
            if key
            in {
                "manual_retry_count",
                "next_attempt_source",
                "side_effects_verified",
                "recovery_required",
            }
        }
        attempt_source = inherited_metadata.get(
            "next_attempt_source",
            "automatic",
        )
        metadata = {
            **inherited_metadata,
            **step.metadata,
            **(
                {"capability_key": node.capability_key}
                if node.capability_key
                else {}
            ),
            **(
                {"tool_name": node.config.tool_name}
                if isinstance(node.config, ToolStepConfig)
                else {}
            ),
            "attempts": len(step.verifications),
            "verifications": [
                item.model_dump(mode="json")
                for item in step.verifications
            ],
            "automatic_verifications_recorded": max(
                int(stored.metadata.get("automatic_verifications_recorded", 0)),
                len(step.verifications),
            ),
        }
        history = list(stored.attempt_history)
        recorded = int(stored.metadata.get("automatic_verifications_recorded", 0))
        if len(step.verifications) > recorded:
            verdict = step.verifications[-1]
            history.append(
                TaskNodeAttemptRecord(
                    attempt=attempt_number,
                    source=attempt_source,
                    status="succeeded" if verdict.passed else "failed",
                    input=node_input,
                    output=step.output,
                    result=step.result,
                    metadata=metadata,
                    error=None if verdict.passed else verdict.message,
                    started_at=stored.started_at or now,
                    finished_at=now,
                )
            )
            if attempt_source == "manual":
                metadata.pop("next_attempt_source", None)
        error_message = (
            step.metadata.get("error")
            or (
                step.verifications[-1].message
                if status is NodeRunStatus.FAILED and step.verifications
                else ""
            )
        )
        if (
            status is NodeRunStatus.FAILED
            and stored.status is NodeRunStatus.RUNNING
            and len(step.verifications) <= recorded
            and (
                not history
                or history[-1].attempt != attempt_number
                or history[-1].status != "failed"
            )
        ):
            history.append(
                TaskNodeAttemptRecord(
                    attempt=attempt_number,
                    source=attempt_source,
                    status="failed",
                    input=node_input,
                    output=step.output,
                    result=step.result,
                    metadata=metadata,
                    error=str(error_message or "Task SOP node failed."),
                    started_at=stored.started_at or now,
                    finished_at=now,
                )
            )
            if attempt_source == "manual":
                metadata.pop("next_attempt_source", None)
        detail = (
            classify_node_error(
                current,
                node,
                metadata,
                str(error_message or "Task node failed."),
            )
            if status is NodeRunStatus.FAILED
            else None
        )
        output = step.output
        if status is NodeRunStatus.FAILED and node.type is StepType.AGENT:
            metadata["authoritative_output_status"] = {
                "status": "failed",
                "error_code": detail.code if detail else "TASK_NODE_EXECUTION_FAILED",
                "message": str(error_message or "Task 节点验收失败。")[:2_000],
                "artifact_generated": False,
            }
            output = _authoritative_failure_header(
                detail.code if detail else "TASK_NODE_EXECUTION_FAILED",
                str(error_message or "Task 节点验收失败。"),
                artifact_generated=False,
            ) + output
        patch = {
            "status": status,
            "attempt": attempt_number,
            "execution_epoch": (
                stored.execution_epoch + 1
                if new_execution_epoch
                else stored.execution_epoch
            ),
            "attempt_history": history,
            "input": node_input if new_execution_epoch else stored.input or node_input,
            "output": output,
            "display_summary": _compact_text(output),
            "result": step.result,
            "metadata": metadata,
            "error": str(error_message)[:2_000] or None,
            "error_detail": detail,
            "started_at": stored.started_at or now,
            "finished_at": (
                now
                if status
                in {
                    NodeRunStatus.SUCCEEDED,
                    NodeRunStatus.FAILED,
                }
                else None
            ),
        }
        # The run stays RUNNING until the current ready batch has reached a
        # checkpoint. The scheduler then parks it in AWAITING_INPUT.
        run_status = RunStatus.RUNNING
        pending_input = (
            step.input_request
            if status is NodeRunStatus.AWAITING_INPUT
            else current.pending_input
        )
        drafts: list[TaskEventDraft] = []
        if event is not None and event.name == "TASK_STEP_STARTED":
            drafts.append(
                TaskEventDraft(
                    type="step.started",
                    node_id=node.id,
                    payload={"step_type": node.type.value, "attempt": attempt_number},
                )
            )
        elif status is NodeRunStatus.AWAITING_INPUT and pending_input is not None:
            drafts.extend(
                [
                    TaskEventDraft(
                        type="input.requested",
                        node_id=node.id,
                        payload=pending_input.model_dump(mode="json"),
                    ),
                ]
            )
        elif status is NodeRunStatus.SUCCEEDED:
            drafts.append(
                TaskEventDraft(
                    type="step.succeeded",
                    node_id=node.id,
                    payload={
                        "summary": _compact_text(output),
                        "step_type": node.type.value,
                        "attempt": attempt_number,
                    },
                )
            )
        elif status is NodeRunStatus.FAILED:
            drafts.append(
                TaskEventDraft(
                    type="step.failed",
                    node_id=node.id,
                    payload={
                        "message": str(error_message or "Task node failed."),
                        "error_detail": (
                            detail.model_dump(mode="json")
                            if detail is not None
                            else None
                        ),
                    },
                )
            )
        elif (
            event is not None
            and event.name == "TASK_STEP_UPDATED"
            and step.verifications
            and not step.verifications[-1].passed
        ):
            rejection_message = step.verifications[-1].message
            drafts.append(
                TaskEventDraft(
                    type="step.attempt_rejected",
                    node_id=node.id,
                    payload={
                        "attempt": attempt_number,
                        "message": rejection_message,
                    },
                )
            )
            # Preserve the pre-durable SOP event name consumed by existing
            # Task clients while exposing the more precise attempt event.
            drafts.append(
                TaskEventDraft(
                    type="step.rejected",
                    node_id=node.id,
                    payload={
                        "attempt": attempt_number,
                        "message": rejection_message,
                    },
                )
            )
        return await self._dag_transition(
            current_run_id,
            owner_token=owner_token,
            fence_epoch=fence_epoch,
            run_patch={
                "status": run_status,
                "pending_input": pending_input,
            },
            node_patches={node.id: patch},
            checkpoint_state=(
                node.id,
                step.model_dump(mode="json"),
            ),
            audit_events=drafts,
        )

    def _dag_previous_output(
        self,
        run: TaskRunRecord,
        node: TaskNode,
    ) -> str:
        """Format a stable serialized view of a node's declared inputs."""

        if run.graph is None:
            return run.input
        ordered_nodes = sorted(run.nodes, key=lambda item: (item.order, item.id))
        if self._is_s01_parallel_pilot_graph(
            (run.source_template_id, run.source_template_version),
            [item.id for item in ordered_nodes],
            run.graph,
        ) and not run.graph.input_bindings.get(node.id):
            parents = [
                edge.source_node_id
                for edge in run.graph.edges
                if edge.target_node_id == node.id
            ]
            if len(parents) == 1:
                predecessor = next(
                    (item for item in run.node_runs if item.node_id == parents[0]),
                    None,
                )
                if predecessor is not None:
                    return predecessor.output
        if self._is_linear_source_template_run(run):
            predecessor = next(
                (
                    item
                    for item in run.node_runs
                    if item.order == node.order - 1
                ),
                None,
            )
            return run.input if predecessor is None else predecessor.output
        inputs = build_inputs_by_node_id(run.graph, run.node_runs, node.id)
        named = build_named_input_values(run.graph, run.node_runs, node.id)
        if not inputs and not named:
            return run.input
        return json.dumps(
            {
                "inputs_by_node_id": inputs,
                "named_inputs": named,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _s01_dag_retrieval_input_values(
        run: TaskRunRecord,
        current: TaskRunRecord,
        node: TaskNode,
        named_inputs: dict[str, Any],
    ) -> dict[str, str]:
        """Resolve S01 pilot queries from its persisted InputStep field bindings."""

        values = _task_run_input_values(current)
        if (
            run.engine != "dag"
            or run.source_template_id != "s01_project_init"
            or run.source_template_version != "3.0.0"
            or current.graph is None
            or node.capability_key
            not in {"project.similar_search", "project.rule_check"}
        ):
            return values
        bindings = current.graph.input_bindings.get(node.id, {})
        if not bindings:
            return values

        expected_fields = (
            {"project_name", "project_brief", "industry"}
            if node.capability_key == "project.similar_search"
            else {"project_brief", "industry", "scope"}
        )
        ordered_nodes = sorted(run.nodes, key=lambda item: (item.order, item.id))
        if len(ordered_nodes) != 7 or ordered_nodes[0].type is not StepType.INPUT:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{node.id}",
                "S01 DAG 的冻结项目输入步骤无法定位。",
            )
        input_node_id = ordered_nodes[0].id
        input_record = next(
            (item for item in current.node_runs if item.node_id == input_node_id),
            None,
        )
        if (
            input_record is None
            or input_record.status is not NodeRunStatus.SUCCEEDED
        ):
            raise TaskGraphValidationError(
                f"graph.input_bindings.{node.id}",
                "S01 项目输入尚未成功保存，不能执行检索。",
            )
        try:
            reply = TaskInputReply.model_validate_json(input_record.output)
        except (TypeError, ValueError, ValidationError) as error:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{node.id}",
                "S01 项目输入快照无效，已拒绝检索。",
            ) from error

        if set(bindings) != expected_fields:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{node.id}",
                "S01 检索节点字段绑定不完整或包含未授权字段。",
            )
        resolved = dict(values)
        for field in expected_fields:
            binding = bindings[field]
            raw_input = named_inputs.get(field)
            if (
                binding.source_node_id != input_node_id
                or binding.source_value != "output"
                or raw_input != input_record.output
            ):
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{node.id}.{field}",
                    "S01 检索字段必须绑定到本 Run 已成功的收集项目资料步骤。",
                )
            field_value = reply.answers.get(field)
            if not isinstance(field_value, str):
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{node.id}.{field}",
                    "S01 项目输入字段缺失或类型无效。",
                )
            if values.get(field, "") != field_value:
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{node.id}.{field}",
                    "S01 字段绑定与 Task Run 已保存答案不一致。",
                )
            if field in {"project_name", "project_brief"} and not field_value.strip():
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{node.id}.{field}",
                    "S01 必填项目字段为空，已拒绝检索。",
                )
            resolved[field] = field_value
        return resolved

    def _dag_scenario_delivery_state(
        self,
        run: TaskRunRecord,
        *,
        terminal_ids: list[str],
        records: dict[str, NodeRunRecord],
    ) -> tuple[str | None, dict[str, Any], str | None]:
        """Require SOP-equivalent node gates before a scenario DAG can deliver.

        This adapter does not implement business checks. It verifies the
        existing TaskSOPStep gate audit persisted for every frozen node, then
        reuses the SOP S02 delivery projection. Only exact, explicitly
        test-authorized linear source graphs can reach this method today.
        """

        if run.source_template_id is None:
            return None, {}, None
        template = self._template_validator.template_for_source(
            run.source_template_id,
            run.source_template_version or "",
        )
        if template is None:
            raise TaskWorkflowConflictError(
                "冻结 DAG Run 的业务模板版本无法定位，拒绝完成交付。"
            )
        ordered_nodes = sorted(run.nodes, key=lambda item: item.order)
        if len(ordered_nodes) != len(template.steps) or not ordered_nodes:
            raise TaskWorkflowConflictError(
                "冻结 DAG Run 与业务模板节点数量不一致，拒绝完成交付。"
            )
        synthetic_task = TaskRecord(
            id=run.task_id,
            user_id=run.user_id,
            title=template.title,
            goal=template.description,
            nodes=ordered_nodes,
            graph=run.graph,
            workflow_mode=TaskWorkflowMode.CUSTOM_FLOW,
            source_template_id=run.source_template_id,
            source_template_version=run.source_template_version,
            source_task_id=run.source_task_id,
            source_task_revision=run.source_task_revision,
            template_step_bindings={
                step.step_key: node.id
                for step, node in zip(template.steps, ordered_nodes)
            },
        )
        blocker = self._scenario_dag_execution_blocker(
            synthetic_task,
            run.graph,
        )
        if blocker:
            raise TaskWorkflowConflictError(
                "冻结 DAG Run 不满足场景交付等价条件：" + blocker
            )
        if terminal_ids != [ordered_nodes[-1].id]:
            raise TaskWorkflowConflictError(
                "版本化场景 DAG 必须以冻结模板最终步骤作为唯一终端，拒绝交付。"
            )

        for step, node in zip(template.steps, ordered_nodes):
            node_run = records.get(node.id)
            if node_run is None or node_run.status is not NodeRunStatus.SUCCEEDED:
                raise TaskWorkflowConflictError(
                    f"来源步骤 {step.step_key} 尚未成功，拒绝生成场景产物。"
                )
            metadata = node_run.metadata
            if node.type is StepType.AGENT:
                ai_verification = metadata.get("task_ai_verification")
                if (
                    not isinstance(ai_verification, dict)
                    or ai_verification.get("method") != "task-verifier"
                    or ai_verification.get("passed") is not True
                ):
                    raise TaskWorkflowConflictError(
                        f"来源步骤 {step.step_key} 缺少通过的 Task AI 验收记录。"
                    )
            if node.output_schema is not None or node.output_contract is not None:
                output_validation = metadata.get("output_contract_validation")
                if (
                    not isinstance(output_validation, dict)
                    or output_validation.get("status") != "passed"
                ):
                    raise TaskWorkflowConflictError(
                        f"来源步骤 {step.step_key} 缺少通过的冻结输出契约审计。"
                    )

        delivery_status: str | None = None
        verification_report: dict[str, Any] = {}
        draft_label: str | None = None
        if run.source_template_id == "s02_project_planning":
            final_metadata = records[ordered_nodes[-1].id].metadata
            if _is_s02_lightweight_run(run):
                source_validation = final_metadata.get(
                    "s02_lightweight_source_validation"
                )
                output_validation = final_metadata.get(
                    "s02_lightweight_output_validation"
                )
                lightweight_verification = final_metadata.get(
                    "s02_lightweight_ai_verification"
                )
                if (
                    not isinstance(source_validation, dict)
                    or source_validation.get("passed") is not True
                    or not isinstance(output_validation, dict)
                    or output_validation.get("passed") is not True
                    or not isinstance(lightweight_verification, dict)
                    or lightweight_verification.get("passed") is not True
                ):
                    raise TaskWorkflowConflictError(
                        "S02 v5.1 来源完整性、章节结构或 AI 验收未全部通过，拒绝交付。"
                    )
            delivery_status, verification_report = _s02_delivery_finalization(
                run,
                final_metadata,
            )
            if delivery_status != "draft_unverified":
                raise TaskWorkflowConflictError(
                    "S02 交付状态必须保持 draft_unverified。"
                )
            if not any(node.artifact is not None for node in ordered_nodes):
                raise TaskWorkflowConflictError(
                    "S02 DAG 缺少冻结模板 DOCX Artifact 配置，拒绝交付。"
                )
            draft_label = "DEMO/SYNTHETIC · 演示草稿 · 未完成完整证据核验"
        return delivery_status, verification_report, draft_label

    async def _finalize_dag_run(
        self,
        run: TaskRunRecord,
        *,
        owner_token: str,
        fence_epoch: int,
    ) -> TaskRunRecord:
        """Persist terminal outputs and write configured artifacts once per path."""

        if run.graph is None:
            raise TaskGraphValidationError("graph", "DAG Run has no graph snapshot.")
        terminal_ids = terminal_node_ids(run.graph, run.nodes)
        records = {item.node_id: item for item in run.node_runs}
        delivery_status, verification_report, draft_label = (
            self._dag_scenario_delivery_state(
                run,
                terminal_ids=terminal_ids,
                records=records,
            )
        )
        final_output = run.final_output
        if not final_output:
            outputs = {
                node_id: records[node_id].output
                for node_id in terminal_ids
            }
            final_output = (
                outputs[terminal_ids[0]]
                if len(terminal_ids) == 1
                else json.dumps(
                    {"terminal_outputs": outputs},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            run = await self._dag_transition(
                run.id,
                owner_token=owner_token,
                fence_epoch=fence_epoch,
                run_patch={
                    "final_output": final_output,
                    "final_summary": _compact_text(final_output),
                },
                audit_events=[
                    TaskEventDraft(
                        type="run.finalizing",
                        payload={"terminal_node_ids": terminal_ids},
                    )
                ],
            )
        artifact_node = _artifact_node(run.nodes, graph=run.graph)
        if (
            run.source_template_id == "s02_project_planning"
            and artifact_node is None
        ):
            raise TaskWorkflowConflictError(
                "S02 DAG 完成交付需要配置 DOCX Artifact。"
            )
        if delivery_status is not None and run.artifacts and any(
            artifact.delivery_status != delivery_status
            for artifact in run.artifacts
        ):
            raise TaskWorkflowConflictError(
                "已有场景 Artifact 的交付状态与本次业务验收不一致，拒绝完成 Run。"
            )
        if artifact_node is not None and not run.artifacts:
            artifact_config = artifact_node.artifact
            if artifact_config is None:
                raise RuntimeError("Artifact configuration is missing.")
            context = ExecutionContext(
                **run.context.model_dump(),
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                task_revision=run.task_revision,
                history=self._execution_history(run),
            )
            # A deterministic spec and run-scoped path make a restart overwrite
            # the same artifact instead of creating a second file.
            generated = generate_artifact(
                output=final_output,
                config=artifact_config,
                default_stem=f"task-{run.id}",
                spec=spec_from_markdown(final_output),
                draft_label=draft_label,
            )
            artifact_run = (
                run.model_copy(
                    update={
                        "delivery_status": delivery_status,
                        "verification_report": verification_report,
                    }
                )
                if delivery_status is not None
                else run
            )
            artifact = await self._store_artifact(
                run=artifact_run,
                node=artifact_node,
                generated=generated,
                context=context,
            )
            run = await self._dag_transition(
                run.id,
                owner_token=owner_token,
                fence_epoch=fence_epoch,
                run_patch={
                    "artifacts": [artifact],
                    **(
                        {
                            "delivery_status": delivery_status,
                            "verification_report": verification_report,
                        }
                        if delivery_status is not None
                        else {}
                    ),
                },
                audit_events=[
                    TaskEventDraft(
                        type="artifact.created",
                        node_id=artifact_node.id,
                        payload=artifact.model_dump(mode="json"),
                    )
                ],
            )
        run = await self._dag_transition(
            run.id,
            owner_token=owner_token,
            fence_epoch=fence_epoch,
            run_patch={
                "status": RunStatus.SUCCEEDED,
                "final_output": final_output,
                "final_summary": _compact_text(final_output),
                "finished_at": _utc_now(),
                "pending_input": None,
                **(
                    {
                        "delivery_status": delivery_status,
                        "verification_report": verification_report,
                    }
                    if delivery_status is not None
                    else {}
                ),
            },
            audit_events=[
                TaskEventDraft(
                    type="run.succeeded",
                    payload={"summary": _compact_text(final_output)},
                )
            ],
            release_owner=True,
        )
        await self._mark_task_run_status(run)
        return run

    async def _advance_sop(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
        *,
        owner_token: str,
        fence_epoch: int,
    ) -> None:
        """Advance a linear Run one official SOP node at a time."""

        run = await self._find_run(run_id)
        if (
            run is None
            or run.status in _TERMINAL_RUN_STATUSES
            or self._is_cancel_requested(run_id)
        ):
            return
        engine: SOPEngine | None = None
        active_node: TaskNode | None = None
        active_node_input = ""
        try:
            for node in run.nodes:
                current = await self._store.get_run(run.user_id, run.id)
                if (
                    current is None
                    or current.status in _TERMINAL_RUN_STATUSES
                    or self._is_cancel_requested(run_id)
                ):
                    return
                if current.status is RunStatus.AWAITING_INPUT:
                    return
                if current.control_mode == "paused":
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        run_patch={"status": RunStatus.QUEUED},
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                    return
                if current.control_mode in {"stop_requested", "stopped"}:
                    return
                if current.control_mode == "pause_requested":
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        run_patch={
                            "status": RunStatus.QUEUED,
                            "control_mode": "paused",
                        },
                        audit_events=[TaskEventDraft(type="run.paused")],
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                    return
                stored = next(
                    item for item in current.node_runs if item.node_id == node.id
                )
                if stored.status is NodeRunStatus.SUCCEEDED:
                    continue
                if stored.status is NodeRunStatus.FAILED:
                    raise RuntimeError(stored.error or "SOP step failed.")
                if stored.status is NodeRunStatus.AWAITING_INPUT:
                    return
                if stored.status is NodeRunStatus.RUNNING:
                    raise TaskWorkflowConflictError(
                        "A SOP node is already running without a recoverable checkpoint."
                    )
                step_state = self._task_sop_step_state(current, node.id)
                engine_state = TaskSOPRunState(
                    id=f"{current.id}:{node.id}",
                    inputs=[UserMsg(name="user", content=current.input)],
                    steps=[step_state],
                    task_node_ids=[node.id],
                )
                engine = self._build_sop_engine(
                    current,
                    knowledge_gateway,
                    selected_nodes=[node],
                    initial_state=engine_state,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                )
                active_node = node
                previous = next(
                    (
                        item
                        for item in current.node_runs
                        if item.order == node.order - 1
                    ),
                    None,
                )
                node_input = current.input if previous is None else previous.output
                active_node_input = node_input
                async for event in engine.reply_stream():
                    if self._is_cancel_requested(run_id):
                        return
                    if isinstance(event, CustomEvent) and event.name.startswith(
                        "TASK_STEP_"
                    ):
                        current = await self._persist_dag_step_checkpoint(
                            current_run_id=run_id,
                            node=node,
                            node_input=node_input,
                            engine=engine,
                            event=event,
                            owner_token=owner_token,
                            fence_epoch=fence_epoch,
                        )
                current = await self._persist_dag_step_checkpoint(
                    current_run_id=run_id,
                    node=node,
                    node_input=node_input,
                    engine=engine,
                    event=None,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                )
                if engine.phase is SOPPhase.AWAITING:
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        run_patch={"status": RunStatus.AWAITING_INPUT},
                        audit_events=[
                            TaskEventDraft(
                                type="run.awaiting_input",
                                node_id=node.id,
                                payload={
                                    "request_id": (
                                        current.pending_input.id
                                        if current.pending_input
                                        else None
                                    )
                                },
                            )
                        ],
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                    return
                if engine.phase is SOPPhase.FAILED:
                    rejected = engine.state.steps[0]
                    raise RuntimeError(
                        rejected.metadata.get("error")
                        or (
                            rejected.verifications[-1].message
                            if rejected.verifications
                            else "SOP step failed."
                        )
                    )
                if engine.phase is not SOPPhase.COMPLETED:
                    raise RuntimeError("SOP node ended without completing.")
                current = await self._store.get_run(run.user_id, run.id) or current
                has_pending = any(
                    item.status is not NodeRunStatus.SUCCEEDED
                    for item in current.node_runs
                )
                if has_pending and current.control_mode in {
                    "single_step",
                    "pause_requested",
                }:
                    step_requested = current.control_mode == "single_step"
                    current = await self._dag_transition(
                        run_id,
                        owner_token=owner_token,
                        fence_epoch=fence_epoch,
                        run_patch={
                            "status": RunStatus.QUEUED,
                            "control_mode": "paused",
                        },
                        audit_events=[
                            TaskEventDraft(
                                type=(
                                    "run.step_completed"
                                    if step_requested
                                    else "run.paused"
                                ),
                                node_id=node.id,
                                payload={"node_id": node.id},
                            )
                        ],
                        release_owner=True,
                    )
                    await self._mark_task_run_status(current)
                    return

            run = await self._store.get_run(run.user_id, run.id) or run
            if any(
                item.status is not NodeRunStatus.SUCCEEDED
                for item in run.node_runs
            ):
                raise RuntimeError("SOP ended before every frozen node succeeded.")
            delivery_status: str | None = None
            verification_report: dict[str, Any] | None = None
            if run.source_template_id == "s02_project_planning":
                final_node = run.node_runs[-1] if run.node_runs else None
                metadata = final_node.metadata if final_node is not None else {}
                delivery_status, verification_report = (
                    _s02_delivery_finalization(run, metadata)
                )
            previous_output = run.node_runs[-1].output
            execution_history = self._execution_history(run)

            artifacts: list[TaskArtifactRecord] = []
            if self._is_cancel_requested(run_id):
                return
            artifact_node = _artifact_node(run.nodes)
            if (
                run.source_template_id == "s02_project_planning"
                and (
                    run.source_template_version in {"4.0.0", "4.1.0"}
                    or _is_s02_lightweight_run(run)
                )
                and artifact_node is None
            ):
                raise RuntimeError(
                    "S02 完成交付需要配置 DOCX Artifact。"
                )
            if artifact_node is not None:
                artifact_config = artifact_node.artifact
                if artifact_config is None:  # pragma: no cover
                    raise RuntimeError("Artifact configuration is missing.")
                artifact_context = ExecutionContext(
                    **run.context.model_dump(),
                    user_id=run.user_id,
                    task_id=run.task_id,
                    run_id=run.id,
                    task_revision=run.task_revision,
                    history=execution_history,
                )
                if any(
                    node.capability_key == "document.citation_verify"
                    for node in run.nodes
                ) or _is_s02_lightweight_run(run):
                    # Keep markdown documents intact after AI step verification.
                    artifact_spec = spec_from_markdown(previous_output)
                else:
                    artifact_spec = await self._build_artifact_spec(
                        output=previous_output,
                        context=artifact_context,
                        format=artifact_config.format.value,
                    )
                generated = generate_artifact(
                    output=previous_output,
                    config=artifact_config,
                    default_stem=f"task-{run.id}",
                    spec=artifact_spec,
                    draft_label=(
                        "DEMO/SYNTHETIC · 演示草稿 · 未完成完整证据核验"
                        if (
                            run.delivery_mode == "demo_draft"
                            or delivery_status == "draft_unverified"
                        )
                        else (
                            "项目策划初稿 · 已通过 Task AI 验收 · 来源事实仍需人工复核"
                            if run.source_template_id == "s02_project_planning"
                            else None
                        )
                    ),
                )
                artifact_run = (
                    run.model_copy(
                        update={"delivery_status": delivery_status}
                    )
                    if delivery_status is not None
                    else run
                )
                artifact = await self._store_artifact(
                    run=artifact_run,
                    node=artifact_node,
                    generated=generated,
                    context=artifact_context,
                )
                artifacts.append(artifact)
                update_fields: dict[str, Any] = {"artifacts": artifacts}
                if delivery_status is not None:
                    update_fields["delivery_status"] = delivery_status
                    update_fields["verification_report"] = (
                        verification_report or {}
                    )
                run = await self._dag_transition(
                    run.id,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                    run_patch=update_fields,
                    audit_events=[
                        TaskEventDraft(
                            type="artifact.created",
                            node_id=artifact_node.id,
                            payload=artifact.model_dump(mode="json"),
                        )
                    ],
                )
            if self._is_cancel_requested(run_id):
                return
            run = await self._dag_transition(
                run.id,
                owner_token=owner_token,
                fence_epoch=fence_epoch,
                run_patch={
                    "status": RunStatus.SUCCEEDED,
                    "final_output": previous_output,
                    "final_summary": _compact_text(previous_output),
                    "finished_at": _utc_now(),
                },
                audit_events=[
                    TaskEventDraft(
                        type="run.succeeded",
                        payload={"summary": _compact_text(previous_output)},
                    )
                ],
                release_owner=True,
            )
            await self._mark_task_run_status(run)
        except asyncio.CancelledError:
            # ``cancel_run`` persists the terminal status; a shutdown does not
            # need to publish a user-facing event.
            return
        except Exception as error:  # pylint: disable=broad-except
            message = str(error) or "Task node execution failed."
            if self._is_cancel_requested(run_id):
                return
            if (
                engine is not None
                and active_node is not None
                and engine.state.steps
                and engine.state.steps[0].phase is SOPPhase.RUNNING
            ):
                running = engine.state.steps[0]
                running.phase = SOPPhase.FAILED
                running.metadata["error"] = message
                await self._persist_dag_step_checkpoint(
                    current_run_id=run_id,
                    node=active_node,
                    node_input=active_node_input,
                    engine=engine,
                    event=None,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                )
            current = await self._find_run(run_id)
            if (
                current is None
                or current.status in _TERMINAL_RUN_STATUSES
                or current.owner_token != owner_token
                or current.fence_epoch != fence_epoch
            ):
                return
            failed_node = next(
                (
                    node_run
                    for node_run in current.node_runs
                    if node_run.status
                    in {NodeRunStatus.RUNNING, NodeRunStatus.FAILED}
                ),
                None,
            )
            artifact_node = (
                _artifact_node(current.nodes)
                if engine is not None
                and engine.phase is SOPPhase.COMPLETED
                else None
            )
            artifact_failure = bool(
                artifact_node is not None and not current.artifacts
            )
            if failed_node is None and artifact_failure and artifact_node:
                failed_node = next(
                    (
                        item
                        for item in current.node_runs
                        if item.node_id == artifact_node.id
                    ),
                    None,
                )
            error_node = next(
                (
                    item
                    for item in current.nodes
                    if failed_node is not None
                    and item.id == failed_node.node_id
                ),
                artifact_node if artifact_failure else None,
            )
            error_detail = (
                classify_node_error(
                    current,
                    error_node,
                    failed_node.metadata if failed_node is not None else {},
                    message,
                    artifact_failure=artifact_failure,
                )
                if error_node is not None
                else None
            )
            node_patches: dict[str, dict[str, Any]] = {}
            audit_events: list[TaskEventDraft] = []
            if failed_node is not None:
                node_updates: dict[str, Any] = {
                    "status": NodeRunStatus.FAILED,
                    "error": message,
                    "error_detail": error_detail,
                    "finished_at": _utc_now(),
                }
                if artifact_failure:
                    error_code = (
                        error_detail.code
                        if error_detail is not None
                        else "TASK_ARTIFACT_GENERATION_FAILED"
                    )
                    metadata = dict(failed_node.metadata)
                    metadata["authoritative_output_status"] = {
                        "status": "failed",
                        "error_code": error_code,
                        "message": message,
                        "artifact_generated": False,
                    }
                    node_updates.update(
                        {
                            "output": _authoritative_failure_header(
                                error_code,
                                message,
                                artifact_generated=False,
                            )
                            + failed_node.output,
                            "metadata": metadata,
                        }
                    )
                if failed_node.status is not NodeRunStatus.FAILED:
                    node_patches[failed_node.node_id] = node_updates
                    audit_events.append(
                        TaskEventDraft(
                            type="step.failed",
                            node_id=failed_node.node_id,
                            payload={
                                "message": message,
                                "error_detail": (
                                    error_detail.model_dump(mode="json")
                                    if error_detail is not None
                                    else None
                                ),
                            },
                        )
                    )
            audit_events.append(
                TaskEventDraft(
                    type="run.failed",
                    payload={
                        "message": message,
                        "error_detail": (
                            error_detail.model_dump(mode="json")
                            if error_detail is not None
                            else None
                        ),
                    },
                )
            )
            current = await self._dag_transition(
                current.id,
                owner_token=owner_token,
                fence_epoch=fence_epoch,
                run_patch={
                    "status": RunStatus.FAILED,
                    "error": message,
                    "error_detail": error_detail,
                    "finished_at": _utc_now(),
                },
                node_patches=node_patches,
                audit_events=audit_events,
                release_owner=True,
            )
            await self._mark_task_run_status(current)

    @asynccontextmanager
    async def _run_guard(
        self,
        run_id: str,
        *,
        ttl_secs: int = 3600,
    ) -> AsyncIterator[None]:
        """Serialize input admission and execution, including across workers."""
        async with self._run_locks.setdefault(run_id, asyncio.Lock()):
            if self._message_bus is None:
                yield
            else:
                async with self._message_bus.acquire_lock(
                    f"lxscope:task:run:{run_id}:lock",
                    ttl_secs=ttl_secs,
                ):
                    yield

    def _schedule_run(
        self,
        run: TaskRunRecord,
        knowledge_gateway: TaskKnowledgeGateway | None,
    ) -> None:
        self._run_users[run.id] = run.user_id
        execution = asyncio.create_task(
            self._execute_dag_run(run.id, knowledge_gateway)
            if run.engine == "dag"
            else (
                self._execute_sop_run(run.id, knowledge_gateway)
                if run.engine == "sop"
                else self._execute_run(run.id, knowledge_gateway)
            )
        )
        self._active_runs[run.id] = execution

        def forget(_: asyncio.Task[None]) -> None:
            if self._active_runs.get(run.id) is execution:
                self._active_runs.pop(run.id, None)
                self._cancel_requested_run_ids.discard(run.id)
                self._run_users.pop(run.id, None)

        execution.add_done_callback(forget)

    async def answer_run(
        self,
        user_id: str,
        run_id: str,
        reply: TaskInputReply,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Validate a correlated reply and queue the same run from its checkpoint."""
        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        # Never block an HTTP request behind a long-running model call.
        if run.status != RunStatus.AWAITING_INPUT:
            return self._duplicate_reply(run, reply)
        async with self._run_guard(run_id):
            run = await self._store.get_run(user_id, run_id)
            if run is None:
                return None
            if run.status != RunStatus.AWAITING_INPUT:
                return self._duplicate_reply(run, reply)
            request = run.pending_input
            if request is None or request.id != reply.request_id:
                return self._duplicate_reply(run, reply)
            allowed = {question.key for question in request.questions}
            if set(reply.answers) - allowed:
                raise ValueError("回答包含当前问题之外的字段。")
            missing = [
                question.label
                for question in request.questions
                if question.required
                and not reply.answers.get(question.key, "").strip()
            ]
            if missing:
                raise ValueError("请补充必填信息：" + "、".join(missing))
            if not any(reply.answers.values()):
                raise ValueError("回答不能为空。")
            if run.engine == "dag":
                state_data = dict(run.sop_state or {})
                dag_states = dict(state_data.get("dag_node_states", {}))
                stored_state = dag_states.get(request.node_id)
                if not isinstance(stored_state, dict):
                    raise ValueError("本次运行没有等待此问题的节点检查点。")
                step = TaskStepState.model_validate(stored_state)
                if (
                    step.input_request is None
                    or step.input_request.id != reply.request_id
                ):
                    return self._duplicate_reply(run, reply)
                step.answers.append(reply)
                step.input_request = None
                dag_states[request.node_id] = step.model_dump(mode="json")
                state_data["dag_node_states"] = dag_states
                try:
                    run = await self._store.transition_run(
                        user_id,
                        run_id,
                        expected_version=run.run_version,
                        run_patch={
                            "status": RunStatus.QUEUED,
                            "pending_input": None,
                            "sop_state": state_data,
                        },
                        node_patches={
                            request.node_id: {
                                "status": NodeRunStatus.PENDING,
                                "error": None,
                                "error_detail": None,
                            }
                        },
                        audit_events=[
                            TaskEventDraft(
                                type="input.answered",
                                node_id=request.node_id,
                                payload={
                                    "request_id": request.id,
                                    "answers": reply.answers,
                                },
                            )
                        ],
                        command_id=f"input:{reply.request_id}",
                    )
                except TaskStoreConflictError:
                    latest = await self._store.get_run(user_id, run_id)
                    if latest is not None:
                        return self._duplicate_reply(latest, reply)
                    raise
                await self._mark_task_run_status(run)
                self._schedule_run(run, knowledge_gateway)
                return run

            state = self._task_sop_state(run)
            try:
                index = state.task_node_ids.index(request.node_id)
            except ValueError as error:
                raise TaskWorkflowConflictError(
                    "The input checkpoint is not bound to this Run snapshot."
                ) from error
            step = TaskStepState.model_validate(
                state.steps[index].model_dump()
            )
            step.answers.append(reply)
            step.input_request = None
            state.steps[index] = step
            pause_after_answer = run.control_mode in {
                "pause_requested",
                "paused",
            }
            run_patch: dict[str, Any] = {
                "status": RunStatus.QUEUED,
                "pending_input": None,
                "sop_state": state.model_dump(mode="json"),
            }
            if pause_after_answer:
                run_patch["control_mode"] = "paused"
            run = await self._store.transition_run(
                user_id,
                run_id,
                expected_version=run.run_version,
                run_patch=run_patch,
                node_patches={
                    request.node_id: {
                        "status": NodeRunStatus.PENDING,
                        "error": None,
                        "error_detail": None,
                    }
                },
                audit_events=[
                    TaskEventDraft(
                        type="input.answered",
                        node_id=request.node_id,
                        payload={
                            "request_id": request.id,
                            "answers": reply.answers,
                        },
                    )
                ],
                command_id=f"input:{reply.request_id}",
            )
            await self._mark_task_run_status(run)
            if not pause_after_answer:
                self._schedule_run(run, knowledge_gateway)
            return run

    @staticmethod
    def _duplicate_reply(
        run: TaskRunRecord, reply: TaskInputReply
    ) -> TaskRunRecord:
        """A retried HTTP answer must not schedule a second execution."""
        for step in _task_sop_step_payloads(run):
            for stored in step.get("answers", []):
                if stored["request_id"] == reply.request_id:
                    if stored["answers"] == reply.answers:
                        return run
                    raise ValueError("该问题已收到不同的回答。")
        raise ValueError("本次运行没有等待此问题的回答。")

    @staticmethod
    def _execution_history(run: TaskRunRecord) -> list[dict[str, object]]:
        definitions = {node.id: node for node in run.nodes}
        history: list[dict[str, object]] = []
        for node in run.node_runs:
            if node.status is not NodeRunStatus.SUCCEEDED:
                continue
            item: dict[str, object] = {
                "node_id": node.node_id,
                "name": node.name,
                "type": node.type.value,
                "output": _history_text(node.output),
                "metadata": node.metadata,
            }
            definition = definitions.get(node.node_id)
            if (
                definition is not None
                and node.type is StepType.TOOL
                and capability_contract(definition.capability_key) is not None
            ):
                try:
                    structured_result = json.loads(node.output)
                    encoded = json.dumps(
                        structured_result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
                    encoded = ""
                if encoded and len(encoded.encode("utf-8")) <= 64_000:
                    item["structured_result"] = json.loads(encoded)
            if (
                definition is not None
                and definition.effective_output_contract is not None
                and definition.effective_output_contract.downstream_binding
                == "validated_result"
                and isinstance(
                    node.metadata.get("task_ai_verification"), dict
                )
                and node.metadata["task_ai_verification"].get("method")
                == "task-verifier"
                and node.metadata["task_ai_verification"].get("passed")
                is True
            ):
                item["ai_verified_result"] = _task_output_value(
                    node.output
                )
            history.append(item)
        return history

    @staticmethod
    def _task_sop_state(run: TaskRunRecord) -> TaskSOPRunState:
        """Restore SOP state against the frozen Run node identity map."""

        node_ids = [node.id for node in run.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise TaskWorkflowConflictError(
                "The frozen Task Run snapshot contains duplicate node IDs."
            )
        fingerprint = _node_snapshot_fingerprint(run.nodes)
        if (
            run.node_snapshot_fingerprint is not None
            and run.node_snapshot_fingerprint != fingerprint
        ):
            raise TaskWorkflowConflictError(
                "The frozen Task node snapshot fingerprint is invalid."
            )
        if run.sop_state:
            try:
                state = TaskSOPRunState.model_validate(run.sop_state)
            except ValidationError as error:
                raise TaskWorkflowConflictError(
                    "The persisted SOP checkpoint is invalid and cannot be restored."
                ) from error
        else:
            state = TaskSOPRunState(
                id=run.id,
                inputs=[UserMsg(name="user", content=run.input)],
                steps=[TaskStepState() for _ in run.nodes],
                task_node_ids=node_ids,
            )
        if state.task_node_ids:
            if state.task_node_ids != node_ids:
                raise TaskWorkflowConflictError(
                    "The persisted SOP node identity map does not match this Run snapshot."
                )
        elif len(state.steps) == len(node_ids):
            # Backward compatibility for Runs written before stable IDs were
            # persisted: only the frozen Run order is accepted.
            state.task_node_ids = node_ids
        elif not state.steps and not node_ids:
            state.task_node_ids = []
        else:
            raise TaskWorkflowConflictError(
                "The legacy SOP state cannot be safely mapped to this Run snapshot."
            )
        if len(state.steps) != len(node_ids):
            raise TaskWorkflowConflictError(
                "The persisted SOP step count does not match this Run snapshot."
            )
        return state

    @classmethod
    def _task_sop_step_state(
        cls,
        run: TaskRunRecord,
        node_id: str,
    ) -> TaskStepState:
        state = cls._task_sop_state(run)
        try:
            index = state.task_node_ids.index(node_id)
        except ValueError as error:
            raise TaskWorkflowConflictError(
                f"Node {node_id!r} is not part of the frozen SOP snapshot."
            ) from error
        return TaskStepState.model_validate(state.steps[index].model_dump())

    def _build_sop_engine(
        self,
        run: TaskRunRecord,
        gateway: TaskKnowledgeGateway | None,
        *,
        selected_nodes: list[TaskNode] | None = None,
        initial_state: SOPRunState | None = None,
        owner_token: str | None = None,
        fence_epoch: int | None = None,
    ) -> SOPEngine:
        self._assert_frozen_template_output_contracts(run)

        async def persist_run_patch(
            current: TaskRunRecord,
            **updates: Any,
        ) -> TaskRunRecord:
            if owner_token is not None and fence_epoch is not None:
                return await self._dag_transition(
                    run.id,
                    owner_token=owner_token,
                    fence_epoch=fence_epoch,
                    run_patch=updates,
                )
            return await self._store.update_run(
                current.user_id,
                current.id,
                **updates,
            )

        async def execute(
            node: TaskNode, state: TaskStepState
        ) -> StepExecutionResult:
            verification_feedback = (
                state.verifications[-1].message
                if state.verifications
                else ""
            )
            current = await self._store.get_run(run.user_id, run.id)
            if (
                current is None
                or current.status is RunStatus.CANCELED
                or current.control_mode in {"stop_requested", "stopped"}
            ):
                raise asyncio.CancelledError
            inputs_by_node_id: dict[str, Any] = {}
            named_inputs: dict[str, Any] = {}
            previous_definition: TaskNode | None = None
            if run.engine == "dag" and current.graph is not None:
                inputs_by_node_id = build_inputs_by_node_id(
                    current.graph,
                    current.node_runs,
                    node.id,
                )
                named_inputs = build_named_input_values(
                    current.graph,
                    current.node_runs,
                    node.id,
                )
                if inputs_by_node_id or named_inputs:
                    previous_output = json.dumps(
                        {
                            "inputs_by_node_id": inputs_by_node_id,
                            "named_inputs": named_inputs,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                else:
                    previous_output = run.input
                direct_parents = [
                    edge.source_node_id
                    for edge in current.graph.edges
                    if edge.target_node_id == node.id
                ]
                if len(direct_parents) == 1:
                    previous_definition = next(
                        (
                            item
                            for item in run.nodes
                            if item.id == direct_parents[0]
                        ),
                        None,
                    )
                    previous_record = next(
                        (
                            item
                            for item in current.node_runs
                            if item.node_id == direct_parents[0]
                        ),
                        None,
                    )
                else:
                    previous_record = None
            else:
                previous_record = (
                    current.node_runs[node.order - 1]
                    if node.order > 0
                    else None
                )
                previous_definition = (
                    run.nodes[node.order - 1]
                    if node.order > 0
                    else None
                )
                previous_output = (
                    run.input
                    if previous_record is None
                    else previous_record.output
                )
            if (
                run.engine == "dag"
                and self._is_linear_source_template_run(run)
                and previous_record is not None
            ):
                # A copied scenario's default linear graph is the same SOP
                # handoff: preserve the predecessor's original text while the
                # DAG-specific structured parent map remains in ExecutionContext.
                previous_output = previous_record.output
            previous_result = None
            if previous_record is not None and previous_definition is not None:
                previous_contract = (
                    previous_definition.effective_output_contract
                )
                previous_verification = previous_record.metadata.get(
                    "task_ai_verification", {}
                )
                if (
                    previous_record.status is NodeRunStatus.SUCCEEDED
                    and previous_contract is not None
                    and previous_contract.downstream_binding
                    == "validated_result"
                    and isinstance(previous_verification, dict)
                    and previous_verification.get("method")
                    == "task-verifier"
                    and previous_verification.get("passed") is True
                ):
                    previous_result = _task_output_value(
                        previous_record.output
                    )
            answers = [
                TaskInputReply.model_validate(answer)
                for step in _task_sop_step_payloads(current)
                for answer in step.get("answers", [])
            ]
            context = ExecutionContext(
                **self._step_context(run, node).model_dump(),
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                task_revision=run.task_revision,
                source_template_id=run.source_template_id,
                source_template_version=run.source_template_version,
                history=self._execution_history(current),
                previous_result=previous_result,
                initial_input=run.input,
                user_answers=answers,
                information_contract=build_information_contract(
                    nodes=run.nodes,
                    graph=run.graph,
                    node_id=node.id,
                    source_template_id=run.source_template_id,
                    source_template_version=run.source_template_version,
                    snapshot_fingerprint=run.node_snapshot_fingerprint,
                    run_id=run.id,
                ),
                clarification_state=state.metadata.get("clarification", {}),
                clarification_rounds=len(state.answers),
                verification_feedback=verification_feedback,
                inputs_by_node_id=inputs_by_node_id,
                named_inputs=named_inputs,
            )
            if (
                isinstance(node.config, ToolStepConfig)
                and node.capability_key in {
                    "project.similar_search",
                    "project.rule_check",
                    "report.rag_retrieve",
                }
            ):
                if gateway is None:
                    return StepExecutionResult(
                        text="Task 知识库服务未配置。",
                        status="failed",
                        reason="Task 知识库服务未配置，不能执行授权资料检索。",
                        metadata={
                            "knowledge_error_code": "knowledge_service_unavailable",
                        },
                    )
                try:
                    selected_sources = _task_run_source_material_ids(current)
                    if selected_sources != current.source_material_ids:
                        current = await persist_run_patch(
                            current,
                            source_material_ids=selected_sources,
                        )
                except TaskKnowledgeError as error:
                    return StepExecutionResult(
                        text=error.message,
                        status="failed",
                        reason=error.message,
                        metadata={"knowledge_error_code": error.code},
                    )
                return await retrieve_scenario_evidence(
                    node.capability_key or "",
                    gateway=gateway,
                    user_id=run.user_id,
                    knowledge_base_ids=run.knowledge_base_ids,
                    source_material_ids=selected_sources,
                    input_values=self._s01_dag_retrieval_input_values(
                        run,
                        current,
                        node,
                        named_inputs,
                    ),
                    authorization_subject=run.user_id,
                )
            if (
                isinstance(node.config, ToolStepConfig)
                and is_scenario_native_capability(node.capability_key)
                and not is_knowledge_native_capability(node.capability_key)
            ):
                prior_results: dict[str, Any] = {}
                if run.engine == "dag" and current.graph is not None:
                    prior_ids = ancestors(current.graph, node.id)
                    prior_pairs = [
                        (prior_node, node_run)
                        for prior_node in run.nodes
                        if prior_node.id in prior_ids
                        for node_run in current.node_runs
                        if node_run.node_id == prior_node.id
                    ]
                else:
                    prior_pairs = list(
                        zip(
                            run.nodes[:node.order],
                            current.node_runs[:node.order],
                        )
                    )
                for prior_node, node_run in prior_pairs:
                    if prior_node.capability_key:
                        prior_results[prior_node.capability_key] = node_run.result
                return execute_scenario_capability(
                    node.capability_key or "",
                    input_values=_task_run_input_values(current),
                    prior_results=prior_results,
                    run_id=run.id,
                )
            if (
                isinstance(node.config, ToolStepConfig)
                and is_task_native_capability(node.capability_key)
            ):
                if gateway is None:
                    return StepExecutionResult(
                        text="Task 知识库服务未配置。",
                        status="failed",
                        reason="Task 知识库服务未配置，不能执行授权检索或引用核验。",
                    )
                if node.capability_key == "planning.template_retrieve":
                    try:
                        selected_sources = _task_run_source_material_ids(
                            current
                        )
                        if selected_sources != current.source_material_ids:
                            current = await persist_run_patch(
                                current,
                                source_material_ids=selected_sources,
                            )
                        template_result = (
                            await gateway.retrieve_planning_template_evidence(
                                run.user_id,
                                run.knowledge_base_ids,
                                top_k=12,
                            )
                        )
                        input_values = _task_run_input_values(current)
                        project_result = (
                            await gateway.retrieve_project_material_evidence(
                                run.user_id,
                                run.knowledge_base_ids,
                                project_name=input_values.get(
                                    "project_name", ""
                                ),
                                source_material_ids=selected_sources,
                                top_k=8,
                            )
                        )
                    except TaskKnowledgeError as error:
                        return StepExecutionResult(
                            text=error.message,
                            status="failed",
                            reason=error.message,
                            metadata={
                                "knowledge_error_code": error.code,
                                "retrieval_diagnostics": error.details,
                            },
                        )
                    template_evidence = normalize_evidence_snapshot(
                        template_result["evidence"],
                        authorization_subject=run.user_id,
                        collected_at=template_result.get("retrieved_at"),
                    )
                    project_evidence = normalize_evidence_snapshot(
                        project_result["evidence"],
                        authorization_subject=run.user_id,
                        collected_at=project_result.get("retrieved_at"),
                    )
                    project_result_for_run = dict(project_result)
                    project_result_for_run["evidence"] = project_evidence
                    result = {
                        **template_result,
                        "template_evidence": template_evidence,
                        "project_material_retrieval": project_result_for_run,
                        "evidence": [*template_evidence, *project_evidence],
                        "citation_protocol": (
                            template_result["citation_protocol"]
                            + " 项目事实只能引用 evidence_type 为 project_material "
                            "的来源；模板规则只能引用 planning_template 来源。"
                        ),
                    }
                    agent_result = dict(result)
                    for key in ("evidence", "template_evidence"):
                        agent_result[key] = [
                            {
                                field: value
                                for field, value in item.items()
                                if field != "authorization_subject"
                            }
                            for item in result[key]
                        ]
                    agent_project_result = dict(project_result_for_run)
                    agent_project_result["evidence"] = [
                        {
                            field: value
                            for field, value in item.items()
                            if field != "authorization_subject"
                        }
                        for item in project_evidence
                    ]
                    agent_result[
                        "project_material_retrieval"
                    ] = agent_project_result
                    return StepExecutionResult(
                        text=gateway.format_task_evidence(agent_result),
                        result=result,
                        metadata={
                            "knowledge_retrieval": {
                                "status": "ready",
                                "evidence_count": len(result["evidence"]),
                                "template_evidence_count": len(
                                    template_evidence
                                ),
                                "project_material_evidence_count": len(
                                    project_evidence
                                ),
                                "project_material_status": project_result[
                                    "status"
                                ],
                                "retrieved_at": result["retrieved_at"],
                                "template_matches": result["template_matches"],
                                "queries": result["queries"],
                                "project_material_queries": project_result[
                                    "queries"
                                ],
                            },
                        },
                    )
                if node.capability_key in {
                    "document.citation_verify",
                    "report.citation_check",
                }:
                    evidence_result = _retrieval_result_for_run(
                        run,
                        current,
                        node.id if run.engine == "dag" else node.order,
                    )
                    if evidence_result is None:
                        detail = "引用核验节点未找到本次运行已保存的授权检索证据。"
                        return StepExecutionResult(
                            text=detail,
                            status="failed",
                            reason=detail,
                            metadata={
                                "citation_verification": {
                                    "passed": False,
                                    "issues": [detail],
                                    "verified_citations": [],
                                },
                            },
                            result={
                                "status": "authorized_evidence_missing",
                                "message": detail,
                            },
                        )
                    authorized_sources = [
                        {
                            field: item.get(field)
                            for field in (
                                "evidence_id",
                                "source_type",
                                "source_id",
                                "knowledge_base_id",
                                "knowledge_base_name",
                                "document_id",
                                "chunk_index",
                                "source",
                                "content_sha256",
                                "source_text",
                                "evidence_type",
                            )
                            if field in item
                        }
                        for item in evidence_result.get("evidence", [])
                        if isinstance(item, dict)
                    ]
                    citation_verification = (
                        verify_task_citations(
                            previous_output,
                            evidence_result.get("evidence", []),
                            authorization_subject=run.user_id,
                        )
                        if "```task-citations" in previous_output
                        else None
                    )
                    if (
                        citation_verification is not None
                        and citation_verification.get("passed") is not True
                    ):
                        issues = citation_verification.get("issues") or [
                            "TaskRun 引用核验失败。"
                        ]
                        detail = "授权来源引用校验失败：" + "；".join(
                            str(item) for item in issues[:8]
                        )
                        return StepExecutionResult(
                            text=detail,
                            result={
                                "status": "citation_verification_failed",
                                "message": detail,
                            },
                            metadata={
                                "citation_verification": citation_verification,
                                "output_contract_validation": {
                                    "contract_id": "task.report-citations.v1",
                                    "contract_version": 1,
                                    "validation_mode": "citations",
                                    "status": "failed",
                                    "target": "authorized_run_snapshot",
                                    "issues": [str(item) for item in issues[:8]],
                                },
                            },
                            status="failed",
                            reason=detail,
                        )
                    review_input = {
                        "status": "ready_for_ai_review",
                        "report_to_review": previous_output,
                        "authorized_sources": authorized_sources,
                        "review_instruction": (
                            "按本节点验收标准结合本次授权来源完成内容与语义审核；"
                            "最终报告必须包含唯一 task-citations 区块，服务端会在"
                            "报告节点执行前确定性核验引用结构、授权主体、来源、原文和哈希。"
                        ),
                    }
                    if citation_verification is not None:
                        review_input["citation_verification"] = (
                            citation_verification
                        )
                    text = json.dumps(
                        review_input,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    citation_metadata: dict[str, Any] = {
                        "citation_review_input": review_input,
                        "citation_protocol_status": (
                            "verified"
                            if citation_verification is not None
                            else "deferred_to_final_output"
                        ),
                    }
                    if citation_verification is not None:
                        citation_metadata["citation_verification"] = (
                            citation_verification
                        )
                        citation_metadata["output_contract_validation"] = {
                            "contract_id": "task.report-citations.v1",
                            "contract_version": 1,
                            "validation_mode": "citations",
                            "status": "passed",
                            "target": "authorized_run_snapshot",
                            "issues": [],
                        }
                    return StepExecutionResult(
                        text=text,
                        result=review_input,
                        metadata=citation_metadata,
                        status="completed",
                    )
            citation_bound_run = any(
                item.capability_key in {
                    "document.citation_verify",
                    "report.citation_check",
                }
                for item in run.nodes
            )
            state.knowledge_context = ""
            if (
                gateway is not None
                and node.type is StepType.AGENT
                and not citation_bound_run
            ):
                knowledge = await self._retrieve_knowledge(
                    gateway, current, node, previous_output
                )
                if knowledge:
                    state.knowledge_context = knowledge
                    context = context.model_copy(
                        update={"knowledge_context": knowledge}
                    )
            execution_node = _with_citation_prompt(run, node)
            result = _normalize_execution_result(
                await self._select_executor(context).execute_node(
                    node=execution_node,
                    previous_output=previous_output,
                    context=context,
                )
            )
            is_s02_lightweight_final_agent = bool(
                result.status == "completed"
                and _is_s02_lightweight_final_node(run, node)
            )
            if is_s02_lightweight_final_agent:
                current = await self._store.get_run(run.user_id, run.id)
                retrieval_result = (
                    _retrieval_result_for_run(
                        run,
                        current,
                        node.id if run.engine == "dag" else node.order,
                    )
                    if current is not None
                    else None
                )
                if retrieval_result is None:
                    detail = (
                        "S02 v5 最终草稿缺少本次 TaskRun 的授权知识库检索记录。"
                    )
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            "s02_lightweight_delivery": {
                                "passed": False,
                                "error_code": "TASK_S02_LIGHTWEIGHT_RETRIEVAL_MISSING",
                                "error": detail,
                            },
                        },
                        status="failed",
                        reason=detail,
                    )
                source_validation = _validate_s02_lightweight_sources(
                    run,
                    current,
                    retrieval_result,
                )
                if source_validation["passed"] is not True:
                    detail = (
                        "S02 v5 授权来源校验失败："
                        + "；".join(source_validation["issues"])
                    )
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            "s02_lightweight_source_validation": (
                                source_validation
                            ),
                            "s02_lightweight_delivery": {
                                "passed": False,
                                "error_code": source_validation[
                                    "error_code"
                                ],
                                "status": "failed",
                                "error": detail,
                            },
                        },
                        status="failed",
                        reason=detail,
                    )
                retrieval_report = _s02_lightweight_retrieval_report(
                    retrieval_result
                )
                retrieval_report["source_integrity"] = {
                    "status": "passed",
                    "evidence_count": source_validation["evidence_count"],
                    "issues": [],
                }
                annotated_output = _annotate_s02_lightweight_draft(
                    result.text,
                    retrieval_report,
                )
                result_metadata = dict(result.metadata or {})
                result_metadata["s02_lightweight_source_validation"] = (
                    source_validation
                )
                result_metadata["s02_lightweight_retrieval"] = retrieval_report
                result_metadata["s02_lightweight_delivery"] = {
                    "passed": False,
                    "delivery_profile": "s02_lightweight_draft",
                    "status": "awaiting_task_verifier",
                    "acceptance_method": "task-verifier",
                    "evidence_ledger_status": "not_performed",
                    "semantic_support": "requires_human_review",
                    "human_review_required": True,
                    "retrieval_status": retrieval_report["status"],
                }
                result = StepExecutionResult(
                    text=annotated_output,
                    result=result.result,
                    metadata=result_metadata,
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            return result

        async def execute_with_output_gates(
            node: TaskNode,
            state: TaskStepState,
        ) -> StepExecutionResult:
            """Apply Task-owned deterministic output gates before AI review."""

            result = await execute(node, state)
            if result.status != "completed":
                return result
            current = await self._store.get_run(run.user_id, run.id)
            if current is None:
                return StepExecutionResult(
                    text=result.text,
                    result=result.result,
                    metadata={
                        **(result.metadata or {}),
                        "output_contract_validation": {
                            "status": "failed",
                            "issues": ["TaskRun 快照已不存在。"],
                        },
                    },
                    status="failed",
                    reason="TaskRun 快照已不存在，无法验证节点输出契约。",
                )

            result = await self._apply_versioned_business_output_gates(
                run=run,
                current=current,
                node=node,
                result=result,
            )
            if result.status != "completed":
                return result
            result_metadata = result.metadata or {}
            if (
                "s02_v3_structured_output" in result_metadata
                or "s02_v4_structured_output" in result_metadata
            ):
                # The versioned S02 adapter validates the raw model JSON
                # against this exact schema before normalizing it into the
                # evidence-backed report text and the typed Task result.
                return result
            return enforce_json_output_contract(node, result)

        async def verify(
            node: TaskNode, output: str, state: TaskStepState
        ) -> StepVerdict:
            """Use the shared AI verifier for every completed Task node."""

            contract_validation = state.metadata.get(
                "output_contract_validation"
            )
            if (
                node.type is StepType.AGENT
                and isinstance(contract_validation, dict)
                and contract_validation.get("status") == "failed"
                and contract_validation.get("validation_mode") == "json_schema"
            ):
                issues = contract_validation.get("issues", [])
                return StepVerdict(
                    passed=False,
                    retryable=True,
                    message=(
                        "结构化结果校验失败："
                        + "；".join(str(issue) for issue in issues[:8])
                    ),
                )

            if (
                isinstance(node.config, ToolStepConfig)
                and node.capability_key
                in {"document.citation_verify", "report.citation_check"}
                and isinstance(
                    state.metadata.get("citation_verification"),
                    dict,
                )
            ):
                citation_result = state.metadata["citation_verification"]
                passed = citation_result.get("passed") is True
                return StepVerdict(
                    passed=passed,
                    retryable=False,
                    message=(
                        "授权来源与逐字引用核验通过。"
                        if passed
                        else "授权来源与逐字引用核验未通过。"
                    ),
                )

            if _is_s02_lightweight_final_node(run, node):
                retrieval = state.metadata.get("s02_lightweight_retrieval")
                source_validation = state.metadata.get(
                    "s02_lightweight_source_validation"
                )
                if (
                    not isinstance(retrieval, dict)
                    or not isinstance(source_validation, dict)
                    or source_validation.get("passed") is not True
                ):
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message=(
                            "S02 轻量草稿缺少本次运行的授权检索或来源完整性记录；"
                            "未生成 DOCX。"
                        ),
                    )

            executor = self._select_executor(self._step_context(run, node))
            method = getattr(executor, "verify_node", None)
            if not callable(method):
                raise RuntimeError(
                    "当前执行器不支持 AI 步骤验收，请使用已配置模型的会话。"
                )
            current = await self._store.get_run(run.user_id, run.id)
            inputs_by_node_id: dict[str, Any] = {}
            named_inputs: dict[str, Any] = {}
            if (
                run.engine == "dag"
                and current is not None
                and current.graph is not None
            ):
                inputs_by_node_id = build_inputs_by_node_id(
                    current.graph,
                    current.node_runs,
                    node.id,
                )
                named_inputs = build_named_input_values(
                    current.graph,
                    current.node_runs,
                    node.id,
                )
            context = ExecutionContext(
                **self._step_context(run, node).model_dump(),
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                task_revision=run.task_revision,
                initial_input=run.input,
                knowledge_context=state.knowledge_context,
                clarification_state=state.metadata.get("clarification", {}),
                clarification_rounds=len(state.answers),
                history=self._execution_history(current or run),
                inputs_by_node_id=inputs_by_node_id,
                named_inputs=named_inputs,
                user_answers=[
                    TaskInputReply.model_validate(answer)
                    for step in _task_sop_step_payloads(current or run)
                    for answer in step.get("answers", [])
                ],
                information_contract=build_information_contract(
                    nodes=run.nodes,
                    graph=run.graph,
                    node_id=node.id,
                    source_template_id=run.source_template_id,
                    source_template_version=run.source_template_version,
                    snapshot_fingerprint=run.node_snapshot_fingerprint,
                    run_id=run.id,
                ),
            )
            verdict = StepVerdict.model_validate(
                await method(
                    node=_with_citation_prompt(run, node),
                    output=output,
                    context=context,
                )
            )
            state.metadata["task_ai_verification"] = {
                "method": "task-verifier",
                "passed": verdict.passed,
                "message": verdict.message,
                "retryable": verdict.retryable,
                "issues": [
                    issue.model_dump(mode="json")
                    for issue in verdict.issues
                ],
            }
            if _is_s02_lightweight_final_node(run, node):
                delivery = state.metadata.get("s02_lightweight_delivery", {})
                state.metadata["s02_lightweight_ai_verification"] = {
                    **state.metadata["task_ai_verification"],
                }
                state.metadata["s02_lightweight_delivery"] = {
                    **(delivery if isinstance(delivery, dict) else {}),
                    "passed": verdict.passed,
                    "status": (
                        "draft_unverified" if verdict.passed else "failed"
                    ),
                    "acceptance_method": "task-verifier",
                }
            return verdict
        steps = []
        for node in selected_nodes or run.nodes:
            output_contract = node.effective_output_contract
            is_s02_final_agent = bool(
                node.type is StepType.AGENT
                and run.source_template_id == "s02_project_planning"
                and run.nodes
                and node.id == run.nodes[-1].id
            )
            is_s02_v2_final_agent = bool(
                is_s02_final_agent
                and run.source_template_version == "2.0.0"
            )
            is_s02_v3_final_agent = bool(
                is_s02_final_agent
                and run.source_template_version
                in S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS
            )
            is_s07_v3_final_agent = bool(
                node.type is StepType.AGENT
                and run.source_template_id == "s07_report_generation"
                and run.source_template_version == "3.0.0"
                and run.nodes
                and node.id == run.nodes[-1].id
            )
            has_recoverable_output_contract = bool(
                node.type is StepType.AGENT
                and output_contract is not None
                and output_contract.validation_mode
                in {"json_schema", "claim_evidence"}
            )
            step_node = node
            if (
                run.engine == "dag"
                and isinstance(node.config, ToolStepConfig)
            ):
                # A tool's side-effect and idempotency guarantees are not part
                # of the frozen Task node contract. Never replay a Tool node
                # automatically after AI rejection.
                step_node = node.model_copy(update={"max_attempts": 1})
            elif is_s02_v2_final_agent:
                step_node = node.model_copy(
                    update={"max_attempts": min(node.max_attempts, 3)}
                )
            elif is_s02_v3_final_agent:
                step_node = node.model_copy(
                    update={"max_attempts": 1}
                )
            elif (
                is_s02_final_agent
                or is_s07_v3_final_agent
                or has_recoverable_output_contract
            ):
                step_node = node.model_copy(
                    update={"max_attempts": min(node.max_attempts, 2)}
                )
            steps.append(
                TaskSOPStep(
                    step_node,
                    lambda state, node=node: execute_with_output_gates(
                        node,
                        state,
                    ),
                    lambda output, state, node=node: verify(node, output, state),
                    validate_scenario_input_with_rules=(
                        run.source_template_id
                        in _BUILTIN_SCENARIO_TEMPLATE_IDS
                    ),
                )
            )
        state = initial_state or (
            self._task_sop_state(run)
            if run.engine in {"sop", "legacy"}
            else (
                SOPRunState.model_validate(run.sop_state)
                if run.sop_state
                else SOPRunState(
                    id=run.id,
                    inputs=[UserMsg(name="user", content=run.input)],
                )
            )
        )
        return SOPEngine(SOP(name=run.task_id, steps=steps), state)

    def _assert_frozen_template_output_contracts(
        self,
        run: TaskRunRecord,
    ) -> None:
        """Fail closed when a stored Run lost its source-version contract."""

        if run.source_template_id is None and run.source_template_version is None:
            return
        if not run.source_template_id or not run.source_template_version:
            raise TaskWorkflowConflictError(
                "冻结 TaskRun 缺少完整模板来源版本，无法验证输出契约。"
            )
        template = self._template_validator.template_for_source(
            run.source_template_id,
            run.source_template_version,
        )
        if template is None:
            raise TaskWorkflowConflictError(
                "冻结 TaskRun 的模板版本无法定位，已拒绝执行以防绕过原输出契约。"
            )

        ordered_nodes = sorted(run.nodes, key=lambda item: item.order)
        contract_steps = [
            step
            for step in template.steps
            if step.output_schema is not None or step.output_contract is not None
        ]
        if not contract_steps:
            return
        if len(ordered_nodes) != len(template.steps):
            raise TaskWorkflowConflictError(
                "冻结 TaskRun 节点数量与来源模板不一致，无法确定性恢复输出契约。"
            )
        for index, (step, node) in enumerate(
            zip(template.steps, ordered_nodes)
        ):
            if node.order != index:
                raise TaskWorkflowConflictError(
                    "冻结 TaskRun 节点顺序与来源模板不一致，无法验证输出契约。"
                )
            expected_contract = step.output_contract
            expected_schema = step.output_schema or (
                expected_contract.json_schema
                if expected_contract is not None
                else None
            )
            if expected_contract is not None and (
                node.output_contract != expected_contract
            ):
                raise TaskWorkflowConflictError(
                    f"冻结节点 {node.id} 缺少或改变了模板版本的输出契约。"
                )
            if expected_schema is not None and node.output_schema != expected_schema:
                raise TaskWorkflowConflictError(
                    f"冻结节点 {node.id} 缺少或改变了模板版本的输出 Schema。"
                )

    async def _apply_versioned_business_output_gates(
        self,
        *,
        run: TaskRunRecord,
        current: TaskRunRecord,
        node: TaskNode,
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        """Apply frozen S02/S07 source and output gates before AI acceptance."""

        is_terminal = (
            node.id in terminal_node_ids(run.graph, run.nodes)
            if run.engine == "dag" and run.graph is not None
            else bool(run.nodes and node.id == run.nodes[-1].id)
        )

        def failed(
            message: str,
            *,
            error_code: str,
            metadata: dict[str, Any],
        ) -> StepExecutionResult:
            return StepExecutionResult(
                text=result.text,
                result=result.result,
                metadata=metadata,
                status="failed",
                questions=result.questions,
                reason=message,
            )

        async def authorized_evidence() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
            current_run = await self._store.get_run(run.user_id, run.id)
            evidence_result = (
                _retrieval_result_for_run(
                    run,
                    current_run,
                    node.id if run.engine == "dag" else node.order,
                )
                if current_run is not None
                else None
            )
            evidence = (
                evidence_result.get("evidence", [])
                if isinstance(evidence_result, dict)
                else []
            )
            if not isinstance(evidence, list):
                evidence = []
            return evidence_result, evidence

        if (
            run.source_template_id == "s02_project_planning"
            and is_terminal
            and node.type is StepType.AGENT
        ):
            version = run.source_template_version or ""
            metadata = dict(result.metadata or {})
            if version in S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS:
                evidence_result, evidence = await authorized_evidence()
                contract = node.effective_output_contract
                schema = (
                    contract.json_schema
                    if contract is not None
                    and contract.json_schema is not None
                    else node.output_schema
                )
                structured_key = (
                    "s02_v4_structured_output"
                    if version in {"4.0.0", "4.1.0"}
                    else "s02_v3_structured_output"
                )
                structured_target = {
                    "4.0.0": "s02_v4_raw_agent_json",
                    "4.1.0": "s02_v4_1_raw_agent_json",
                }.get(version, "s02_v3_raw_agent_json")
                if evidence_result is None or not evidence:
                    message = "S02 历史模板缺少本次 TaskRun 的授权 Evidence 快照。"
                    structured = {
                        "passed": False,
                        "error_code": "TASK_S02_EVIDENCE_SNAPSHOT_MISSING",
                        "error": message,
                        "issues": [message],
                    }
                elif not isinstance(schema, dict):
                    message = "S02 历史模板的冻结结构化输出 Schema 缺失。"
                    structured = {
                        "passed": False,
                        "error_code": "TASK_S02_OUTPUT_SCHEMA_MISSING",
                        "error": message,
                        "issues": [message],
                    }
                elif version in {"4.0.0"}:
                    structured = assemble_s02_v4_output(
                        result.text,
                        schema,
                        evidence,
                        authorization_subject=run.user_id,
                        delivery_mode=run.delivery_mode,
                        template_version=version,
                    )
                elif version == "4.1.0":
                    structured = assemble_s02_v4_1_output(
                        result.text,
                        schema,
                        evidence,
                        authorization_subject=run.user_id,
                        delivery_mode=run.delivery_mode,
                    )
                else:
                    structured = assemble_s02_v3_output(
                        result.text,
                        schema,
                        evidence,
                        authorization_subject=run.user_id,
                        template_version=version,
                        delivery_mode=run.delivery_mode,
                    )
                metadata[structured_key] = structured
                if version in {"4.0.0", "4.1.0"}:
                    adapter_report = structured.get("delivery_report")
                    metadata["s02_v4_delivery"] = {
                        "passed": structured.get("passed") is True,
                        "delivery_report": adapter_report,
                        **(
                            adapter_report
                            if isinstance(adapter_report, dict)
                            else {}
                        ),
                    }
                if structured.get("passed") is not True:
                    issues = structured.get("issues") or [
                        structured.get("error", "S02 结构化输出校验失败。")
                    ]
                    diagnostics = structured.get("diagnostics", [])
                    diagnostic_messages = [
                        str(item.get("message"))
                        for item in diagnostics[:8]
                        if isinstance(item, dict) and item.get("message")
                    ] if isinstance(diagnostics, list) else []
                    message = (
                        "S02 历史结构化输出校验失败："
                        + "；".join(str(item) for item in issues[:8])
                    )
                    if diagnostic_messages:
                        message += "；诊断：" + "；".join(
                            diagnostic_messages
                        )
                    metadata["output_contract_validation"] = {
                        "contract_id": (
                            contract.contract_id
                            if contract is not None
                            else "task.s02-structured-output.v1"
                        ),
                        "contract_version": (
                            contract.contract_version if contract else 1
                        ),
                        "validation_mode": "json_schema",
                        "status": "failed",
                        "target": structured_target,
                        "issues": [str(item) for item in issues[:8]],
                    }
                    if structured.get("validation_stage") == "json_schema":
                        metadata["output_schema_validation"] = {
                            "status": "failed",
                            "target": structured_target,
                            "issues": [str(item) for item in issues[:8]],
                        }
                    return failed(
                        message,
                        error_code=str(
                            structured.get(
                                "error_code",
                                "TASK_S02_STRUCTURED_OUTPUT_INVALID",
                            )
                        ),
                        metadata=metadata,
                    )

                assembled_document = structured.get("document")
                if not isinstance(assembled_document, str):
                    message = "S02 服务端适配器未生成可核验报告正文。"
                    return failed(
                        message,
                        error_code="TASK_S02_ADAPTER_OUTPUT_INVALID",
                        metadata={
                            **metadata,
                            "output_contract_validation": {
                                "status": "failed",
                                "validation_mode": "json_schema",
                                "issues": [message],
                            },
                        },
                    )

                policy = get_scenario_evidence_policy(
                    "s02_project_planning",
                    version,
                )
                citation_document, citation_actions = (
                    materialize_task_citations_from_bindings(
                        assembled_document,
                        evidence,
                        authorization_subject=run.user_id,
                        policy=policy,
                    )
                )
                citation_verification = verify_task_citations(
                    citation_document,
                    evidence,
                    authorization_subject=run.user_id,
                )
                evidence_ledger = materialize_s02_project_planning_ledger(
                    citation_document,
                    evidence,
                    authorization_subject=run.user_id,
                    template_version=version,
                )
                metadata["citation_verification"] = citation_verification
                metadata["final_citation_verification"] = citation_verification
                metadata["evidence_ledger"] = evidence_ledger
                metadata["citation_materialization_actions"] = citation_actions
                delivery_report = structured.get("delivery_report")
                if isinstance(delivery_report, dict):
                    metadata["s02_delivery_report"] = {
                        **delivery_report,
                        "evidence_ledger_passed": (
                            evidence_ledger.get("passed") is True
                        ),
                        "source_ledger": evidence_ledger.get("ledger", []),
                        "citation_verification": citation_verification,
                    }
                if evidence_ledger.get("passed") is not True:
                    issues = evidence_ledger.get("issues") or [
                        "S02 Claim–Evidence 台账校验失败。"
                    ]
                    message = "S02 历史证据台账校验失败：" + "；".join(
                        str(item) for item in issues[:8]
                    )
                    metadata["output_contract_validation"] = {
                        "contract_id": (
                            contract.contract_id
                            if contract is not None
                            else "task.s02-evidence-ledger.v1"
                        ),
                        "contract_version": (
                            contract.contract_version if contract else 1
                        ),
                        "validation_mode": "claim_evidence",
                        "status": "failed",
                        "target": "authorized_run_snapshot",
                        "issues": [str(item) for item in issues[:8]],
                    }
                    return failed(
                        message,
                        error_code="TASK_S02_EVIDENCE_LEDGER_INVALID",
                        metadata=metadata,
                    )

                metadata["output_contract_validation"] = {
                    "contract_id": (
                        contract.contract_id
                        if contract is not None
                        else "task.s02-structured-output.v1"
                    ),
                    "contract_version": contract.contract_version if contract else 1,
                    "validation_mode": "json_schema",
                    "status": "passed",
                    "target": structured_target,
                    "issues": [],
                }
                metadata["output_schema_validation"] = {
                    "status": "passed",
                    "target": structured_target,
                    "issues": [],
                }
                business_result = structured.get("normalized_business_result")
                if business_result is None:
                    business_result = structured.get("business_result")
                try:
                    # If a versioned adapter only returns report text, recover
                    # its already schema-validated source payload. Adapters
                    # that normalize business arrays retain that normalized
                    # value for downstream validated_result bindings.
                    if business_result is None:
                        business_result = json.loads(result.text)
                except (json.JSONDecodeError, TypeError, ValueError):
                    message = "S02 适配器已通过但原始结构化结果无法恢复。"
                    return failed(
                        message,
                        error_code="TASK_S02_ADAPTER_RESULT_INVALID",
                        metadata={
                            **metadata,
                            "output_contract_validation": {
                                "status": "failed",
                                "validation_mode": "json_schema",
                                "issues": [message],
                            },
                        },
                    )
                return StepExecutionResult(
                    text=evidence_ledger.get("document", citation_document),
                    result=business_result,
                    metadata=metadata,
                    status="completed",
                )

            if version not in S02_LIGHTWEIGHT_TEMPLATE_VERSIONS:
                evidence_result, evidence = await authorized_evidence()
                if evidence_result is None or not evidence:
                    message = "S02 历史 Run 缺少本次运行的授权 Evidence 快照。"
                    return failed(
                        message,
                        error_code="TASK_S02_EVIDENCE_SNAPSHOT_MISSING",
                        metadata={
                            **metadata,
                            "evidence_ledger": {
                                "passed": False,
                                "issues": [message],
                                "ledger": [],
                            },
                        },
                    )
                policy = get_scenario_evidence_policy(
                    "s02_project_planning",
                    version,
                )
                citation_document, citation_actions = (
                    materialize_task_citations_from_bindings(
                        result.text,
                        evidence,
                        authorization_subject=run.user_id,
                        policy=policy,
                    )
                )
                citation_verification = verify_task_citations(
                    citation_document,
                    evidence,
                    authorization_subject=run.user_id,
                )
                evidence_ledger = materialize_s02_project_planning_ledger(
                    citation_document,
                    evidence,
                    authorization_subject=run.user_id,
                    template_version=version,
                )
                metadata.update(
                    {
                        "citation_verification": citation_verification,
                        "final_citation_verification": citation_verification,
                        "evidence_ledger": evidence_ledger,
                        "citation_materialization_actions": citation_actions,
                        "output_contract_validation": {
                            "contract_id": "task.s02-legacy-evidence-ledger.v1",
                            "contract_version": 1,
                            "validation_mode": "claim_evidence",
                            "status": (
                                "passed"
                                if evidence_ledger.get("passed") is True
                                else "failed"
                            ),
                            "target": "authorized_run_snapshot",
                            "issues": evidence_ledger.get("issues", []),
                        },
                    },
                )
                if evidence_ledger.get("passed") is not True:
                    issues = evidence_ledger.get("issues") or [
                        "S02 历史来源和 Claim–Evidence 校验失败。"
                    ]
                    message = "S02 历史来源和证据校验失败：" + "；".join(
                        str(item) for item in issues[:8]
                    )
                    return failed(
                        message,
                        error_code="TASK_S02_LEGACY_EVIDENCE_INVALID",
                        metadata=metadata,
                    )
                return StepExecutionResult(
                    text=evidence_ledger.get("document", citation_document),
                    result=result.result,
                    metadata=metadata,
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )

            lightweight_validation = validate_s02_lightweight_markdown(
                result.text
            )
            metadata["s02_lightweight_output_validation"] = (
                lightweight_validation
            )
            if lightweight_validation.get("passed") is not True:
                issues = lightweight_validation.get("issues") or [
                    "S02 轻量报告章节结构校验失败。"
                ]
                message = "S02 轻量报告结构校验失败：" + "；".join(
                    str(item) for item in issues[:8]
                )
                metadata["s02_lightweight_delivery"] = {
                    **(
                        metadata.get("s02_lightweight_delivery", {})
                        if isinstance(
                            metadata.get("s02_lightweight_delivery"), dict
                        )
                        else {}
                    ),
                    "passed": False,
                    "status": "failed",
                    "error_code": lightweight_validation.get("error_code"),
                    "error": message,
                }
                return failed(
                    message,
                    error_code=str(
                        lightweight_validation.get(
                            "error_code",
                            "TASK_S02_LIGHTWEIGHT_OUTPUT_INVALID",
                        )
                    ),
                    metadata=metadata,
                )
            return StepExecutionResult(
                text=result.text,
                result=result.result,
                metadata=metadata,
                status=result.status,
                questions=result.questions,
                reason=result.reason,
            )

        contract = node.effective_output_contract
        requires_claim_evidence = bool(
            contract is not None
            and contract.validation_mode == "claim_evidence"
        )
        requires_citations = bool(
            contract is not None and contract.validation_mode == "citations"
        )
        is_legacy_s07_contract = bool(
            run.source_template_id == "s07_report_generation"
            and run.source_template_version in {"1.0.0", "2.0.0", "3.0.0"}
            and node.type is StepType.AGENT
            and any(
                item.capability_key == "report.citation_check"
                for item in run.nodes
            )
        )
        if (requires_citations or is_legacy_s07_contract) and (
            node.type is StepType.AGENT
        ):
            evidence_result, evidence = await authorized_evidence()
            if evidence_result is None or not evidence:
                citation_verification = {
                    "passed": False,
                    "issues": ["本次 TaskRun 缺少授权来源快照。"],
                    "verified_citations": [],
                }
            else:
                citation_verification = verify_task_citations(
                    result.text,
                    evidence,
                    authorization_subject=run.user_id,
                )
            metadata = dict(result.metadata or {})
            metadata["citation_output_validation"] = citation_verification
            metadata["output_contract_validation"] = (
                contract_validation_record(
                    contract=contract,
                    passed=citation_verification.get("passed") is True,
                    target="authorized_run_snapshot",
                    issues=citation_verification.get("issues", []),
                )
                if contract is not None
                else {
                    "contract_id": "task.report-citations.v1",
                    "contract_version": 1,
                    "validation_mode": "citations",
                    "status": (
                        "passed"
                        if citation_verification.get("passed") is True
                        else "failed"
                    ),
                    "target": "authorized_run_snapshot",
                    "issues": citation_verification.get("issues", []),
                }
            )
            if citation_verification.get("passed") is not True:
                issues = citation_verification.get("issues") or [
                    "引用校验失败。"
                ]
                message = "引用输出契约校验失败：" + "；".join(
                    str(item) for item in issues[:8]
                )
                return failed(
                    message,
                    error_code="TASK_CITATION_OUTPUT_INVALID",
                    metadata=metadata,
                )
            result = StepExecutionResult(
                text=result.text,
                result=result.result,
                metadata=metadata,
                status=result.status,
                questions=result.questions,
                reason=result.reason,
            )

        if requires_claim_evidence:
            evidence_result, evidence = await authorized_evidence()
            metadata = dict(result.metadata or {})
            if evidence_result is None or not evidence:
                citation_verification = {
                    "passed": False,
                    "issues": ["本次 TaskRun 缺少授权来源快照。"],
                    "verified_citations": [],
                }
                claim_validation = {
                    "passed": False,
                    "issues": ["本次 TaskRun 缺少授权来源快照。"],
                    "diagnostics": {},
                }
                finalized_document = result.text
            else:
                policy = get_scenario_evidence_policy(
                    run.source_template_id or "",
                    run.source_template_version or "",
                )
                finalized_document, materialization_actions = (
                    materialize_task_citations_from_bindings(
                        result.text,
                        evidence,
                        authorization_subject=run.user_id,
                        policy=policy,
                    )
                )
                citation_verification = verify_task_citations(
                    finalized_document,
                    evidence,
                    authorization_subject=run.user_id,
                )
                claim_validation = validate_claim_evidence_document(
                    finalized_document,
                    evidence,
                    authorization_subject=run.user_id,
                    citation_verification=citation_verification,
                    policy=policy,
                )
                metadata["citation_materialization_actions"] = (
                    materialization_actions
                )
            passed = bool(
                citation_verification.get("passed") is True
                and claim_validation.get("passed") is True
            )
            issues = [
                *citation_verification.get("issues", []),
                *claim_validation.get("issues", []),
            ]
            metadata["citation_verification"] = citation_verification
            metadata["final_citation_verification"] = citation_verification
            metadata["evidence_ledger"] = claim_validation
            metadata["output_contract_validation"] = contract_validation_record(
                contract=contract,
                passed=passed,
                target="authorized_run_snapshot",
                issues=issues[:8],
            )
            if not passed:
                message = "Claim–Evidence 输出契约校验失败：" + "；".join(
                    str(item) for item in issues[:8]
                )
                return failed(
                    message,
                    error_code="TASK_CLAIM_EVIDENCE_OUTPUT_INVALID",
                    metadata=metadata,
                )
            return StepExecutionResult(
                text=finalized_document,
                result=result.result,
                metadata=metadata,
                status=result.status,
                questions=result.questions,
                reason=result.reason,
            )

        return result

    @staticmethod
    def _step_context(run: TaskRunRecord, node: TaskNode) -> TaskContext:
        """Honor frozen per-step bindings without modifying the run context."""
        overrides = {}
        if isinstance(node.config, AgentStepConfig):
            if node.config.agent_id:
                overrides["agent_id"] = node.config.agent_id
            if node.config.session_id:
                overrides["session_id"] = node.config.session_id
        return run.context.model_copy(update=overrides)

    async def _checkpoint_sop(
        self,
        run: TaskRunRecord,
        engine: SOPEngine,
        event: CustomEvent | None = None,
    ) -> TaskRunRecord:
        """Project official state to the legacy UI, without deciding progression."""
        current = await self._store.get_run(run.user_id, run.id)
        if current is None or current.status in _TERMINAL_RUN_STATUSES:
            raise asyncio.CancelledError
        projections = []
        pending = None
        for definition, record, stored in zip(
            run.nodes, engine.state.steps, current.node_runs
        ):
            status = {
                SOPPhase.PENDING: NodeRunStatus.PENDING,
                SOPPhase.RUNNING: NodeRunStatus.RUNNING,
                SOPPhase.AWAITING: NodeRunStatus.AWAITING_INPUT,
                SOPPhase.COMPLETED: NodeRunStatus.SUCCEEDED,
                SOPPhase.FAILED: NodeRunStatus.FAILED,
            }[record.phase]
            started = stored.started_at
            if record.phase is not SOPPhase.PENDING:
                started = started or _utc_now()
            finished = stored.finished_at
            if status in {NodeRunStatus.SUCCEEDED, NodeRunStatus.FAILED}:
                finished = finished or _utc_now()
            pending = record.input_request or pending
            error_message = (
                record.metadata.get("error")
                or (
                    record.verifications[-1].message
                    if record.phase is SOPPhase.FAILED
                    and record.verifications
                    else ""
                )
            )
            error_detail = (
                classify_node_error(
                    run,
                    definition,
                    record.metadata,
                    str(error_message or "Task 节点执行失败。"),
                )
                if status is NodeRunStatus.FAILED
                else None
            )
            node_output = record.output
            node_metadata = dict(record.metadata)
            if (
                status is NodeRunStatus.FAILED
                and definition.type is StepType.AGENT
            ):
                message = str(error_message or "Task 节点验收失败。")
                error_code = (
                    error_detail.code
                    if error_detail is not None
                    else "TASK_NODE_EXECUTION_FAILED"
                )
                node_metadata["authoritative_output_status"] = {
                    "status": "failed",
                    "error_code": error_code,
                    "message": message,
                    "artifact_generated": False,
                }
                node_output = _authoritative_failure_header(
                    error_code,
                    message,
                    artifact_generated=False,
                ) + node_output
            projections.append(
                stored.model_copy(
                    update={
                        "status": status,
                        "output": node_output,
                        "display_summary": _compact_text(node_output),
                        "result": record.result,
                        "metadata": {
                            **node_metadata,
                            **(
                                {"capability_key": definition.capability_key}
                                if definition.capability_key
                                else {}
                            ),
                            **(
                                {"tool_name": definition.config.tool_name}
                                if isinstance(definition.config, ToolStepConfig)
                                else {}
                            ),
                            "attempts": len(record.verifications),
                            "verifications": [
                                v.model_dump(mode="json")
                                for v in record.verifications
                            ],
                        },
                        "error": error_message or None,
                        "error_detail": error_detail,
                        "started_at": started,
                        "finished_at": finished,
                        "input": (
                            run.input
                            if definition.order == 0
                            else projections[-1].output
                        ),
                    }
                )
            )
        status = (
            RunStatus.AWAITING_INPUT
            if engine.phase is SOPPhase.AWAITING
            else RunStatus.RUNNING
        )
        current = await self._store.update_run(
            run.user_id,
            run.id,
            engine="sop",
            sop_state=engine.state.model_dump(mode="json"),
            node_runs=projections,
            status=status,
            pending_input=pending,
            error_detail=next(
                (
                    item.error_detail
                    for item in projections
                    if item.error_detail is not None
                ),
                None,
            ),
        )
        await self._mark_task_run_status(current)
        if event is not None:
            node_id = event.value["node_id"]
            node = next(
                node for node in projections if node.node_id == node_id
            )
            if event.name == "TASK_STEP_STARTED":
                await self._emit(
                    current,
                    "step.started",
                    node_id=node_id,
                    payload={"step_type": node.type.value},
                )
            elif node.status is NodeRunStatus.AWAITING_INPUT:
                await self._emit(
                    current,
                    "input.requested",
                    node_id=node_id,
                    payload=pending.model_dump(mode="json"),
                )
                await self._emit(
                    current,
                    "run.awaiting_input",
                    node_id=node_id,
                    payload={"request_id": pending.id},
                )
            elif node.status is NodeRunStatus.SUCCEEDED:
                await self._emit(
                    current,
                    "step.succeeded",
                    node_id=node_id,
                    payload={
                        "summary": node.display_summary,
                        "step_type": node.type.value,
                    },
                )
            elif node.status is NodeRunStatus.PENDING:
                await self._emit(
                    current,
                    "step.rejected",
                    node_id=node_id,
                    payload={
                        "message": node.metadata["verifications"][-1][
                            "message"
                        ]
                    },
                )
        return current

    async def _build_artifact_spec(
        self,
        *,
        output: str,
        context: ExecutionContext,
        format: str,
    ) -> ArtifactSpec:
        """Use the bound AgentScope model to shape Office output when available."""

        fallback = spec_from_markdown(output)
        if format not in {"docx", "xlsx"}:
            return fallback
        if not (
            self._agentscope_executor
            and context.agent_id
            and context.session_id
        ):
            return fallback
        prompt = artifact_spec_prompt(output)
        planner = TaskNode(
            id=f"artifact-spec-{context.run_id}",
            name="规划文件结构",
            prompt=prompt,
            type=StepType.AGENT,
            config=AgentStepConfig(prompt=prompt),
            order=999,
        )
        try:
            result = await self._agentscope_executor.execute_node(
                node=planner,
                previous_output="",
                context=context,
            )
            normalized = _normalize_execution_result(result)
            return parse_agent_spec(normalized.text, output)
        except Exception:  # pylint: disable=broad-except
            # Artifact planning is an enhancement; the deterministic fallback
            # keeps the Task result available when a model call is unavailable.
            return fallback

    async def _store_artifact(
        self,
        *,
        run: TaskRunRecord,
        node: TaskNode,
        generated: GeneratedArtifact,
        context: ExecutionContext,
    ) -> TaskArtifactRecord:
        """Write generated bytes and return the persisted Task metadata."""

        if self._artifact_writer is None:
            raise RuntimeError(
                "生成文件产物需要绑定 AgentScope 会话和 Workspace。"
            )
        path = f"artifacts/{run.id}/{generated.name}"
        size_bytes = await self._artifact_writer.write_artifact(
            path=path,
            data=generated.data,
            context=context,
        )
        return TaskArtifactRecord(
            run_id=run.id,
            task_id=run.task_id,
            node_id=node.id,
            name=generated.name,
            format=generated.format,
            media_type=generated.media_type,
            path=path,
            size_bytes=size_bytes,
            preview_text=generated.preview_text,
            delivery_status=run.delivery_status,
        )

    def _select_executor(self, context: TaskContext) -> TaskExecutor:
        """Use AgentScope only when a real Chat/Session context is attached."""

        if (
            self._agentscope_executor is not None
            and context.agent_id
            and context.session_id
        ):
            return self._agentscope_executor
        return self._preview_executor

    async def _retrieve_knowledge(
        self,
        gateway: TaskKnowledgeGateway,
        run: TaskRunRecord,
        node: TaskNode,
        previous_output: str,
    ) -> str:
        """Resolve a node's effective KB scope without breaking execution."""

        if node.knowledge_base_mode == "disabled":
            return ""
        knowledge_base_ids = (
            node.knowledge_base_ids
            if node.knowledge_base_mode == "override"
            else run.knowledge_base_ids
        )
        if not knowledge_base_ids:
            return ""
        query = (
            f"任务节点：{node.name}\n"
            f"节点要求：{node.prompt}\n"
            f"上一步输出：{previous_output[-4000:]}"
        )
        try:
            return await gateway.retrieve(
                run.user_id,
                knowledge_base_ids,
                query,
            )
        except Exception:  # noqa: BLE001 — KB is an optional execution aid
            return ""

    async def _find_run(self, run_id: str) -> TaskRunRecord | None:
        """Find a run without knowing its owner for internal execution."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            return None
        return await self._store.get_run(user_id, run_id)

    def _is_cancel_requested(self, run_id: str) -> bool:
        """Return whether this local worker must preserve cancellation."""

        return run_id in self._cancel_requested_run_ids

    async def _mark_task_run_status(self, run: TaskRunRecord) -> None:
        """Reflect the latest run status on the reusable task summary."""

        task = await self._store.get_task(run.user_id, run.task_id)
        if task is not None and task.last_run_id == run.id:
            await self._store.save_task(
                task.model_copy(
                    update={
                        "last_run_id": run.id,
                        "last_run_status": run.status,
                    },
                ),
            )

    async def _emit(
        self,
        run: TaskRunRecord,
        event_type: str,
        *,
        payload: dict[str, object],
        node_id: str | None = None,
    ) -> None:
        """Append one event with a monotonic sequence number."""

        sequence = await self._store.next_event_sequence(run.id)
        await self._store.append_event(
            TaskEventRecord(
                type=event_type,
                task_id=run.task_id,
                run_id=run.id,
                node_id=node_id,
                sequence=sequence,
                payload=payload,
            ),
        )


def _compact_text(value: str, limit: int = 240) -> str:
    """Create a short UI summary without an additional model call."""

    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def _history_text(value: str, limit: int = 12_000) -> str:
    """Bound one prior result before exposing it to a later AgentStep."""

    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _task_output_value(value: str) -> Any:
    """Decode JSON for downstream use without deciding whether it is valid."""

    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return value


def _normalize_execution_result(
    value: StepExecutionResult | str,
) -> StepExecutionResult:
    """Keep custom TaskExecutor adapters source-compatible with the MVP."""

    if isinstance(value, StepExecutionResult):
        return value
    return StepExecutionResult(text=str(value))


def _artifact_node(
    nodes: list[TaskNode],
    *,
    graph: TaskGraph | None = None,
) -> TaskNode | None:
    """Return the only final-node artifact configuration, if present."""

    configured = [node for node in nodes if node.artifact is not None]
    if not configured:
        return None
    if len(configured) != 1 or configured[0].type is StepType.INPUT:
        raise RuntimeError("文件产物只能配置在一个终端 Task 节点。")
    if graph is None:
        if configured[0].id != nodes[-1].id:
            raise RuntimeError("文件产物只能配置在最后一个 Task 节点。")
    elif configured[0].id not in terminal_node_ids(graph, nodes):
        raise RuntimeError("DAG 文件产物必须配置在终端 Task 节点。")
    return configured[0]
