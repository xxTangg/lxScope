# -*- coding: utf-8 -*-
"""Standalone Task orchestration module for the example agent service.

The package deliberately owns its task definitions, runs and events.  The
AgentScope runtime is accessed only through :class:`TaskExecutor` adapters so
the core task domain does not depend on ChatService internals.
"""

from ._agentscope_executor import AgentScopeTaskExecutor
from ._executor import PreviewTaskExecutor, TaskExecutor
from ._models import (
    CreateTaskRequest,
    ExecutionContext,
    NodeRunRecord,
    TaskEventRecord,
    TaskNode,
    TaskRecord,
    TaskRunRecord,
    TaskRunRequest,
    UpdateTaskRequest,
)
from ._router import task_router
from ._service import TaskService
from ._store import TaskStore

__all__ = [
    "AgentScopeTaskExecutor",
    "CreateTaskRequest",
    "ExecutionContext",
    "NodeRunRecord",
    "PreviewTaskExecutor",
    "TaskEventRecord",
    "TaskExecutor",
    "TaskNode",
    "TaskRecord",
    "TaskRunRecord",
    "TaskRunRequest",
    "TaskService",
    "TaskStore",
    "UpdateTaskRequest",
    "task_router",
]
