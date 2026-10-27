# -*- coding: utf-8 -*-
"""Input protocol at model, API, and durable storage boundaries."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
from fastapi import FastAPI
from pydantic import ValidationError

from agentscope.app.deps import get_current_user_id
from examples.agent_service.task._agentscope_executor import (
    AgentScopeTaskExecutor,
    _render_task_value,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._clarification import EffectiveRequirements
from examples.agent_service.task._models import (
    AgentStepConfig,
    AgentStepOutcome,
    CreateTaskRequest,
    ExecutionContext,
    InputStepConfig,
    TaskInputQuestion,
    TaskInputReply,
    TaskContext,
    TaskNode,
    TaskPlanDraft,
    TaskPlanNode,
    TaskRunRequest,
    StepVerdict,
    ToolStepConfig,
)
from examples.agent_service.task._plan_validation import validate_task_plan
from examples.agent_service.task._redis_store import RedisTaskStore
from examples.agent_service.task._router import task_router
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def _context() -> ExecutionContext:
    return ExecutionContext(
        user_id="owner",
        task_id="task",
        run_id="run",
        task_revision=1,
        agent_id="agent",
        session_id="session",
        initial_input="从上海出发",
        user_answers=[
            TaskInputReply(
                request_id="question",
                answers={"people": "2", "destination": "北京"},
            ),
        ],
    )


class TaskInputModelTest(TestCase):
    def test_outcome_cannot_confuse_a_question_with_completion(self) -> None:
        for payload in (
            {"status": "succeeded", "output": ""},
            {"status": "needs_input", "questions": []},
            {
                "status": "succeeded",
                "output": "done",
                "questions": [{"key": "date", "label": "日期？"}],
            },
            {
                "status": "failed",
                "reason": "实际执行失败",
                "questions": [{"key": "date", "label": "日期？"}],
            },
        ):
            with self.assertRaises(ValidationError):
                AgentStepOutcome.model_validate(payload)
        with self.assertRaises(ValidationError):
            InputStepConfig(
                questions=[TaskInputQuestion(key="date", label="日期？")] * 2
            )

    def test_bindings_preserve_types_and_do_not_reinterpret_user_data(
        self,
    ) -> None:
        context = _context()
        arguments = _render_task_value(
            {"count": "{{answers.people}}", "city": "{{answers.destination}}"},
            "上一步",
            context,
            typed=True,
        )
        self.assertEqual(arguments, {"count": 2, "city": "北京"})
        self.assertEqual(
            _render_task_value(
                "{{user_input}} / {previous_output}", "上一步", context
            ),
            "从上海出发 / 上一步",
        )
        context.initial_input = "{{answers.missing}}"
        self.assertEqual(
            _render_task_value("{{user_input}}", "", context),
            context.initial_input,
        )
        with self.assertRaises(ValueError):
            _render_task_value("{{answers.missing}}", "", context)

    def test_planner_accepts_typed_future_input_and_rejects_unknown_sources(
        self,
    ) -> None:
        draft = TaskPlanDraft(
            suggested_title="出差",
            intent="规划出差",
            nodes=[
                TaskPlanNode(
                    name="人数",
                    type="input",
                    config=InputStepConfig(
                        questions=[
                            TaskInputQuestion(key="people", label="人数？")
                        ]
                    ),
                ),
                TaskPlanNode(
                    name="查询",
                    type="tool",
                    config=ToolStepConfig(
                        tool_name="lookup",
                        arguments={"people": "{{answers.people}}"},
                    ),
                ),
                TaskPlanNode(name="交付", prompt="生成方案"),
            ],
        )
        schemas = [
            {
                "name": "lookup",
                "input_schema": {
                    "type": "object",
                    "required": ["people"],
                    "properties": {"people": {"type": "integer"}},
                },
            }
        ]
        self.assertEqual(validate_task_plan(draft, schemas), [])
        draft.nodes[1].config.arguments["people"] = "{{answers.unknown}}"
        self.assertTrue(validate_task_plan(draft, schemas))


class TaskAgentProtocolTest(IsolatedAsyncioTestCase):
    async def test_frozen_required_input_is_requested_before_model_call(self) -> None:
        storage = AsyncMock()
        storage.get_session.return_value = SimpleNamespace(
            config=SimpleNamespace(chat_model_config=object())
        )
        storage.get_agent.return_value = SimpleNamespace(
            data=SimpleNamespace(system_prompt="助手", name="助手")
        )
        model = AsyncMock()
        model.generate_structured_output.return_value = SimpleNamespace(
            content={
                "effective_goal": "规划出差",
                "fields": [
                    {
                        "key": "date",
                        "label": "出差日期",
                        "status": "unknown",
                        "blocking": True,
                        "blocking_reason": "筛选车次需要确定日期。",
                    }
                ],
            },
            usage=None,
        )
        executor = AgentScopeTaskExecutor(
            storage=storage, resource_access_service=AsyncMock()
        )
        node = TaskNode(
            name="收集需求",
            config=AgentStepConfig(
                prompt="需求：{{user_input}}；人数：{{answers.people}}"
            ),
        )
        with patch(
            "agentscope.app._service._model.get_model", return_value=model
        ):
            result = await executor.execute_node(
                node=node,
                previous_output="",
                context=_context().model_copy(
                    update={
                        "knowledge_context": "资料中的 {{unknown}} 是原文",
                        "information_contract": {
                            "requirements": [
                                {
                                    "key": "date",
                                    "label": "出差日期",
                                    "classification": "required",
                                    "reason": "筛选车次需要确定日期。",
                                    "condition": None,
                                    "condition_state": "active",
                                    "source_path": "test.contract.date",
                                }
                            ]
                        },
                    }
                ),
            )
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.questions[0].key, "date")
        self.assertEqual(model.generate_structured_output.call_count, 0)
        stored_prompt = storage.upsert_message.call_args_list[0].args[-1]
        prompt = stored_prompt.get_text_content()
        self.assertIn("从上海出发", prompt)
        self.assertNotIn("{{user_input}}", stored_prompt.get_text_content())
        self.assertIn("当前有效需求", stored_prompt.get_text_content())
        self.assertIn("资料中的 {{unknown}} 是原文", prompt)


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_node(self, **kwargs) -> StepExecutionResult:
        del kwargs
        self.calls += 1
        return StepExecutionResult(text="真实交付")

    async def verify_node(self, **kwargs) -> StepVerdict:
        del kwargs
        return StepVerdict(passed=True, message="input-flow fixture accepted")


class TaskInputAPITest(IsolatedAsyncioTestCase):
    async def test_api_rejects_stale_answers_and_streams_waiting_then_completion(
        self,
    ) -> None:
        executor = _Executor()
        service = TaskService(TaskStore(), preview_executor=executor)
        app = FastAPI()
        app.state.task_service = service
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.include_router(task_router)
        try:
            task = await service.create_task(
                "owner",
                CreateTaskRequest(
                    goal="出差",
                    nodes=[
                        TaskNode(
                            name="输入",
                            type="input",
                            config=InputStepConfig(
                                questions=[
                                    TaskInputQuestion(
                                        key="date", label="日期？"
                                    )
                                ]
                            ),
                        ),
                        TaskNode(
                            name="交付", config=AgentStepConfig(prompt="交付")
                        ),
                    ],
                ),
            )
            run = await service.create_run("owner", task.id, TaskRunRequest())
            await service._active_runs[run.id]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                state = (await client.get(f"/tasks/runs/{run.id}")).json()
                self.assertEqual(state["status"], "awaiting_input")
                replay = await client.get(f"/tasks/runs/{run.id}/events")
                self.assertIn("input.requested", replay.text)
                stale = await client.post(
                    f"/tasks/runs/{run.id}/input",
                    json={"request_id": "stale", "answers": {"date": "明天"}},
                )
                self.assertEqual(stale.status_code, 409)
                reply = {
                    "request_id": state["pending_input"]["id"],
                    "answers": {"date": "明天"},
                }
                accepted = await client.post(
                    f"/tasks/runs/{run.id}/input", json=reply
                )
                self.assertEqual(accepted.status_code, 202)
                self.assertEqual(accepted.json()["id"], run.id)
                active = service._active_runs.get(run.id)
                if active:
                    await active
                completed = await client.get(f"/tasks/runs/{run.id}")
                self.assertEqual(completed.json()["status"], "succeeded")
                duplicate = await client.post(
                    f"/tasks/runs/{run.id}/input", json=reply
                )
                self.assertEqual(duplicate.status_code, 202)
                self.assertEqual(executor.calls, 1)
                replay = await client.get(f"/tasks/runs/{run.id}/events")
                self.assertIn("input.answered", replay.text)
                self.assertIn("run.succeeded", replay.text)
                app.dependency_overrides[get_current_user_id] = lambda: "other"
                forbidden = await client.post(
                    f"/tasks/runs/{run.id}/input", json=reply
                )
                self.assertEqual(forbidden.status_code, 404)
        finally:
            await service.shutdown()

    async def test_agent_dynamic_needs_input_is_returned_by_api_and_resumes_same_run(
        self,
    ) -> None:
        storage = AsyncMock()
        storage.get_session.return_value = SimpleNamespace(
            config=SimpleNamespace(chat_model_config=object())
        )
        storage.get_agent.return_value = SimpleNamespace(
            data=SimpleNamespace(system_prompt="Task test agent", name="助手")
        )
        model = AsyncMock()
        model.generate_structured_output.side_effect = [
            SimpleNamespace(
                content={"effective_goal": "整理项目目标", "fields": []},
                usage=None,
            ),
            SimpleNamespace(
                content={
                    "status": "needs_input",
                    "reason": "缺少项目背景原文，无法据此提取真实目标。",
                    "questions": [
                        {
                            "key": "project_brief",
                            "label": "请粘贴项目背景原文",
                        }
                    ],
                },
                usage=None,
            ),
            SimpleNamespace(
                content={
                    "effective_goal": "整理项目目标",
                    "fields": [
                        {
                            "key": "project_brief",
                            "label": "项目背景原文",
                            "status": "unknown",
                            "blocking": False,
                            "aliases": [],
                        }
                    ],
                },
                usage=None,
            ),
            SimpleNamespace(
                content={"status": "succeeded", "output": "项目目标草稿"},
                usage=None,
            ),
            SimpleNamespace(
                content={"passed": True, "message": "交付符合节点要求。"},
                usage=None,
            ),
        ]
        executor = AgentScopeTaskExecutor(
            storage=storage, resource_access_service=AsyncMock()
        )
        model_patch = patch(
            "agentscope.app._service._model.get_model", return_value=model
        )
        model_patch.start()
        service = TaskService(TaskStore(), agentscope_executor=executor)
        app = FastAPI()
        app.state.task_service = service
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.include_router(task_router)
        try:
            task = await service.create_task(
                "owner",
                CreateTaskRequest(
                    goal="整理项目主要目标",
                    nodes=[
                        TaskNode(
                            id="n1",
                            name="整理项目主要目标",
                            config=AgentStepConfig(prompt="依据真实背景整理项目目标"),
                        )
                    ],
                    source_context=TaskContext(
                        agent_id="agent", session_id="session"
                    ),
                ),
            )
            run = await service.create_run(
                "owner",
                task.id,
                TaskRunRequest(input="整理项目目标"),
            )
            await service._active_runs[run.id]

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                waiting = await client.get(f"/tasks/runs/{run.id}")
                self.assertEqual(waiting.status_code, 200)
                waiting_data = waiting.json()
                self.assertEqual(waiting_data["status"], "awaiting_input")
                self.assertEqual(
                    waiting_data["pending_input"]["questions"][0]["key"],
                    "project_brief",
                )
                self.assertEqual(
                    waiting_data["pending_input"]["questions"][0]["label"],
                    "请粘贴项目背景原文",
                )
                self.assertIn(
                    "input.requested",
                    (await client.get(f"/tasks/runs/{run.id}/events")).text,
                )

                accepted = await client.post(
                    f"/tasks/runs/{run.id}/input",
                    json={
                        "request_id": waiting_data["pending_input"]["id"],
                        "answers": {
                            "project_brief": "原始背景：新建一套客户管理项目"
                        },
                    },
                )
                self.assertEqual(accepted.status_code, 202)
                self.assertEqual(accepted.json()["id"], run.id)
                await service._active_runs[run.id]
                completed = await client.get(f"/tasks/runs/{run.id}")
                self.assertEqual(completed.json()["status"], "succeeded")
                self.assertEqual(completed.json()["id"], run.id)

            requirement_payload = model.generate_structured_output.call_args_list[
                2
            ].kwargs["messages"][-1].get_text_content()
            self.assertIn("原始背景：新建一套客户管理项目", requirement_payload)
            generation_prompt = model.generate_structured_output.call_args_list[
                3
            ].kwargs["messages"][-1].get_text_content()
            self.assertIn("整理项目目标", generation_prompt)
            self.assertIn("原始背景：新建一套客户管理项目", generation_prompt)
            stored = await service.get_run("owner", run.id)
            self.assertEqual(stored.id, run.id)
            self.assertEqual(
                stored.sop_state["steps"][0]["answers"][0]["answers"][
                    "project_brief"
                ],
                "原始背景：新建一套客户管理项目",
            )
        finally:
            model_patch.stop()
            app.dependency_overrides.pop(get_current_user_id, None)
            await service.shutdown()

    async def test_redis_round_trip_resumes_after_rebuilding_service(
        self,
    ) -> None:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RedisTaskStore(redis)
        executor = _Executor()
        original = TaskService(store, preview_executor=executor)
        resumed = TaskService(store, preview_executor=executor)
        try:
            task = await original.create_task(
                "owner",
                CreateTaskRequest(
                    goal="出差",
                    nodes=[
                        TaskNode(
                            name="输入",
                            type="input",
                            config=InputStepConfig(
                                questions=[
                                    TaskInputQuestion(
                                        key="date", label="日期？"
                                    )
                                ]
                            ),
                        ),
                        TaskNode(
                            name="交付", config=AgentStepConfig(prompt="交付")
                        ),
                    ],
                ),
            )
            run = await original.create_run("owner", task.id, TaskRunRequest())
            await original._active_runs[run.id]
            parked = await store.get_run("owner", run.id)
            await original.shutdown()
            await resumed.answer_run(
                "owner",
                run.id,
                TaskInputReply(
                    request_id=parked.pending_input.id,
                    answers={"date": "明天"},
                ),
            )
            await resumed._active_runs[run.id]
            completed = await store.get_run("owner", run.id)
            self.assertEqual(completed.status.value, "succeeded")
            self.assertEqual(executor.calls, 1)
            self.assertEqual(
                completed.sop_state["steps"][0]["answers"][0]["answers"],
                {"date": "明天"},
            )
        finally:
            await original.shutdown()
            await resumed.shutdown()
            await redis.aclose()
