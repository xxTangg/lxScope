# 龙信助手 Docker 启动说明

本文说明如何使用仓库根目录的 `docker-compose.yml` 构建并启动龙信助手。

## 1. 环境要求

- Docker Engine 24 或更高版本
- Docker Compose v2（使用 `docker compose` 命令）
- 建议至少 4 GB 可用内存
- 服务器能够访问配置的模型 API

使用 Logto 登录的 Windows 部署，可以运行 `scripts/setup-logto.ps1` 自动配置 Logto API 权限、组织角色、SPA 回调、初始管理员和项目 `.env`，并启动 Compose。首次需在 Logto 管理台创建 M2M 应用并授予 `Logto Management API access`。详细步骤见 [Logto 一键配置说明](./docs/LOGTO_部署与权限配置说明.md)。

检查 Docker：

```bash
docker --version
docker compose version
```

## 2. 创建环境配置

进入项目根目录，将示例配置复制为 `.env`。

Linux/macOS：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少修改登录密码、JWT 密钥、下载密钥和模型 API Key：

```env
# 浏览器访问端口
WEB_UI_PORT=8000
AGENTSCOPE_API_PORT=8001

# 初始管理员账号
AGENTSCOPE_USERNAME=admin
AGENTSCOPE_USER_ID=local-user
AGENTSCOPE_PASSWORD=请替换为安全密码

# JWT 签名密钥必须至少 32 字节
AGENTSCOPE_JWT_SECRET=请替换为至少32字节的随机长字符串

# JWT 有效期，单位为分钟；1440 表示 24 小时
AGENTSCOPE_JWT_EXPIRE_MINUTES=1440

# 文件下载签名密钥
AGENTSCOPE_DOWNLOAD_SECRET=请替换为随机长字符串

# 模型配置
SILICONFLOW_API_KEY=你的APIKey
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_CHAT_MODEL=deepseek-ai/DeepSeek-V3.2
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
SILICONFLOW_EMBEDDING_DIMENSIONS=1024

# 可选：Redis 密码
REDIS_PASSWORD=

# lxScope 应用业务持久化。AgentScope Core 仍然使用 RedisStorage。
POSTGRES_DB=lxscope
POSTGRES_USER=lxscope
POSTGRES_PASSWORD=change-me
LXSCOPE_DATABASE_URL=postgresql+asyncpg://lxscope:change-me@postgres:5432/lxscope
LXSCOPE_TASK_STORE_BACKEND=postgres

# 管理员升级与备份。确认部署目录后再填写两个 target 路径。
LONGXIN_DATA_DIR=/app/longxin-data
LONGXIN_APP_TARGET_DIR=
LONGXIN_CORE_TARGET_DIR=
LONGXIN_UPGRADE_RESTART_COMMAND=
LONGXIN_UPGRADE_HEALTHCHECK_URL=http://127.0.0.1:8000/health
```

生产环境可使用以下命令生成随机密钥：

```bash
openssl rand -hex 32
```

请勿将包含真实密码和密钥的 `.env` 提交到 Git。

## 3. 构建并启动

在项目根目录执行：

```bash
docker compose up -d --build
```

首次构建需要下载基础镜像和项目依赖，耗时取决于网络速度。启动后查看状态：

```bash
docker compose ps
```

正常情况下，以下服务应处于 `Up` 或 `healthy` 状态：

- `postgres`：lxScope 应用业务数据持久化
- `redis`：账号、会话等数据存储
- `agentscope`：后端 API
- `web-ui`：龙信助手前端

## 4. 访问系统

默认访问地址：

```text
http://服务器IP:8000
```

本机启动时可访问：

```text
http://127.0.0.1:8000
```

使用 `.env` 中的 `AGENTSCOPE_USERNAME` 和 `AGENTSCOPE_PASSWORD` 登录，也可以在登录页注册新账号。每个注册账号会获得独立的用户 ID 和数据空间。

Web UI 会从浏览器直接访问后端 API，默认地址为：

```text
http://服务器IP:8001
```

服务器防火墙或安全组需要同时允许 `WEB_UI_PORT` 和 `AGENTSCOPE_API_PORT`。如果修改 `AGENTSCOPE_API_PORT`，必须使用 `docker compose up -d --build` 重新构建前端镜像。

## 4.1 开发模式：修改代码无需重新构建

如果需要频繁修改后端或前端代码，使用开发 Compose 覆盖配置。

首次启动时构建一次开发镜像：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

之后修改代码直接执行：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

开发模式会：

- 将项目源码挂载到 `agentscope` 容器，并启用 Python 热重载；
- 使用 Vite 开发服务器运行 Web UI，并启用前端 HMR；
- 继续使用原来的 Redis、工作区、文件和 Qdrant 数据卷。
- 额外使用 `postgres-data` 保存 lxScope 应用业务数据。

只有修改 `pyproject.toml`、Python 依赖或 `examples/web_ui/package.json`、
`pnpm-lock.yaml` 等依赖配置时，才需要重新构建对应镜像：

```bash
# 修改 Python 依赖
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build agentscope

# 修改前端依赖
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build web-ui
```

已有部署如果还没有把 Redis 中的 Task 数据迁移到 PostgreSQL，应先将
`LXSCOPE_TASK_STORE_BACKEND` 保持为 `redis`；完成迁移后再改为 `postgres`。
应用启动时会自动执行 `examples/agent_service/migrations` 下的正向迁移。

## 5. 检查与排错

查看所有服务状态：

```bash
docker compose ps -a
```

持续查看日志：

```bash
docker compose logs -f --tail=200
```

只查看后端日志：

```bash
docker compose logs -f --tail=200 agentscope
```

检查认证服务：

```bash
curl -i http://127.0.0.1:8001/auth/health
```

正常响应状态为 `204 No Content`。

## 6. 常用维护命令

重新构建并更新服务：

```bash
docker compose up -d --build
```

只重建后端配置：

```bash
docker compose up -d --force-recreate agentscope
```

停止服务但保留数据：

```bash
docker compose down
```

重新启动：

```bash
docker compose restart
```

查看资源占用：

```bash
docker stats
```

## 7. 数据持久化

Compose 使用以下 Docker 数据卷：

- `redis-data`：注册账号、会话及 Redis 数据
- `postgres-data`：lxScope 应用业务数据（租户、Task、订单、审计等）
- `agentscope-workspaces`：用户工作区
- `agentscope-blobs`：上传和生成的文件
- `qdrant-data`：向量数据库数据
- `longxin-data`：管理员发布包、升级临时文件和升级备份

执行普通的 `docker compose down` 不会删除这些数据。不要在生产环境随意执行下面的命令：

```bash
docker compose down -v
```

该命令会删除上述数据卷，注册账号和业务数据可能无法恢复。

## 8. JWT 配置说明

- `AGENTSCOPE_JWT_EXPIRE_MINUTES` 控制新签发 JWT 的有效期。
- 修改过期时间后，需要重建或重新创建 `agentscope` 容器才能读取新环境变量。
- 已签发的 JWT 仍使用签发时的过期时间。
- 修改 `AGENTSCOPE_JWT_SECRET` 会让所有旧 JWT 失效，所有用户需要重新登录。

更完整的服务器、离线部署和反向代理说明参见 `OPS_DEPLOYMENT.md`。
