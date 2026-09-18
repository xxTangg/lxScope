# -*- coding: utf-8 -*-
"""Execution ports and a deterministic preview adapter."""

import asyncio
from typing import Protocol

from ._models import ExecutionContext, TaskNode


class TaskExecutor(Protocol):
    """Port used by the task engine to execute one node."""

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> str:
        """Execute a single node and return text for the next node."""


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
    ) -> str:
        """Return a predictable preview result without calling a model."""

        del context
        await asyncio.sleep(0.25)
        if "[fail]" in node.prompt.lower():
            raise RuntimeError("Preview node requested a failure with [fail].")
        source = previous_output.strip() or "任务初始输入为空"
        excerpt = source if len(source) <= 220 else f"{source[:220]}…"
        return (
            f"{node.name} 已完成（预览执行）\n\n"
            f"Prompt：{node.prompt}\n\n"
            f"上一节点上下文：{excerpt}"
        )
