# Agent Service

Agent service is a FastAPI-based, multi-tenant and multi-session service built with AgentScope 2.0.

This example demonstrates

- how to set up the agent service with Redis storage, and
- how to launch the service and its companion Web UI

Details about the agent service please refer to the [tutorial](https://docs.agentscope.io/latest/en/deploy/agent-service).

## Prerequisites

- Python ≥ 3.11
- Node.js ≥ 20 with `npx`
- SiliconFlow API key in `.env` for the OpenAI-compatible chat and embedding APIs

## Quickstart

Install AgentScope from PyPI or source:

```bash
uv pip install agentscope[full]
# or
# uv pip install -e [full]
```

Install Redis and start it as backend storage:

```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Linux (systemd)
sudo apt install redis-server
sudo systemctl start redis-server

# Docker (cross-platform)
docker run --rm -p 6379:6379 redis:7
```

Start the agent service:

```bash
cd examples/agent_service

python main.py
```

### Project-level observability

The service exposes one project-level observability boundary across HTTP
requests, Agent runs, model calls, tool calls, and configured dependencies.
Each response carries an `X-Request-ID`; when an OpenTelemetry SDK is active,
it also carries an `X-Trace-ID`. Authenticated administrators can scrape
Prometheus-compatible metrics from:

```text
GET /observability/metrics
```

The administrator runtime analysis projection is available at:

```text
GET /admin/observability/overview?days=14
```

The model, Agent and Tool/MCP cards link to their drill-down projections:

```text
GET /admin/observability/model?days=14
GET /admin/observability/agent?days=14
GET /admin/observability/tool?days=14
```

It is intentionally project-level: the response combines request volume and
latency, per-user token usage, model calls, Agent runs, Tool/MCP calls and
recent failures. Skill analytics remains a separate section of the same
admin observability module at `/admin/observability/skills`; it is not mixed
into the project-level overview and is not the owner of the module.

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export the AgentScope trace spans to an
OTLP/HTTP collector. Leaving it empty keeps the service self-contained while
still producing local metrics and structured diagnostics.

### Project observability storage

Project-level runtime events are persisted in PostgreSQL when
`PROJECT_OBSERVABILITY_DATABASE_URL` is configured:

```text
PROJECT_OBSERVABILITY_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
```

At startup the service creates the application-owned
`project_observability_events` table and its indexes if they do not exist. The
table stores bounded dimensions such as users, models, Agents, Tools, timing,
Token counts, status and correlation IDs. It never stores prompts, messages,
model output or credentials. Existing deployments may temporarily omit the
new variable; the service falls back to `SKILL_OBSERVABILITY_DATABASE_URL` so
the migration does not require changing the current database URL.

### Optional Skill analysis storage

The service can additionally persist Skill-specific event history to
PostgreSQL without replacing the existing Redis storage. Install the
`observability-postgres` extra and configure:

```text
SKILL_OBSERVABILITY_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
```

At startup the service creates the application-owned
`skill_observability_events` table and its indexes if they do not exist. When
the variable is empty, the project-level metrics and diagnostics remain
enabled. The table is only a Skill-specific history store; it does not store
prompts, Skill Markdown, model output, or credentials.

Launch the Web UI in a separate terminal to experience a chat-style interface:

```bash
cd examples/web_ui/

pnpm install
# or npm install

# Run in dev mode
pnpm dev
```

After that, you can set the API endpoint `http://localhost:8000` in the Web UI and start experiencing the agent service.

<img src="https://gw.alicdn.com/imgextra/i2/O1CN01Phmg1G1brIVC8WXyU_!!6000000003518-2-tps-2938-1736.png" alt="Web UI Screenshot" width="100%">

## What Next

- You can customize the service in `main.py` by adding your own MCPs, middlewares, or workspace manager implementations.

- Experience the agent service, including
    - human-in-the-loop interactions & permission system
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01vGGiBw20agWwpzmjy_!!6000000006866-2-tps-2934-1732.png" alt="Permission System" width="100%">

    - schedule tasks
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01Xi3Qw71E2haKKu4z0_!!6000000000294-2-tps-2932-1738.png" alt="Schedule Tasks" width="100%">

    - and more! (stay tuned for future updates)
