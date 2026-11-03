# -*- coding: utf-8 -*-
"""Adapt Task milestones to the official SOP state machine.

There is no Task-owned progression loop: SOPEngine owns ordering and retry
budgets. The service checkpoints this serializable state and projects it for
the existing Task API. Waiting never retains a live coroutine.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from pydantic import Field

from agentscope.event import CustomEvent
from agentscope.message import TextBlock
from agentscope.sop import SOPPhase, SOPStepBase, SOPStepRunState
from agentscope.sop import SOPRunState

from ._executor import StepExecutionResult
from ._models import (
    InputStepConfig,
    StepVerdict,
    TaskInputReply,
    TaskInputRequest,
    TaskNode,
)


class TaskStepState(SOPStepRunState):
    """Task-specific payload preserved by SOPRunState's SerializeAsAny."""

    output: str = ""
    result: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    input_request: TaskInputRequest | None = None
    answers: list[TaskInputReply] = Field(default_factory=list)
    knowledge_context: str = ""


class TaskSOPRunState(SOPRunState):
    """Persist the Task node IDs alongside the official ordered SOP state.

    AgentScope's SOP state remains the execution state. This Task-owned field
    binds each serialized step to the frozen Run node identity so recovery
    never guesses from the current Task template.
    """

    task_node_ids: list[str] = Field(default_factory=list)


class TaskSOPStep(SOPStepBase):
    """One typed Task step, including explicit and model-requested user input."""

    state_type = TaskStepState

    def __init__(
        self,
        node: TaskNode,
        execute: Callable[[TaskStepState], Awaitable[StepExecutionResult]],
        verify: Callable[[str, TaskStepState], Awaitable[StepVerdict]],
        *,
        validate_scenario_input_with_rules: bool = False,
    ) -> None:
        super().__init__(
            node.name,
            node.acceptance_criteria or node.prompt,
            node.max_attempts,
        )
        self.node = node
        self._execute = execute
        self._verify = verify
        self._validate_scenario_input_with_rules = (
            validate_scenario_input_with_rules
        )

    async def reply_stream(
        self, inputs: Any, state: TaskStepState
    ) -> AsyncGenerator[Any, None]:
        """Make one attempt and record either a verdict or an input request."""
        del inputs  # Task answers are restored in this step's durable state.
        if state.verifications and (
            state.metadata.get("task_retryable") is False
            or len(state.verifications) >= self.max_attempts
        ):
            # Task reconstructs this adapter for resumed Runs. Persisted
            # terminal verdicts and exhausted budgets must remain terminal.
            self.max_attempts = min(
                self.max_attempts, len(state.verifications)
            )
            state.phase = SOPPhase.FAILED
            yield CustomEvent(
                name="TASK_STEP_UPDATED", value={"node_id": self.node.id}
            )
            return
        state.phase = SOPPhase.RUNNING
        yield CustomEvent(
            name="TASK_STEP_STARTED", value={"node_id": self.node.id}
        )

        if isinstance(self.node.config, InputStepConfig):
            if state.answers:
                result = StepExecutionResult(
                    text=state.answers[-1].model_dump_json()
                )
            else:
                result = StepExecutionResult(
                    text="",
                    status="needs_input",
                    reason=self.node.config.reason,
                    questions=self.node.config.questions,
                )
        else:
            result = await self._execute(state)

        state.output = result.text
        state.result = result.result
        state.metadata = result.metadata or {}
        state.input_request = None
        if result.status == "needs_input":
            state.input_request = TaskInputRequest(
                node_id=self.node.id,
                reason=result.reason,
                questions=result.questions or [],
            )
            state.phase = SOPPhase.AWAITING
        elif result.status == "failed":
            state.phase = SOPPhase.FAILED
            failure_message = result.reason or result.text or "Step failed."
            state.metadata["error"] = failure_message
            state.submission = [TextBlock(text=result.text)]
            validation = state.metadata.get("output_contract_validation")
            schema_validation = state.metadata.get("output_schema_validation")
            verifier = (
                "task-output-contract"
                if isinstance(validation, dict) or isinstance(schema_validation, dict)
                else "task-execution"
            )
            self.record(state, False, failure_message, verifier)
            state.metadata["task_retryable"] = False
            self.max_attempts = len(state.verifications)
        elif result.status == "completed":
            if (
                self._validate_scenario_input_with_rules
                and isinstance(self.node.config, InputStepConfig)
            ):
                verdict = _validate_input_step_submission(
                    self.node.config,
                    result.text,
                    state,
                )
                verifier = "task-input-rules"
                state.metadata["task_input_validation"] = {
                    "method": verifier,
                    "passed": verdict.passed,
                    "message": verdict.message,
                }
            else:
                verdict = await self._verify(result.text, state)
                validation = state.metadata.get("output_contract_validation")
                verifier = (
                    "task-output-contract"
                    if isinstance(validation, dict)
                    and validation.get("status") == "failed"
                    else "task-verifier"
                )
            state.submission = [TextBlock(text=result.text)]
            self.record(
                state, verdict.passed, verdict.message, verifier
            )
            if not verdict.passed:
                state.metadata["task_retryable"] = verdict.retryable
                if not verdict.retryable:
                    # SOPEngine owns progression and reads this existing
                    # adapter limit after each settled verifier result.
                    self.max_attempts = len(state.verifications)
        else:
            raise ValueError(
                f"Unsupported step result status: {result.status}"
            )
        yield CustomEvent(
            name="TASK_STEP_UPDATED", value={"node_id": self.node.id}
        )


def _validate_input_step_submission(
    config: InputStepConfig,
    output: str,
    state: TaskStepState,
) -> StepVerdict:
    """Validate captured answers as input data, never as an Agent deliverable."""

    if not state.answers:
        return StepVerdict(
            passed=False,
            retryable=False,
            message="没有收到本步骤的问题回答。",
        )

    try:
        submitted = TaskInputReply.model_validate_json(output)
    except Exception:
        return StepVerdict(
            passed=False,
            retryable=False,
            message="用户补充信息格式无效，无法按输入字段校验。",
        )

    recorded = state.answers[-1]
    if submitted.model_dump(mode="json") != recorded.model_dump(mode="json"):
        return StepVerdict(
            passed=False,
            retryable=False,
            message="提交内容与本次 TaskRun 保存的用户回答不一致。",
        )

    allowed_keys = {question.key for question in config.questions}
    unexpected = set(submitted.answers) - allowed_keys
    if unexpected:
        return StepVerdict(
            passed=False,
            retryable=False,
            message="回答包含当前问题之外的字段。",
        )

    missing = [
        question.label
        for question in config.questions
        if question.required
        and not submitted.answers.get(question.key, "").strip()
    ]
    if missing:
        return StepVerdict(
            passed=False,
            retryable=False,
            message="请补充必填信息：" + "、".join(missing),
        )

    if not any(value.strip() for value in submitted.answers.values()):
        return StepVerdict(
            passed=False,
            retryable=False,
            message="回答不能为空。",
        )

    return StepVerdict(
        passed=True,
        retryable=False,
        message="用户补充信息已通过字段、必填项和空值校验。",
    )
