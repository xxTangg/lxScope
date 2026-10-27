"""Real Redis/PostgreSQL S01 DAG recovery coverage for E2-F.

These tests are skipped unless explicit disposable Task test backends are
configured. PostgreSQL database creation/drop and Redis key cleanup are
limited to random test-owned records in the isolated service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_dag_e2_non_linear_s01_test as e2  # noqa: E402
import task_r7_nine_scenarios_integration_test as r7  # noqa: E402
import task_sop_r6_integration_test as r6  # noqa: E402

from examples.agent_service.persistence.database import ApplicationDatabase  # noqa: E402
from examples.agent_service.task._models import (  # noqa: E402
    TaskEventDraft,
    TaskInputReply,
    TaskPreflightRequest,
    TaskRunControlRequest,
    TaskRunRequest,
    UpdateTaskRequest,
)
from examples.agent_service.task._postgres_store import PostgresTaskStore  # noqa: E402
from examples.agent_service.task._redis_store import RedisTaskStore  # noqa: E402
from examples.agent_service.task._service import TaskService  # noqa: E402
from examples.agent_service.task._store import TaskStoreConflictError  # noqa: E402
from examples.agent_service.task._template_models import (  # noqa: E402
    TemplateInstantiateRequest,
)
from examples.agent_service.task._template_registry import TemplateRegistry  # noqa: E402
from examples.agent_service.task._template_service import TaskTemplateService  # noqa: E402


_PG_URL = os.getenv("LXSCOPE_TASK_TEST_DATABASE_URL", "")
_REDIS_URL = os.getenv("LXSCOPE_TASK_TEST_REDIS_URL", "")
_REDIS_ISOLATED = os.getenv("LXSCOPE_TASK_TEST_REDIS_ISOLATED", "")


class _DurableSyntheticExecutor(r7._SyntheticModelAndWorkspace):
    """Use the existing deterministic business adapter and a temp-file artifact port."""

    def __init__(self, scenario_id: str, inputs: dict[str, Any], root: Path) -> None:
        super().__init__(scenario_id, inputs)
        self._root = root.resolve()
        self._call_log = self._root / "executor-calls.jsonl"

    def _artifact_path(self, path: str) -> Path:
        suffix = Path(path).suffix[:16]
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
        target = (self._root / f"{digest}{suffix}").resolve()
        if self._root not in target.parents:
            raise ValueError("Synthetic artifact escaped the test root.")
        return target

    async def execute_node(self, *, node, previous_output, context):
        result = await super().execute_node(
            node=node,
            previous_output=previous_output,
            context=context,
        )
        with self._call_log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "run_id": context.run_id,
                        "node_id": node.id,
                    },
                )
                + "\n",
            )
        return result

    async def write_artifact(self, *, path: str, data: bytes, context) -> int:
        del context
        if not data:
            raise ValueError("Synthetic artifact must not be empty.")
        self._artifact_path(path).write_bytes(data)
        self.artifacts[path] = data
        return len(data)

    async def read_artifact(self, *, path: str, context) -> bytes:
        del context
        data = self._artifact_path(path).read_bytes()
        self.artifacts[path] = data
        return data


class _LoggedPilotKnowledge(e2._PilotKnowledge):
    """Reuse E2's scoped deterministic adapter and log its per-query outcomes."""

    def __init__(self, owner: str, root: Path) -> None:
        super().__init__(owner)
        self._query_log = root / "knowledge-queries.jsonl"

    async def retrieve_task_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        query: str,
        *,
        source_material_ids: list[str],
        top_k: int,
    ) -> dict[str, Any]:
        capability = (
            "project.rule_check"
            if "规则" in query or "办法" in query
            else "project.similar_search"
        )
        try:
            result = await super().retrieve_task_evidence(
                user_id,
                knowledge_base_ids,
                query,
                source_material_ids=source_material_ids,
                top_k=top_k,
            )
        except Exception:
            status = "failed"
            raise
        else:
            status = "succeeded"
            return result
        finally:
            with self._query_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "user_id": user_id,
                            "knowledge_base_ids": knowledge_base_ids,
                            "capability": capability,
                            "status": status,
                            "query_sha256": hashlib.sha256(
                                query.encode("utf-8"),
                            ).hexdigest(),
                        },
                    )
                    + "\n",
                )


def _s01_service(store, owner: str, inputs: dict[str, Any], root: Path):
    registry = TemplateRegistry()
    template = registry.get_current_template(e2.SCENARIO_ID)
    assert template is not None
    executor = _DurableSyntheticExecutor(e2.SCENARIO_ID, inputs, root)
    service = TaskService(
        store,
        preview_executor=executor,
        artifact_writer=executor,
        enable_dag_scheduler=True,
        enable_dag_editing=True,
        dag_scenario_test_allowlist={
            (e2.SCENARIO_ID, e2.SCENARIO_VERSION),
        },
    )
    return service, registry, template, executor


async def _resume_s01_run_in_new_process(
    backend_kind: str,
    backend_url: str,
    owner: str,
    task_id: str,
    run_id: str,
    failed_node_id: str,
    inputs: dict[str, Any],
    root: str,
    result_queue,
) -> None:
    database = None
    client = None
    service = None
    try:
        if backend_kind == "postgres":
            database = ApplicationDatabase(backend_url)
            await database.initialize()
            store = PostgresTaskStore(database.engine)
            await store.initialize()
        else:
            from redis.asyncio import Redis

            client = Redis.from_url(backend_url, decode_responses=True)
            await client.ping()
            store = RedisTaskStore(client)

        root_path = Path(root)
        executor = _DurableSyntheticExecutor(e2.SCENARIO_ID, inputs, root_path)
        gateway = _LoggedPilotKnowledge(owner, root_path)
        service = TaskService(
            store,
            preview_executor=executor,
            artifact_writer=executor,
            enable_dag_scheduler=True,
            enable_dag_editing=True,
            dag_scenario_test_allowlist={
                (e2.SCENARIO_ID, e2.SCENARIO_VERSION),
            },
        )
        before = await service.get_run(owner, run_id, gateway)
        if before is None or before.status.value != "failed":
            raise AssertionError("Persisted S01 Run was not failed before recovery.")

        retry_request = TaskRunControlRequest(
            request_id="e2f-s01-read-only-branch-retry",
            expected_run_version=before.run_version,
            side_effects_verified=True,
        )
        retried = await service.retry_node(
            owner,
            run_id,
            failed_node_id,
            retry_request,
        )
        if retried is None or retried.id != run_id:
            raise AssertionError("Retry did not preserve the Run ID.")
        duplicate = await service.retry_node(
            owner,
            run_id,
            failed_node_id,
            retry_request,
        )
        if duplicate is None or duplicate.run_version != retried.run_version:
            raise AssertionError("Duplicate retry request was not idempotent.")

        continued = await service.continue_run(
            owner,
            run_id,
            TaskRunControlRequest(
                request_id="e2f-s01-continue-after-retry",
                expected_run_version=retried.run_version,
            ),
            gateway,
        )
        if continued is None or continued.id != run_id:
            raise AssertionError("Continue did not resume the same Run.")
        final = await r7._drive(service, owner, run_id, gateway, inputs)
        if final.status.value != "succeeded":
            raise AssertionError(f"Recovered S01 Run ended as {final.status.value}.")
        result_queue.put(
            {
                "status": final.status.value,
                "run_id": final.id,
                "run_version": final.run_version,
                "event_sequence": final.event_sequence,
                "failed_node_id": failed_node_id,
                "node_runs": [
                    {
                        "node_id": item.node_id,
                        "status": item.status.value,
                        "attempt": item.attempt,
                        "attempt_history": [
                            entry.status for entry in item.attempt_history
                        ],
                    }
                    for item in final.node_runs
                ],
                "artifact_path": final.artifacts[0].path
                if final.artifacts
                else None,
                "artifact_format": final.artifacts[0].format.value
                if final.artifacts
                else None,
            },
        )
    except BaseException:
        result_queue.put({"error": traceback.format_exc()})
        raise
    finally:
        if service is not None:
            await service.shutdown()
        if database is not None:
            await database.close()
        if client is not None:
            await client.aclose()


def _s01_recovery_process(
    backend_kind: str,
    backend_url: str,
    owner: str,
    task_id: str,
    run_id: str,
    failed_node_id: str,
    inputs: dict[str, Any],
    root: str,
    result_queue,
) -> None:
    try:
        asyncio.run(
            _resume_s01_run_in_new_process(
                backend_kind,
                backend_url,
                owner,
                task_id,
                run_id,
                failed_node_id,
                inputs,
                root,
                result_queue,
            ),
        )
    except BaseException:
        # The coroutine already sends the full error payload to the parent.
        raise


@pytest.mark.parametrize("backend_kind", ["postgres", "redis"])
def test_real_store_s01_parallel_retry_survives_new_worker_process(
    backend_kind: str,
) -> None:
    if backend_kind == "postgres" and not _PG_URL:
        pytest.skip("Set LXSCOPE_TASK_TEST_DATABASE_URL to a disposable PostgreSQL URL")
    if backend_kind == "redis" and not _REDIS_URL:
        pytest.skip("Set LXSCOPE_TASK_TEST_REDIS_URL to a disposable Redis URL")

    async def scenario() -> None:
        backend = await r6._Backend(
            backend_kind,
            _PG_URL if backend_kind == "postgres" else _REDIS_URL,
        ).open()
        owner = f"e2f-s01-{uuid4().hex}"
        registry = TemplateRegistry()
        template = registry.get_current_template(e2.SCENARIO_ID)
        assert template is not None
        inputs = r7._inputs_for(e2.SCENARIO_ID, template)
        with tempfile.TemporaryDirectory(prefix="task-e2f-s01-") as temporary:
            root = Path(temporary).resolve()
            gateway = _LoggedPilotKnowledge(owner, root)
            service = None
            child = None
            try:
                service, registry, template, executor = _s01_service(
                    backend.store,
                    owner,
                    inputs,
                    root,
                )
                source = await TaskTemplateService(registry, service).instantiate(
                    owner,
                    e2.SCENARIO_ID,
                    TemplateInstantiateRequest(
                        template_version=e2.SCENARIO_VERSION,
                        inputs=inputs,
                    ),
                )
                if e2.SCENARIO_ID in e2.e1.SCENARIOS_WITH_SYNTHETIC_KB:
                    source = await service.update_task(
                        owner,
                        source.id,
                        UpdateTaskRequest(knowledge_base_ids=[r7.KB_ID]),
                    )
                    assert source is not None
                task = await service.create_custom_flow_copy(
                    owner,
                    source.id,
                    mode="dag",
                )
                assert task is not None and task.graph is not None
                task = await service.update_task(
                    owner,
                    task.id,
                    UpdateTaskRequest(graph=e2._pilot_graph(task)),
                )
                assert task is not None and task.graph is not None
                backend.owned.extend(
                    [
                        ("task", owner, source.id),
                        ("task", owner, task.id),
                    ],
                )

                preflight = await service.preflight_task(
                    owner,
                    task.id,
                    TaskPreflightRequest(),
                    gateway,
                )
                assert preflight is not None and preflight.ready
                ids = e2._ids(task)
                gateway.fail_runtime_capability = "project.similar_search"
                gateway.hold_capabilities = {
                    "project.similar_search",
                    "project.rule_check",
                }
                for event in gateway.entered.values():
                    event.clear()
                for event in gateway.release.values():
                    event.clear()
                created = await service.create_run(
                    owner,
                    task.id,
                    TaskRunRequest(input="E2-F isolated S01 storage acceptance"),
                    gateway,
                )
                assert created is not None and created.engine == "dag"
                backend.owned.extend(
                    [
                        ("run", owner, created.id),
                        ("run_index", owner, task.id),
                    ],
                )

                waiting = await e2._wait_for_run(
                    service,
                    owner,
                    created.id,
                    lambda run: run.status.value == "awaiting_input",
                )
                assert waiting.pending_input is not None

                # Exercise real backend CAS and Run+event atomic updates while
                # the Task is checkpointed at its initial InputStep.
                writers = 12

                async def cas_update(index: int) -> None:
                    for _ in range(100):
                        current = await backend.store.get_run(owner, created.id)
                        assert current is not None
                        report = dict(current.verification_report)
                        report[f"e2f-writer-{index}"] = {"committed": True}
                        try:
                            await backend.store.transition_run(
                                owner,
                                created.id,
                                expected_version=current.run_version,
                                run_patch={"verification_report": report},
                                audit_events=[
                                    TaskEventDraft(
                                        type="e2f.storage.cas.updated",
                                        payload={"writer": index},
                                    ),
                                ],
                            )
                            return
                        except TaskStoreConflictError:
                            continue
                    raise AssertionError("S01 storage CAS did not converge.")

                initial = await backend.store.get_run(owner, created.id)
                assert initial is not None
                await asyncio.gather(*(cas_update(index) for index in range(writers)))
                after_cas = await backend.store.get_run(owner, created.id)
                cas_events = await backend.store.list_events(owner, created.id)
                assert after_cas is not None
                assert after_cas.run_version == initial.run_version + writers
                assert after_cas.event_sequence == initial.event_sequence + writers
                assert after_cas.event_sequence == len(cas_events)
                assert [item.sequence for item in cas_events] == list(
                    range(1, len(cas_events) + 1),
                )
                assert sum(
                    item.type == "e2f.storage.cas.updated" for item in cas_events
                ) == writers

                current = await service.get_run(owner, created.id, gateway)
                assert current is not None
                reply = TaskInputReply(
                    request_id=current.pending_input.id,
                    answers={
                        question.key: r7._answer_value(question.key, inputs)
                        for question in current.pending_input.questions
                    },
                )
                answered = await service.answer_run(
                    owner,
                    created.id,
                    reply,
                    gateway,
                )
                assert answered is not None
                await asyncio.wait_for(
                    asyncio.gather(
                        gateway.entered["project.similar_search"].wait(),
                        gateway.entered["project.rule_check"].wait(),
                    ),
                    timeout=10,
                )
                # Hold both real TaskService-dispatched read-only adapters until
                # both branches are inside the gateway, then release together.
                gateway.release["project.similar_search"].set()
                gateway.release["project.rule_check"].set()
                failed = await e2._wait_for_run(
                    service,
                    owner,
                    created.id,
                    lambda run: run.status.value == "failed",
                )
                assert gateway.max_active_queries >= 2
                before = {item.node_id: item for item in failed.node_runs}
                for step_key in (
                    "collect_project_input",
                    "extract_project_profile",
                    "check_project_rules",
                ):
                    record = before[ids[step_key]]
                    assert record.status.value == "succeeded"
                    assert record.attempt == 1
                assert before[ids["search_similar_projects"]].status.value == "failed"
                assert before[ids["search_similar_projects"]].attempt == 1
                assert before[ids["draft_initiation_recommendation"]].status.value == "pending"
                assert failed.artifacts == []
                retained = {
                    node_id: before[node_id].model_dump(mode="json")
                    for node_id in (
                        ids["collect_project_input"],
                        ids["extract_project_profile"],
                        ids["check_project_rules"],
                    )
                }
                failed_events = await service.list_events(owner, created.id)
                assert any(item.type == "run.failed" for item in failed_events)
                assert [item.sequence for item in failed_events] == list(
                    range(1, failed.event_sequence + 1),
                )
                await service.shutdown()
                service = None

                # The next worker is a real spawned process using the same
                # dedicated PostgreSQL database or Redis instance.
                context = multiprocessing.get_context("spawn")
                result_queue = context.Queue()
                child = context.Process(
                    target=_s01_recovery_process,
                    args=(
                        backend_kind,
                        backend.url,
                        owner,
                        task.id,
                        created.id,
                        ids["search_similar_projects"],
                        inputs,
                        str(root),
                        result_queue,
                    ),
                )
                child.start()
                await asyncio.to_thread(child.join, 120)
                assert not child.is_alive(), "S01 recovery worker exceeded 120 seconds."
                assert child.exitcode == 0
                child_result = result_queue.get(timeout=5)
                assert "error" not in child_result, child_result
                assert child_result["run_id"] == created.id
                assert child_result["status"] == "succeeded"
                assert child_result["artifact_format"] == "markdown"

                final = await backend.store.get_run(owner, created.id)
                assert final is not None and final.status.value == "succeeded"
                assert final.id == created.id
                assert final.source_template_version == e2.SCENARIO_VERSION
                assert len(final.artifacts) == 1
                final_nodes = {item.node_id: item for item in final.node_runs}
                for node_id, snapshot in retained.items():
                    assert final_nodes[node_id].model_dump(mode="json") == snapshot
                retry_node = final_nodes[ids["search_similar_projects"]]
                assert retry_node.attempt == 2
                assert [item.status for item in retry_node.attempt_history] == [
                    "failed",
                    "succeeded",
                ]
                assert final_nodes[ids["check_project_rules"]].attempt == 1
                assert final_nodes[ids["draft_initiation_recommendation"]].status.value == "succeeded"
                final_events = await backend.store.list_events(owner, created.id)
                assert [item.sequence for item in final_events] == list(
                    range(1, final.event_sequence + 1),
                )
                assert final_events[-1].type == "run.succeeded"
                artifact_bytes = executor._artifact_path(final.artifacts[0].path).read_bytes()
                artifact_text = r7._artifact_text(
                    artifact_bytes,
                    final.artifacts[0].format.value,
                )
                r7._assert_business_results(
                    e2.SCENARIO_ID,
                    final,
                    inputs,
                    artifact_text,
                )

                query_records = [
                    json.loads(line)
                    for line in (root / "knowledge-queries.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                similar_records = [
                    item
                    for item in query_records
                    if item["capability"] == "project.similar_search"
                ]
                rule_records = [
                    item
                    for item in query_records
                    if item["capability"] == "project.rule_check"
                ]
                assert [item["status"] for item in similar_records] == [
                    "failed",
                    "succeeded",
                ]
                assert [item["status"] for item in rule_records] == ["succeeded"]
                executor_calls = [
                    json.loads(line)
                    for line in (root / "executor-calls.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                assert sum(
                    item["node_id"] == ids["extract_project_profile"]
                    for item in executor_calls
                ) == 1
            finally:
                if child is not None and child.is_alive():
                    child.terminate()
                    await asyncio.to_thread(child.join, 5)
                if service is not None:
                    await service.shutdown()
                await backend.close()

    asyncio.run(scenario())
