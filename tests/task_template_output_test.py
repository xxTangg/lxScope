"""Template artifact and structured-output contract tests."""

import asyncio
from importlib.util import find_spec
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._capability_contracts import capability_contract
from examples.agent_service.task._artifacts import generate_artifact
from examples.agent_service.task._artifact_spec import spec_from_markdown
from examples.agent_service.task._models import (
    ArtifactConfig,
    ArtifactFormat,
    RunStatus,
    StepVerdict,
    TaskContext,
    TaskInputReply,
    TaskToolSchema,
    TaskRunRequest,
    UpdateTaskRequest,
)
from examples.agent_service.task._output_validation import (
    OutputSchemaDefinitionError,
    describe_output_schema,
    validate_output_schema_definition,
    validate_structured_result,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_models import (
    TaskTemplateDefinition,
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService
from examples.agent_service.task._template_validation import TaskTemplateValidator


_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"accepted": {"type": "boolean"}},
    "required": ["accepted"],
    "additionalProperties": False,
}


class _PlannerMustNotRun:
    async def plan(self, **_: Any) -> Any:
        raise AssertionError("Template instantiation must not call Planner.")


class _SyntheticRegistry:
    def __init__(self, template: TaskTemplateDefinition) -> None:
        self.template = template

    def get_current_template(self, template_id: str):
        return self.template if template_id == self.template.template_id else None

    def get_template_version(self, template_id: str, version: str):
        if (
            template_id == self.template.template_id
            and version == self.template.template_version
        ):
            return self.template
        return None


class _DiskTaskExecutor:
    """Task adapter that stores actual artifact bytes under a test directory."""

    def __init__(
        self,
        root: Path,
        tools: list[TaskToolSchema],
        *,
        valid: Any = True,
    ):
        self.root = root
        self.tools = tools
        self.valid = valid
        self.artifact_writes: list[tuple[str, bytes]] = []
        self.executed_nodes: list[str] = []
        self.write_error: str | None = None

    async def list_tools(self, context):
        del context
        return self.tools

    async def preflight_tool_permission(
        self,
        *,
        tool_name,
        arguments,
        context,
        arguments_resolved,
    ):
        del tool_name, arguments, context, arguments_resolved
        return "allowed", "Task output test fixture permission"

    async def preflight_artifact_writer(self, *, context):
        del context
        return True, "Isolated test-directory writer is available."

    def business_data_verified(self, capability_key):
        return capability_key in {
            "expert.lookup",
            "expert.conflict_check",
            "expert.rank",
        }

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        self.executed_nodes.append(node.name)
        if node.capability_key == "expert.lookup":
            value = {
                "contract_version": "task.expert-capability.v1",
                "source": {
                    "system_id": "test-only",
                    "dataset_id": "test-only",
                    "data_version": "fixture-1",
                    "queried_at": "2026-10-10T00:00:00Z",
                },
                "query_context": {
                    "project_profile": {
                        "project_name": "测试项目",
                        "project_brief": "测试项目背景",
                        "professional_fields": ["测试领域"],
                    },
                    "candidate_limit": 1,
                },
                "candidates": [
                    {
                        "expert_id": "fixture-only",
                        "authorized_fields": {"field": "测试领域"},
                        "evidence_refs": [],
                    }
                ],
            }
        elif node.capability_key == "expert.conflict_check":
            value = {
                "contract_version": "task.expert-capability.v1",
                "source": {
                    "system_id": "test-only",
                    "dataset_id": "test-only",
                    "data_version": "fixture-1",
                    "queried_at": "2026-10-10T00:00:00Z",
                },
                "rule_version": "fixture-rules-1",
                "checked_at": "2026-10-10T00:00:01Z",
                "candidates": [
                    {
                        "expert_id": "fixture-only",
                        "conflict_status": "clear",
                        "evidence_refs": [],
                    }
                ],
            }
        elif node.capability_key == "expert.rank":
            value = {
                "contract_version": "task.expert-capability.v1",
                "source": {
                    "system_id": "test-only",
                    "dataset_id": "test-only",
                    "data_version": "fixture-1",
                    "queried_at": "2026-10-10T00:00:00Z",
                },
                "ranking_algorithm_version": "fixture-ranker-1",
                "scored_at": "2026-10-10T00:00:02Z",
                "candidates": [
                    {
                        "expert_id": "fixture-only",
                        "conflict_status": "clear",
                        "rank": 1,
                        "score": 1,
                        "score_components": {"fixture": 1},
                        "evidence_refs": [],
                    }
                ],
            }
        elif node.name == "形成项目画像":
            value = {
                "project_profile": {
                    "project_name": "测试项目",
                    "project_brief": "测试项目背景",
                    "professional_fields": ["测试领域"],
                },
                "candidate_limit": 1,
            }
        else:
            value = {"accepted": self.valid}
        return StepExecutionResult(
            text=json.dumps(value, ensure_ascii=False),
            result=value,
        )

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True, message="test verifier accepted")

    async def write_artifact(self, *, path, data, context):
        del context
        if self.write_error:
            raise OSError(self.write_error)
        target = (self.root / path).resolve()
        assert target.is_relative_to(self.root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.artifact_writes.append((path, data))
        return target.stat().st_size

    async def read_artifact(self, *, path, context):
        del context
        return (self.root / path).read_bytes()


def _synthetic_template(
    artifact_format: str = "markdown",
) -> TaskTemplateDefinition:
    source = TemplateRegistry().get_template_version(
        "s03_expert_matching", "1.0.0"
    )
    assert source is not None
    payload = source.model_dump(mode="json", by_alias=True)
    last_step = payload["steps"][-1]
    extension = {"markdown": "md", "docx": "docx", "xlsx": "xlsx"}[
        artifact_format
    ]
    last_step["artifact"] = {
        "format": artifact_format,
        "filename": f"专家匹配结果.{extension}",
    }
    last_step["output_schema"] = _OUTPUT_SCHEMA
    return TaskTemplateDefinition.model_validate(payload)


def _tools(template: TaskTemplateDefinition) -> list[TaskToolSchema]:
    return [
        TaskToolSchema(
            name=capability,
            description="Task output test tool",
            input_schema=(
                capability_contract(capability).input_schema or {}
                if capability_contract(capability) is not None
                else {"type": "object"}
            ),
            is_read_only=True,
        )
        for capability in template.required_capabilities
    ]


async def _finish_input_steps(service: TaskService, run_id: str):
    for _ in range(8):
        active = service._active_runs.get(run_id)
        if active is not None:
            await active
        run = await service.get_run("template-owner", run_id)
        assert run is not None
        if run.status is not RunStatus.AWAITING_INPUT:
            return run
        assert run.pending_input is not None
        questions = run.pending_input.questions
        answers = {
            question.key: "测试输入"
            for question in questions
            if question.required
        }
        if not answers and questions:
            answers[questions[0].key] = "测试输入"
        await service.answer_run(
            "template-owner",
            run.id,
            TaskInputReply(
                request_id=run.pending_input.id,
                answers=answers,
            ),
        )
    raise AssertionError("Template Task did not leave its input steps.")


def test_template_artifact_and_schema_compile_without_changing_catalog() -> None:
    template = _synthetic_template()
    compiled = TaskTemplateCompiler().compile_nodes(template, _tools(template))

    assert len(compiled.nodes) == 8
    assert compiled.nodes[-1].artifact is not None
    assert compiled.nodes[-1].artifact.format.value == "markdown"
    assert compiled.nodes[-1].artifact.filename == "专家匹配结果.md"
    assert compiled.nodes[-1].output_schema == _OUTPUT_SCHEMA

    original = TemplateRegistry().get_template_version(
        "s03_expert_matching", "1.0.0"
    )
    assert original is not None
    assert all(step.artifact is None for step in original.steps)
    assert all(step.output_schema is None for step in original.steps)


@pytest.mark.parametrize(
    "artifact",
    [
        {"format": "pdf"},
        {"format": "docx", "filename": "../../outside.docx"},
        {"format": "xlsx", "filename": "C:\\outside.xlsx"},
    ],
)
def test_template_rejects_unsupported_or_unsafe_artifact_config(artifact) -> None:
    source = TemplateRegistry().get_template_version(
        "s03_expert_matching", "1.0.0"
    )
    assert source is not None
    payload = source.model_dump(mode="json", by_alias=True)
    payload["steps"][-1]["artifact"] = artifact

    with pytest.raises(ValidationError):
        TaskTemplateDefinition.model_validate(payload)


def test_output_schema_rejects_unimplemented_and_unbounded_features() -> None:
    with pytest.raises(OutputSchemaDefinitionError):
        validate_output_schema_definition(
            {"type": "object", "properties": {}, "patternProperties": {}}
        )

    schema = {"type": "object", "properties": {}}
    for index in range(9):
        schema = {
            "type": "object",
            "properties": {f"level_{index}": schema},
        }
    with pytest.raises(OutputSchemaDefinitionError, match="嵌套深度"):
        validate_output_schema_definition(schema)

    source = TemplateRegistry().get_template_version(
        "s03_expert_matching", "1.0.0"
    )
    assert source is not None
    payload = source.model_dump(mode="json", by_alias=True)
    payload["steps"][-1]["output_schema"] = {
        "type": "object",
        "patternProperties": {},
    }
    with pytest.raises(ValidationError):
        TaskTemplateDefinition.model_validate(payload)

    payload = source.model_dump(mode="json", by_alias=True)
    payload["steps"][0]["output_schema"] = _OUTPUT_SCHEMA
    with pytest.raises(ValidationError, match="Agent steps only"):
        TaskTemplateDefinition.model_validate(payload)


def test_output_schema_supports_bounded_discriminated_one_of() -> None:
    schema = {
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "required": ["kind", "text"],
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["ordinary"]},
                    "text": {"type": "string", "minLength": 1},
                },
            },
            {
                "type": "object",
                "required": ["kind", "text", "reason"],
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["pending"]},
                    "text": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        ],
    }

    validate_output_schema_definition(schema)
    ordinary = validate_structured_result(
        schema,
        {"kind": "ordinary", "text": "content"},
    )
    pending = validate_structured_result(
        schema,
        {"kind": "pending", "text": "content", "reason": "missing source"},
    )
    invalid = validate_structured_result(
        schema,
        {"kind": "ordinary", "text": "content", "reason": "not allowed"},
    )

    assert ordinary.passed is True
    assert pending.passed is True
    assert invalid.passed is False
    assert "必须且仅符合以下一种结构" in describe_output_schema(schema)


def test_structured_result_validation_has_bounded_trace() -> None:
    passed = validate_structured_result(_OUTPUT_SCHEMA, {"accepted": True})
    failed = validate_structured_result(_OUTPUT_SCHEMA, {"accepted": "yes"})
    missing = validate_structured_result(_OUTPUT_SCHEMA, None)

    assert passed.passed is True
    assert failed.passed is False
    assert failed.issues == ("$.accepted: type",)
    assert missing.passed is False
    assert "64 KB" not in missing.issues[0]

    agent_output = validate_structured_result(
        _OUTPUT_SCHEMA,
        {
            "status": "completed",
            "output": '{"accepted": true}',
            "reason": "",
            "questions": [],
        },
        agent_output_json=True,
    )
    malformed_agent_output = validate_structured_result(
        _OUTPUT_SCHEMA,
        {
            "status": "completed",
            "output": "not JSON",
            "reason": "",
            "questions": [],
        },
        agent_output_json=True,
    )
    assert agent_output.passed is True
    assert agent_output.target == "agent_output_json"
    assert malformed_agent_output.passed is False
    assert "合法 JSON" in malformed_agent_output.issues[0]


@pytest.mark.parametrize(
    ("format", "signature"),
    [
        (ArtifactFormat.MARKDOWN, b"# "),
        (ArtifactFormat.DOCX, b"PK"),
        (ArtifactFormat.XLSX, b"PK"),
    ],
)
def test_existing_artifact_writer_serializes_all_supported_formats(
    format: ArtifactFormat,
    signature: bytes,
) -> None:
    required_module = {
        ArtifactFormat.DOCX: "docx",
        ArtifactFormat.XLSX: "openpyxl",
    }.get(format)
    if required_module and find_spec(required_module) is None:
        with pytest.raises(ModuleNotFoundError, match=required_module):
            generate_artifact(
                output="# test result\n",
                config=ArtifactConfig(format=format, filename="task-result"),
                default_stem="task-test",
                spec=spec_from_markdown("# test result\n"),
            )
        return

    output = "# 测试结果\n\n- 已完成\n"
    artifact = generate_artifact(
        output=output,
        config=ArtifactConfig(format=format, filename="任务结果"),
        default_stem="task-test",
        spec=spec_from_markdown(output),
    )

    assert artifact.format is format
    assert artifact.name.endswith(
        {ArtifactFormat.MARKDOWN: ".md", ArtifactFormat.DOCX: ".docx", ArtifactFormat.XLSX: ".xlsx"}[format]
    )
    assert artifact.data.startswith(signature)
    assert artifact.preview_text == output


async def _template_instantiation_defers_real_artifact_until_run(
    tmp_path: Path,
) -> None:
    template = _synthetic_template()
    registry = _SyntheticRegistry(template)
    store = TaskStore()
    executor = _DiskTaskExecutor(tmp_path, _tools(template))
    planner = _PlannerMustNotRun()
    service = TaskService(
        store,
        agentscope_executor=executor,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
    )
    service._template_validator = TaskTemplateValidator(
        registry=registry,  # type: ignore[arg-type]
        compiler=TaskTemplateCompiler(),
    )
    templates = TaskTemplateService(
        registry,  # type: ignore[arg-type]
        service,
    )

    try:
        task = await templates.instantiate(
            "template-owner",
            template.template_id,
            TemplateInstantiateRequest(
                template_version=template.template_version,
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "专家匹配能力测试",
                    "professional_fields": ["管理"],
                    "required_expert_count": 2,
                },
                source_context=TaskContext(
                    agent_id="output-test-agent",
                    session_id="output-test-session",
                ),
            ),
        )
        assert executor.artifact_writes == []
        assert task.nodes[-1].artifact is not None
        assert task.nodes[-1].output_schema == _OUTPUT_SCHEMA
        edited_nodes = [node.model_copy() for node in task.nodes]
        edited_nodes[-1] = edited_nodes[-1].model_copy(
            update={
                "artifact": ArtifactConfig(
                    format=ArtifactFormat.MARKDOWN,
                    filename="用户副本成果.md",
                ),
            },
        )
        validation = await service.validate_task_definition(
            "template-owner",
            task,
            edited_nodes,
            context=task.source_context,
        )
        assert validation.valid, [
            (issue.path, issue.message.encode("unicode_escape"))
            for issue in validation.issues
        ]
        task = await service.update_task(
            "template-owner",
            task.id,
            UpdateTaskRequest(nodes=edited_nodes),
        )
        assert task is not None
        assert task.nodes[-1].artifact is not None
        assert task.nodes[-1].artifact.filename == "用户副本成果.md"
        original = TemplateRegistry().get_template_version(
            "s03_expert_matching", "1.0.0"
        )
        assert original is not None
        assert original.steps[-1].artifact is None

        run = await service.create_run(
            "template-owner",
            task.id,
            TaskRunRequest(input="执行模板输出校验"),
        )
        assert run is not None
        completed = await _finish_input_steps(service, run.id)
        assert completed.status is RunStatus.SUCCEEDED
        assert len(executor.artifact_writes) == 1
        artifact_path, artifact_bytes = executor.artifact_writes[0]
        assert (tmp_path / artifact_path).is_file()
        assert (tmp_path / artifact_path).read_bytes() == artifact_bytes
        assert artifact_bytes.startswith(b"{")
        assert len(completed.artifacts) == 1
        assert completed.artifacts[0].run_id == completed.id
        assert completed.artifacts[0].task_id == task.id
        assert completed.artifacts[0].node_id == task.nodes[-1].id

        owned = await service.read_artifact(
            "template-owner", completed.id, completed.artifacts[0].id
        )
        other_user = await service.read_artifact(
            "other-user", completed.id, completed.artifacts[0].id
        )
        assert owned is not None and owned[1] == artifact_bytes
        assert other_user is None
        assert executor.executed_nodes
    finally:
        await service.shutdown()


async def _structured_schema_failure_is_persisted_and_fails_run(
    tmp_path: Path,
) -> None:
    template = _synthetic_template()
    registry = _SyntheticRegistry(template)
    store = TaskStore()
    executor = _DiskTaskExecutor(tmp_path, _tools(template), valid="no")
    service = TaskService(
        store,
        agentscope_executor=executor,  # type: ignore[arg-type]
    )
    service._template_validator = TaskTemplateValidator(
        registry=registry,  # type: ignore[arg-type]
        compiler=TaskTemplateCompiler(),
    )
    templates = TaskTemplateService(registry, service)  # type: ignore[arg-type]

    try:
        task = await templates.instantiate(
            "template-owner",
            template.template_id,
            TemplateInstantiateRequest(
                template_version=template.template_version,
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "专家匹配能力测试",
                    "professional_fields": ["管理"],
                    "required_expert_count": 2,
                },
                source_context=TaskContext(
                    agent_id="output-test-agent",
                    session_id="output-test-session",
                ),
            ),
        )
        run = await service.create_run(
            "template-owner",
            task.id,
            TaskRunRequest(input="触发 Schema 失败"),
        )
        assert run is not None
        failed = await _finish_input_steps(service, run.id)
        assert failed.status is RunStatus.FAILED
        last_node = failed.node_runs[-1]
        assert last_node.output.endswith('{"accepted": "no"}')
        assert last_node.output.startswith(
            "> **Task 服务端验收：失败**"
        )
        assert "TASK_OUTPUT_SCHEMA_MISMATCH" in last_node.output
        assert last_node.result == {"accepted": "no"}
        assert last_node.error == "结构化结果校验失败：$.accepted: type"
        assert last_node.metadata["output_schema_validation"] == {
            "status": "failed",
            "target": "result",
            "issues": ["$.accepted: type"],
        }
        assert executor.artifact_writes == []
    finally:
        await service.shutdown()


async def _office_artifact_runtime_is_accurate(
    tmp_path: Path,
) -> None:
    template = _synthetic_template("docx")
    registry = _SyntheticRegistry(template)
    executor = _DiskTaskExecutor(tmp_path, _tools(template))
    service = TaskService(
        TaskStore(),
        agentscope_executor=executor,  # type: ignore[arg-type]
    )
    service._template_validator = TaskTemplateValidator(
        registry=registry,  # type: ignore[arg-type]
        compiler=TaskTemplateCompiler(),
    )
    templates = TaskTemplateService(registry, service)  # type: ignore[arg-type]

    try:
        task = await templates.instantiate(
            "template-owner",
            template.template_id,
            TemplateInstantiateRequest(
                template_version=template.template_version,
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "专家匹配能力测试",
                    "professional_fields": ["管理"],
                    "required_expert_count": 2,
                },
                source_context=TaskContext(
                    agent_id="output-test-agent",
                    session_id="output-test-session",
                ),
            ),
        )
        run = await service.create_run(
            "template-owner",
            task.id,
            TaskRunRequest(input="验证 DOCX Renderer 缺失行为"),
        )
        assert run is not None
        result = await _finish_input_steps(service, run.id)
        if find_spec("docx") is None:
            assert result.status is RunStatus.FAILED
            assert "docx" in (result.error or "")
            assert result.artifacts == []
            assert executor.artifact_writes == []
        else:
            assert result.status is RunStatus.SUCCEEDED
            assert len(result.artifacts) == 1
            assert len(executor.artifact_writes) == 1
    finally:
        await service.shutdown()


async def _artifact_write_failure_is_not_recorded_as_success(
    tmp_path: Path,
) -> None:
    template = _synthetic_template()
    registry = _SyntheticRegistry(template)
    executor = _DiskTaskExecutor(tmp_path, _tools(template))
    executor.write_error = "test Workspace write failure"
    service = TaskService(
        TaskStore(),
        agentscope_executor=executor,  # type: ignore[arg-type]
    )
    service._template_validator = TaskTemplateValidator(
        registry=registry,  # type: ignore[arg-type]
        compiler=TaskTemplateCompiler(),
    )
    templates = TaskTemplateService(registry, service)  # type: ignore[arg-type]

    try:
        task = await templates.instantiate(
            "template-owner",
            template.template_id,
            TemplateInstantiateRequest(
                template_version=template.template_version,
                inputs={
                    "project_name": "测试项目",
                    "project_brief": "专家匹配能力测试",
                    "professional_fields": ["管理"],
                    "required_expert_count": 2,
                },
                source_context=TaskContext(
                    agent_id="output-test-agent",
                    session_id="output-test-session",
                ),
            ),
        )
        run = await service.create_run(
            "template-owner",
            task.id,
            TaskRunRequest(input="验证 Workspace 写入失败"),
        )
        assert run is not None
        failed = await _finish_input_steps(service, run.id)
        assert failed.status is RunStatus.FAILED
        assert failed.error == "test Workspace write failure"
        assert failed.artifacts == []
        assert executor.artifact_writes == []
        artifact_node = next(
            node for node in failed.nodes if node.artifact is not None
        )
        artifact_run = next(
            node_run
            for node_run in failed.node_runs
            if node_run.node_id == artifact_node.id
        )
        assert artifact_run.metadata["authoritative_output_status"][
            "error_code"
        ] == "TASK_ARTIFACT_GENERATION_FAILED"
        assert artifact_run.output.startswith(
            "> **Task 服务端验收：失败**"
        )
        events = await service.list_events("template-owner", run.id)
        assert all(event.type != "artifact.created" for event in events)
    finally:
        await service.shutdown()


def test_template_instantiation_defers_real_artifact_until_run(
    tmp_path: Path,
) -> None:
    asyncio.run(_template_instantiation_defers_real_artifact_until_run(tmp_path))


def test_structured_schema_failure_is_persisted_and_fails_run(
    tmp_path: Path,
) -> None:
    asyncio.run(_structured_schema_failure_is_persisted_and_fails_run(tmp_path))


def test_office_artifact_generation_uses_existing_renderer(
    tmp_path: Path,
) -> None:
    asyncio.run(_office_artifact_runtime_is_accurate(tmp_path))


def test_artifact_write_failure_is_not_recorded_as_success(
    tmp_path: Path,
) -> None:
    asyncio.run(_artifact_write_failure_is_not_recorded_as_success(tmp_path))
