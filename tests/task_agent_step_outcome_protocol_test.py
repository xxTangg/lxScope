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
from examples.agent_service.task._clarification import EXECUTION_POLICY
from examples.agent_service.task._errors import classify_node_error
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    AgentStepOutcome,
    CreateTaskRequest,
    ExecutionContext,
    RunStatus,
    TaskContext,
    TaskGraph,
    TaskInputQuestion,
    TaskInputReply,
    TaskInformationRequirement,
    TaskNode,
    TaskRunRequest,
    StepVerdict,
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


def _response(content):
    return SimpleNamespace(content=content, usage=None)


def _information_contract(key, classification="required"):
    return {
        "contract_id": "task.frozen-information-contract.v1",
        "requirements": [
            {
                "key": key,
                "label": "项目背景和目标",
                "classification": classification,
                "reason": "节点契约声明的必要输入。"
                if classification == "required"
                else "",
                "condition": None,
                "condition_state": "active",
                "source_path": f"run.nodes[0].information_requirements.{key}",
            }
        ],
    }


class AgentStepOutcomeProtocolTest(TestCase):
    def setUp(self):
        self.context = _context()

    def _parse(self, payload):
        return _parse_agent_step_outcome(
            payload,
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

    def test_failed_with_questions_is_rejected_without_status_rewrite(self):
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

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.questions, [])
        self.assertEqual(protocol["status"], "rejected")
        self.assertEqual(protocol["code"], "outcome_schema_invalid")
        self.assertEqual(
            protocol["validation"]["protocol_issue"],
            "failed_with_questions",
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
                self.assertNotIn(reason, outcome.reason)
                self.assertIn("结构化输出协议错误", outcome.reason)
                self.assertEqual(outcome.questions, [])
                self.assertEqual(protocol["status"], "rejected")
                self.assertEqual(
                    protocol["validation"]["protocol_issue"],
                    "failed_with_questions",
                )

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
        self.assertIn("结构化输出协议错误", outcome.reason)
        self.assertEqual(protocol["code"], "outcome_schema_invalid")
        self.assertEqual(
            protocol["validation"]["protocol_issue"],
            "succeeded_with_questions",
        )

    def test_malformed_questions_are_rejected(self):
        cases = (
            (
                {"key": "brief", "required": True},
                "questions.0.label",
                "missing",
            ),
            ("not an object", "questions.0", "model_type"),
            (
                {
                    "key": "brief",
                    "label": "补充背景",
                    "unexpected_sensitive_name": "do not echo",
                },
                "questions.0.<extra>",
                "extra_forbidden",
            ),
            (
                {"key": "brief", "label": "补充背景", "options": [7]},
                "questions.0.options.0",
                "string_type",
            ),
            (
                {"question": "unexpected container shape"},
                "questions",
                "list_type",
            ),
        )
        for question, expected_path, expected_type in cases:
            with self.subTest(expected_path=expected_path):
                outcome, protocol = self._parse(
                    {
                        "status": "needs_input",
                        "questions": (
                            question
                            if expected_path == "questions"
                            else [question]
                        ),
                    }
                )
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(protocol["code"], "outcome_schema_invalid")
                self.assertIn(
                    expected_path,
                    protocol["validation"]["validation_paths"],
                )
                self.assertIn(
                    expected_type,
                    [
                        item["error_type"]
                        for item in protocol["validation"]["field_types"]
                    ],
                )

    def test_canonical_question_boolean_is_repaired_and_revalidated_once(self):
        outcome, protocol = self._parse(
            {
                "status": "needs_input",
                "questions": [
                    {
                        "key": "brief",
                        "label": "补充背景",
                        "required": "true",
                    }
                ],
            }
        )

        self.assertEqual(outcome.status, "needs_input")
        self.assertIs(outcome.questions[0].required, True)
        self.assertEqual(protocol["status"], "valid")
        self.assertEqual(
            protocol["format_repair"],
            {
                "kind": "canonical_question_boolean",
                "paths": ["questions.0.required"],
            },
        )

    def test_historical_six_question_shape_keeps_safe_diagnostics(self):
        # The incident persisted a count of six but no response body; the last
        # item's malformed value below is a synthetic diagnostic fixture.
        questions = [
            {"key": f"field_{index}", "label": f"字段 {index}"}
            for index in range(5)
        ]
        questions.append(
            {"key": "field_5", "label": "字段 5", "required": "yes"}
        )

        outcome, protocol = self._parse(
            {"status": "needs_input", "questions": questions}
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(protocol["question_count"], 6)
        self.assertIn(
            "questions.5.required",
            protocol["validation"]["validation_paths"],
        )
        self.assertNotIn("yes", str(protocol))

    def test_validation_logs_paths_and_types_without_task_content(self):
        payload = {
            "status": "needs_input",
            "reason": "PRIVATE_REASON_MUST_NOT_BE_LOGGED",
            "questions": [
                {
                    "label": "PRIVATE_LABEL_MUST_NOT_BE_LOGGED",
                    "required": "true",
                }
            ],
        }

        with self.assertLogs(
            "examples.agent_service.task._agentscope_executor", level="WARNING"
        ) as captured:
            outcome, protocol = self._parse(payload)

        log_text = "\n".join(captured.output)
        self.assertIn("questions.0.key", log_text)
        self.assertIn("questions.0.required", log_text)
        self.assertIn('"value_type":"string"', log_text)
        self.assertNotIn("PRIVATE_REASON_MUST_NOT_BE_LOGGED", log_text)
        self.assertNotIn("PRIVATE_LABEL_MUST_NOT_BE_LOGGED", log_text)
        self.assertIn("questions.0.key", protocol["validation"]["missing_fields"])
        self.assertEqual(outcome.questions, [])

    def test_agent_schema_is_the_question_schema_and_forbids_extra_fields(self):
        schema = AgentStepOutcome.model_json_schema()
        question_schema = schema["$defs"]["TaskInputQuestion"]

        self.assertEqual(
            schema["properties"]["questions"]["items"]["$ref"],
            "#/$defs/TaskInputQuestion",
        )
        self.assertFalse(question_schema["additionalProperties"])
        self.assertEqual(
            question_schema["properties"]["required"]["type"], "boolean"
        )
        self.assertEqual(
            question_schema["properties"]["options"]["items"]["type"],
            "string",
        )

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
        self.assertEqual(protocol["code"], "outcome_schema_invalid")
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
            AgentStepOutcome,
        )

    async def test_agent_needs_input_reaches_sop_and_dag_resume_flows(self):
        for engine in ("sop", "dag"):
            with self.subTest(engine=engine):
                self.model.generate_structured_output.reset_mock()
                node = TaskNode(
                    id="n1",
                    name="整理项目主要目标",
                    config=AgentStepConfig(prompt="整理项目主要目标"),
                    information_requirements=[
                        TaskInformationRequirement(
                            key="project_brief",
                            label="项目背景和主要目标",
                            classification="required",
                            reason="当前节点契约明确要求提供项目背景。",
                        )
                    ],
                )
                self.model.generate_structured_output.side_effect = [
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
                            nodes=[node],
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
                        parked.node_runs[0].metadata[
                            "agent_outcome_protocol"
                        ]["status"],
                        "task_policy",
                    )
                    # The frozen missing-field check parks the run before the
                    # first model call; it does not depend on model wording.
                    self.assertEqual(
                        self.model.generate_structured_output.call_count, 0
                    )
                    self.assertEqual(
                        parked.pending_input.questions[0].key,
                        "project_brief",
                    )
                    self.assertTrue(parked.pending_input.questions[0].required)
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
                    self.assertEqual(
                        resumed.node_runs[0].attempt_history[-1].metadata[
                            "agent_outcome_protocol"
                        ]["model_status"],
                        "succeeded",
                    )
                finally:
                    await service.shutdown()

    async def test_uncontracted_needs_input_resumes_same_sop_and_dag_run(self):
        for engine in ("sop", "dag"):
            with self.subTest(engine=engine):
                self.model.generate_structured_output.reset_mock()
                self.model.generate_structured_output.side_effect = [
                    self._requirements_response(),
                    _response(
                        {
                            "status": "needs_input",
                            "reason": "缺少项目背景原文，无法提取真实目标。",
                            "questions": [
                                {
                                    "key": "project_brief",
                                    "label": "请粘贴项目背景原文",
                                }
                            ],
                        }
                    ),
                    _response(
                        {
                            "effective_goal": "整理项目主要目标",
                            "fields": [
                                {
                                    "key": "project_brief",
                                    "label": "项目背景原文",
                                    "status": "unknown",
                                    "blocking": False,
                                    "aliases": [],
                                }
                            ],
                        }
                    ),
                    _response(
                        {"status": "succeeded", "output": "项目目标草稿"}
                    ),
                    _response({"passed": True, "message": "内容符合节点要求。"}),
                ]
                service = TaskService(
                    TaskStore(),
                    agentscope_executor=self.executor,
                    enable_dag_scheduler=True,
                )
                try:
                    task = await service.create_task(
                        "owner",
                        CreateTaskRequest(
                            goal="整理项目主要目标",
                            nodes=[
                                TaskNode(
                                    id="n1",
                                    name="整理项目主要目标",
                                    config=AgentStepConfig(
                                        prompt="依据真实背景整理项目目标"
                                    ),
                                )
                            ],
                            graph=TaskGraph() if engine == "dag" else None,
                            source_context=TaskContext(
                                agent_id="agent", session_id="session"
                            ),
                        ),
                    )
                    run = await service.create_run(
                        "owner",
                        task.id,
                        TaskRunRequest(input="整理项目目标的操作指令"),
                    )
                    await service._active_runs[run.id]
                    parked = await service.get_run("owner", run.id)
                    self.assertEqual(parked.engine, engine)
                    self.assertEqual(parked.status, RunStatus.AWAITING_INPUT)
                    self.assertEqual(
                        parked.node_runs[0].metadata[
                            "agent_outcome_protocol"
                        ]["code"],
                        "agent_needs_input_accepted",
                    )
                    self.assertEqual(
                        parked.pending_input.questions[0].key,
                        "project_brief",
                    )
                    self.assertEqual(
                        self.model.generate_structured_output.call_count, 2
                    )

                    await service.answer_run(
                        "owner",
                        run.id,
                        TaskInputReply(
                            request_id=parked.pending_input.id,
                            answers={
                                "project_brief": "原始背景：新建客户管理项目"
                            },
                        ),
                    )
                    await service._active_runs[run.id]
                    resumed = await service.get_run("owner", run.id)
                    self.assertEqual(resumed.id, run.id)
                    self.assertEqual(resumed.status, RunStatus.SUCCEEDED)
                    self.assertEqual(
                        resumed.node_runs[0].attempt_history[-1].metadata[
                            "agent_outcome_protocol"
                        ]["model_status"],
                        "succeeded",
                    )
                    requirement_payload = (
                        self.model.generate_structured_output.call_args_list[
                            2
                        ].kwargs["messages"][-1].get_text_content()
                    )
                    self.assertIn(
                        "原始背景：新建客户管理项目", requirement_payload
                    )
                    generation_prompt = (
                        self.model.generate_structured_output.call_args_list[
                            3
                        ].kwargs["messages"][-1].get_text_content()
                    )
                    self.assertIn("整理项目目标的操作指令", generation_prompt)
                    self.assertIn(
                        "原始背景：新建客户管理项目", generation_prompt
                    )
                finally:
                    await service.shutdown()

    async def test_dag_input_pauses_after_ready_batch_and_keeps_independent_success(
        self,
    ):
        class _IndependentBranches:
            def __init__(self):
                self.waiting_calls = 0
                self.independent_calls = 0

            async def execute_node(self, *, node, **kwargs):
                del kwargs
                if node.id == "n-wait":
                    self.waiting_calls += 1
                    if self.waiting_calls == 1:
                        return StepExecutionResult(
                            text="",
                            status="needs_input",
                            reason="缺少原始项目背景。",
                            questions=[
                                TaskInputQuestion(
                                    key="project_brief",
                                    label="请粘贴项目背景原文",
                                )
                            ],
                        )
                    return StepExecutionResult(text="项目目标已整理")
                self.independent_calls += 1
                return StepExecutionResult(text="独立分支已完成")

            async def verify_node(self, **kwargs):
                del kwargs
                return StepVerdict(passed=True, message="分支结果有效")

        executor = _IndependentBranches()
        service = TaskService(
            TaskStore(),
            agentscope_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            task = await service.create_task(
                "owner",
                CreateTaskRequest(
                    goal="并行整理项目背景与独立资料",
                    nodes=[
                        TaskNode(
                            id="n-wait",
                            name="整理项目目标",
                            config=AgentStepConfig(prompt="根据原始背景整理项目目标"),
                        ),
                        TaskNode(
                            id="n-independent",
                            name="整理独立资料",
                            config=AgentStepConfig(prompt="整理已绑定的独立资料"),
                        ),
                    ],
                    graph=TaskGraph(max_concurrency=2),
                    source_context=TaskContext(
                        agent_id="agent", session_id="session"
                    ),
                ),
            )
            run = await service.create_run(
                "owner", task.id, TaskRunRequest(input="整理项目目标")
            )
            await service._active_runs[run.id]
            parked = await service.get_run("owner", run.id)
            self.assertEqual(
                parked.status,
                RunStatus.AWAITING_INPUT,
                parked.error,
            )
            nodes = {item.node_id: item for item in parked.node_runs}
            self.assertEqual(nodes["n-wait"].status.value, "awaiting_input")
            self.assertEqual(nodes["n-independent"].status.value, "succeeded")
            self.assertEqual(executor.independent_calls, 1)

            await service.answer_run(
                "owner",
                run.id,
                TaskInputReply(
                    request_id=parked.pending_input.id,
                    answers={"project_brief": "真实项目背景"},
                ),
            )
            await service._active_runs[run.id]
            completed = await service.get_run("owner", run.id)
            self.assertEqual(completed.id, run.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            nodes = {item.node_id: item for item in completed.node_runs}
            self.assertEqual(nodes["n-independent"].attempt, 1)
            self.assertEqual(executor.independent_calls, 1)
            self.assertEqual(executor.waiting_calls, 2)
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
                AgentStepOutcome,
            )

    async def test_frozen_required_input_satisfied_does_not_ask_again(self):
        self.model.generate_structured_output.side_effect = [
            self._requirements_response(),
            _response({"status": "succeeded", "output": "已整理项目目标"}),
        ]

        result = await self.executor.execute_node(
            node=self.node,
            previous_output="",
            context=_context(
                information_contract=_information_contract("project_brief"),
                user_answers=[
                    TaskInputReply(
                        request_id="previous-request",
                        answers={"project_brief": "既有项目背景"},
                    )
                ],
            ),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(self.model.generate_structured_output.call_count, 2)
        self.assertEqual(result.questions, [])

    async def test_missing_optional_input_does_not_block_agent_output(self):
        self.model.generate_structured_output.side_effect = [
            self._requirements_response(),
            _response({"status": "succeeded", "output": "可供复核的草稿"}),
        ]

        result = await self.executor.execute_node(
            node=self.node,
            previous_output="",
            context=_context(
                information_contract=_information_contract(
                    "optional_material", classification="optional"
                )
            ),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(self.model.generate_structured_output.call_count, 2)
        self.assertEqual(result.questions, [])

    async def test_repeated_invalid_model_output_is_not_retried_unboundedly(self):
        try:
            AgentStepOutcome.model_validate(
                {
                    "status": "failed",
                    "reason": "PRIVATE_FAILURE_DETAIL",
                    "questions": [
                        {"key": "brief", "label": "PRIVATE_QUESTION"}
                    ],
                }
            )
        except ValidationError as cause:
            invalid_response = StructuredOutputError("invalid structured output")
            invalid_response.__cause__ = cause
        else:  # pragma: no cover - fixture must remain schema-invalid
            self.fail("The malformed protocol fixture unexpectedly validated")
        self.model.generate_structured_output.side_effect = [
            self._requirements_response(),
            invalid_response,
            invalid_response,
        ]

        with self.assertLogs(
            "examples.agent_service.task._agentscope_executor", level="WARNING"
        ) as captured:
            result = await self.executor.execute_node(
                node=self.node,
                previous_output="",
                context=_context(),
            )

        self.assertEqual(self.model.generate_structured_output.call_count, 2)
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.metadata["agent_outcome_protocol"]["code"],
            "outcome_schema_invalid",
        )
        self.assertIn("failed_with_questions", "\n".join(captured.output))
        self.assertNotIn("PRIVATE_FAILURE_DETAIL", "\n".join(captured.output))
        self.assertNotIn("PRIVATE_QUESTION", "\n".join(captured.output))
