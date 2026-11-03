# -*- coding: utf-8 -*-
"""Task-local opt-in for running the DAG scheduler in the service process."""

import os
from unittest import TestCase
from unittest.mock import patch

from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


class DagSchedulerConfigurationTests(TestCase):
    def test_scheduler_is_disabled_without_runtime_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            service = TaskService(TaskStore())

        self.assertFalse(service._dag_scheduler_enabled)

    def test_task_local_environment_flag_enables_scheduler(self) -> None:
        with patch.dict(os.environ, {"LXSCOPE_TASK_DAG_ENABLED": "true"}, clear=True):
            service = TaskService(TaskStore())

        self.assertTrue(service._dag_scheduler_enabled)

    def test_explicit_constructor_argument_overrides_environment(self) -> None:
        with patch.dict(os.environ, {"LXSCOPE_TASK_DAG_ENABLED": "true"}, clear=True):
            service = TaskService(TaskStore(), enable_dag_scheduler=False)

        self.assertFalse(service._dag_scheduler_enabled)
