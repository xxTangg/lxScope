# -*- coding: utf-8 -*-
"""Minimal adapter from Task nodes to the existing AgentScope ChatService."""

from typing import TYPE_CHECKING

from ._models import ExecutionContext, TaskNode

if TYPE_CHECKING:
    from agentscope.app.storage import StorageBase
    from agentscope.app._service import ChatService


class AgentScopeTaskExecutor:
    """Reuse the existing chat execution path without changing AgentScope.

    The adapter intentionally depends on the application service boundary,
    not on Task models inside the AgentScope package.  The task engine can be
    tested with :class:`PreviewTaskExecutor` and a future executor can be
    added without changing its API or persistence model.
    """

    def __init__(self, chat_service: "ChatService", storage: "StorageBase") -> None:
        """Bind the existing chat service and message store."""

        self._chat_service = chat_service
        self._storage = storage

    async def execute_node(
        self,
        *,
        node: TaskNode,
        previous_output: str,
        context: ExecutionContext,
    ) -> str:
        """Run a node through ChatService and read its persisted reply."""

        if not context.agent_id or not context.session_id:
            raise RuntimeError(
                "An AgentScope task run needs both agent_id and session_id "
                "in its execution context.",
            )
        # Keep the AgentScope message type behind this optional adapter.  The
        # task domain and preview executor remain importable on their own.
        from agentscope.message import UserMsg

        before, _ = await self._storage.list_messages(
            context.user_id,
            context.session_id,
            limit=200,
        )
        before_ids = {message.id for message in before}
        prompt = node.prompt
        if previous_output.strip():
            prompt = f"{prompt}\n\n上一节点输出：\n{previous_output}"

        await self._chat_service.run(
            user_id=context.user_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            input_msg=UserMsg(name="user", content=prompt),
        )

        after, _ = await self._storage.list_messages(
            context.user_id,
            context.session_id,
            limit=200,
        )
        new_assistant_messages = [
            message
            for message in after
            if message.id not in before_ids and message.role == "assistant"
        ]
        if not new_assistant_messages:
            raise RuntimeError(
                "AgentScope completed without a persisted assistant result.",
            )
        return _message_text(new_assistant_messages[-1])


def _message_text(message: object) -> str:
    """Extract text blocks without coupling Task to a concrete Msg subtype."""

    blocks = getattr(message, "content", [])
    text = "\n".join(
        str(getattr(block, "text", ""))
        for block in blocks
        if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise RuntimeError("AgentScope returned an assistant message without text.")
    return text
