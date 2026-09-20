# -*- coding: utf-8 -*-
"""Management-owned MCP health probing.

The generic AgentScope workspace endpoint reports the health of the MCP client
instance stored in the workspace. A stale stateful client can keep reporting
``ClosedResourceError`` after the underlying server is otherwise reachable.
This router creates a fresh client for an MCP that is already attached to the
current workspace and completes connect/list/close in the same request task.
It is an application adapter and must not change AgentScope's generic
workspace implementation.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from agentscope.app.deps import get_current_user_id, get_workspace_service
from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


class MCPProbeTool(BaseModel):
    """The small tool shape needed by the management UI."""

    name: str
    description: str | None = None


class MCPProbeStatus(BaseModel):
    """Fresh MCP status returned by the management adapter."""

    name: str
    is_stateful: bool
    mcp_config: StdioMCPConfig | HttpMCPConfig
    is_healthy: bool
    tools: list[MCPProbeTool] = Field(default_factory=list)
    error: str | None = None


management_mcp_router = APIRouter(
    prefix="/management/mcp",
    tags=["management-mcp"],
)


def _describe_probe_error(error: Exception) -> str:
    message = str(error).strip()
    return message or type(error).__name__


@management_mcp_router.post("/probe", response_model=MCPProbeStatus)
async def probe_mcp(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    name: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    workspace_service: Any = Depends(get_workspace_service),
) -> MCPProbeStatus:
    """Probe an MCP already attached to the authenticated workspace.

    The client configuration is intentionally read from the resolved workspace
    rather than accepted from the request body. This keeps the management
    adapter from becoming an arbitrary command-execution or SSRF endpoint.
    """
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    clients = await workspace.list_mcps(
        agent_id=agent_id,
        session_id=session_id,
    )
    source = next((client for client in clients if client.name == name), None)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'MCP server "{name}" is not attached to this workspace.',
        )

    # Rebuild the model so this endpoint never reuses private connection state
    # from the workspace's cached stateful client.
    client = MCPClient.model_validate(source.model_dump())
    tools: list[MCPProbeTool] = []
    error: str | None = None

    try:
        if client.is_stateful:
            await client.connect()
        for tool in await client.list_tools():
            tools.append(
                MCPProbeTool(
                    name=tool.name,
                    description=getattr(tool, "description", None),
                ),
            )
    except Exception as exc:
        error = _describe_probe_error(exc)
    finally:
        # Stateful MCPs must be closed before the request task exits. This
        # keeps AnyIO task-group resources in the same task that opened them
        # and avoids leaving a stale process/session behind.
        if client.is_stateful and client.is_connected:
            try:
                await client.close()
            except Exception as exc:
                if error is None:
                    error = _describe_probe_error(exc)

    return MCPProbeStatus(
        name=client.name,
        is_stateful=client.is_stateful,
        mcp_config=client.mcp_config,
        is_healthy=error is None,
        tools=tools if error is None else [],
        error=error,
    )