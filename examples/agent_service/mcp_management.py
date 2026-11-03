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

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from agentscope.app.deps import get_current_user_id, get_workspace_service
from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig
from auth import AuthUser
from mcp_access import activate_published_mcp


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


class ActivatePublishedMcpRequest(BaseModel):
    """A browser may select a publication, never submit an MCP config."""

    publication_id: str = Field(min_length=5, max_length=256)


class ActivatePublishedMcpResponse(BaseModel):
    status: Literal["added", "already_attached"]
    name: str


management_mcp_router = APIRouter(
    prefix="/management/mcp",
    tags=["management-mcp"],
)


def _describe_probe_error(error: Exception) -> str:
    message = str(error).strip()
    return message or type(error).__name__


async def get_current_auth_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthUser:
    """Use the product auth service rather than exposing a core dependency."""

    return await request.app.state.auth.get_current_user(authorization)


@management_mcp_router.post(
    "/activate",
    response_model=ActivatePublishedMcpResponse,
    status_code=status.HTTP_201_CREATED,
)
async def activate_mcp(
    body: ActivatePublishedMcpRequest,
    request: Request,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user: AuthUser = Depends(get_current_auth_user),
    workspace_service: Any = Depends(get_workspace_service),
) -> ActivatePublishedMcpResponse:
    """Attach an administrator-authorized MCP to the caller's workspace."""

    try:
        result, name = await activate_published_mcp(
            admin_service=request.app.state.admin_service,
            user=user,
            workspace_service=workspace_service,
            agent_id=agent_id,
            session_id=session_id,
            publication_id=body.publication_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "mcp_activation_conflict",
            "message": str(exc),
        }) from exc
    return ActivatePublishedMcpResponse(status=result, name=name)


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
