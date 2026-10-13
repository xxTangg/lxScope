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


class TaskSOPStep(SOPStepBase):
    """One typed Task step, including explicit and model-requested user input."""

    state_type = TaskStepState

    def __init__(
        self,
        node: TaskNode,
        execute: Callable[[TaskStepState], Awaitable[StepExecutionResult]],
        verify: Callable[[str, TaskStepState], Awaitable[StepVerdict]],
    ) -> None:
        super().__init__(
            node.name,
            node.acceptance_criteria or node.prompt,
            node.max_attempts,
        )
        self.node = node
        self._execute = execute
        self._verify = verify

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
            state.metadata["error"] = (
                result.reason or result.text or "Step failed."
            )
        elif result.status == "completed":
            verdict = await self._verify(result.text, state)
            state.submission = [TextBlock(text=result.text)]
            self.record(
                state, verdict.passed, verdict.message, "task-verifier"
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
