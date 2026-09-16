# -*- coding: utf-8 -*-
"""The example script to start the agent service."""
from contextlib import asynccontextmanager
import asyncio
import os
import sys
from uuid import uuid4

from pydantic import SecretStr
import uvicorn
from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app, SubAgentTemplate
from agentscope.app.channel import (
    DingTalkChannel,
    DiscordChannel,
    FeishuChannel,
)
from agentscope.app.deps import get_current_user_id
from agentscope.app.hub import ClawSkillHub, GitHubMCPHub
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import OpenAICredential
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.rag import (
    ApproxTokenChunker,
    ExcelParser,
    ImageParser,
    PDFParser,
    PPTParser,
    QdrantStore,
    TextParser,
    WordParser,
)
from agentscope.workspace import WorkspaceBase

from admin_api import AdminService, admin_router, sales_hub_router
from auth import AuthUser, load_auth_from_env
from longxin_admin.credential_policy import AdminManagedCredentialPolicy
from longxin_admin.plan_billing import PlanBillingService, plan_billing_router
from longxin_admin.upgrade import UpgradeService, upgrade_router

playwright_mcp_command = os.getenv("PLAYWRIGHT_MCP_COMMAND", "npx")
playwright_browsers_path = os.getenv(
    "PLAYWRIGHT_BROWSERS_PATH",
    "/ms-playwright",
)
playwright_mcp_args = (
    ["--headless", "--browser", "chromium", "--no-sandbox"]
    if playwright_mcp_command == "playwright-mcp"
    else ["-y", "@playwright/mcp@latest"]
)

default_mcps = [
    MCPClient(
        name="browser-use",
        mcp_config=StdioMCPConfig(
            command=playwright_mcp_command,
            args=playwright_mcp_args,
            # Explicitly pass this to the child process. Some Playwright MCP
            # launch paths do not reliably inherit the container environment
            # and otherwise fall back to /root/.cache/ms-playwright.
            env={"PLAYWRIGHT_BROWSERS_PATH": playwright_browsers_path},
        ),
        is_stateful=True,
    ),
]

storage = RedisStorage(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    password=os.getenv("REDIS_PASSWORD") or None,
)

# Qdrant must be persistent in a service deployment.  The previous
# ``:memory:`` configuration made every API restart look like an empty
# knowledge base because all vectors disappeared with the process.
vector_store = QdrantStore(
    path=os.getenv("QDRANT_PATH", "/app/qdrant_data"),
)


async def _ensure_siliconflow_credential(user_ids: tuple[str, ...]) -> None:
    """Make the configured SiliconFlow credential available to administrators.

    SiliconFlow exposes an OpenAI-compatible API, so the existing OpenAI
    credential/model implementation is the correct adapter. The record is
    provisioned only for administrator-owned model configuration.
    """
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        return

    credential = OpenAICredential(
        id=os.getenv("SILICONFLOW_CREDENTIAL_ID", "siliconflow"),
        name="SiliconFlow",
        api_key=SecretStr(api_key),
        base_url=os.getenv(
            "SILICONFLOW_BASE_URL",
            "https://api.siliconflow.cn/v1",
        ),
    )
    for user_id in user_ids:
        await storage.upsert_credential(user_id, credential)


async def _provision_registered_user(user_id: str) -> None:
    """Keep newly registered accounts free of provider credentials."""
    del user_id


auth = load_auth_from_env(
    storage=storage,
    on_registered=_provision_registered_user,
)


async def longterm_memory_factory(
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace: WorkspaceBase,
) -> list[MiddlewareBase]:
    """Attach Markdown-file long-term memory, stored under the session's
    workspace so it is reachable through whichever backend is bound."""
    del user_id, agent_id, session_id
    return [
        AgenticMemoryMiddleware(
            workdir=workspace.workdir,
            backend=workspace.get_backend(),
        ),
    ]


app = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    # -- To use a Redis-backed message bus instead (recommended for
    # -- multi-process / production deployments), uncomment the lines
    # -- below and replace the InMemoryMessageBus() above:
    #
    # from agentscope.app.message_bus import RedisMessageBus
    # message_bus=RedisMessageBus(
    #     host="localhost",
    #     port=6379,
    # ),
    workspace_manager=LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
        # The default MCP servers that will be added into the workspace
        default_mcps=default_mcps,
    ),
    # Knowledge base feature — backed by a persistent local Qdrant store. The
    # CollectionPerKbManager allocates one collection per knowledge base,
    # so any embedding dimension is allowed.
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    # Chunker classes users can pick from when creating a knowledge base;
    # the chosen type and parameters are pinned on the knowledge base.
    knowledge_chunkers=[ApproxTokenChunker],
    # Register the built-in document parsers so the Web UI can accept
    # text, PDF, Word, Excel, PowerPoint, and image files.
    knowledge_parsers=[
        TextParser(),
        PDFParser(),
        WordParser(),
        ExcelParser(),
        PPTParser(),
        ImageParser(),
    ],
    # Resource hubs the UI browses under /hub. Neither needs credentials
    # of its own — an individual MCP card declares whatever key it wants
    # from the user in its ``inputs_schema``. Passing a ClawHub token
    # only raises the rate limit.
    mcp_hubs=[GitHubMCPHub()],
    skill_hubs=[ClawSkillHub(api_token=os.getenv("CLAWHUB_API_TOKEN"))],
    resource_access_policy=AdminManagedCredentialPolicy(auth),
    # Customize your own subagent templates
    custom_subagent_templates=[
        SubAgentTemplate(
            type="explorer",
            description=(
                "Read-only agents specialized in exploration tasks. It can "
                "read files but cannot modify, create, or delete them. Use "
                "this agent type when you need to investigate the codebase, "
                "understand its structure, or gather information from files "
                "to support planning—without making any changes."
            ),
            system_prompt_template="""You are {member_name}, an explorer \
agent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

## Responsibilities
- Complete the exploration tasks assigned by the team leader.
- You are read-only: you may inspect files and the codebase, but you must \
never modify, create, or delete anything.

## Reporting
- Always report the task result back to {leader_name} using the TeamSay \
tool, whether the task succeeds or fails.
- Keep your private reasoning private; only share conclusions and findings \
that the leader needs.

Note: `TeamSay` is your ONLY channel to communicate with {leader_name} and \
the other team members. Any other output you produce is invisible to them, \
so anything you want them to see MUST be sent through `TeamSay`.""",
            permission_context=PermissionContext(
                # Read-only
                mode=PermissionMode.EXPLORE,
            ),
        ),
    ],
    # Long-term memory. The default PER_AGENT workspace isolation makes
    # the memory survive across sessions of the same agent.
    extra_agent_middlewares=longterm_memory_factory,
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
    channels=[
        DingTalkChannel,
        DiscordChannel,
        FeishuChannel,
    ],
    download_secret=os.getenv("AGENTSCOPE_DOWNLOAD_SECRET"),
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate one correlation ID and echo it on every application response."""
    request_id = request.headers.get("X-Request-ID", "").strip() or f"req-{uuid4().hex}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(HTTPException)
async def admin_error_handler(request: Request, exc: HTTPException):
    """Add the contract correlation ID only to the product API domains."""
    if not (
        request.url.path.startswith("/admin")
        or request.url.path.startswith("/integration/sales/v1")
    ):
        return await http_exception_handler(request, exc)
    if not isinstance(exc.detail, dict):
        return await http_exception_handler(request, exc)
    detail = dict(exc.detail)
    detail.setdefault("request_id", getattr(request.state, "request_id", ""))
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def admin_validation_error_handler(request: Request, exc: RequestValidationError):
    if not (
        request.url.path.startswith("/admin")
        or request.url.path.startswith("/integration/sales/v1")
    ):
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(request, exc)
    fields: dict[str, str] = {}
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        fields[location or "request"] = str(error.get("msg", "Invalid request."))
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "validation_error",
                "message": "The request payload is invalid.",
                "fields": fields,
                "request_id": getattr(request.state, "request_id", ""),
            },
        },
    )


app.state.auth = auth
app.state.plan_billing_service = PlanBillingService(storage, auth)
app.state.credential_access_check = auth.is_admin_user
app.state.chat_access_check = app.state.plan_billing_service.ensure_chat_allowed
app.state.admin_service = AdminService(
    storage,
    auth,
    plan_billing=app.state.plan_billing_service,
)
app.state.upgrade_service = UpgradeService(storage, auth)
app.state.sales_hub_authorizer = app.state.admin_service.authorize_hub
app.include_router(auth.router)
app.include_router(admin_router)
app.include_router(sales_hub_router)
app.include_router(plan_billing_router)
app.include_router(upgrade_router)
app.dependency_overrides[get_current_user_id] = auth.get_current_user_id

# Seed the env-backed credential only after AgentScope has entered its normal
# storage lifespan.  Keeping the wrapper here avoids changing the library's
# generic application factory just for this deployment example.
_base_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _application_lifespan(app_instance):
    async with _base_lifespan(app_instance):
        await _ensure_siliconflow_credential(auth.admin_user_ids)
        recharge_sync_task = asyncio.create_task(
            _sales_hub_recharge_sync_loop(app_instance.state.admin_service),
        )
        try:
            yield
        finally:
            recharge_sync_task.cancel()
            await asyncio.gather(recharge_sync_task, return_exceptions=True)


app.router.lifespan_context = _application_lifespan


async def _sales_hub_recharge_sync_loop(service: AdminService) -> None:
    """Poll approved Sales Hub orders so delivery status advances automatically."""
    try:
        interval = max(
            5.0,
            float(os.getenv("LONGXIN_RECHARGE_SYNC_INTERVAL_SECONDS", "30")),
        )
    except ValueError:
        interval = 30.0
    actor = AuthUser(
        id="system-sales-hub-sync",
        username="system-sales-hub-sync",
        role="admin",
    )
    while True:
        try:
            config = await service.hub_config()
            if config.get("hub_url") and config.get("token_masked"):
                await service.sync_recharge(
                    actor,
                    request_id=f"req-auto-sync-{uuid4().hex}",
                    idempotency_key=f"idem-auto-sync-{uuid4().hex}",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The next cycle retries.  Credentials and response bodies are
            # intentionally not logged by this background task.
            pass
        await asyncio.sleep(interval)


if __name__ == "__main__":
    # Start the service
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("AGENTSCOPE_PORT", "8000")),
        # Hot reload forces a SelectorEventLoop on Windows, which cannot
        # spawn the subprocesses that the builtin tools rely on
        reload=(
            os.getenv("UVICORN_RELOAD", "false").lower()
            in {"1", "true", "yes", "on"}
            and sys.platform != "win32"
        ),
    )
