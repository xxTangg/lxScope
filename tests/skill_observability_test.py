# -*- coding: utf-8 -*-
"""Tests for application-level Skill instructions and diagnostics."""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import Agent
from agentscope.message import ToolCallBlock
from agentscope.skill import Skill, SkillLoaderBase
from agentscope.tool import Toolkit, ToolChunk, ToolResponse

from utils import MockModel

from examples.agent_service.skill_observability import (
    APPLICATION_SKILL_INSTRUCTIONS,
    SkillReconcileSummary,
    SkillUsageMiddleware,
)


class MockSkillLoader(SkillLoaderBase):
    """Provide deterministic Skills to a real AgentScope Toolkit."""

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = skills

    async def list_skills(self) -> list[Skill]:
        """Return the configured Skills."""
        return self._skills


class SkillObservabilityTest(IsolatedAsyncioTestCase):
    """Lock down the application-level 2.0 Skill contract."""

    async def test_prompt_requires_skill_viewer_before_resources(self) -> None:
        """The product prompt directs the model through ``Skill`` first."""
        summary = SkillReconcileSummary(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            skill_names=("paper-innov-compare",),
        )
        middleware = SkillUsageMiddleware(summary)

        prompt = await middleware.on_system_prompt(None, "base prompt")

        self.assertIn("Skill", prompt)
        self.assertIn("exact Skill name", prompt)
        self.assertIn("Do not read the root `SKILL.md` directly", prompt)
        self.assertIn(APPLICATION_SKILL_INSTRUCTIONS, prompt)

        second_prompt = await middleware.on_system_prompt(None, "base prompt")
        self.assertIn(APPLICATION_SKILL_INSTRUCTIONS, second_prompt)

    async def test_real_agent_session_contains_skill_tool_and_instructions(
        self,
    ) -> None:
        """The real Toolkit and Agent prompt expose the same Skill scope."""
        skill = Skill(
            name="paper-innov-compare",
            description="Compare innovation claims in papers.",
            dir="/workspace/skills/paper-innov-compare",
            markdown="# private skill instructions",
            updated_at=0.0,
        )
        toolkit = Toolkit(skills_or_loaders=[MockSkillLoader([skill])])
        summary = SkillReconcileSummary(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            skill_names=(skill.name,),
        )
        agent = Agent(
            name="agent-1",
            system_prompt="base prompt",
            model=MockModel(),
            toolkit=toolkit,
            middlewares=[SkillUsageMiddleware(summary)],
        )

        with patch(
            "examples.agent_service.skill_observability.logger.info",
        ) as log_info:
            prepared = await agent._prepare_model_input()

        prompt = prepared["messages"][0].get_text_content()
        tool_names = {
            schema["function"]["name"] for schema in prepared["tools"]
        }

        self.assertIn("Skill", tool_names)
        self.assertIn("paper-innov-compare", prompt)
        self.assertIn("Compare innovation claims in papers.", prompt)
        self.assertIn(APPLICATION_SKILL_INSTRUCTIONS, prompt)
        self.assertNotIn("# private skill instructions", prompt)
        exposed_logs = [call.args[0] for call in log_info.call_args_list]
        self.assertTrue(
            any(message.startswith("skill.exposed") for message in exposed_logs),
        )
        exposed_call = next(
            call
            for call in log_info.call_args_list
            if call.args[0].startswith("skill.exposed")
        )
        self.assertEqual(exposed_call.args[5], 1)
        self.assertTrue(exposed_call.args[6])

    async def test_real_skill_viewer_is_observed_without_rewriting_result(
        self,
    ) -> None:
        """The middleware wraps the real Toolkit Skill viewer unchanged."""
        skill = Skill(
            name="paper-innov-compare",
            description="Compare innovation claims in papers.",
            dir="/workspace/skills/paper-innov-compare",
            markdown="# private skill instructions",
            updated_at=0.0,
        )
        toolkit = Toolkit(skills_or_loaders=[MockSkillLoader([skill])])
        summary = SkillReconcileSummary(
            "u",
            "a",
            "s",
            skill_names=(skill.name,),
        )
        middleware = SkillUsageMiddleware(summary)
        agent = Agent(
            name="a",
            system_prompt="base",
            model=MockModel(),
            toolkit=toolkit,
            middlewares=[middleware],
        )
        tool_call = ToolCallBlock(
            id="call-3",
            name="Skill",
            input=json.dumps({"skill": skill.name}),
        )

        with patch(
            "examples.agent_service.skill_observability.logger.info",
        ) as log_info:
            results = [item async for item in agent._acting(tool_call)]

        self.assertIsInstance(results[-1], ToolResponse)
        self.assertEqual(results[-1].state, "success")
        self.assertIn("# private skill instructions", results[-1].content[0].text)
        observed_events = [call.args[0] for call in log_info.call_args_list]
        self.assertTrue(
            any(message.startswith("skill.invoked") for message in observed_events),
        )
        self.assertTrue(
            any(message.startswith("skill.completed") for message in observed_events),
        )
        self.assertNotIn("# private skill instructions", str(log_info.call_args_list))

    async def test_non_skill_tool_calls_pass_through(self) -> None:
        """The middleware does not interfere with ordinary tools."""
        summary = SkillReconcileSummary("u", "a", "s")
        middleware = SkillUsageMiddleware(summary)
        calls: list[str] = []

        async def next_handler(**kwargs: Any) -> AsyncGenerator:
            calls.append(kwargs["tool_call"].name)
            yield "ordinary-result"

        tool_call = ToolCallBlock(
            id="call-1",
            name="Read",
            input=json.dumps({"file_path": "notes.md"}),
        )
        results = [
            item
            async for item in middleware.on_acting(
                None,
                {"tool_call": tool_call},
                next_handler,
            )
        ]

        self.assertEqual(calls, ["Read"])
        self.assertEqual(results, ["ordinary-result"])

    async def test_skill_tool_call_is_forwarded_and_observed(self) -> None:
        """A ``Skill`` call reaches the real viewer and is not rewritten."""
        summary = SkillReconcileSummary("u", "a", "s")
        middleware = SkillUsageMiddleware(summary)
        calls: list[str] = []

        async def next_handler(**kwargs: Any) -> AsyncGenerator:
            calls.append(kwargs["tool_call"].input)
            yield ToolChunk(content=[])
            yield ToolResponse(id="call-2")

        tool_call = ToolCallBlock(
            id="call-2",
            name="Skill",
            input=json.dumps({"skill": "paper-innov-compare"}),
        )
        results = [
            item
            async for item in middleware.on_acting(
                None,
                {"tool_call": tool_call},
                next_handler,
            )
        ]

        self.assertEqual(calls, [tool_call.input])
        self.assertIsInstance(results[-1], ToolResponse)
        self.assertEqual(results[-1].state, "success")

    async def test_reconcile_summary_tracks_partial_result(self) -> None:
        """A failed optional install is visible without changing the flow."""
        summary = SkillReconcileSummary("u", "a", "s")
        summary.failure_count = 1
        summary.finish()

        self.assertEqual(summary.result, "partial")
        self.assertGreaterEqual(summary.duration_seconds, 0.0)
