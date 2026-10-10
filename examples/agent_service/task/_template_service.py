# -*- coding: utf-8 -*-
"""Create user-owned Tasks from the package's read-only template catalog."""

from __future__ import annotations

import json

from ._models import CreateTaskRequest, TaskRecord
from ._service import TaskService
from ._template_compiler import (
    TaskTemplateCompiler,
    TemplateCompileError,
)
from ._template_models import (
    TemplateInstantiationErrorDetail,
    TemplateInstantiateRequest,
)
from ._template_registry import TemplateRegistry


class TemplateInstantiationError(ValueError):
    """A safe API error raised before any Task is persisted."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        missing_capabilities: tuple[str, ...] = (),
        blocked_capabilities: tuple[str, ...] = (),
        issues: tuple[str, ...] = (),
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.missing_capabilities = missing_capabilities
        self.blocked_capabilities = blocked_capabilities
        self.issues = issues
        super().__init__(code)

    def public_detail(self) -> dict[str, object]:
        """Return a stable error shape without filesystem or input values."""

        return TemplateInstantiationErrorDetail(
            code=self.code,
            message=self.message,
            missing_capabilities=list(self.missing_capabilities),
            blocked_capabilities=list(self.blocked_capabilities),
            issues=list(self.issues),
        ).model_dump(exclude_defaults=True)


class TaskTemplateService:
    """Orchestrate validation, capability binding, and TaskService creation."""

    def __init__(
        self,
        registry: TemplateRegistry,
        task_service: TaskService,
        *,
        compiler: TaskTemplateCompiler | None = None,
    ) -> None:
        self._registry = registry
        self._task_service = task_service
        self._compiler = compiler or TaskTemplateCompiler()

    async def instantiate(
        self,
        user_id: str,
        template_id: str,
        request: TemplateInstantiateRequest,
    ) -> TaskRecord:
        """Validate and create a private copy without invoking the Planner."""

        template = self._registry.get_template(template_id)
        if template is None:
            raise TemplateInstantiationError(
                404,
                "TEMPLATE_NOT_FOUND",
                "内置 Task 模板不存在。",
            )
        if request.template_version != template.template_version:
            raise TemplateInstantiationError(
                409,
                "TEMPLATE_VERSION_MISMATCH",
                "请求的模板版本与当前内置版本不一致。",
                issues=(
                    "template_version: 当前版本为 "
                    f"{template.template_version}。",
                ),
            )

        try:
            inputs = self._compiler.validate_inputs(template, request.inputs)
        except TemplateCompileError as error:
            raise _as_instantiation_error(error) from error

        tool_schemas = await self._task_service.list_tools(
            user_id,
            request.source_context,
        )
        try:
            compiled = self._compiler.compile_nodes(template, tool_schemas)
        except TemplateCompileError as error:
            raise _as_instantiation_error(error) from error

        input_json = json.dumps(
            inputs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        goal = f"{template.description}\n\n模板输入参数（JSON）：\n{input_json}"
        if len(goal) > 20_000:
            raise TemplateInstantiationError(
                422,
                "TEMPLATE_INPUT_INVALID",
                "输入内容超过 Task 可保存的长度限制。",
                issues=("inputs: 输入内容过长。",),
            )

        body = CreateTaskRequest(
            title=template.title,
            goal=goal,
            nodes=compiled.nodes,
            source_context=request.source_context,
        )
        return await self._task_service.create_task(
            user_id,
            body,
            source_template_id=template.template_id,
            source_template_version=template.template_version,
            template_step_bindings=compiled.step_bindings,
        )


def _as_instantiation_error(
    error: TemplateCompileError,
) -> TemplateInstantiationError:
    """Translate compiler errors into the Task API's structured 422 shape."""

    return TemplateInstantiationError(
        422,
        error.code,
        error.message,
        missing_capabilities=error.missing_capabilities,
        blocked_capabilities=error.blocked_capabilities,
        issues=error.issues,
    )
