# -*- coding: utf-8 -*-
"""Task AgentStepOutcome protocol and input-flow regression coverage."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from agentscope.exception import StructuredOutputError
from pydantic import ValidationError

from examples.agent_service.task._agentscope_executor import (
    AgentScopeTaskExecutor,
    _parse_agent_step_outcome,
)
from examples.agent_service.task._clarification import (
    EXECUTION_POLICY,
    EffectiveRequirement,
    EffectiveRequirements,
)
from examples.agent_service.task._errors import classify_node_error
from examples.agent_service.task._models import (
    AgentStepConfig,
    AgentStepOutcome,
    AgentStepOutcomeCandidate,
    CreateTaskRequest,
    ExecutionContext,
    RunStatus,
    TaskContext,
    TaskGraph,
    TaskInputReply,
    TaskNode,
    TaskRunRequest,
)
from examples.agent_service.task._output_validation import (
    validate_structured_result,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def _context(**updates) -> ExecutionContext:
    values = {
        "user_id": "owner",
        "task_id": "task",
        "run_id": "run",
        "task_revision": 1,
        "agent_id": "agent",
        "session_id": "session",
        "initial_input": "整理项目目标",
    }
    values.update(updates)
    return ExecutionContext(**values)


def _requirements(*fields) -> EffectiveRequirements:
    return EffectiveRequirements(
        effective_goal="整理项目目标",
        fields=list(fields),
    )


def _response(content):
    return SimpleNamespace(content=content, usage=None)


class AgentStepOutcomeProtocolTest(TestCase):
    def setUp(self):
        self.requirements = _requirements()
        self.context = _context()

    def _parse(self, payload):
        return _parse_agent_step_outcome(
            payload,
            requirements=self.requirements,
            context=self.context,
        )

    def test_succeeded_without_questions_is_valid_and_maps_to_task_envelope(self):
        outcome, protocol = self._parse(
            {"status": "succeeded", "output": '{"accepted": true}'}
        )

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.questions, [])
        self.assertEqual(protocol["status"], "valid")
        engine_result = outcome.model_dump(mode="json")
        engine_result["status"] = "completed"
        validation = validate_structured_result(
            {
                "type": "object",
                "properties": {"accepted": {"type": "boolean"}},
                "required": ["accepted"],
            },
            engine_result,
            agent_output_json=True,
        )
        self.assertTrue(validation.passed)
        self.assertEqual(validation.target, "agent_output_json")
        invalid = validate_structured_result(
            {
                "type": "object",
                "properties": {"accepted": {"type": "boolean"}},
                "required": ["accepted"],
            },
            {
                **engine_result,
                "output": '{"accepted": "yes"}',
            },
            agent_output_json=True,
        )
        self.assertFalse(invalid.passed)

    def test_needs_input_preserves_question_and_required_marker(self):
        outcome, protocol = self._parse(
            {
                "status": "needs_input",
                "reason": "缺少项目背景，请补充后继续。",
                "questions": [
                    {
                        "key": "project_brief",
                        "label": "请补充项目背景和目标",
                        "required": True,
                        "options": ["新建", "改造"],
                    }
                ],
            }
        )

        self.assertEqual(outcome.status, "needs_input")
        self.assertEqual(outcome.questions[0].key, "project_brief")
        self.assertTrue(outcome.questions[0].required)
        self.assertEqual(outcome.questions[0].options, ["新建", "改造"])
        self.assertEqual(protocol["status"], "valid")

    def test_failed_without_questions_stays_failed(self):
        outcome, protocol = self._parse(
            {"status": "failed", "reason": "接口调用失败，服务不可用。"}
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "接口调用失败，服务不可用。")
        self.assertEqual(protocol["status"], "valid")

    def test_failed_with_clear_information_request_is_repaired_once(self):
        raw = {
            "status": "failed",
            "reason": "缺少项目背景，需要用户补充信息。",
            "questions": [
                {
                    "key": "project_brief",
                    "label": "请提供项目背景和主要目标",
                    "required": True,
                }
            ],
        }

        outcome, protocol = self._parse(raw)

        self.assertEqual(outcome.status, "needs_input")
        self.assertEqual(outcome.questions[0].key, "project_brief")
        self.assertTrue(outcome.questions[0].required)
        self.assertEqual(protocol["status"], "repaired")
        self.assertEqual(protocol["repair"], "status_only")
        # The repair must still satisfy the original strict schema.
        self.assertEqual(
            AgentStepOutcome.model_validate(outcome.model_dump()).status,
            "needs_input",
        )

    def test_failed_with_hard_blocker_keeps_cause_and_is_not_input(self):
        for reason in (
            "外部工具调用失败：权限不足。",
            "审批未通过，必须保留人工审批。",
        ):
            with self.subTest(reason=reason):
                outcome, protocol = self._parse(
                    {
                        "status": "failed",
                        "reason": reason,
                        "questions": [
                            {
                                "key": "account",
                                "label": "请提供账户资料",
                                "required": True,
                            }
                        ],
                    }
                )

                self.assertEqual(outcome.status, "failed")
                self.assertIn(reason, outcome.reason)
                self.assertIn("结构化输出协议错误", outcome.reason)
                self.assertEqual(outcome.questions, [])
                self.assertEqual(protocol["status"], "rejected")

        detail = classify_node_error(
            SimpleNamespace(task_id="task", id="run"),
            TaskNode(
                name="整理项目目标",
                config=AgentStepConfig(prompt="整理项目目标"),
            ),
            {"agent_outcome_protocol": protocol},
            outcome.reason,
        )
        self.assertEqual(detail.code, "TASK_AGENT_OUTCOME_PROTOCOL_INVALID")

    def test_active_frozen_blocker_prevents_failed_question_repair(self):
        blocked = EffectiveRequirements(
            effective_goal="整理项目目标",
            fields=[
                EffectiveRequirement(
                    key="project_code",
                    label="项目编号",
                    status="unknown",
                    blocking=True,
                    blocking_reason="冻结契约要求提供项目编号。",
                    classification="required",
                )
            ],
        )
        outcome, protocol = _parse_agent_step_outcome(
            {
                "status": "failed",
                "reason": "缺少项目资料，请补充后继续。",
                "questions": [
                    {
                        "key": "project_brief",
                        "label": "请补充项目背景",
                        "required": True,
                    }
                ],
            },
            requirements=blocked,
            context=self.context,
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(protocol["status"], "rejected")

    def test_already_asked_question_is_not_repaired(self):
        context = _context(
            clarification_state={
                "asked_fields": [
                    {"key": "project_brief", "label": "项目背景"}
                ]
            }
        )
        outcome, protocol = _parse_agent_step_outcome(
            {
                "status": "failed",
                "reason": "缺少项目背景，请补充。",
                "questions": [
                    {
                        "key": "project_brief",
                        "label": "请补充项目背景",
                        "required": True,
                    }
                ],
            },
            requirements=self.requirements,
            context=context,
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(protocol["code"], "failed_with_questions_not_safe_to_repair")

    def test_succeeded_with_questions_is_rejected_not_silently_accepted(self):
        outcome, protocol = self._parse(
            {
                "status": "succeeded",
                "output": "看似完成的草稿",
                "questions": [{"key": "brief", "label": "请补充背景"}],
            }
        )

        self.assertEqual(outcome.status, "failed")
        self.assertNotEqual(outcome.output, "看似完成的草稿")
        self.assertIn("succeeded 不得携带 questions", outcome.reason)
        self.assertEqual(protocol["code"], "succeeded_with_questions")

    def test_malformed_questions_are_rejected(self):
        for question in (
            {"key": "brief", "required": True},
            "not an object",
            {"key": "brief", "label": "请补充背景", "required": "true"},
            {"key": "brief", "label": "请补充背景", "extra": "unknown field"},
        ):
            with self.subTest(question=question):
                outcome, protocol = self._parse(
                    {"status": "needs_input", "questions": [question]}
                )
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(protocol["code"], "invalid_questions")
                self.assertIn("结构化输出协议错误", outcome.reason)

    def test_invalid_outer_shape_is_rejected_with_bounded_diagnostic(self):
        outcome, protocol = self._parse(
            {
                "status": "failed",
                "reason": "bad",
                "questions": [],
                "raw_response": "MUST_NOT_BE_PERSISTED",
            }
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(protocol["code"], "candidate_shape_invalid")
        self.assertNotIn("MUST_NOT_BE_PERSISTED", outcome.reason)

    def test_existing_strict_validator_still_rejects_wire_mismatches(self):
        with self.assertRaises(ValidationError):
            AgentStepOutcome.model_validate(
                {
                    "status": "failed",
                    "reason": "缺少资料",
                    "questions": [
                        {"key": "brief", "label": "请补充项目背景"}
                    ],
                }
            )
        with self.assertRaises(ValidationError):
            AgentStepOutcome.model_validate(
                {"status": "succeeded", "output": "完成", "questions": [] , "extra": 1}
            )


class AgentStepOutcomeExecutorTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = AsyncMock()
        self.storage.get_session.return_value = SimpleNamespace(
            config=SimpleNamespace(chat_model_config=object())
        )
        self.storage.get_agent.return_value = SimpleNamespace(
            data=SimpleNamespace(system_prompt="助手", name="助手")
        )
        self.model = AsyncMock()
        self.executor = AgentScopeTaskExecutor(
            storage=self.storage,
            resource_access_service=AsyncMock(),
        )
        self.model_patch = patch(
            "agentscope.app._service._model.get_model",
            return_value=self.model,
        )
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.node = TaskNode(
            id="n1",
            name="整理项目主要目标",
            config=AgentStepConfig(prompt="整理项目主要目标"),
        )

    def _requirements_response(self):
        return _response({"effective_goal": "整理项目主要目标", "fields": []})

    async def _execute_with(self, outcome_payload):
        self.model.generate_structured_output.side_effect = [
            self._requirements_response(),
            _response(outcome_payload),
        ]
        return await self.executor.execute_node(
            node=self.node,
            previous_output="",
            context=_context(),
        )

    async def test_executor_maps_succeeded_to_existing_task_completed_status(self):
        result = await self._execute_with(
            {"status": "succeeded", "output": "项目目标草稿"}
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result["status"], "completed")
        self.assertEqual(
            result.metadata["agent_outcome_protocol"]["model_status"],
            "succeeded",
        )
        self.assertIs(
            self.model.generate_structured_output.call_args_list[1].kwargs[
                "structured_model"
            ],
            AgentStepOutcomeCandidate,
        )

    async def test_agent_needs_input_reaches_sop_and_dag_resume_flows(self):
        for engine in ("sop", "dag"):
            with self.subTest(engine=engine):
                self.model.generate_structured_output.reset_mock()
                question = {
                    "key": "project_brief",
                    "label": "请补充项目背景和主要目标",
                    "required": True,
                }
                model_outcome = {
                    "status": "needs_input",
                    "reason": "缺少项目背景，请补充后继续。",
                    "questions": [question],
                }
                if engine == "sop":
                    # Exercise the actual Run failure shape from this incident.
                    model_outcome["status"] = "failed"
                self.model.generate_structured_output.side_effect = [
                    self._requirements_response(),
                    _response(model_outcome),
                    self._requirements_response(),
                    _response({"status": "succeeded", "output": "目标草稿"}),
                    _response({"passed": True, "message": "结果符合要求。"}),
                ]
                store = TaskStore()
                service = TaskService(
                    store,
                    agentscope_executor=self.executor,
                    enable_dag_scheduler=True,
                )
                try:
                    task = await service.create_task(
                        "owner",
                        CreateTaskRequest(
                            goal="整理项目主要目标",
                            nodes=[self.node],
                            graph=TaskGraph() if engine == "dag" else None,
                            source_context=TaskContext(
                                agent_id="agent", session_id="session"
                            ),
                        ),
                    )
                    run = await service.create_run(
                        "owner",
                        task.id,
                        TaskRunRequest(input="整理项目主要目标"),
                    )
                    await service._active_runs[run.id]
                    parked = await store.get_run("owner", run.id)
                    self.assertEqual(parked.engine, engine)
                    self.assertEqual(parked.status, RunStatus.AWAITING_INPUT)
                    self.assertEqual(
                        parked.pending_input.questions[0].key,
                        "project_brief",
                    )
                    self.assertTrue(parked.pending_input.questions[0].required)
                    if engine == "sop":
                        self.assertEqual(
                            parked.node_runs[0].metadata[
                                "agent_outcome_protocol"
                            ]["status"],
                            "repaired",
                        )

                    await service.answer_run(
                        "owner",
                        run.id,
                        TaskInputReply(
                            request_id=parked.pending_input.id,
                            answers={
                                "project_brief": "新建一套客户管理项目"
                            },
                        ),
                    )
                    await service._active_runs[run.id]
                    resumed = await store.get_run("owner", run.id)
                    self.assertEqual(resumed.status, RunStatus.SUCCEEDED)
                    self.assertEqual(resumed.id, run.id)
                finally:
                    await service.shutdown()

    async def test_structured_output_error_stops_after_one_executor_call(self):
        self.model.generate_structured_output.side_effect = [
            self._requirements_response(),
            StructuredOutputError("SECRET_RAW_MODEL_RESPONSE"),
        ]

        result = await self.executor.execute_node(
            node=self.node,
            previous_output="",
            context=_context(),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(self.model.generate_structured_output.call_count, 2)
        self.assertEqual(
            result.metadata["agent_outcome_protocol"]["code"],
            "structured_output_unavailable",
        )
        self.assertNotIn("SECRET_RAW_MODEL_RESPONSE", result.reason)
        self.assertNotIn(
            "SECRET_RAW_MODEL_RESPONSE",
            str(result.metadata["agent_outcome_protocol"]),
        )

    async def test_sop_and_dag_agent_nodes_share_the_same_protocol_prompt(self):
        self.model.generate_structured_output.side_effect = [
            self._requirements_response(),
            _response({"status": "succeeded", "output": "SOP 结果"}),
            self._requirements_response(),
            _response({"status": "succeeded", "output": "DAG 结果"}),
        ]

        for run_id in ("sop-run", "dag-run"):
            result = await self.executor.execute_node(
                node=self.node,
                previous_output="",
                context=_context(run_id=run_id),
            )
            self.assertEqual(result.status, "completed")
            messages = self.model.generate_structured_output.call_args_list[-1].kwargs[
                "messages"
            ]
            self.assertIn(
                EXECUTION_POLICY,
                [message.get_text_content() for message in messages],
            )
            self.assertIs(
                self.model.generate_structured_output.call_args_list[-1].kwargs[
                    "structured_model"
                ],
                AgentStepOutcomeCandidate,
            )
