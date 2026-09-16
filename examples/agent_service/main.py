# -*- coding: utf-8 -*-
"""The example script to start the agent service."""
from contextlib import asynccontextmanager
import os
import sys

from pydantic import SecretStr
import uvicorn
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
from agentscope.app.storage import RedisKnowledgeGraphStore, RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import OpenAICredential
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.model import OpenAIChatModel
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
    KnowledgeGraphExtractor,
)
from agentscope.workspace import WorkspaceBase

from admin_api import AdminService, admin_router, sales_hub_router
from auth import load_auth_from_env
from sales_integration import create_sales_integration_router
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


def _build_knowledge_graph_extractor() -> KnowledgeGraphExtractor | None:
    """Build the optional graph extractor from the existing LLM settings."""
    siliconflow_key = os.getenv("SILICONFLOW_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    api_key = siliconflow_key or deepseek_key
    if not api_key:
        return None
    using_siliconflow = bool(siliconflow_key)
    provider_name = "SiliconFlow" if using_siliconflow else "DeepSeek"
    model = OpenAIChatModel(
        credential=OpenAICredential(
            id=(
                os.getenv("SILICONFLOW_CREDENTIAL_ID", "siliconflow")
                if using_siliconflow
                else os.getenv("DEEPSEEK_CREDENTIAL_ID", "deepseek")
            ),
            name=f"{provider_name} graph extractor",
            api_key=SecretStr(api_key),
            base_url=os.getenv(
                "SILICONFLOW_BASE_URL"
                if using_siliconflow
                else "DEEPSEEK_BASE_URL",
                "https://api.siliconflow.cn/v1"
                if using_siliconflow
                else "https://api.deepseek.com/v1",
            ),
        ),
        model=(
            os.getenv("LXSCOPE_GRAPH_MODEL")
            or os.getenv(
                "SILICONFLOW_CHAT_MODEL"
                if using_siliconflow
                else "DEEPSEEK_CHAT_MODEL",
                "deepseek-ai/DeepSeek-V3.2"
                if using_siliconflow
                else "deepseek-v4-flash",
            )
        ),
        parameters=OpenAIChatModel.Parameters(
            temperature=0.1,
            max_tokens=1200,
            thinking_enable=False,
        ),
        stream=False,
    )
    return KnowledgeGraphExtractor(model=model)


knowledge_graph_extractor = _build_knowledge_graph_extractor()
knowledge_graph_store = (
    RedisKnowledgeGraphStore(storage)
    if knowledge_graph_extractor is not None
    else None
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
    knowledge_graph_store=knowledge_graph_store,
    knowledge_graph_extractor=knowledge_graph_extractor,
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
app.include_router(create_sales_integration_router(storage=storage, auth=auth))
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
        yield


app.router.lifespan_context = _application_lifespan


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
