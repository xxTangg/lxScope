"""Application-level authorization helpers for administrator-managed MCPs."""

from typing import Any, Literal

from auth import AuthUser


async def activate_published_mcp(
    *,
    admin_service: Any,
    user: AuthUser,
    workspace_service: Any,
    agent_id: str,
    session_id: str,
    publication_id: str,
) -> tuple[Literal["added", "already_attached"], str]:
    """Attach one currently authorized published MCP to a workspace.

    The browser supplies only a publication id; the server resolves its
    private MCP configuration after checking the active publication scope.
    """

    record = await admin_service.resolve_published_mcp_for_user(
        user,
        publication_id,
    )
    workspace = await workspace_service.resolve(user.id, agent_id, session_id)
    attached = await workspace.list_mcps(agent_id=agent_id, session_id=session_id)
    if any(client.name == record.client.name for client in attached):
        return "already_attached", record.client.name
    await workspace.add_mcp(
        record.client,
        agent_id=agent_id,
        session_id=session_id,
    )
    return "added", record.client.name


async def reconcile_workspace_mcps(
    *,
    admin_service: Any,
    user: AuthUser,
    workspace: Any,
    agent_id: str,
    session_id: str,
) -> None:
    """Remove previously attached managed MCPs that have been revoked.

    This is a defence-in-depth check for task execution.  Publication updates
    also detach revoked MCPs immediately, but a task must never depend on that
    cleanup having completed successfully.
    """

    visible = await admin_service.published_resources(user, "mcp")
    visible_names = {resource.name for resource in visible.resources}
    managed_names = await admin_service.managed_resource_names("mcp")
    attached = await workspace.list_mcps(agent_id=agent_id, session_id=session_id)
    for client in attached:
        if client.name in managed_names and client.name not in visible_names:
            await workspace.remove_mcp(
                client.name,
                agent_id=agent_id,
                session_id=session_id,
            )
