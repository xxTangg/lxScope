"""Task API capability contract for isolated scenario DAG execution."""

from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def test_default_service_exposes_no_scenario_dag_execution_allowlist(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_SCENARIO_DAG_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("LXSCOPE_TASK_SCENARIO_DAG_ALLOWED_TEMPLATES", raising=False)
    capabilities = TaskService(
        TaskStore(),
        enable_dag_scheduler=True,
        enable_dag_editing=True,
    ).get_dag_capabilities()

    assert capabilities.execution_enabled is True
    assert capabilities.scenario_execution_allowlist == []


def test_isolated_service_exposes_only_exact_authorized_template_version(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LXSCOPE_TASK_SCENARIO_DAG_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("LXSCOPE_TASK_SCENARIO_DAG_ALLOWED_TEMPLATES", raising=False)
    capabilities = TaskService(
        TaskStore(),
        enable_dag_scheduler=True,
        enable_dag_editing=True,
        dag_scenario_test_allowlist={("s01_project_init", "3.0.0")},
    ).get_dag_capabilities()

    assert [item.model_dump() for item in capabilities.scenario_execution_allowlist] == [
        {"template_id": "s01_project_init", "template_version": "3.0.0"},
    ]
