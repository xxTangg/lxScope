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
from opentelemetry.trace import StatusCode

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
from agentscope._logging import logger
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.middleware import (
    AgenticMemoryMiddleware,
    MiddlewareBase,
    TracingMiddleware,
)
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

from admin_api import AdminService, admin_router, resource_router, sales_hub_router
from auth import AuthUser, load_auth_from_env
from longxin_admin.credential_policy import AdminManagedCredentialPolicy
from longxin_admin.plan_billing import PlanBillingService, plan_billing_router
from longxin_admin.upgrade import UpgradeService, upgrade_router
from skill_analytics_api import skill_analytics_router
from skill_observability import (
    SkillReconcileSummary,
    SkillUsageMiddleware,
    log_skill_reconcile_completed,
    log_skill_reconcile_started,
    persist_skill_observation,
    _reconcile_event,
)
from skill_observability_store import (
    BestEffortSkillObservationSink,
    NullSkillObservationSink,
    PostgresSkillObservationStore,
    SkillObservationSink,
)
from project_observability_store import PostgresProjectObservabilityStore
from project_observability import (
    ProjectObservability,
    new_request_id,
    observability_router,
)
from observability_analytics_api import observability_analytics_router
from mcp_management import management_mcp_router
from persistence import ApplicationDatabase
from task import (
    AgentScopeTaskExecutor,
    AgentScopeTaskPlanner,
    PostgresTaskStore,
    RedisTaskStore,
    TaskService,
    TaskStore,
    task_router,
)

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

# Skill analysis persistence is an application-level optional sink.  The
# default keeps the existing stdout-only diagnostics when PostgreSQL is not
# configured, so Redis-only deployments remain backwards compatible.
skill_observation_sink: SkillObservationSink = NullSkillObservationSink()
skill_observation_store: PostgresSkillObservationStore | None = None
project_observability_store: PostgresProjectObservabilityStore | None = None
application_database: ApplicationDatabase | None = None
project_observability = ProjectObservability()

# Product-owned office skills are seeded into every new workspace. They do
# not become user-installed library records, so every account can use them
# without downloading or installing anything first.
builtin_skills_dir = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "builtin_skills",
)
builtin_skill_paths = [
    os.path.join(builtin_skills_dir, name)
    for name in sorted(os.listdir(builtin_skills_dir))
    if os.path.isdir(os.path.join(builtin_skills_dir, name))
]

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


_builtin_skill_paths_by_id = {
    os.path.basename(path): path
    for path in builtin_skill_paths
}


async def _sync_current_user_skills(
    user_id: str,
    agent_id: str,
    workspace: WorkspaceBase,
    session_id: str | None = None,
) -> SkillReconcileSummary:
    """Align one live workspace with this user's published skill scope.

    This is an application-level reconciliation before the AgentScope
    toolkit is assembled. It deliberately uses the existing workspace and
    skill-hub APIs instead of changing the AgentScope core skill loader.
    """

    summary = SkillReconcileSummary(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        observation_sink=skill_observation_sink,
    )
    log_skill_reconcile_started(summary)
    await persist_skill_observation(
        summary,
        _reconcile_event(
            summary,
            event_name="skill.reconcile.started",
            result="started",
        ),
    )
    try:
        account = await auth._account_by_id(user_id)
        application = globals().get("app")
        if account is None or account.status != "active" or application is None:
            summary.result = "skipped"
            summary.error_code = "account_not_active"
            return summary

        admin_service = application.state.admin_service
        current_user = auth._public_user(account)
        visible_resources = await admin_service.published_resources(
            current_user,
            "skill",
        )
        visible_names = {
            resource.name for resource in visible_resources.resources
        }
        summary.visible_count = len(visible_names)
        managed_names = await admin_service.managed_resource_names("skill")

        current_skills = await workspace.list_skills(agent_id=agent_id)
        summary.before_count = len(current_skills)
        current_names = {skill.name for skill in current_skills}

        # Keep user-created/local skills intact. Only remove skills that the
        # administrator publication catalog controls and has revoked for this
        # particular user.
        for skill in current_skills:
            if skill.name in managed_names and skill.name not in visible_names:
                await workspace.remove_skill(skill.name, agent_id=agent_id)
                current_names.discard(skill.name)
                summary.removed_count += 1

        # Built-ins are seeded by the workspace manager. If a previous scope
        # reconciliation removed one and it becomes visible again, restore it
        # from the product-owned source directory.
        for resource in visible_resources.resources:
            source_id = resource.id.removeprefix("skill:")
            builtin_path = _builtin_skill_paths_by_id.get(source_id)
            if builtin_path is None or resource.name in current_names:
                continue
            try:
                await workspace.add_skill(builtin_path, agent_id=agent_id)
                current_names.add(resource.name)
                summary.restored_count += 1
            except Exception:
                summary.failure_count += 1
                logger.exception(
                    "Unable to restore builtin skill %s",
                    resource.name,
                )

        # Installed skills are already copied into the user's library when an
        # administrator publishes them. Missing visible skills are downloaded
        # into this workspace only when this user actually starts a turn here.
        skill_hubs = getattr(application.state, "skill_hubs", {})
        workspace_service = getattr(application.state, "workspace_service", None)
        if workspace_service is None:
            final_skills = await workspace.list_skills(agent_id=agent_id)
            summary.after_count = len(final_skills)
            summary.skill_names = tuple(
                sorted(skill.name for skill in final_skills)
            )
            summary.result = "partial"
            summary.error_code = "workspace_service_unavailable"
            return summary
        for record in await storage.list_skills(user_id):
            if record.name not in visible_names or record.name in current_names:
                continue
            hub = skill_hubs.get(record.hub_id or "")
            if hub is None:
                summary.failure_count += 1
                summary.error_code = "skill_hub_not_found"
                continue
            try:
                archive = await hub.download(
                    user_id,
                    record.card_id or record.name,
                    record.version,
                )
                await workspace_service.install_skill(
                    workspace,
                    archive.stream,
                    archive.format,
                    record.name,
                    agent_id=agent_id,
                )
                current_names.add(record.name)
                summary.installed_count += 1
            except Exception:
                summary.failure_count += 1
                summary.error_code = "skill_install_failed"
                logger.exception(
                    "Unable to equip published skill %s",
                    record.name,
                )

        # This is the application-level provisioned check. It does not replace
        # AgentScope's loader; it confirms the workspace is ready before the
        # generic toolkit builder receives it.
        final_skills = await workspace.list_skills(agent_id=agent_id)
        summary.after_count = len(final_skills)
        summary.skill_names = tuple(sorted(skill.name for skill in final_skills))
        return summary
    except Exception:
        summary.result = "failed"
        summary.error_code = summary.error_code or "unknown_error"
        logger.exception(
            "skill.reconcile.failed user_id=%s agent_id=%s session_id=%s",
            user_id,
            agent_id,
            session_id,
        )
        raise
    finally:
        summary.finish()
        log_skill_reconcile_completed(summary)
        await persist_skill_observation(
            summary,
            _reconcile_event(
                summary,
                event_name="skill.reconcile.completed",
                result=summary.result,
            ),
        )


async def longterm_memory_factory(
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace: WorkspaceBase,
) -> list[MiddlewareBase]:
    """Attach Markdown-file long-term memory, stored under the session's
    workspace so it is reachable through whichever backend is bound."""
    summary = await _sync_current_user_skills(
        user_id,
        agent_id,
        workspace,
        session_id=session_id,
    )
    middlewares: list[MiddlewareBase] = [
        AgenticMemoryMiddleware(
            workdir=workspace.workdir,
            backend=workspace.get_backend(),
        ),
    ]
    if project_observability.settings.enabled:
        # Application-owned measurements are attached through AgentScope's
        # public middleware extension point. No framework internals are
        # changed for this service-level integration.
        middlewares.extend(
            [
                project_observability.agent_middleware(),
                TracingMiddleware(),
            ],
        )
    middlewares.append(SkillUsageMiddleware(summary))
    return middlewares


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
        # All users start with the product-owned office skills available.
        skill_paths=builtin_skill_paths,
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

# Task is an application-level module.  It owns its repository and execution
# port so the generic AgentScope application factory remains unchanged.
app.state.task_store = TaskStore()
app.state.observability = project_observability


async def _request_user_id(request: Request) -> str:
    """Resolve the request identity once for project-level event context."""
    authorization = request.headers.get("authorization")
    auth = getattr(request.app.state, "auth", None)
    if not authorization or auth is None:
        return ""
    try:
        return await auth.get_current_user_id(authorization)
    except Exception:
        # Authentication dependencies remain the source of truth.  The
        # observability path must not turn an unauthenticated request into a
        # service failure.
        return ""


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """Correlate and measure every HTTP request at the app boundary."""
    request_id = new_request_id(request)
    request.state.request_id = request_id
    started_at = asyncio.get_running_loop().time()
    response = None
    status_code = 500
    user_id = await _request_user_id(request)
    try:
        with project_observability.http_span(request) as span:
            request.state.trace_id = project_observability.trace_id(span)
            with project_observability.context(
                request_id,
                request.state.trace_id,
                user_id,
            ):
                try:
                    response = await call_next(request)
                    status_code = response.status_code
                    span.set_attribute("http.response.status_code", status_code)
                    route = getattr(request.scope.get("route"), "path", None)
                    if route:
                        span.set_attribute("http.route", route)
                    if status_code >= 500:
                        span.set_status(StatusCode.ERROR)
                except BaseException as exc:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR)
                    raise
    finally:
        duration_seconds = asyncio.get_running_loop().time() - started_at
        route = getattr(request.scope.get("route"), "path", None) or "__unmatched__"
        project_observability.record_http(
            method=request.method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
            request_id=request_id,
            trace_id=getattr(request.state, "trace_id", ""),
            user_id=user_id,
        )

    assert response is not None
    response.headers["X-Request-ID"] = request_id
    trace_id = getattr(request.state, "trace_id", "")
    if trace_id:
        response.headers["X-Trace-ID"] = trace_id
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
    workspace_service_provider=lambda: getattr(app.state, "workspace_service", None),
)
app.state.upgrade_service = UpgradeService(storage, auth)
app.state.sales_hub_authorizer = app.state.admin_service.authorize_hub
app.include_router(auth.router)
app.include_router(admin_router)
app.include_router(skill_analytics_router)
app.include_router(observability_analytics_router)
app.include_router(resource_router)
app.include_router(sales_hub_router)
app.include_router(plan_billing_router)
app.include_router(upgrade_router)
app.include_router(task_router)
app.include_router(observability_router)
app.include_router(management_mcp_router)
app.dependency_overrides[get_current_user_id] = auth.get_current_user_id

# Seed the env-backed credential only after AgentScope has entered its normal
# storage lifespan.  Keeping the wrapper here avoids changing the library's
# generic application factory just for this deployment example.
_base_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _application_lifespan(app_instance):
    global skill_observation_sink, skill_observation_store
    global project_observability_store, application_database

    # Configure the application-owned observability layer before the
    # AgentScope lifespan creates the ChatService and its runtime agents.
    # The integration uses only public extension points.
    project_observability.configure()

    task_store_backend = os.getenv(
        "LXSCOPE_TASK_STORE_BACKEND",
        "redis",
    ).strip().lower()
    application_database_url = os.getenv(
        "LXSCOPE_DATABASE_URL",
        "",
    ).strip()
    local_application_database: ApplicationDatabase | None = None
    if application_database_url:
        local_application_database = ApplicationDatabase(application_database_url)
        try:
            await local_application_database.initialize()
            application_database = local_application_database
            logger.info(
                "lxscope.persistence.database_ready backend=postgres "
                "schema=longxin_app",
            )
        except Exception:
            logger.exception("lxscope.persistence.database_unavailable")
            await local_application_database.close()
            local_application_database = None
            application_database = None
            if task_store_backend == "postgres":
                raise
    elif task_store_backend == "postgres":
        raise RuntimeError(
            "LXSCOPE_TASK_STORE_BACKEND=postgres requires LXSCOPE_DATABASE_URL.",
        )

    project_url = (
        os.getenv("PROJECT_OBSERVABILITY_DATABASE_URL", "").strip()
        or os.getenv("SKILL_OBSERVABILITY_DATABASE_URL", "").strip()
        or application_database_url
    )
    local_project_store: PostgresProjectObservabilityStore | None = None
    if project_url:
        local_project_store = PostgresProjectObservabilityStore(project_url)
        try:
            await local_project_store.initialize()
            project_observability.set_persistent_store(local_project_store)
            restored_count = await project_observability.restore_persisted_events()
            logger.info(
                "project.observability.store_ready backend=postgres "
                "table=project_observability_events restored=%s",
                restored_count,
            )
        except Exception:
            logger.exception(
                "project.observability.store_unavailable backend=postgres",
            )
            await local_project_store.close()
            local_project_store = None

    project_observability_store = local_project_store

    # The database is deliberately opt-in.  A failed optional observability
    # backend falls back to stdout diagnostics and must not prevent the
    # business service from starting.
    configured_url = (
        os.getenv("SKILL_OBSERVABILITY_DATABASE_URL", "").strip()
        or application_database_url
    )
    local_store: PostgresSkillObservationStore | None = None
    local_sink: SkillObservationSink = NullSkillObservationSink()
    if configured_url:
        local_store = PostgresSkillObservationStore(configured_url)
        try:
            await local_store.initialize()
            local_sink = BestEffortSkillObservationSink(local_store)
            logger.info(
                "skill.observation.store_ready backend=postgres "
                "table=skill_observability_events",
            )
        except Exception:
            logger.exception(
                "skill.observation.store_unavailable backend=postgres",
            )
            await local_store.close()
            local_store = None

    skill_observation_store = local_store
    skill_observation_sink = local_sink
    app_instance.state.skill_observation_store = local_store
    app_instance.state.skill_observation_sink = local_sink

    try:
        async with _base_lifespan(app_instance):
            await app_instance.state.admin_service.ensure_default_builtin_publications()
            await _ensure_siliconflow_credential(auth.admin_user_ids)
            if (
                task_store_backend == "postgres"
                and local_application_database is not None
            ):
                app_instance.state.task_store = PostgresTaskStore(
                    local_application_database.engine,
                    tenant_code=os.getenv("LXSCOPE_TENANT_CODE", "default"),
                    tenant_name=os.getenv(
                        "LXSCOPE_TENANT_NAME",
                        "Default Tenant",
                    ),
                    tenant_id=os.getenv("LXSCOPE_TENANT_ID") or None,
                )
                await app_instance.state.task_store.initialize()
                logger.info(
                    "lxscope.persistence.task_store_ready backend=postgres",
                )
            else:
                # Keep AgentScope Core on its managed Redis Storage and use a
                # separate namespace for the application Task store.
                app_instance.state.task_store = RedisTaskStore(
                    storage.get_client(),
                )
            app_instance.state.task_service = TaskService(
                app_instance.state.task_store,
                agentscope_executor=AgentScopeTaskExecutor(
                    storage=storage,
                    resource_access_service=(
                        app_instance.state.resource_access_service
                    ),
                    workspace_manager=app_instance.state.workspace_manager,
                    scheduler_manager=app_instance.state.scheduler_manager,
                    background_task_manager=(
                        app_instance.state.background_task_manager
                    ),
                    message_bus=app_instance.state.message_bus,
                    extra_agent_tools=app_instance.state.extra_agent_tools,
                    sub_agent_templates=(
                        app_instance.state.custom_subagent_templates
                    ),
                ),
                planner=AgentScopeTaskPlanner(
                    storage=storage,
                    resource_access_service=(
                        app_instance.state.resource_access_service
                    ),
                ),
            )
            recharge_sync_task = asyncio.create_task(
                _sales_hub_recharge_sync_loop(app_instance.state.admin_service),
            )
            try:
                yield
            finally:
                await app_instance.state.task_service.shutdown()
                recharge_sync_task.cancel()
                await asyncio.gather(recharge_sync_task, return_exceptions=True)
    finally:
        await project_observability.close_persistent_store()
        project_observability_store = None
        if local_store is not None:
            await local_store.close()
        if local_application_database is not None:
            await local_application_database.close()
        application_database = None
        project_observability.shutdown()
        skill_observation_store = None
        skill_observation_sink = NullSkillObservationSink()


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
