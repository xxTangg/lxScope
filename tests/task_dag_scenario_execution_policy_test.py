"""Formal Task-local authorization for the current scenario DAG profiles."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_dag_e1_business_gate_test as e1  # noqa: E402
import task_dag_e2_non_linear_s01_test as e2  # noqa: E402
import task_r7_nine_scenarios_integration_test as r7  # noqa: E402

from examples.agent_service.task._models import (  # noqa: E402
    TaskPreflightRequest,
    TaskRunRequest,
)
from examples.agent_service.task._service import (  # noqa: E402
    TaskService,
    TaskWorkflowConflictError,
)
from examples.agent_service.task._store import TaskStore  # noqa: E402
from examples.agent_service.task._template_registry import (  # noqa: E402
    CURRENT_TEMPLATE_VERSIONS,
)


_DAG_ENABLED = "LXSCOPE_TASK_DAG_ENABLED"
_SCENARIO_DAG_ENABLED = "LXSCOPE_TASK_SCENARIO_DAG_EXECUTION_ENABLED"
_SCENARIO_DAG_TEMPLATES = "LXSCOPE_TASK_SCENARIO_DAG_ALLOWED_TEMPLATES"
_ALL_CURRENT_SCOPES = tuple(sorted(CURRENT_TEMPLATE_VERSIONS))


def _set_formal_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_ENABLED, "true")
    monkeypatch.setenv(
        _SCENARIO_DAG_TEMPLATES,
        ",".join(f"{template_id}@{version}" for template_id, version in _ALL_CURRENT_SCOPES),
    )


@pytest.mark.parametrize(
    "scenario_id",
    [template_id for template_id, _ in _ALL_CURRENT_SCOPES],
)
def test_formal_task_policy_creates_current_linear_scenario_dag_run(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Preflight and the real TaskService.create_run authorization path."""

    _set_formal_authorization(monkeypatch)

    async def exercise() -> None:
        service, _, _, dag, _, gateway, owner, _ = (
            await e1._prepare_linear_scenario_dag(
                scenario_id,
                authorize_test_execution=False,
            )
        )
        try:
            assert service.get_dag_capabilities().execution_enabled is True
            assert {
                (item.template_id, item.template_version)
                for item in service.get_dag_capabilities().scenario_execution_allowlist
            } == set(_ALL_CURRENT_SCOPES)

            preflight = await service.preflight_task(
                owner,
                dag.id,
                TaskPreflightRequest(),
                gateway,
            )
            assert preflight is not None and preflight.ready, (
                scenario_id,
                preflight.model_dump(mode="json") if preflight else None,
            )

            created = await service.create_run(
                owner,
                dag.id,
                TaskRunRequest(input="formal scenario DAG authorization test"),
                gateway,
            )
            assert created is not None
            assert created.engine == "dag"
            assert created.source_template_id == scenario_id
            assert created.source_template_version == dict(_ALL_CURRENT_SCOPES)[scenario_id]
            assert any(item.id == created.id for item in await service.list_runs(owner, dag.id))
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_formal_s01_scope_keeps_the_exact_e2_non_linear_profile_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_TEMPLATES, "s01_project_init@3.0.0")

    async def exercise() -> None:
        service, _, dag, _, gateway, owner, _ = await e2._prepare_pilot(
            authorize_test_execution=False,
        )
        try:
            preflight = await service.preflight_task(
                owner,
                dag.id,
                TaskPreflightRequest(),
                gateway,
            )
            assert preflight is not None and preflight.ready, (
                preflight.model_dump(mode="json") if preflight else None,
            )
            created = await service.create_run(
                owner,
                dag.id,
                TaskRunRequest(input="formal S01 E2 profile authorization test"),
                gateway,
            )
            assert created is not None and created.engine == "dag"
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_default_formal_policy_is_closed_and_test_allowlist_stays_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (_DAG_ENABLED, _SCENARIO_DAG_ENABLED, _SCENARIO_DAG_TEMPLATES):
        monkeypatch.delenv(name, raising=False)

    default_service = TaskService(TaskStore(), enable_dag_scheduler=True)
    assert default_service.get_dag_capabilities().scenario_execution_allowlist == []

    test_service = TaskService(
        TaskStore(),
        enable_dag_scheduler=True,
        dag_scenario_test_allowlist={("s01_project_init", "3.0.0")},
    )
    assert [
        (item.template_id, item.template_version)
        for item in test_service.get_dag_capabilities().scenario_execution_allowlist
    ] == [("s01_project_init", "3.0.0")]


def test_scenario_authorization_does_not_override_the_global_dag_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_DAG_ENABLED, raising=False)
    monkeypatch.setenv(_SCENARIO_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_TEMPLATES, "s01_project_init@3.0.0")

    capabilities = TaskService(TaskStore()).get_dag_capabilities()

    assert capabilities.execution_enabled is False
    assert [
        (item.template_id, item.template_version)
        for item in capabilities.scenario_execution_allowlist
    ] == [("s01_project_init", "3.0.0")]


@pytest.mark.parametrize(
    "configured_scopes",
    [
        "s01_project_init@3.0.0,unknown_template@1.0.0",
        "s02_project_planning@5.0.0",
        "s01_project_init",
    ],
)
def test_invalid_or_non_current_formal_scope_fails_closed(
    configured_scopes: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_SCENARIO_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_TEMPLATES, configured_scopes)
    service = TaskService(TaskStore(), enable_dag_scheduler=True)
    assert service.get_dag_capabilities().scenario_execution_allowlist == []


def test_unlisted_current_scenario_is_rejected_by_preflight_and_create_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_ENABLED, "true")
    monkeypatch.setenv(_SCENARIO_DAG_TEMPLATES, "s01_project_init@3.0.0")

    async def exercise() -> None:
        service, _, _, dag, _, gateway, owner, _ = (
            await e1._prepare_linear_scenario_dag(
                "s09_expense_settlement",
                authorize_test_execution=False,
            )
        )
        try:
            preflight = await service.preflight_task(
                owner,
                dag.id,
                TaskPreflightRequest(),
                gateway,
            )
            assert preflight is not None
            assert any(issue.path == "graph.scenario_execution" for issue in preflight.issues)
            with pytest.raises(TaskWorkflowConflictError, match="versioned scenario Tasks"):
                await service.create_run(
                    owner,
                    dag.id,
                    TaskRunRequest(input="unauthorized scenario scope"),
                    gateway,
                )
            assert await service.list_runs(owner, dag.id) == []
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_partial_template_provenance_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_formal_authorization(monkeypatch)

    async def exercise() -> None:
        service, _, _, dag, _, gateway, owner, _ = (
            await e1._prepare_linear_scenario_dag(
                "s09_expense_settlement",
                authorize_test_execution=False,
            )
        )
        try:
            malformed = dag.model_copy(update={"source_template_id": None})
            await service._store.save_task(malformed)
            preflight = await service.preflight_task(
                owner,
                malformed.id,
                TaskPreflightRequest(),
                gateway,
            )
            assert preflight is not None
            assert any(issue.path == "graph.scenario_execution" for issue in preflight.issues)
            with pytest.raises(TaskWorkflowConflictError, match="缺少完整模板来源"):
                await service.create_run(
                    owner,
                    malformed.id,
                    TaskRunRequest(input="partial provenance must fail closed"),
                    gateway,
                )
            assert await service.list_runs(owner, malformed.id) == []
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_sop_create_run_path_remains_sop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_formal_authorization(monkeypatch)

    async def exercise() -> None:
        service, _, sop_task, _, _, gateway, owner, _ = await r7._prepare(
            "s09_expense_settlement",
        )
        try:
            created = await service.create_run(
                owner,
                sop_task.id,
                TaskRunRequest(input="SOP compatibility control"),
                gateway,
            )
            assert created is not None and created.engine == "sop"
        finally:
            await service.shutdown()

    asyncio.run(exercise())
