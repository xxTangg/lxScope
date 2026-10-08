# -*- coding: utf-8 -*-
"""Regression cases for repeated collection, optional inputs and resumption."""

import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from examples.agent_service.task._agentscope_executor import (
    AgentScopeTaskExecutor,
)
from examples.agent_service.task._clarification import (
    EffectiveRequirements,
    plan_clarification,
    reconcile_requirements,
)
from examples.agent_service.task._models import (
    AgentStepConfig,
    AgentStepOutcome,
    CreateTaskRequest,
    ExecutionContext,
    RunStatus,
    StepVerdict,
    TaskContext,
    TaskInputReply,
    TaskNode,
    TaskRunRecord,
    TaskRunRequest,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def _requirements(*fields):
    return {
        "effective_goal": "查询雄安到北京的高铁二等座，当天往返",
        "fields": list(fields),
    }


def _field(key, value="", *, blocking=False, status=None, aliases=()):
    return {
        "key": key,
        "label": key,
        "value": value,
        "status": status or ("provided" if value else "unknown"),
        "blocking": blocking,
        "blocking_reason": "筛选交通候选需要此参数" if blocking else "",
        "aliases": list(aliases),
    }


def _context(**updates):
    return ExecutionContext(
        user_id="owner",
        task_id="task",
        run_id="run",
        task_revision=1,
        agent_id="agent",
        session_id="session",
        initial_input="制定一次出差规划助手",
        **updates,
    )


def _response(content, *, usage=None):
    return SimpleNamespace(content=content, usage=usage)


class ClarificationPolicyTest(TestCase):
    def test_composite_answer_aliases_do_not_fuse_distinct_facts(self):
        previous = _requirements(
            _field("origin", "雄安", aliases=["origin_destination"]),
            _field("destination", "北京", aliases=["origin_destination"]),
            _field("budget", "二等座"),
        )
        requirements = EffectiveRequirements.model_validate(
            _requirements(
                _field("origin", "雄安", aliases=["origin_destination"]),
                _field("destination", "北京", aliases=["origin_destination"]),
                _field("seat", "二等座", aliases=["budget"]),
                _field("budget"),
            )
        )
        merged = reconcile_requirements(requirements, previous)
        self.assertEqual(
            [field.key for field in merged.fields],
            ["origin", "destination", "seat", "budget"],
        )

    def test_preferences_and_inapplicable_fields_cannot_block(self):
        requirements = EffectiveRequirements.model_validate(
            _requirements(
                _field("departure", status="no_preference", blocking=True),
                _field("hotel", status="not_applicable", blocking=True),
                _field("date", "2026-10-09", blocking=True),
                _field("budget"),
            )
        )
        self.assertEqual(requirements.blockers, [])

    def test_new_alias_and_fullwidth_label_do_not_reopen_same_question(self):
        previous = _requirements(_field("travel_dates", blocking=True))
        new = EffectiveRequirements.model_validate(
            _requirements(
                _field("date", blocking=True, aliases=["travel_dates"])
            )
        )
        merged = reconcile_requirements(new, previous)
        self.assertEqual(merged.fields[0].key, "travel_dates")
        questions, stop = plan_clarification(
            merged,
            _context(
                clarification_state={
                    "asked_fields": [{"key": "travel_dates", "label": "日期"}]
                }
            ),
            max_rounds=3,
            max_questions=3,
        )
        self.assertEqual(questions, [])
        self.assertEqual(stop, "repeated_blockers")

        new.fields[0].label = "日期？"
        questions, stop = plan_clarification(
            new,
            _context(
                clarification_state={
                    "asked_fields": [{"key": "other", "label": "日期?"}]
                }
            ),
            max_rounds=3,
            max_questions=3,
        )
        self.assertEqual(stop, "repeated_blockers")

    def test_answered_field_is_not_asked_again_even_for_legacy_run(self):
        requirements = EffectiveRequirements.model_validate(
            _requirements(_field("date", blocking=True, aliases=["dates"]))
        )
        questions, stop = plan_clarification(
            requirements,
            _context(
                user_answers=[
                    TaskInputReply(request_id="old", answers={"dates": "未知"})
                ]
            ),
            max_rounds=3,
            max_questions=3,
        )
        self.assertEqual(questions, [])
        self.assertEqual(stop, "repeated_blockers")

    def test_necessary_new_questions_are_bounded_and_optional_ones_omitted(
        self,
    ):
        requirements = EffectiveRequirements.model_validate(
            _requirements(
                *[
                    _field(f"required_{index}", blocking=True)
                    for index in range(5)
                ],
                _field("optional"),
            )
        )
        questions, stop = plan_clarification(
            requirements, _context(), max_rounds=3, max_questions=3
        )
        self.assertEqual(stop, "")
        self.assertEqual(len(questions), 3)
        self.assertTrue(all(q.required for q in questions))
        self.assertNotIn("optional", [q.key for q in questions])

    def test_round_limit_does_not_block_work_when_requirements_are_sufficient(
        self,
    ):
        context = _context(clarification_rounds=6)
        questions, stop = plan_clarification(
            EffectiveRequirements.model_validate(
                _requirements(_field("date", blocking=True))
            ),
            context,
            max_rounds=3,
            max_questions=3,
        )
        self.assertEqual(stop, "round_limit")
        questions, stop = plan_clarification(
            EffectiveRequirements.model_validate(
                _requirements(_field("date", "2026-10-09"))
            ),
            context,
            max_rounds=3,
            max_questions=3,
        )
        self.assertEqual((questions, stop), ([], ""))

    def test_missing_data_requires_specific_reason_and_provided_value(self):
        for field in (
            {**_field("date", blocking=True), "blocking_reason": ""},
            {**_field("date"), "status": "provided"},
        ):
            with self.assertRaises(ValidationError):
                EffectiveRequirements.model_validate(_requirements(field))


class ClarificationExecutorTest(IsolatedAsyncioTestCase):
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
            storage=self.storage, resource_access_service=AsyncMock()
        )
        self.node = TaskNode(
            name="收集需求",
            config=AgentStepConfig(prompt="确认日期、预算、住宿"),
        )
        self.patch = patch(
            "agentscope.app._service._model.get_model", return_value=self.model
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def _execute(self, context=None):
        return await self.executor.execute_node(
            node=self.node, previous_output="", context=context or _context()
        )

    async def test_merged_requirements_replace_conflicting_raw_history_in_execution(
        self,
    ):
        replies = [
            TaskInputReply(
                request_id="first",
                answers={"date": "2026.10.9", "return": "越早越好"},
            ),
            TaskInputReply(
                request_id="second",
                answers={"date": "2026-10-9", "return": "随便，当天回即可"},
            ),
        ]
        requirements = _requirements(
            _field("date", "2026-10-09"),
            _field("return", "当天回即可", status="no_preference"),
            _field("budget"),
            _field("hotel", status="not_applicable"),
        )
        self.model.generate_structured_output.side_effect = [
            _response(requirements),
            _response({"status": "completed", "output": "已整理有效交通需求"}),
        ]
        result = await self._execute(_context(user_answers=replies))
        self.assertEqual(result.status, "completed")
        calls = self.model.generate_structured_output.call_args_list
        normalization = json.loads(
            calls[0].kwargs["messages"][-1].get_text_content()
        )
        self.assertEqual(
            normalization["answers_in_chronological_order"][-1]["request_id"],
            "second",
        )
        self.assertEqual(normalization["timezone"], "Asia/Shanghai")
        self.assertIn("current_time", normalization)
        execution_prompt = calls[1].kwargs["messages"][-1].get_text_content()
        self.assertIn("2026-10-09", execution_prompt)
        self.assertNotIn("越早越好", execution_prompt)
        self.assertEqual(
            result.metadata["clarification"]["requirements"], requirements
        )

    async def test_only_missing_necessary_field_is_requested(self):
        self.model.generate_structured_output.return_value = _response(
            _requirements(_field("date", blocking=True), _field("budget"))
        )
        result = await self._execute()
        self.assertEqual(result.status, "needs_input")
        self.assertEqual([q.key for q in result.questions], ["date"])
        self.assertEqual(self.model.generate_structured_output.call_count, 1)
        self.assertEqual(
            result.metadata["clarification"]["asked_fields"][0]["key"], "date"
        )

    async def test_optional_model_question_is_repaired_without_parking(self):
        self.model.generate_structured_output.side_effect = [
            _response(
                _requirements(_field("date", "2026-10-09"), _field("budget"))
            ),
            _response(
                {
                    "status": "needs_input",
                    "questions": [{"key": "budget", "label": "预算金额？"}],
                }
            ),
            _response({"status": "completed", "output": "已整理有效交通需求"}),
        ]
        result = await self._execute()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.questions, [])
        self.assertEqual(self.model.generate_structured_output.call_count, 3)

    async def test_model_insisting_on_questions_stops_without_fake_completion(
        self,
    ):
        self.model.generate_structured_output.side_effect = [
            _response(_requirements(_field("date", "2026-10-09"))),
            *[
                _response(
                    {
                        "status": "needs_input",
                        "questions": [{"key": "date", "label": "日期？"}],
                    }
                )
                for _ in range(2)
            ],
        ]
        result = await self._execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.questions, [])
        self.assertEqual(
            result.metadata["clarification"]["stop_reason"],
            "model_repeated_question",
        )
        self.assertEqual(self.model.generate_structured_output.call_count, 3)

    async def test_repeat_or_round_limit_stops_before_execution(self):
        for context, reason in (
            (
                _context(
                    user_answers=[
                        TaskInputReply(
                            request_id="old", answers={"date": "不知道"}
                        )
                    ]
                ),
                "repeated_blockers",
            ),
            (_context(clarification_rounds=3), "round_limit"),
        ):
            with self.subTest(reason=reason):
                self.model.generate_structured_output.reset_mock()
                self.model.generate_structured_output.return_value = _response(
                    _requirements(_field("date", blocking=True))
                )
                result = await self._execute(context)
                self.assertEqual(result.status, "failed")
                self.assertEqual(
                    result.metadata["clarification"]["stop_reason"], reason
                )
                self.assertEqual(
                    self.model.generate_structured_output.call_count, 1
                )

    async def test_verifier_uses_same_effective_scope_without_old_answers(
        self,
    ):
        requirements = _requirements(
            _field("date", "2026-10-09"), _field("budget")
        )
        self.model.generate_structured_output.return_value = _response(
            {"passed": True}
        )
        verdict = await self.executor.verify_node(
            node=self.node,
            output="已整理有效交通需求",
            context=_context(
                clarification_state={"requirements": requirements}
            ),
        )
        self.assertTrue(verdict.passed)
        call = self.model.generate_structured_output.call_args.kwargs
        content = call["messages"][-1].get_text_content()
        payload = json.loads(content.split("\n", 1)[1])
        self.assertEqual(payload["effective_requirements"], requirements)
        self.assertNotIn("user_answers", payload)
        self.assertIn("latest_answer_values", payload)

    async def test_resume_after_service_restart_keeps_policy_state_and_scope(
        self,
    ):
        store = TaskStore()
        original = TaskService(store, agentscope_executor=self.executor)
        resumed = TaskService(store, agentscope_executor=self.executor)
        requirements = _requirements(
            _field("date", "2026-10-09"), _field("budget")
        )
        self.model.generate_structured_output.side_effect = [
            _response(
                _requirements(_field("date", blocking=True), _field("budget"))
            ),
            _response(requirements),
            _response({"status": "completed", "output": "已整理有效交通需求"}),
            _response({"passed": True}),
        ]
        try:
            task = await original.create_task(
                "owner",
                CreateTaskRequest(
                    goal="交通规划",
                    nodes=[self.node],
                    source_context=TaskContext(
                        agent_id="agent", session_id="session"
                    ),
                ),
            )
            run = await original.create_run("owner", task.id, TaskRunRequest())
            await original._active_runs[run.id]
            parked = await store.get_run("owner", run.id)
            self.assertEqual(parked.status, RunStatus.AWAITING_INPUT)
            await store.save_run(
                TaskRunRecord.model_validate_json(parked.model_dump_json())
            )
            await original.shutdown()
            await resumed.answer_run(
                "owner",
                run.id,
                TaskInputReply(
                    request_id=parked.pending_input.id,
                    answers={"date": "2026-10-09"},
                ),
            )
            await resumed._active_runs[run.id]
            completed = await store.get_run("owner", run.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            metadata = completed.sop_state["steps"][0]["metadata"][
                "clarification"
            ]
            self.assertEqual(metadata["answered_rounds"], 1)
            self.assertEqual(metadata["asked_fields"][0]["key"], "date")
            self.assertEqual(metadata["requirements"], requirements)
            calls = self.model.generate_structured_output.call_args_list
            normalization = json.loads(
                calls[1].kwargs["messages"][-1].get_text_content()
            )
            self.assertEqual(
                normalization["previous_effective_requirements"]["fields"][0][
                    "key"
                ],
                "date",
            )
            self.assertIs(
                calls[2].kwargs["structured_model"], AgentStepOutcome
            )
            self.assertIs(calls[3].kwargs["structured_model"], StepVerdict)
        finally:
            await original.shutdown()
            await resumed.shutdown()
