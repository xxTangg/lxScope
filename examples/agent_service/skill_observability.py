# -*- coding: utf-8 -*-
"""Application-level Skill loading instructions and diagnostics.

The AgentScope core owns Skill discovery and the built-in ``Skill`` viewer.
This module only adds product-level instructions and best-effort diagnostics
around the existing lifecycle.  It intentionally does not modify the core
Skill loader or workspace implementation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, AsyncGenerator, Callable

from agentscope._logging import logger
from agentscope.message import ToolCallBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse

try:
    # ``main.py`` is launched from this directory in the example service.
    from skill_observability_store import (
        SkillObservationEvent,
        SkillObservationSink,
    )
except ModuleNotFoundError:
    # The same module is imported as ``examples.agent_service`` by tests.
    from examples.agent_service.skill_observability_store import (
        SkillObservationEvent,
        SkillObservationSink,
    )


APPLICATION_SKILL_INSTRUCTIONS = """<application-skill-protocol>
When a task matches an available Skill, follow this order:
1. Call the built-in `Skill` tool with the exact Skill name.
2. Read and follow the returned Skill instructions before acting.
3. Use the Skill directory only for the referenced scripts and resources.
Do not read the root `SKILL.md` directly with `Read`, `Bash`, or `PowerShell`
as a substitute for the `Skill` call.
</application-skill-protocol>"""


@dataclass
class SkillReconcileSummary:
    """Application-level result of synchronizing one workspace's Skills."""

    user_id: str
    agent_id: str
    session_id: str | None
    result: str = "success"
    error_code: str | None = None
    before_count: int = 0
    visible_count: int = 0
    after_count: int = 0
    removed_count: int = 0
    restored_count: int = 0
    installed_count: int = 0
    failure_count: int = 0
    skill_names: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    observation_sink: SkillObservationSink | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _started_at: float = field(
        default_factory=perf_counter,
        repr=False,
        compare=False,
    )

    def finish(self) -> None:
        """Finalize elapsed time and derive a partial result when needed."""
        self.duration_seconds = perf_counter() - self._started_at
        if self.result == "success" and self.failure_count:
            self.result = "partial"


def log_skill_reconcile_started(summary: SkillReconcileSummary) -> None:
    """Log the start of a Skill reconciliation without Skill contents."""
    logger.info(
        "skill.reconcile.started user_id=%s agent_id=%s session_id=%s",
        summary.user_id,
        summary.agent_id,
        summary.session_id,
    )


def log_skill_reconcile_completed(summary: SkillReconcileSummary) -> None:
    """Log a bounded reconciliation summary."""
    logger.info(
        "skill.reconcile.completed user_id=%s agent_id=%s session_id=%s "
        "result=%s error_code=%s duration_seconds=%.4f "
        "before_count=%d visible_count=%d after_count=%d "
        "removed_count=%d restored_count=%d installed_count=%d "
        "failure_count=%d",
        summary.user_id,
        summary.agent_id,
        summary.session_id,
        summary.result,
        summary.error_code,
        summary.duration_seconds,
        summary.before_count,
        summary.visible_count,
        summary.after_count,
        summary.removed_count,
        summary.restored_count,
        summary.installed_count,
        summary.failure_count,
    )


async def persist_skill_observation(
    summary: SkillReconcileSummary,
    event: SkillObservationEvent,
) -> None:
    """Persist one event through the optional application-level sink.

    Persistence is deliberately best-effort at this boundary as well as in
    the production sink wrapper.  A custom test or deployment sink must not
    be able to break Skill loading or a user turn.
    """
    if summary.observation_sink is None:
        return
    try:
        await summary.observation_sink.record(event)
    except Exception:
        logger.warning(
            "skill.observation.record_failed event_name=%s result=%s "
            "user_id=%s agent_id=%s session_id=%s",
            event.event_name,
            event.result,
            event.user_id,
            event.agent_id,
            event.session_id,
            exc_info=True,
        )


def _reconcile_event(
    summary: SkillReconcileSummary,
    *,
    event_name: str,
    result: str,
) -> SkillObservationEvent:
    """Build a bounded event from a reconciliation summary."""
    return SkillObservationEvent(
        event_name=event_name,
        result=result,
        user_id=summary.user_id,
        agent_id=summary.agent_id,
        session_id=summary.session_id,
        error_code=summary.error_code,
        duration_seconds=(
            summary.duration_seconds if event_name.endswith("completed") else None
        ),
        before_count=summary.before_count,
        visible_count=summary.visible_count,
        after_count=summary.after_count,
        removed_count=summary.removed_count,
        restored_count=summary.restored_count,
        installed_count=summary.installed_count,
        failure_count=summary.failure_count,
    )


def _skill_name_from_call(tool_call: ToolCallBlock) -> str | None:
    """Extract only the Skill name from a raw tool-call payload."""
    try:
        payload = json.loads(tool_call.input or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    skill_name = payload.get("skill")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return None
    return skill_name.strip()


def _skill_completion_error_code(result: str) -> str | None:
    """Return a bounded reason code without persisting exception text."""
    if result == ToolResultState.SUCCESS.value:
        return None
    return {
        ToolResultState.ERROR.value: "skill_viewer_error",
        ToolResultState.DENIED.value: "skill_viewer_denied",
        ToolResultState.INTERRUPTED.value: "skill_viewer_interrupted",
    }.get(result, "skill_viewer_non_success")


class SkillUsageMiddleware(MiddlewareBase):
    """Add application Skill instructions and observe Skill tool usage."""

    def __init__(self, summary: SkillReconcileSummary) -> None:
        self.summary = summary
        self._exposed = False

    async def on_system_prompt(
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Append the application-level Skill protocol once per agent."""
        if not self.summary.skill_names:
            return current_prompt

        if not self._exposed:
            self._exposed = True
            skill_tool_available: bool | None = None
            listed_skill_count = 0
            try:
                toolkit = getattr(agent, "toolkit", None)
                if toolkit is not None:
                    skill_tool_available = (
                        await toolkit.get_tool("Skill") is not None
                    )
                    listed_skill_count = sum(
                        f"<name>{skill_name}</name>" in current_prompt
                        for skill_name in self.summary.skill_names
                    )
            except Exception:
                # Diagnostics must not make a valid agent turn fail. The
                # application prompt is still appended below.
                logger.exception(
                    "skill.exposed.check_failed user_id=%s agent_id=%s "
                    "session_id=%s",
                    self.summary.user_id,
                    self.summary.agent_id,
                    self.summary.session_id,
                )
            logger.info(
                "skill.exposed user_id=%s agent_id=%s session_id=%s "
                "count=%d listed_skill_count=%d skill_tool_available=%s",
                self.summary.user_id,
                self.summary.agent_id,
                self.summary.session_id,
                len(self.summary.skill_names),
                listed_skill_count,
                skill_tool_available,
            )
            await persist_skill_observation(
                self.summary,
                SkillObservationEvent(
                    event_name="skill.exposed",
                    result=(
                        "failed" if skill_tool_available is False else "success"
                    ),
                    user_id=self.summary.user_id,
                    agent_id=self.summary.agent_id,
                    session_id=self.summary.session_id,
                    error_code=(
                        "skill_tool_unavailable"
                        if skill_tool_available is False
                        else None
                    ),
                    skill_count=len(self.summary.skill_names),
                    listed_skill_count=listed_skill_count,
                    skill_tool_available=skill_tool_available,
                ),
            )
        if APPLICATION_SKILL_INSTRUCTIONS in current_prompt:
            return current_prompt
        return current_prompt.rstrip() + "\n\n" + APPLICATION_SKILL_INSTRUCTIONS

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Record only calls to the built-in ``Skill`` viewer."""
        del agent
        tool_call = input_kwargs.get("tool_call")
        if not isinstance(tool_call, ToolCallBlock) or tool_call.name != "Skill":
            async for item in next_handler(**input_kwargs):
                yield item
            return

        skill_name = _skill_name_from_call(tool_call)
        if skill_name is None:
            logger.warning(
                "skill.invoked result=invalid_input user_id=%s "
                "agent_id=%s session_id=%s",
                self.summary.user_id,
                self.summary.agent_id,
                self.summary.session_id,
            )
            await persist_skill_observation(
                self.summary,
                SkillObservationEvent(
                    event_name="skill.invoked",
                    result="invalid_input",
                    user_id=self.summary.user_id,
                    agent_id=self.summary.agent_id,
                    session_id=self.summary.session_id,
                    error_code="invalid_skill_input",
                ),
            )
        else:
            logger.info(
                "skill.invoked result=started user_id=%s agent_id=%s "
                "session_id=%s skill_name=%s",
                self.summary.user_id,
                self.summary.agent_id,
                self.summary.session_id,
                skill_name,
            )
            await persist_skill_observation(
                self.summary,
                SkillObservationEvent(
                    event_name="skill.invoked",
                    result="started",
                    user_id=self.summary.user_id,
                    agent_id=self.summary.agent_id,
                    session_id=self.summary.session_id,
                    skill_name=skill_name,
                ),
            )

        final_state: str | None = None
        started_at = perf_counter()
        try:
            async for item in next_handler(**input_kwargs):
                if isinstance(item, ToolResponse):
                    final_state = getattr(item.state, "value", str(item.state))
                yield item
        except BaseException:
            logger.exception(
                "skill.completed result=failed user_id=%s agent_id=%s "
                "session_id=%s skill_name=%s",
                self.summary.user_id,
                self.summary.agent_id,
                self.summary.session_id,
                skill_name,
            )
            await persist_skill_observation(
                self.summary,
                SkillObservationEvent(
                    event_name="skill.completed",
                    result="failed",
                    user_id=self.summary.user_id,
                    agent_id=self.summary.agent_id,
                    session_id=self.summary.session_id,
                    skill_name=skill_name,
                    error_code="skill_viewer_exception",
                    duration_seconds=perf_counter() - started_at,
                ),
            )
            raise

        result = final_state or ToolResultState.SUCCESS.value
        logger.info(
            "skill.completed result=%s user_id=%s agent_id=%s "
            "session_id=%s skill_name=%s",
            result,
            self.summary.user_id,
            self.summary.agent_id,
            self.summary.session_id,
            skill_name,
        )
        await persist_skill_observation(
            self.summary,
            SkillObservationEvent(
                event_name="skill.completed",
                result=result,
                user_id=self.summary.user_id,
                agent_id=self.summary.agent_id,
                session_id=self.summary.session_id,
                skill_name=skill_name,
                error_code=_skill_completion_error_code(result),
                duration_seconds=perf_counter() - started_at,
            ),
        )
