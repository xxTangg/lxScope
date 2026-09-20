# -*- coding: utf-8 -*-
"""Execution ports and a deterministic preview adapter."""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from ._models import (
    ExecutionContext,
    PythonStepConfig,
    TaskNode,
    ToolStepConfig,
)


@dataclass(slots=True)
class StepExecutionResult:
    """Normalized result returned by every Task step adapter."""

    text: str
    result: Any = None
    metadata: dict[str, Any] | None = None


class TaskExecutor(Protocol):
    """Port used by the task engine to execute one node."""

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> StepExecutionResult | str:
        """Execute a single step and return its text for the next step."""


class TaskArtifactWriter(Protocol):
    """Port for storing and reading Task artifacts in a Workspace."""

    async def write_artifact(
        self,
        *,
        path: str,
        data: bytes,
        context: ExecutionContext,
    ) -> int:
        """Write one artifact and return its stored size in bytes."""

    async def read_artifact(
        self,
        *,
        path: str,
        context: ExecutionContext,
    ) -> bytes:
        """Read one previously stored artifact."""

class PreviewTaskExecutor:
    """Local adapter used when no AgentScope session is attached.

    It keeps the Task UI fully runnable in a fresh development environment;
    an attached Chat/Session is routed through ``AgentScopeTaskExecutor``.
    """

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        """Return a predictable preview result without calling a model."""

        del context
        await asyncio.sleep(0.25)
        if "[fail]" in (node.prompt or "").lower():
            raise RuntimeError("Preview node requested a failure with [fail].")
        source = previous_output.strip() or "任务初始输入为空"
        excerpt = source if len(source) <= 220 else f"{source[:220]}…"
        if isinstance(node.config, ToolStepConfig):
            description = (
                f"工具：{node.config.tool_name}\n"
                f"参数：{node.config.arguments}"
            )
        elif isinstance(node.config, PythonStepConfig):
            description = "PythonStep 已在预览模式跳过实际执行。"
        else:
            description = f"Prompt：{node.prompt}"
        return (
            StepExecutionResult(
                text=(
                    f"{node.name} 已完成（预览执行）\n\n"
                    f"{description}\n\n"
                    f"上一节点上下文：{excerpt}"
                ),
                result={
                    "mode": "preview",
                    "step_type": node.type.value,
                },
            )
        )
