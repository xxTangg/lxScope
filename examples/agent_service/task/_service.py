# -*- coding: utf-8 -*-
"""Application service for reusable linear Tasks."""

import asyncio
import json
import logging
import os
import re
from uuid import uuid4
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from agentscope.event import CustomEvent
from agentscope.message import UserMsg
from agentscope.sop import SOP, SOPEngine, SOPPhase, SOPRunState
from datetime import datetime, timezone
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
    TaskErrorCategory,
    TaskErrorDetail,
    TaskGenerationStatus,
    TaskNode,
    TaskRecord,
    TaskWorkflowMode,
    TaskToolSchema,
    TaskRunRecord,
    TaskRunRequest,
    TaskInputReply,
    TaskPreflightRequest,
    ToolStepConfig,
    StepVerdict,
    StepType,
    TaskStatus,
    UpdateTaskRequest,
    TaskValidationIssue,
    TaskValidationResponse,
)
from ._planner import PreviewTaskPlanner, TaskPlanner
from ._plan_validation import format_plan_issues, validate_task_plan
from ._store import TaskStoreProtocol
from ._knowledge import TaskKnowledgeError, TaskKnowledgeGateway
from ._citation_verifier import TASK_CITATION_PROMPT, verify_task_citations
from ._evidence_contract import (
    TASK_CLAIM_EVIDENCE_PROMPT,
    TASK_CLAIM_EVIDENCE_REPAIR_PROMPT,
    TASK_CLAIM_EVIDENCE_REPAIR_LOCKED_CITATIONS_PROMPT,
    claim_evidence_failure_is_retryable,
    claim_evidence_protocol_envelope_failure_is_retryable,
    claim_evidence_protocol_repair_body_matches,
    compare_claim_evidence_inventories,
    diagnose_task_claim_evidence_protocol_materialization,
    _evidence_records,
    extract_claim_evidence_inventory,
    extract_claim_evidence_protocol_repair_inventory,
    extract_locked_claim_evidence_content_conflicts,
    materialize_locked_claim_evidence_content_repair,
    materialize_task_citations_from_bindings,
    materialize_task_claim_evidence_protocol_repair,
    wrap_claim_evidence_repair_payload_for_validation,
    normalize_evidence_snapshot,
    normalize_task_protocol_format,
    validate_claim_evidence_document,
)
from ._s02_project_planning_evidence import (
    materialize_s02_project_planning_ledger,
)
from ._s02_structured_output import (
    S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS,
    assemble_s02_v3_output,
    assemble_s02_v4_output,
    assemble_s02_v4_1_output,
)
from ._s02_lightweight_output import (
    S02_LIGHTWEIGHT_TEMPLATE_VERSION,
    validate_s02_lightweight_markdown,
)
from ._output_validation import validate_structured_result
from ._errors import classify_node_error
from ._capability_contracts import (
    capability_business_verified,
    capability_category,
    capability_contract,
    capability_external_side_effect,
    is_task_artifact_capability,
    is_task_native_capability,
    validate_capability_output,
    validate_contract_tool_schema,
)
from ._sop import TaskSOPStep, TaskStepState
from ._template_validation import (
    TaskTemplateValidator,
    TaskValidationFailure,
)
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
    for step in (run.sop_state or {}).get("steps", []):
        if not isinstance(step, dict):
            continue
        for answer in step.get("answers", []):
            try:
                reply = TaskInputReply.model_validate(answer)
            except (TypeError, ValueError):
                continue
            values.update(reply.answers)
    return values


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
    before_order: int,
) -> dict[str, Any] | None:
    """Return persisted, owner-scoped evidence from earlier RAG nodes.

    The S02 retrieval result remains structurally intact. Other scenarios may
    have multiple Task-owned retrieval nodes; their actual snapshots are
    combined without inventing evidence or widening the current Task scope.
    """

    limit = min(before_order, len(run.nodes), len(current.node_runs))
    retrievals: list[tuple[TaskNode, dict[str, Any]]] = []
    for index in range(limit):
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
    """Identify only the immutable S02 v5 template execution contract."""

    return bool(
        getattr(run, "workflow_mode", None)
        is TaskWorkflowMode.TEMPLATE_INSTANCE
        and run.source_template_id == "s02_project_planning"
        and run.source_template_version == S02_LIGHTWEIGHT_TEMPLATE_VERSION
    )


def _is_s02_lightweight_final_node(
    run: TaskRunRecord,
    node: TaskNode,
) -> bool:
    """Match the S02 v5 final Agent without affecting copies or older versions."""

    return bool(
        _is_s02_lightweight_run(run)
        and run.nodes
        and node.type is StepType.AGENT
        and node.id == run.nodes[-1].id
    )


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
Task 服务端会从协议区块生成来源证据台账和引用索引。
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
`task-citations` 可以按上方格式随协议输出；如缺失，Task 服务只会在 Claim–Evidence 区块完整且
绑定无歧义时从绑定生成候选索引，该索引仍须通过本次 Run 的来源、权限、哈希和原文校验。
正文后必须有一个完整的 `task-claim-evidence` 区块，JSON 与围栏必须闭合。
不要在正文声称服务端校验通过、文件已生成或审批已通过。
结构和来源由服务端确定性校验；语义支持仍需人工复核。
"""


_S02_TARGETED_CONTENT_REPAIR_PROMPT = """\
Task 对 S02 v2 最终输出仅授权一次局部内容修订：
只处理反馈中点名的一个 occurrence。将其中有授权来源支持的陈述与待核实事项
拆成相邻的独立正文段落/表格行：原 occurrence 保留原有引用编号并改为有来源支持的
Claim；新增一个独立 Claim 承载待核实内容，不带任何引用编号，并沿用反馈中的
unverified_id、reason 和 expected_evidence_types。不得删除待核实内容。
每个锚点必须内联在它对应的正文同一物理行末尾；每段/表格行只允许一个锚点。
除目标行和紧邻新增行外，报告正文必须逐行原样保留；其他 Claim 类型、occurrence、
待核实登记、引用编号和引用来源不得改变。目标行原有文字必须全部保留，不得补写新事实。
输出完整报告正文，随后只输出一个紧凑 JSON `task-claim-evidence` 区块，顶层且仅含
`claims` 与 `unverified_items`；claims 每项仅含 claim_id/kind，覆盖正文全部锚点；
不输出 bindings、citations 或 `task-citations`。Task 服务会使用本次运行锁定的引用
声明生成完整协议，并继续执行所有原有来源身份、权限、哈希、逐字原文和场景校验。
无法严格遵守时原样返回失败，不要声称校验通过。
"""


def _task_protocol_materialization_notice(metadata: dict[str, Any]) -> str:
    repair = metadata.get("task_protocol_repair")
    if (
        not isinstance(repair, dict)
        or repair.get("materialization_passed") is not False
    ):
        return ""
    diagnostics = repair.get("materialization_diagnostics")
    messages = [
        str(item["message"])
        for item in diagnostics
        if isinstance(item, dict) and item.get("message")
    ] if isinstance(diagnostics, list) else []
    if not messages:
        return "协议修复无法安全物化，来源与引用校验尚未开始。"
    return (
        "协议修复无法安全物化，来源与引用校验尚未开始："
        + "；".join(messages)
    )


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
    ) -> None:
        """Bind repository and execution adapters."""

        self._store = store
        self._template_validator = TaskTemplateValidator()
        self._message_bus = message_bus
        self._run_locks: dict[str, asyncio.Lock] = {}
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
        )
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
    ) -> TaskRecord | None:
        """Create a private, editable copy of a standard template Task."""

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
        title_suffix = "（自定义）"
        copied_title = source.title[: 160 - len(title_suffix)] + title_suffix
        copied = source.model_copy(
            deep=True,
            update={
                "id": uuid4().hex,
                "title": copied_title,
                "nodes": copied_nodes,
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
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            },
        )
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
        candidate = current.model_copy(update=updates)
        if (
            candidate.source_template_id is not None
            or candidate.source_template_version is not None
            or body.nodes is not None
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

    async def validate_task_definition(
        self,
        user_id: str,
        task: TaskRecord | None,
        nodes: list[TaskNode],
        *,
        context: TaskContext | None = None,
        run_input: str | None = None,
    ) -> TaskValidationResponse:
        """Validate structure and template rules; runtime readiness is preflight-only."""

        del user_id, context
        return self._template_validator.validate(
            task,
            nodes,
            run_input=run_input,
        )

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
                side_effect = side_effect or not tool.is_read_only
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
                    message = "该能力包含外部副作用，当前 Task 阶段不支持运行。"
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
                    "Task 内确定性适配可用；仅处理本次 Task 明确提交的数据，"
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
                    "Task 内确定性引用校验器可用；运行时只检查本次 TaskRun "
                    "前序授权检索节点保存的 Evidence 快照，不会重新检索或接受"
                    "用户声明的来源。语义支持仍需人工复核。"
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
                "；确定性校验器可用，但只核验编号、来源身份和逐字摘录，"
                "不自动判定语义支持"
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
        is_s02_v4_task = bool(
            task.workflow_mode == TaskWorkflowMode.TEMPLATE_INSTANCE
            and task.source_template_id == "s02_project_planning"
            and task.source_template_version in {"4.0.0", "4.1.0"}
        )
        is_s02_v5_task = bool(
            task.workflow_mode == TaskWorkflowMode.TEMPLATE_INSTANCE
            and task.source_template_id == "s02_project_planning"
            and task.source_template_version == S02_LIGHTWEIGHT_TEMPLATE_VERSION
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
                                "lightweight_draft 仅适用于标准 S02 v5.0.0 模板 Task。"
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
        run = TaskRunRecord(
            task_id=task.id,
            user_id=user_id,
            task_revision=task.revision,
            engine="sop",
            workflow_mode=task.workflow_mode,
            source_template_id=task.source_template_id,
            source_template_version=task.source_template_version,
            source_task_id=task.source_task_id,
            source_task_revision=task.source_task_revision,
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
                )
                for node in run_nodes
            ],
            knowledge_base_ids=task.knowledge_base_ids,
            source_material_ids=_task_source_material_ids(
                task.template_inputs
            ),
            input=run_input,
            context=context,
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
        await self._emit(
            run, "run.created", payload={"task_revision": task.revision}
        )
        self._schedule_run(run, knowledge_gateway)
        return await self._store.get_run(user_id, run.id)

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Get one run owned by the caller."""

        return await self._store.get_run(user_id, run_id)

    async def list_runs(
        self, user_id: str, task_id: str
    ) -> list[TaskRunRecord]:
        """List all runs for one task."""

        if await self._store.get_task(user_id, task_id) is None:
            return []
        return await self._store.list_runs(user_id, task_id)

    async def cancel_run(
        self, user_id: str, run_id: str
    ) -> TaskRunRecord | None:
        """Persist cancellation and notify the worker that owns execution."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.status in _TERMINAL_RUN_STATUSES:
            return run
        # Record intent before yielding to storage or cancellation.  A task
        # executor may catch CancelledError internally and otherwise continue
        # to write a later failed/succeeded state.
        self._cancel_requested_run_ids.add(run_id)
        active = self._active_runs.get(run_id)
        if active is not None:
            active.cancel()
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
            async with self._run_guard(run_id):
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

    async def _advance_sop(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> None:
        """Rebuild the engine from durable state, then persist its transitions."""

        run = await self._find_run(run_id)
        if (
            run is None
            or run.status in _TERMINAL_RUN_STATUSES
            or self._is_cancel_requested(run_id)
        ):
            return
        engine: SOPEngine | None = None
        try:
            run = await self._store.update_run(
                run.user_id,
                run.id,
                status=RunStatus.RUNNING,
                started_at=run.started_at or _utc_now(),
            )
            await self._mark_task_run_status(run)
            await self._emit(run, "run.started", payload={})
            engine = self._build_sop_engine(run, knowledge_gateway)
            async for event in engine.reply_stream():
                if self._is_cancel_requested(run_id):
                    return
                if isinstance(event, CustomEvent) and event.name.startswith(
                    "TASK_STEP_"
                ):
                    run = await self._checkpoint_sop(run, engine, event)
            run = await self._checkpoint_sop(run, engine)
            if engine.phase is SOPPhase.AWAITING:
                return
            if engine.phase is SOPPhase.FAILED:
                rejected = next(
                    step
                    for step in engine.state.steps
                    if step.phase is SOPPhase.FAILED
                )
                raise RuntimeError(
                    rejected.metadata.get("error")
                    or (
                        rejected.verifications[-1].message
                        if rejected.verifications
                        else "SOP step failed."
                    )
                )
            if engine.phase is not SOPPhase.COMPLETED:
                raise RuntimeError("SOP ended without completing all steps.")
            run = await self._store.get_run(run.user_id, run.id) or run
            delivery_status: str | None = None
            verification_report: dict[str, Any] | None = None
            if (
                run.source_template_id == "s02_project_planning"
                and run.source_template_version in {"4.0.0", "4.1.0"}
            ):
                final_node = run.node_runs[-1] if run.node_runs else None
                metadata = final_node.metadata if final_node is not None else {}
                adapter_result = metadata.get("s02_v4_delivery")
                report = (
                    adapter_result.get("delivery_report")
                    if isinstance(adapter_result, dict)
                    else None
                )
                ledger_status = metadata.get("evidence_ledger")
                if (
                    not isinstance(report, dict)
                    or not isinstance(adapter_result, dict)
                    or adapter_result.get("passed") is not True
                    or not isinstance(ledger_status, dict)
                    or ledger_status.get("passed") is not True
                ):
                    raise RuntimeError(
                        "S02 v4 交付状态缺少已完成的服务端证据台账校验。"
                    )
                delivery_status = (
                    "draft_unverified"
                    if run.delivery_mode == "demo_draft"
                    else "strict_verified"
                )
                ledger = ledger_status.get("ledger", [])
                source_ledger = [
                    {
                        field: item.get(field)
                        for field in (
                            "section",
                            "claim",
                            "claim_id",
                            "unverified_id",
                            "citation_id",
                            "evidence_id",
                            "knowledge_base_name",
                            "document_id",
                            "chunk_index",
                            "evidence_type",
                            "source",
                            "quote",
                            "status",
                            "status_detail",
                        )
                        if field in item
                    }
                    for item in ledger
                    if isinstance(item, dict)
                ] if isinstance(ledger, list) else []
                report = {
                    **report,
                    "chapter_count": report.get("chapter_count", 8),
                    "completed_chapters": report.get(
                        "completed_chapters",
                        [f"第{number}章" for number in range(1, 9)],
                    ),
                    "source_ledger": source_ledger,
                    "adapter_passed": adapter_result.get("passed") is True,
                    "claim_count": adapter_result.get("claim_count", 0),
                    "occurrence_count": adapter_result.get("occurrence_count", 0),
                    "binding_count": adapter_result.get("binding_count", 0),
                    "citation_count": adapter_result.get("citation_count", 0),
                    "unverified_count": adapter_result.get("unverified_count", 0),
                    "mode": run.delivery_mode,
                    "status": delivery_status,
                    "evidence_ledger_passed": True,
                    "warnings": list(report.get("warnings", [])),
                    "semantic_support": "requires_human_review",
                    "human_review_required": True,
                }
                verification_report = report
            elif _is_s02_lightweight_run(run):
                final_node = run.node_runs[-1] if run.node_runs else None
                metadata = final_node.metadata if final_node is not None else {}
                output_validation = metadata.get(
                    "s02_lightweight_output_validation"
                )
                retrieval_report = metadata.get("s02_lightweight_retrieval")
                source_validation = metadata.get(
                    "s02_lightweight_source_validation"
                )
                delivery = metadata.get("s02_lightweight_delivery")
                if (
                    not isinstance(output_validation, dict)
                    or output_validation.get("passed") is not True
                    or not isinstance(retrieval_report, dict)
                    or not isinstance(source_validation, dict)
                    or source_validation.get("passed") is not True
                    or not isinstance(delivery, dict)
                    or delivery.get("passed") is not True
                ):
                    raise RuntimeError(
                        "S02 v5 轻量草稿缺少有效的输出结构或授权检索记录。"
                    )
                delivery_status = "draft_unverified"
                verification_report = {
                    "delivery_profile": "s02_lightweight_draft",
                    "mode": "lightweight_draft",
                    "status": delivery_status,
                    "chapter_count": output_validation.get(
                        "chapter_count", 0
                    ),
                    "completed_chapters": output_validation.get(
                        "completed_chapters", []
                    ),
                    "lightweight_output_validation": {
                        "status": "passed",
                        "pending_marker_present": output_validation.get(
                            "pending_marker_present"
                        ) is True,
                        "issues": [],
                    },
                    "retrieval": retrieval_report,
                    "source_integrity": source_validation,
                    "evidence_ledger_status": "not_performed",
                    "semantic_support": "requires_human_review",
                    "human_review_required": True,
                    "warnings": [
                        {
                            "warning_code": "TASK_S02_EVIDENCE_LEDGER_NOT_PERFORMED",
                            "message": (
                                "轻量版仅检查报告结构和本次授权检索记录；"
                                "未执行逐条 Claim–Evidence 审计，内容需人工复核。"
                            ),
                            "disposition": "manual_review_required",
                        }
                    ],
                }
            previous_output = engine.state.steps[-1].output
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
                ) or (
                    run.source_template_id == "s02_project_planning"
                    and (
                        run.source_template_version in {"4.0.0", "4.1.0"}
                        or _is_s02_lightweight_run(run)
                    )
                ):
                    # Do not reshape verified content with a second model call.
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
                        "项目策划初稿 · 尚未完成正式核验"
                        if _is_s02_lightweight_run(run)
                        else (
                            "DEMO/SYNTHETIC · 演示草稿 · 未完成完整证据核验"
                            if delivery_status == "draft_unverified"
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
                run = await self._store.update_run(
                    run.user_id,
                    run.id,
                    **update_fields,
                )
                await self._emit(
                    run,
                    "artifact.created",
                    node_id=artifact_node.id,
                    payload=artifact.model_dump(mode="json"),
                )
            if self._is_cancel_requested(run_id):
                return
            run = await self._store.update_run(
                run.user_id,
                run.id,
                status=RunStatus.SUCCEEDED,
                final_output=previous_output,
                final_summary=_compact_text(previous_output),
                finished_at=_utc_now(),
            )
            await self._mark_task_run_status(run)
            await self._emit(
                run,
                "run.succeeded",
                payload={"summary": _compact_text(previous_output)},
            )
        except asyncio.CancelledError:
            # ``cancel_run`` persists the terminal status; a shutdown does not
            # need to publish a user-facing event.
            return
        except Exception as error:  # pylint: disable=broad-except
            message = str(error) or "Task node execution failed."
            if self._is_cancel_requested(run_id):
                return
            if engine is not None:
                running = next(
                    (
                        step
                        for step in engine.state.steps
                        if step.phase is SOPPhase.RUNNING
                    ),
                    None,
                )
                if running is not None:
                    running.phase = SOPPhase.FAILED
                    running.metadata["error"] = message
                    await self._checkpoint_sop(run, engine)
            current = await self._find_run(run_id)
            if current is None or current.status in _TERMINAL_RUN_STATUSES:
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
                current = await self._store.update_node_run(
                    current.user_id,
                    current.id,
                    failed_node.node_id,
                    **node_updates,
                )
                await self._emit(
                    current,
                    "step.failed",
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
            current = await self._store.update_run(
                current.user_id,
                current.id,
                status=RunStatus.FAILED,
                error=message,
                error_detail=error_detail,
                finished_at=_utc_now(),
            )
            await self._mark_task_run_status(current)
            await self._emit(
                current,
                "run.failed",
                payload={
                    "message": message,
                    "error_detail": (
                        error_detail.model_dump(mode="json")
                        if error_detail is not None
                        else None
                    ),
                },
            )

    @asynccontextmanager
    async def _run_guard(self, run_id: str) -> AsyncIterator[None]:
        """Serialize input admission and execution, including across workers."""
        async with self._run_locks.setdefault(run_id, asyncio.Lock()):
            if self._message_bus is None:
                yield
            else:
                async with self._message_bus.acquire_lock(
                    f"lxscope:task:run:{run_id}:lock",
                    ttl_secs=3600,
                ):
                    yield

    def _schedule_run(
        self,
        run: TaskRunRecord,
        knowledge_gateway: TaskKnowledgeGateway | None,
    ) -> None:
        self._run_users[run.id] = run.user_id
        execution = asyncio.create_task(
            self._execute_run(run.id, knowledge_gateway)
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
            state = SOPRunState.model_validate(run.sop_state)
            index = next(
                index
                for index, node in enumerate(run.nodes)
                if node.id == request.node_id
            )
            step = TaskStepState.model_validate(
                state.steps[index].model_dump()
            )
            step.answers.append(reply)
            step.input_request = None
            state.steps[index] = step
            run = await self._store.update_run(
                user_id,
                run_id,
                status=RunStatus.QUEUED,
                pending_input=None,
                sop_state=state.model_dump(mode="json"),
            )
            await self._mark_task_run_status(run)
            await self._emit(
                run,
                "input.answered",
                node_id=request.node_id,
                payload={"request_id": request.id, "answers": reply.answers},
            )
            self._schedule_run(run, knowledge_gateway)
            return run

    @staticmethod
    def _duplicate_reply(
        run: TaskRunRecord, reply: TaskInputReply
    ) -> TaskRunRecord:
        """A retried HTTP answer must not schedule a second execution."""
        for step in (run.sop_state or {}).get("steps", []):
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
                and node.metadata.get("output_contract_validation", {}).get(
                    "status"
                )
                == "passed"
                and node.result is not None
            ):
                item["structured_result"] = node.result
            history.append(item)
        return history

    def _build_sop_engine(
        self, run: TaskRunRecord, gateway: TaskKnowledgeGateway | None
    ) -> SOPEngine:
        async def execute(
            node: TaskNode, state: TaskStepState
        ) -> StepExecutionResult:
            retry_policy = state.metadata.get(
                "claim_evidence_retry_policy", {}
            )
            retry_inventory = (
                retry_policy.get("inventory")
                if isinstance(retry_policy, dict)
                and retry_policy.get("retryable") is True
                else None
            )
            protocol_repair = (
                retry_inventory.get("protocol_repair")
                if isinstance(retry_inventory, dict)
                else None
            )
            targeted_content_repair = (
                retry_inventory.get("s02_targeted_content_repair")
                if isinstance(retry_inventory, dict)
                else None
            )
            verification_feedback = (
                state.verifications[-1].message
                if state.verifications
                else ""
            )
            if (
                isinstance(protocol_repair, dict)
                and isinstance(protocol_repair.get("body"), str)
            ):
                if "locked_citations" in protocol_repair:
                    repair_scope = (
                        "只补齐 Claim/Occurrence 与待核实元数据；已有的完整"
                        " task-citations JSON 区块由服务端锁定并按协议放到末尾，"
                        "不得重写或生成新的 citations。不得重写、删减或增补正文，"
                        "不得推断或伪造 Claim 类型、待核实事项、来源或原文摘录。"
                    )
                else:
                    repair_scope = (
                        "只补齐一个合法的 task-claim-evidence JSON 区块及必要的"
                        " task-citations 区块；不得重写、删减或增补正文，不得推断或"
                        "伪造 Claim 类型、待核实事项、来源或原文摘录。"
                    )
                verification_feedback += (
                    "\n\nTask 仅允许进行一次协议包络修复。必须原样保留下方报告正文，"
                    + repair_scope
                    + "生成结果仍会经过本次 TaskRun 的完整来源、权限、哈希、引用和 "
                    "Claim 校验。\n原正文 SHA-256："
                    + str(protocol_repair.get("body_sha256", ""))
                    + "\n<task-preserved-body>\n"
                    + protocol_repair["body"]
                    + "\n</task-preserved-body>"
                )
            if (
                isinstance(targeted_content_repair, dict)
                and isinstance(
                    targeted_content_repair.get("protocol_repair"), dict
                )
            ):
                locked = targeted_content_repair["protocol_repair"]
                verification_feedback += (
                    "\n\nTask 仅允许按反馈修订一个被点名的 S02 v2 内容冲突。"
                    "以下是原始报告正文；除目标 occurrence 行外必须逐行保留。"
                    "正文 SHA-256："
                    + str(locked.get("body_sha256", ""))
                    + "\n目标冲突："
                    + json.dumps(
                        targeted_content_repair.get("conflict", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n原正文如下：\n<task-preserved-body>\n"
                    + str(locked.get("body", ""))
                    + "\n</task-preserved-body>"
                )
            current = await self._store.get_run(run.user_id, run.id)
            if current is None or current.status is RunStatus.CANCELED:
                raise asyncio.CancelledError
            previous_record = (
                current.node_runs[node.order - 1]
                if node.order > 0
                else None
            )
            previous_output = (
                run.input if previous_record is None else previous_record.output
            )
            previous_result = None
            if previous_record is not None:
                previous_definition = run.nodes[node.order - 1]
                previous_contract = (
                    previous_definition.effective_output_contract
                )
                previous_validation = previous_record.metadata.get(
                    "output_contract_validation", {}
                )
                if (
                    previous_record.status is NodeRunStatus.SUCCEEDED
                    and previous_contract is not None
                    and previous_contract.downstream_binding
                    == "validated_result"
                    and previous_validation.get("status") == "passed"
                ):
                    previous_result = previous_record.result
            answers = [
                TaskInputReply.model_validate(answer)
                for step in (current.sop_state or {}).get("steps", [])
                for answer in step.get("answers", [])
            ]
            context = ExecutionContext(
                **self._step_context(run, node).model_dump(),
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                task_revision=run.task_revision,
                history=self._execution_history(current),
                previous_result=previous_result,
                initial_input=run.input,
                user_answers=answers,
                clarification_state=state.metadata.get("clarification", {}),
                clarification_rounds=len(state.answers),
                verification_feedback=verification_feedback,
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
                        current = await self._store.update_run(
                            run.user_id,
                            run.id,
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
                    input_values=_task_run_input_values(current),
                    authorization_subject=run.user_id,
                )
            if (
                isinstance(node.config, ToolStepConfig)
                and is_scenario_native_capability(node.capability_key)
                and not is_knowledge_native_capability(node.capability_key)
            ):
                prior_results: dict[str, Any] = {}
                for prior_node, node_run in zip(
                    run.nodes[:node.order],
                    current.node_runs[:node.order],
                ):
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
                            current = await self._store.update_run(
                                run.user_id,
                                run.id,
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
                    result = _retrieval_result_for_run(
                        run,
                        current,
                        node.order,
                    )
                    if result is None:
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
                                "passed": False,
                                "issues": [detail],
                                "verified_citations": [],
                            },
                        )
                    verification = verify_task_citations(
                        previous_output,
                        result["evidence"],
                        authorization_subject=run.user_id,
                    )
                    text = json.dumps(
                        verification,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    return StepExecutionResult(
                        text=text,
                        result=verification,
                        metadata={"citation_verification": verification},
                        status=(
                            "completed"
                            if verification["passed"]
                            else "failed"
                        ),
                        reason=(
                            None
                            if verification["passed"]
                            else "引用核验失败：" + "；".join(verification["issues"])
                        ),
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
            if (
                isinstance(protocol_repair, dict)
                and isinstance(protocol_repair.get("body"), str)
                and isinstance(execution_node.config, AgentStepConfig)
            ):
                repair_prompt = (
                    execution_node.config.prompt.rstrip()
                    + "\n\n"
                    + (
                        TASK_CLAIM_EVIDENCE_REPAIR_LOCKED_CITATIONS_PROMPT
                        if "locked_citations" in protocol_repair
                        else TASK_CLAIM_EVIDENCE_REPAIR_PROMPT
                    )
                )
                repair_config = execution_node.config.model_copy(
                    update={"prompt": repair_prompt}
                )
                execution_node = execution_node.model_copy(
                    update={"prompt": repair_prompt, "config": repair_config}
                )
            if (
                isinstance(targeted_content_repair, dict)
                and isinstance(execution_node.config, AgentStepConfig)
            ):
                repair_prompt = (
                    execution_node.config.prompt.rstrip()
                    + "\n\n"
                    + _S02_TARGETED_CONTENT_REPAIR_PROMPT
                )
                repair_config = execution_node.config.model_copy(
                    update={"prompt": repair_prompt}
                )
                execution_node = execution_node.model_copy(
                    update={"prompt": repair_prompt, "config": repair_config}
                )
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
                    _retrieval_result_for_run(run, current, node.order)
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
                validation = validate_s02_lightweight_markdown(
                    annotated_output
                )
                result_metadata = dict(result.metadata or {})
                result_metadata["s02_lightweight_output_validation"] = {
                    **validation,
                    "status": "passed" if validation["passed"] else "failed",
                }
                result_metadata["s02_lightweight_source_validation"] = (
                    source_validation
                )
                result_metadata["s02_lightweight_retrieval"] = retrieval_report
                result_metadata["s02_lightweight_delivery"] = {
                    "passed": validation["passed"],
                    "delivery_profile": "s02_lightweight_draft",
                    "status": (
                        "draft_unverified"
                        if validation["passed"]
                        else "failed"
                    ),
                    "evidence_ledger_status": "not_performed",
                    "semantic_support": "requires_human_review",
                    "human_review_required": True,
                    "retrieval_status": retrieval_report["status"],
                }
                if not validation["passed"]:
                    detail = "S02 v5 轻量草稿结构校验失败：" + "；".join(
                        str(item) for item in validation.get("issues", [])
                    )
                    result_metadata["s02_lightweight_delivery"]["error"] = (
                        detail
                    )
                    return StepExecutionResult(
                        text=annotated_output,
                        result=result.result,
                        metadata=result_metadata,
                        status="failed",
                        reason=detail,
                    )
                result = StepExecutionResult(
                    text=annotated_output,
                    result=result.result,
                    metadata=result_metadata,
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            is_s02_v3_final_agent = bool(
                result.status == "completed"
                and node.type is StepType.AGENT
                and run.source_template_id == "s02_project_planning"
                and run.source_template_version
                in S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS
                and run.nodes
                and node.id == run.nodes[-1].id
            )
            if is_s02_v3_final_agent:
                output_contract = node.effective_output_contract
                if (
                    output_contract is None
                    or output_contract.validation_mode != "json_schema"
                    or output_contract.json_schema is None
                ):
                    detail = "S02 v3 最终节点缺少 JSON Schema 输出契约。"
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            "s02_v3_structured_output": {
                                "passed": False,
                                "error_code": "TASK_S02_V3_CONTRACT_MISSING",
                                "error": detail,
                            },
                        },
                        status="failed",
                        reason=detail,
                    )
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is None:
                    detail = "S02 v3 最终输出缺少本次运行的授权 Evidence 快照。"
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            "s02_v3_structured_output": {
                                "passed": False,
                                "error_code": "TASK_S02_V3_EVIDENCE_MISSING",
                                "error": detail,
                            },
                        },
                        status="failed",
                        reason=detail,
                    )
                template_version = run.source_template_version or "3.0.0"
                if template_version == "4.0.0":
                    assembled = assemble_s02_v4_output(
                        result.text,
                        output_contract.json_schema,
                        evidence_result["evidence"],
                        authorization_subject=run.user_id,
                        delivery_mode=run.delivery_mode,
                    )
                elif template_version == "4.1.0":
                    assembled = assemble_s02_v4_1_output(
                        result.text,
                        output_contract.json_schema,
                        evidence_result["evidence"],
                        authorization_subject=run.user_id,
                        delivery_mode=run.delivery_mode,
                    )
                else:
                    assembled = assemble_s02_v3_output(
                        result.text,
                        output_contract.json_schema,
                        evidence_result["evidence"],
                        authorization_subject=run.user_id,
                        template_version=template_version,
                    )
                structured_metadata_key = (
                    "s02_v4_delivery"
                    if template_version in {"4.0.0", "4.1.0"}
                    else "s02_v3_structured_output"
                )
                if not assembled.get("passed"):
                    detail = str(
                        assembled.get("error")
                        or "S02 v3 结构化结果或来源候选校验失败。"
                    )
                    diagnostics = assembled.get("diagnostics")
                    if isinstance(diagnostics, list) and diagnostics:
                        visible_details = [
                            item.get("message")
                            for item in diagnostics[:3]
                            if isinstance(item, dict)
                            and isinstance(item.get("message"), str)
                        ]
                        if visible_details:
                            detail += " 诊断：" + "；".join(visible_details)
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            structured_metadata_key: {
                                key: value
                                for key, value in assembled.items()
                                if key not in {"document", "business_result"}
                            },
                        },
                        status="failed",
                        reason=detail,
                    )
                result_metadata = dict(result.metadata or {})
                result_metadata[structured_metadata_key] = {
                    "passed": True,
                    "status": (
                        assembled.get("delivery_report", {}).get("status")
                        if template_version in {"4.0.0", "4.1.0"}
                        else "assembled"
                    ),
                    "mode": (
                        run.delivery_mode
                        if template_version in {"4.0.0", "4.1.0"}
                        else None
                    ),
                    "claim_count": assembled["claim_count"],
                    "occurrence_count": assembled["occurrence_count"],
                    "binding_count": assembled["binding_count"],
                    "unverified_count": assembled["unverified_count"],
                    "citation_count": assembled["citation_count"],
                    "semantic_support": "requires_human_review",
                    "raw_model_output": assembled["raw_model_output"],
                    "normalization_changes": assembled[
                        "normalization_changes"
                    ],
                    **(
                        {"delivery_report": assembled["delivery_report"]}
                        if template_version in {"4.0.0", "4.1.0"}
                        else {}
                    ),
                }
                if template_version in {"4.0.0", "4.1.0"}:
                    result_metadata["output_contract_validation"] = {
                        "contract_id": output_contract.contract_id,
                        "contract_version": output_contract.contract_version,
                        "status": "passed",
                        "target": (
                            "s02_v4_1_raw_agent_json"
                            if template_version == "4.1.0"
                            else "s02_v4_raw_agent_json"
                        ),
                        "issues": [],
                    }
                result = StepExecutionResult(
                    text=assembled["document"],
                    result=assembled["business_result"],
                    metadata=result_metadata,
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            if (
                isinstance(protocol_repair, dict)
                and isinstance(protocol_repair.get("body"), str)
            ):
                materialization_diagnostics: list[dict[str, Any]] = []
                repaired_text, repair_actions = (
                    materialize_task_claim_evidence_protocol_repair(
                        protocol_repair["body"],
                        result.text,
                        protocol_repair,
                    )
                )
                if not repair_actions:
                    materialization_diagnostics = (
                        diagnose_task_claim_evidence_protocol_materialization(
                            protocol_repair["body"],
                            result.text,
                            protocol_repair,
                        )
                    )
                    repaired_text, repair_actions = (
                        wrap_claim_evidence_repair_payload_for_validation(
                            protocol_repair["body"],
                            result.text,
                            protocol_repair,
                        )
                    )
                result = StepExecutionResult(
                    text=repaired_text,
                    result=result.result,
                    metadata={
                        **(result.metadata or {}),
                        "task_protocol_repair": {
                            "status": (
                                "materialized"
                                if "materialized_claim_evidence_from_compact_repair"
                                in repair_actions
                                else (
                                    "wrapped_for_validation"
                                    if (
                                        "wrapped_unmaterialized_payload_for_full_validation"
                                        in repair_actions
                                    )
                                    else "refused"
                                )
                            ),
                            "actions": repair_actions,
                            "materialization_passed": (
                                "materialized_claim_evidence_from_compact_repair"
                                in repair_actions
                            ),
                            "materialization_diagnostics": (
                                materialization_diagnostics
                            ),
                            "verification_required": True,
                        },
                    },
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            normalized_text, normalization_actions = (
                normalize_task_protocol_format(result.text)
            )
            if normalization_actions:
                result = StepExecutionResult(
                    text=normalized_text,
                    result=result.result,
                    metadata={
                        **(result.metadata or {}),
                        "task_protocol_normalization": {
                            "status": "applied",
                            "actions": normalization_actions,
                        },
                    },
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            if isinstance(targeted_content_repair, dict):
                repaired_text, repair_check = (
                    materialize_locked_claim_evidence_content_repair(
                        result.text, targeted_content_repair
                    )
                )
                result = StepExecutionResult(
                    text=(
                        repaired_text
                        if repair_check.get("passed")
                        else result.text
                    ),
                    result=result.result,
                    metadata={
                        **(result.metadata or {}),
                        "task_s02_targeted_content_repair": repair_check,
                    },
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            result_metadata = dict(result.metadata or {})
            # This key is server-owned. Never accept an executor/model-supplied
            # retry inventory as the authority for what must be preserved.
            result_metadata.pop("_claim_evidence_retry_inventory", None)
            result_metadata.pop("claim_evidence_retry_policy", None)
            result_metadata.pop("task_retryable", None)
            if retry_inventory is not None:
                result_metadata["_claim_evidence_retry_inventory"] = (
                    retry_inventory
                )
            result = StepExecutionResult(
                text=result.text,
                result=result.result,
                metadata=result_metadata,
                status=result.status,
                questions=result.questions,
                reason=result.reason,
            )
            output_contract = node.effective_output_contract
            has_claim_evidence_contract = bool(
                output_contract is not None
                and output_contract.validation_mode == "claim_evidence"
            )
            is_final_agent = bool(
                node.type is StepType.AGENT
                and run.nodes
                and node.id == run.nodes[-1].id
            )
            needs_claim_evidence_protocol = has_claim_evidence_contract or (
                is_final_agent
                and (
                    (
                        run.source_template_id == "s02_project_planning"
                        and not _is_s02_lightweight_run(run)
                    )
                    or (
                        run.source_template_id == "s07_report_generation"
                        and run.source_template_version == "3.0.0"
                    )
                )
            )
            if (
                result.status == "completed"
                and node.type is StepType.AGENT
                and needs_claim_evidence_protocol
            ):
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is not None:
                    finalized_text, materialization_actions = (
                        materialize_task_citations_from_bindings(
                            result.text,
                            evidence_result["evidence"],
                            authorization_subject=run.user_id,
                            policy=get_scenario_evidence_policy(
                                run.source_template_id,
                                run.source_template_version,
                            ),
                        )
                    )
                    if materialization_actions:
                        result_metadata = dict(result.metadata or {})
                        result_metadata["task_protocol_materialization"] = {
                            "status": "candidate_appended",
                            "actions": materialization_actions,
                            "verification_required": True,
                        }
                        result = StepExecutionResult(
                            text=finalized_text,
                            result=result.result,
                            metadata=result_metadata,
                            status=result.status,
                            questions=result.questions,
                            reason=result.reason,
                        )
            if (
                run.source_template_id == "s02_project_planning"
                and not _is_s02_lightweight_run(run)
                and run.nodes
                and node.id == run.nodes[-1].id
            ):
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is None:
                    detail = "最终策划输出缺少本次运行保存的授权来源证据。"
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            "evidence_ledger": {
                                "passed": False,
                                "error_code": "TASK_EVIDENCE_LEDGER_FAILED",
                                "error": detail,
                                "issues": [detail],
                                "ledger": [],
                            },
                        },
                        status="failed",
                        reason=detail,
                    )
                ledger_result = materialize_s02_project_planning_ledger(
                    result.text,
                    evidence_result["evidence"],
                    authorization_subject=run.user_id,
                    template_version=(
                        run.source_template_version or "1.0.0"
                    ),
                )
                if not ledger_result["passed"]:
                    diagnostic_counts = {
                        key: len(value)
                        for key, value in ledger_result.get(
                            "diagnostics", {}
                        ).items()
                        if value
                    }
                    diagnostic_labels = {
                        "structure": "结构异常",
                        "source": "来源异常",
                        "citation": "引用异常",
                        "unverified": "待核实关联异常",
                    }
                    summary = (
                        "、".join(
                            f"{diagnostic_labels.get(key, key)}{count}项"
                            for key, count in diagnostic_counts.items()
                        )
                        or f"{len(ledger_result['issues'])}项"
                    )
                    repair_metadata = (result.metadata or {}).get(
                        "task_protocol_repair"
                    )
                    materialization_diagnostics = (
                        repair_metadata.get("materialization_diagnostics", [])
                        if isinstance(repair_metadata, dict)
                        and repair_metadata.get("materialization_passed")
                        is False
                        else []
                    )
                    if materialization_diagnostics:
                        detail = (
                            "Claim–Evidence 协议修复无法安全生成完整结构；"
                            "本次最终来源身份、权限、哈希和引用核验尚未开始："
                            + "；".join(
                                str(item.get("message"))
                                for item in materialization_diagnostics
                                if isinstance(item, dict)
                                and item.get("message")
                            )
                            + "。完整结构诊断见节点 task_protocol_repair 元数据。"
                        )
                    else:
                        detail = (
                            "最终来源证据台账校验失败："
                            + summary
                            + "；完整诊断见节点 evidence_ledger 元数据。"
                        )
                    evidence_ledger_metadata = {
                        "passed": False,
                        "error_code": "TASK_EVIDENCE_LEDGER_FAILED",
                        "error": detail,
                        "issues": (
                            [detail]
                            if materialization_diagnostics
                            else ledger_result["issues"]
                        ),
                        "diagnostics": ledger_result.get(
                            "diagnostics", {}
                        ),
                        "all_diagnostics": ledger_result.get(
                            "all_diagnostics", []
                        ),
                        "ledger": ledger_result["ledger"],
                        "verification_scope": ledger_result.get(
                            "verification_scope",
                            {"protocol_parse": "not_evaluated"},
                        ),
                    }
                    if materialization_diagnostics:
                        evidence_ledger_metadata[
                            "protocol_materialization_diagnostics"
                        ] = materialization_diagnostics
                    return StepExecutionResult(
                        text=result.text,
                        result=result.result,
                        metadata={
                            **(result.metadata or {}),
                            "evidence_ledger": evidence_ledger_metadata,
                        },
                        status=result.status,
                        questions=result.questions,
                        reason=result.reason,
                    )
                return StepExecutionResult(
                    text=ledger_result["document"],
                    result=result.result,
                    metadata={
                        **(result.metadata or {}),
                        "evidence_ledger": {
                            "passed": True,
                            "issues": [],
                            "diagnostics": ledger_result.get(
                                "diagnostics", {}
                            ),
                            "all_diagnostics": ledger_result.get(
                                "all_diagnostics", []
                            ),
                            "ledger": ledger_result["ledger"],
                            "verification_scope": ledger_result[
                                "verification_scope"
                            ],
                        },
                    },
                    status=result.status,
                    questions=result.questions,
                    reason=result.reason,
                )
            return result

        async def verify(
            node: TaskNode, output: str, state: TaskStepState
        ) -> StepVerdict:
            if _is_s02_lightweight_final_node(run, node):
                validation = state.metadata.get(
                    "s02_lightweight_output_validation"
                )
                retrieval = state.metadata.get("s02_lightweight_retrieval")
                if (
                    not isinstance(validation, dict)
                    or validation.get("passed") is not True
                    or not isinstance(retrieval, dict)
                ):
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message=(
                            "S02 v5 轻量草稿未通过服务端结构检查，"
                            "或缺少本次授权检索状态；未生成 DOCX。"
                        ),
                    )
                return StepVerdict(
                    passed=True,
                    retryable=False,
                    message=(
                        "八章 Markdown 草稿和本次授权检索记录已检查；"
                        "逐条 Claim–Evidence 与语义支持未执行，需人工复核。"
                    ),
                )
            previous_inventory = state.metadata.get(
                "_claim_evidence_retry_inventory"
            )
            targeted_content_repair = (
                previous_inventory.get("s02_targeted_content_repair")
                if isinstance(previous_inventory, dict)
                else None
            )
            protocol_repair = (
                previous_inventory.get("protocol_repair")
                if isinstance(previous_inventory, dict)
                else None
            )
            previous_protocol_repair = protocol_repair
            targeted_content_repair_attempted = False
            if isinstance(targeted_content_repair, dict):
                execution_check = state.metadata.get(
                    "task_s02_targeted_content_repair", {}
                )
                # The S02 ledger materializer replaces the internal Claim
                # protocol with its user-facing ledger. Check the locked
                # protocol snapshot captured before that transformation; the
                # materializer independently rechecks citations in its final
                # document.
                scope_check = (
                    execution_check.get("protocol_lock_check")
                    if isinstance(execution_check, dict)
                    else None
                )
                if (
                    not isinstance(execution_check, dict)
                    or execution_check.get("passed") is not True
                    or not isinstance(scope_check, dict)
                    or scope_check.get("passed") is not True
                ):
                    reason = (
                        execution_check.get("reason")
                        if isinstance(execution_check, dict)
                        and execution_check.get("passed") is not True
                        else (
                            scope_check.get("reason")
                            if isinstance(scope_check, dict)
                            else "修订响应缺少服务端协议锁定校验"
                        )
                    )
                    message = (
                        "S02 局部内容修订已拒绝："
                        + str(reason or "修订范围或引用锁定校验失败")
                        + "。Task 保留失败状态，不会继续重试。"
                    )
                    state.metadata["task_s02_targeted_content_repair_check"] = {
                        "passed": False,
                        "reason": reason,
                        "scope": scope_check,
                    }
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": False,
                        "inventory": previous_inventory,
                    }
                    state.metadata["evidence_ledger"] = {
                        "passed": False,
                        "error_code": "TASK_EVIDENCE_LEDGER_FAILED",
                        "error": message,
                        "issues": [message],
                        "diagnostics": {
                            "structure": [
                                {
                                    "error_code": "targeted_content_repair_refused",
                                    "claim_id": targeted_content_repair.get(
                                        "conflict", {}
                                    ).get("claim_id"),
                                    "unverified_id": None,
                                    "location": targeted_content_repair.get(
                                        "conflict", {}
                                    ).get("occurrence_id"),
                                    "error_type": "structure",
                                    "evidence_ids": [],
                                    "message": message,
                                }
                            ],
                            "source": [],
                            "citation": [],
                            "unverified": [],
                        },
                    }
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message=message,
                    )
                targeted_content_repair_attempted = True
                state.metadata["task_s02_targeted_content_repair_check"] = {
                    **execution_check.get("scope", {}),
                    "passed": True,
                    "protocol_lock_check": scope_check,
                }
                previous_inventory = None
            elif isinstance(protocol_repair, dict):
                if not claim_evidence_protocol_repair_body_matches(
                    output, protocol_repair
                ):
                    repair_result = state.metadata.get(
                        "task_protocol_repair"
                    )
                    if (
                        isinstance(repair_result, dict)
                        and repair_result.get("status") == "refused"
                    ):
                        message = (
                            "协议修复已拒绝：修复响应无法在不改变正文的条件下"
                            "安全封装。Task 保留原结果并停止重试。"
                        )
                    else:
                        message = (
                            "协议修复已拒绝：最终报告正文与前一轮失败输出不一致。"
                            "Task 保留原结果并停止重试。"
                        )
                    state.metadata["claim_evidence_protocol_repair_check"] = {
                        "passed": False,
                        "reason": message,
                    }
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": False,
                        "inventory": previous_inventory,
                    }
                    state.metadata["evidence_ledger"] = {
                        "passed": False,
                        "error_code": "TASK_EVIDENCE_LEDGER_FAILED",
                        "error": message,
                        "issues": [message],
                        "diagnostics": {
                            "structure": [
                                {
                                    "error_code": "protocol_repair_body_changed",
                                    "claim_id": None,
                                    "unverified_id": None,
                                    "location": None,
                                    "error_type": "structure",
                                    "evidence_ids": [],
                                    "message": message,
                                }
                            ],
                            "source": [],
                            "citation": [],
                            "unverified": [],
                        },
                    }
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message=message,
                    )
                state.metadata["claim_evidence_protocol_repair_check"] = {
                    "passed": True,
                    "body_sha256": protocol_repair.get("body_sha256"),
                }
                # The original malformed envelope has no trustworthy Claim
                # inventory. Exact body preservation plus the normal strict
                # validators protect this bounded repair attempt.
                previous_inventory = None
            elif previous_inventory is not None:
                current_inventory = extract_claim_evidence_inventory(output)
                inventory_check = compare_claim_evidence_inventories(
                    previous_inventory, current_inventory
                )
                state.metadata["claim_evidence_retry_inventory_check"] = (
                    inventory_check
                )
                if not inventory_check["passed"]:
                    message = (
                        "受限结构修复已拒绝："
                        + str(inventory_check["reason"])
                        + " 清单差异："
                        + json.dumps(
                            inventory_check,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "。Task 保留原失败结果并停止重试。"
                    )
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": False,
                        "inventory": previous_inventory,
                    }
                    state.metadata["evidence_ledger"] = {
                        "passed": False,
                        "error_code": "TASK_EVIDENCE_LEDGER_FAILED",
                        "error": message,
                        "issues": [message],
                        "diagnostics": {
                            "structure": [
                                {
                                    "error_code": "retry_inventory_lost",
                                    "claim_id": None,
                                    "unverified_id": None,
                                    "location": None,
                                    "error_type": "structure",
                                    "evidence_ids": [],
                                    "message": message,
                                }
                            ],
                            "source": [],
                            "citation": [],
                            "unverified": [],
                        },
                    }
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message=message,
                    )
            if (
                run.source_template_id == "s02_project_planning"
                and run.nodes
                and node.id == run.nodes[-1].id
            ):
                ledger_status = state.metadata.get("evidence_ledger")
                if (
                    isinstance(ledger_status, dict)
                    and ledger_status.get("passed") is False
                ):
                    if (
                        run.source_template_version
                        in S02_STRUCTURED_OUTPUT_TEMPLATE_VERSIONS
                    ):
                        state.metadata["claim_evidence_retry_policy"] = {
                            "retryable": False,
                            "inventory": None,
                        }
                        return StepVerdict(
                            passed=False,
                            retryable=False,
                            message=(
                                "S02 v3 最终来源证据台账校验失败；Task 已由服务端"
                                "确定性组装结构化输出，不会自动要求模型重写协议。"
                            ),
                        )
                    current_inventory = extract_claim_evidence_inventory(
                        output
                    )
                    diagnostics = ledger_status.get("diagnostics")
                    output_protocol_repair = (
                        extract_claim_evidence_protocol_repair_inventory(
                            output
                        )
                    )
                    protocol_retryable = bool(
                        output_protocol_repair is not None
                        and claim_evidence_protocol_envelope_failure_is_retryable(
                            diagnostics
                        )
                    )
                    retryable = (
                        False
                        if targeted_content_repair_attempted
                        else (
                            protocol_retryable
                            or (
                                claim_evidence_failure_is_retryable(diagnostics)
                                and current_inventory is not None
                            )
                        )
                    )
                    retry_inventory = current_inventory
                    if protocol_retryable:
                        retry_inventory = {
                            "protocol_repair": output_protocol_repair
                        }
                    targeted_conflict = None
                    if (
                        run.source_template_version == "2.0.0"
                        and isinstance(previous_protocol_repair, dict)
                        and "locked_citations" in previous_protocol_repair
                        and current_inventory is None
                        and not protocol_retryable
                        and isinstance(diagnostics, dict)
                    ):
                        diagnostic_codes = [
                            str(item.get("error_code"))
                            for category in (
                                "structure",
                                "source",
                                "citation",
                                "unverified",
                            )
                            for item in diagnostics.get(category, [])
                            if isinstance(item, dict)
                        ]
                        if diagnostic_codes == ["invalid_top_level_fields"]:
                            targeted_conflict = (
                                extract_locked_claim_evidence_content_conflicts(
                                    output, previous_protocol_repair
                                )
                            )
                    if targeted_conflict is not None:
                        retryable = True
                        retry_inventory = {
                            "s02_targeted_content_repair": targeted_conflict
                        }
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": retryable,
                        "inventory": retry_inventory,
                    }
                    retry_instruction = ""
                    if protocol_retryable:
                        retry_instruction = (
                            " 原正文将通过服务端指纹锁定；只允许修复协议区块，"
                            "修复后仍须通过全部来源和引用校验。"
                        )
                    elif targeted_content_repair_attempted:
                        retry_instruction = (
                            " 局部内容修订后完整校验仍未通过；本步骤只提供一次局部修订，"
                            "当前结果停止重试并保持失败。"
                        )
                    elif targeted_conflict is not None:
                        conflict = targeted_conflict["conflict"]
                        retry_instruction = (
                            " 检测到协议修复结果仅缺少服务端生成的 bindings，"
                            "并发现 occurrence "
                            + str(conflict["claim_id"])
                            + ":"
                            + str(conflict["occurrence_id"])
                            + " 被声明为 unverified 却带有正文引用 "
                            + ", ".join(conflict["citation_ids"])
                            + "。仅可将该 occurrence 拆成保留原引用的来源支持陈述，"
                            "以及相邻、无引用且沿用原待核实登记的独立陈述；"
                            "其他正文、Claim、待核实登记和引用必须保持不变。"
                        )
                    elif retryable:
                        retry_instruction = (
                            " 保留全部既有 claim_id（"
                            + ", ".join(current_inventory["claim_ids"])
                            + "）及 unverified_id（"
                            + ", ".join(
                                current_inventory["unverified_ids"]
                            )
                            + "）；只能修复定位、occurrence 和关联结构，"
                            "不得删除或弱化失败声明，不得修改来源 ID 或原文摘录。"
                        )
                    return StepVerdict(
                        passed=False,
                        retryable=retryable,
                        message=str(
                            ledger_status.get("error")
                            or "最终来源证据台账校验失败。"
                        )
                        + retry_instruction,
                    )
                if (
                    isinstance(ledger_status, dict)
                    and ledger_status.get("passed") is True
                ):
                    current_inventory = extract_claim_evidence_inventory(
                        output
                    )
                    if current_inventory is not None:
                        state.metadata["claim_evidence_retry_policy"] = {
                            "retryable": True,
                            "inventory": current_inventory,
                        }

            if (
                isinstance(node.config, ToolStepConfig)
                and node.capability_key in {
                    "document.citation_verify",
                    "report.citation_check",
                }
            ):
                verification = state.result
                if not isinstance(verification, dict):
                    return StepVerdict(
                        passed=False,
                        message="引用核验节点没有结构化校验结果。",
                    )
                return StepVerdict(
                    passed=verification.get("passed") is True,
                    message=(
                        "引用编号、授权来源身份和逐字摘录已通过确定性核验；"
                        "语义支持尚未自动判定。"
                        if verification.get("passed") is True
                        else "引用核验失败："
                        + "；".join(
                            str(item)
                            for item in verification.get("issues", [])
                        )
                    ),
                )

            if (
                isinstance(node.config, ToolStepConfig)
                and node.capability_key != "planning.template_retrieve"
                and (
                    is_scenario_native_capability(node.capability_key)
                    or node.capability_key in {
                        "project.similar_search",
                        "project.rule_check",
                        "report.rag_retrieve",
                    }
                )
            ):
                if not isinstance(state.result, dict):
                    return StepVerdict(
                        passed=False,
                        message="Task 原生能力未保存结构化执行结果。",
                    )
                return StepVerdict(
                    passed=True,
                    message=(
                        "Task 原生能力已完成确定性输入/来源处理；"
                        "业务语义及外部数据真实性仍按节点边界处理。"
                    ),
                )

            if (
                node.type is StepType.AGENT
                and not (
                    node.effective_output_contract is not None
                    and node.effective_output_contract.validation_mode
                    == "citations"
                )
                and any(
                    item.capability_key in {
                        "document.citation_verify",
                        "report.citation_check",
                    }
                    and item.order < node.order
                    for item in run.nodes
                )
            ):
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is None:
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message="最终输出缺少本次运行的授权检索证据，不能生成文件。",
                    )
                verification = verify_task_citations(
                    output,
                    evidence_result["evidence"],
                    authorization_subject=run.user_id,
                )
                state.metadata["final_citation_verification"] = verification
                if not verification["passed"]:
                    scope = verification.get("verification_scope", {})
                    current_inventory = extract_claim_evidence_inventory(
                        output
                    )
                    protocol_repair = (
                        extract_claim_evidence_protocol_repair_inventory(
                            output
                        )
                    )
                    protocol_only_issue = (
                        len(verification.get("issues", [])) == 1
                        and str(verification["issues"][0]).startswith(
                            (
                                "文档必须包含且仅包含一个 task-citations JSON 区块",
                                "task-citations 区块之后不能追加未核验内容。",
                            )
                        )
                    )
                    protocol_retryable = bool(
                        protocol_only_issue and protocol_repair is not None
                    )
                    citation_structure_only = (
                        protocol_retryable
                        or (
                            scope.get("citation_id_set") == "failed"
                            and scope.get("source_identity_and_quote")
                            in {"passed", "not_applicable"}
                        )
                    )
                    retryable = (
                        protocol_retryable
                        or (
                            citation_structure_only
                            and current_inventory is not None
                            and (
                                node.effective_output_contract is not None
                                and node.effective_output_contract.validation_mode
                                == "claim_evidence"
                                or run.source_template_id
                                == "s07_report_generation"
                            )
                        )
                    )
                    if current_inventory is not None:
                        retry_inventory = current_inventory
                    elif protocol_retryable:
                        retry_inventory = {
                            "protocol_repair": protocol_repair
                        }
                    else:
                        retry_inventory = None
                    if retry_inventory is not None:
                        state.metadata["claim_evidence_retry_policy"] = {
                            "retryable": retryable,
                            "inventory": retry_inventory,
                        }
                    return StepVerdict(
                        passed=False,
                        retryable=retryable,
                        message=(
                            "最终输出引用核验失败："
                            + "；".join(verification["issues"])
                        )
                        + (
                            (
                                " 仅进行一次协议包络修复，且必须保留原正文。"
                                if protocol_retryable
                                else " 仅修复正文/引用编号结构并保留所有既有声明、"
                                "occurrence_id 和 unverified_id。"
                            )
                            if retryable
                            else "来源身份、原文摘录或失败类型不满足自动重试条件。"
                        ),
                    )

            output_contract = node.effective_output_contract
            if (
                node.type is StepType.AGENT
                and output_contract is not None
                and output_contract.validation_mode == "citations"
            ):
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is None:
                    verification = {
                        "passed": False,
                        "issues": [
                            "引用输出契约要求本次 TaskRun 的授权证据快照。"
                        ],
                        "verified_citations": [],
                        "verification_scope": {
                            "citation_id_set": "not_applicable",
                            "source_identity_and_quote": "failed",
                            "semantic_support": "not_automatically_evaluated",
                        },
                    }
                else:
                    verification = verify_task_citations(
                        output,
                        evidence_result["evidence"],
                        authorization_subject=run.user_id,
                    )
                state.metadata["citation_output_validation"] = verification
                state.metadata["output_contract_validation"] = {
                    "contract_id": output_contract.contract_id,
                    "contract_version": output_contract.contract_version,
                    "status": (
                        "passed"
                        if verification.get("passed") is True
                        else "failed"
                    ),
                    "target": "authorized_run_citations",
                    "issues": list(verification.get("issues", [])),
                }
                if verification.get("passed") is not True:
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message=(
                            "Task 引用输出契约校验失败："
                            + "；".join(
                                str(item)
                                for item in verification.get("issues", [])
                            )
                        )
                        + " 来源、权限、哈希或逐字原文错误不会自动放宽。",
                    )
                state.metadata["semantic_review_required"] = True

            if (
                node.type is StepType.AGENT
                and run.source_template_id == "s07_report_generation"
                and run.source_template_version == "3.0.0"
                and run.nodes
                and node.id == run.nodes[-1].id
            ):
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is None:
                    return StepVerdict(
                        passed=False,
                        retryable=False,
                        message="报告最终输出缺少本次 TaskRun 的授权来源快照。",
                    )
                policy = get_scenario_evidence_policy(
                    run.source_template_id,
                    run.source_template_version,
                )
                evidence_validation = validate_claim_evidence_document(
                    output,
                    evidence_result["evidence"],
                    authorization_subject=run.user_id,
                    policy=policy,
                )
                state.metadata["claim_evidence_validation"] = evidence_validation
                if not evidence_validation.get("passed"):
                    current_inventory = extract_claim_evidence_inventory(
                        output
                    )
                    protocol_repair = (
                        extract_claim_evidence_protocol_repair_inventory(
                            output
                        )
                    )
                    protocol_retryable = bool(
                        protocol_repair is not None
                        and claim_evidence_protocol_envelope_failure_is_retryable(
                            evidence_validation.get("diagnostics")
                        )
                    )
                    inventory_check = (
                        compare_claim_evidence_inventories(
                            previous_inventory, current_inventory
                        )
                        if previous_inventory is not None
                        else {"passed": True}
                    )
                    retryable = (
                        protocol_retryable
                        or (
                            claim_evidence_failure_is_retryable(
                                evidence_validation.get("diagnostics")
                            )
                            and current_inventory is not None
                        )
                        and inventory_check.get("passed") is True
                    )
                    retry_inventory = current_inventory
                    if protocol_retryable:
                        retry_inventory = {
                            "protocol_repair": protocol_repair
                        }
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": retryable,
                        "inventory": retry_inventory,
                    }
                    materialization_notice = (
                        _task_protocol_materialization_notice(
                            state.metadata
                        )
                    )
                    return StepVerdict(
                        passed=False,
                        retryable=retryable,
                        message=(
                            "报告 Claim–Evidence 确定性校验失败："
                            + "；".join(evidence_validation.get("issues", []))
                        )
                        + (
                            " " + materialization_notice
                            if materialization_notice
                            else ""
                        )
                        + (
                            (
                                " 仅进行一次协议包络修复，且必须保留原正文。"
                                if protocol_retryable
                                else " 保留全部既有 claim_id、occurrence_id 和 "
                                "unverified_id；仅修复已识别的定位/关联结构。"
                            )
                            if retryable
                            else " 来源、声明清单或失败诊断不可自动修复；Task 停止重试。"
                        ),
                    )
                state.metadata["semantic_review_required"] = True
                retry_inventory = extract_claim_evidence_inventory(output)
                if retry_inventory is not None:
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": True,
                        "inventory": retry_inventory,
                    }

            output_contract = node.effective_output_contract
            if (
                output_contract is not None
                and output_contract.validation_mode == "claim_evidence"
            ):
                current = await self._store.get_run(run.user_id, run.id)
                evidence_result = (
                    _retrieval_result_for_run(run, current, node.order)
                    if current is not None
                    else None
                )
                if evidence_result is None:
                    validation_result = {
                        "passed": False,
                        "issues": [
                            "节点输出契约要求本次 TaskRun 的授权证据快照。"
                        ],
                        "all_diagnostics": [],
                    }
                else:
                    validation_result = validate_claim_evidence_document(
                        output,
                        evidence_result["evidence"],
                        authorization_subject=run.user_id,
                        policy=get_scenario_evidence_policy(
                            run.source_template_id,
                            run.source_template_version,
                        ),
                    )
                state.metadata["claim_evidence_validation"] = (
                    validation_result
                )
                state.metadata["output_contract_validation"] = {
                    "contract_id": output_contract.contract_id,
                    "contract_version": output_contract.contract_version,
                    "status": (
                        "passed"
                        if validation_result.get("passed") is True
                        else "failed"
                    ),
                    "target": "claim_evidence_document",
                    "issues": list(validation_result.get("issues", [])),
                }
                if validation_result.get("passed") is not True:
                    current_inventory = extract_claim_evidence_inventory(
                        output
                    )
                    protocol_repair = (
                        extract_claim_evidence_protocol_repair_inventory(
                            output
                        )
                    )
                    protocol_retryable = bool(
                        protocol_repair is not None
                        and claim_evidence_protocol_envelope_failure_is_retryable(
                            validation_result.get("diagnostics")
                        )
                    )
                    inventory_check = (
                        compare_claim_evidence_inventories(
                            previous_inventory, current_inventory
                        )
                        if previous_inventory is not None
                        else {"passed": True}
                    )
                    retryable = (
                        protocol_retryable
                        or (
                            claim_evidence_failure_is_retryable(
                                validation_result.get("diagnostics")
                            )
                            and current_inventory is not None
                        )
                        and inventory_check.get("passed") is True
                    )
                    retry_inventory = current_inventory
                    if protocol_retryable:
                        retry_inventory = {
                            "protocol_repair": protocol_repair
                        }
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": retryable,
                        "inventory": retry_inventory,
                    }
                    materialization_notice = (
                        _task_protocol_materialization_notice(
                            state.metadata
                        )
                    )
                    return StepVerdict(
                        passed=False,
                        retryable=retryable,
                        message=(
                            "Claim–Evidence 输出契约校验失败："
                            + "；".join(validation_result.get("issues", []))
                        )
                        + (
                            " " + materialization_notice
                            if materialization_notice
                            else ""
                        )
                        + (
                            (
                                " 仅进行一次协议包络修复，且必须保留原正文。"
                                if protocol_retryable
                                else " 保留全部既有 claim_id、occurrence_id 和 "
                                "unverified_id；不得删除、弱化声明或改变来源/原文。"
                            )
                            if retryable
                            else " 来源、声明清单或诊断不满足自动修复条件，Task 停止重试。"
                        ),
                    )
                state.metadata["semantic_review_required"] = True
                retry_inventory = extract_claim_evidence_inventory(output)
                if retry_inventory is not None:
                    state.metadata["claim_evidence_retry_policy"] = {
                        "retryable": True,
                        "inventory": retry_inventory,
                    }

            schema = (
                output_contract.json_schema
                if output_contract is not None
                else None
            )
            s02_v4_adapter_validated = bool(
                run.source_template_id == "s02_project_planning"
                and run.source_template_version in {"4.0.0", "4.1.0"}
                and run.nodes
                and node.id == run.nodes[-1].id
                and isinstance(
                    state.metadata.get("s02_v4_delivery"), dict
                )
                and state.metadata["s02_v4_delivery"].get("passed")
                is True
                and state.metadata.get("output_contract_validation", {}).get(
                    "target"
                )
                == (
                    "s02_v4_1_raw_agent_json"
                    if run.source_template_version == "4.1.0"
                    else "s02_v4_raw_agent_json"
                )
            )
            if schema is None:
                schema = node.output_schema
            if (
                schema is not None
                and (
                    output_contract is None
                    or output_contract.validation_mode == "json_schema"
                )
                and not s02_v4_adapter_validated
            ):
                if node.type is StepType.TOOL and node.capability_key:
                    validation = validate_capability_output(
                        node.capability_key,
                        schema,
                        state.result,
                        output,
                    )
                else:
                    validation = validate_structured_result(
                        schema,
                        state.result,
                        agent_output_json=node.type is StepType.AGENT,
                        tool_output_text=(
                            output if node.type is StepType.TOOL else None
                        ),
                    )
                validation_key = (
                    "capability_output_validation"
                    if node.type is StepType.TOOL and node.capability_key
                    else "output_schema_validation"
                )
                state.metadata[validation_key] = {
                    "status": "passed" if validation.passed else "failed",
                    "target": validation.target,
                    "issues": list(validation.issues),
                }
                state.metadata["output_contract_validation"] = {
                    "contract_id": (
                        output_contract.contract_id
                        if output_contract is not None
                        else "task.output-schema"
                    ),
                    "contract_version": (
                        output_contract.contract_version
                        if output_contract is not None
                        else 1
                    ),
                    "status": "passed" if validation.passed else "failed",
                    "target": validation.target,
                    "issues": list(validation.issues),
                }
                if not validation.passed:
                    detail = "；".join(validation.issues)
                    return StepVerdict(
                        passed=False,
                        message=f"结构化结果校验失败：{detail}",
                    )
                state.result = validation.value
            elif s02_v4_adapter_validated:
                state.metadata["output_contract_validation"] = {
                    **state.metadata["output_contract_validation"],
                    "status": "passed",
                    "target": (
                        "s02_v4_1_raw_agent_json"
                        if run.source_template_version == "4.1.0"
                        else "s02_v4_raw_agent_json"
                    ),
                    "issues": [],
                }
            elif output_contract is not None and output_contract.validation_mode not in {
                "citations",
                "claim_evidence",
            }:
                state.metadata["output_contract_validation"] = {
                    "contract_id": output_contract.contract_id,
                    "contract_version": output_contract.contract_version,
                    "status": "not_required",
                    "target": output_contract.output_type,
                    "issues": [],
                }

            executor = self._select_executor(self._step_context(run, node))
            method = getattr(executor, "verify_node", None)
            if not node.acceptance_criteria.strip() and (
                not callable(method) or node.type is not StepType.AGENT
            ):
                return StepVerdict(passed=True)
            if not callable(method):
                raise RuntimeError(
                    "当前执行器不支持步骤验收，请使用已配置模型的会话。"
                )
            current = await self._store.get_run(run.user_id, run.id)
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
                user_answers=[
                    TaskInputReply.model_validate(answer)
                    for step in ((current or run).sop_state or {}).get(
                        "steps", []
                    )
                    for answer in step.get("answers", [])
                ],
            )
            return StepVerdict.model_validate(
                await method(node=node, output=output, context=context)
            )

        steps = []
        for node in run.nodes:
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
            if is_s02_v2_final_agent:
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
                    lambda state, node=node: execute(node, state),
                    lambda output, state, node=node: verify(node, output, state),
                )
            )
        state = (
            SOPRunState.model_validate(run.sop_state)
            if run.sop_state
            else SOPRunState(
                id=run.id,
                inputs=[UserMsg(name="user", content=run.input)],
            )
        )
        return SOPEngine(SOP(name=run.task_id, steps=steps), state)

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


def _normalize_execution_result(
    value: StepExecutionResult | str,
) -> StepExecutionResult:
    """Keep custom TaskExecutor adapters source-compatible with the MVP."""

    if isinstance(value, StepExecutionResult):
        return value
    return StepExecutionResult(text=str(value))


def _artifact_node(nodes: list[TaskNode]) -> TaskNode | None:
    """Return the only final-node artifact configuration, if present."""

    configured = [node for node in nodes if node.artifact is not None]
    if not configured:
        return None
    if (
        len(configured) != 1
        or configured[0].id != nodes[-1].id
        or configured[0].type is StepType.INPUT
    ):
        raise RuntimeError("文件产物只能配置在最后一个 Task 节点。")
    return configured[0]
