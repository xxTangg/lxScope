# P7-A-R：Task 非浏览器验收恢复与真实服务核查报告

核查日期：2026-10-12
分支：`integrate/dev_l`
HEAD：`1418f2b`

## 1. 结论摘要

本轮未使用 Computer Use 或浏览器自动化。已用 Docker、源码、只读 HTTP 探测及 Task 专项测试完成可独立进行的验收。

| 验收项 | 状态 | 结论 |
|---|---|---|
| 服务容器与监听端口 | PASS | web UI、AgentScope、Redis、PostgreSQL 容器运行；后端进程存在 |
| Task 路由注册 | PASS | `/openapi.json` 返回 200，列出 Task 路由 |
| Task API 无凭据只读探测 | BLOCKED | 路由存在且服务可达，但请求按设计返回 401，要求 Logto Bearer |
| 认证身份、Agent/Session、模型、知识库与 Workspace | BLOCKED | 本轮没有合法认证会话，不能检查用户授权上下文 |
| S02 真实创建与运行 | BLOCKED | 未尝试创建或运行；不能在没有可信用户身份和数据权限时继续 |
| Task 自动化回归 | PASS | 与 P6 基线一致：169 passed、2 skipped；PostgreSQL 两项仍跳过 |
| Python 编译、Task ESLint、前端构建 | PASS | 全部通过；构建输出现有大 chunk 提示 |
| PostgreSQL 真实集成测试 | BLOCKED | 现有 Postgres 为应用持久化服务；未找到专用测试 URL，未连接或写入 |
| 浏览器交互 | NOT TESTED | 本轮明确不使用 Computer Use；HTTP 静态页 200 不等于交互验收 |

**没有发现本轮 Task API 的 5xx 或后端错误日志。** 当前明确阻塞是 CLI 请求没有合法 Logto Bearer 认证，以及没有独立一次性 PostgreSQL 测试库。没有把 401 误判为服务不可达。

## 2. 工作区保护与修改

开始时工作区已有多项未提交内容，包括 Task 代码/测试、九个 v2 模板、阶段报告，以及销售系统文件和临时目录。本轮保留这些内容，没有暂存或提交。

本轮唯一的现有源文件修改是 `examples/web_ui/frontend/src/pages/task/task-template-gallery.tsx` 的 import 顺序调整。Task 范围 ESLint 初次执行在该未修改文件中发现两条 import 顺序错误；修复只移动 import 并移除不符合规则的空行，不改变运行逻辑。新增本报告位于 Task docs 目录。

没有修改 Task 后端、共享认证、共享 API、AgentScope 官方代码、数据库迁移、依赖、构建配置或销售系统。`git diff --check` 通过；Git 仅提示工作区文件的 LF/CRLF 自动转换警告。未执行 `git add`、commit 或 push。

## 3. 阶段 A1：服务与 Task API

### 3.1 容器、端口与进程

执行 `docker ps --format ...`、`Get-NetTCPConnection -State Listen`、`docker top lxscope2-agentscope-1` 和 `docker inspect`：

| 容器 | 状态 | 端口/用途 |
|---|---|---|
| `lxscope2-web-ui-1` | running / healthy | 主机 `8000` → 容器 `80` |
| `lxscope2-agentscope-1` | running / healthy | 主机 `8001` → 容器 `8000`；进程为 `python main.py` |
| `lxscope2-logto-1` | running | 主机 `3001–3002` |
| `lxscope2-logto-db-1` | running / healthy | 容器内部 `5432` |
| `lxscope2-redis-1` | running / healthy | 容器内部 `6379` |
| `lxscope2-postgres-1` | running / healthy | 容器内部 `5432`，使用命名持久卷 `lxscope2_postgres-data` |

主机上 `8000`、`8001`、`3001`、`3002` 由 Docker 代理监听；未发现主机 `5173`、`5432` 或 `5433` 监听。

### 3.2 路由来源与 HTTP 记录

源码确认 Task Router 前缀为 `/tasks`（`examples/agent_service/task/_router.py:62`），由 `examples/agent_service/main.py:689` 注册。应用 OpenAPI 位于 FastAPI 的 `/openapi.json`；实际响应中共列出 22 个 Task 路径操作，包含模板列表、实例化、Preflight、Task/Run、知识库列表和产物读取等路由。

本轮只对 GET 路由发起无身份请求，没有发送身份头或 Bearer：

| 请求 | HTTP 结果 | 解释 |
|---|---:|---|
| `GET http://127.0.0.1:8001/openapi.json` | 200 | HTTP 服务正常；Task 路由确实注册 |
| `GET http://127.0.0.1:8001/auth/health` | 204 | 认证服务健康端点可达 |
| `GET http://127.0.0.1:8001/health` | 401 | 端点存在，但受全局认证保护；不是 404 或服务不可达 |
| `GET http://127.0.0.1:8001/tasks/templates` | 401 | 返回 `Bearer Logto access token is required.` |
| `GET http://127.0.0.1:8001/tasks/` | 401 | 返回 `Bearer Logto access token is required.` |
| `GET http://127.0.0.1:8001/tasks/knowledge-bases` | 401 | 返回 `Bearer Logto access token is required.` |
| `GET http://127.0.0.1:8000/task` | 200 | 仅确认服务返回 967 字节 HTML，不代表页面交互已验收 |

Task API 的依赖由 `main.py:692` 覆盖为应用的 Logto 认证函数。`examples/agent_service/auth.py:625-648` 要求 Bearer 访问令牌，并检查令牌身份、账户状态和 token version。没有使用 `X-User-ID` 伪造身份，也没有尝试绕过认证。

### 3.3 日志

`docker logs --since 30m --tail 500 lxscope2-agentscope-1` 检查到 204 行日志，未匹配 `ERROR`、`CRITICAL` 或 `Traceback`。日志有 `lxscope.persistence.database_ready backend=postgres schema=longxin_app` 启动记录；本轮探测产生的未认证访问以 INFO 级 `http_401` 记录，不是后端异常。

## 4. 阶段 A2：S02 真实运行条件

本轮 CLI 没有提供合法 Logto Bearer，会话身份因此不可验证。Task 模板和知识库只读接口都返回 401；无法确认当前用户的 Agent、Session、模型配置、知识库授权、Workspace 权限，也不能运行 Preflight。

没有调用模板实例化、创建 Task、创建 Run 或 Artifact API。S02 v2 的文件定义与离线回归可验证，但不能据此声称正式 API 创建成功或真实链路完成。S02 执行状态为 **BLOCKED**，原因是缺少可合法用于验收的认证上下文和授权数据源，不是已观察到的模型或知识库业务调用失败。

## 5. 阶段 A3：自动化回归

### 5.1 Task 全量 pytest

执行命令：

```powershell
$taskTests = @(Get-ChildItem -Path tests -Filter 'task_*.py' | ForEach-Object { $_.FullName })
& .\.venv\Scripts\python.exe -m pytest @taskTests -q
```

结果：**169 passed, 2 skipped, 2 subtests passed in 91.00s**。Task 专项共 16 个测试文件。与 P6 记录的 `169 passed, 2 skipped` 相比通过/跳过数量相同，没有回归差异。

### 5.2 PostgreSQL 专项测试

执行命令：

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/task_postgres_sop_test.py -q
```

结果：**2 skipped in 9.05s**。`LXSCOPE_TASK_TEST_DATABASE_URL` 未设置。测试文件明确要求 URL 的数据库名以 `lxscope_task_test` 开头，并会创建、删除随机后缀数据库；因此没有尝试使用现有应用数据库。

### 5.3 编译、ESLint、构建及 diff

- Python：对 `examples/agent_service/task` 和全部 `tests/task_*.py` 执行 `python -m compileall -q`，**PASS**。
- Task 前端 ESLint：`pnpm exec eslint src/api/task.ts src/pages/task`，**PASS**。本轮修复 `task-template-gallery.tsx` 的两项导入排序错误后重跑通过。
- 前端构建：`pnpm build`，**PASS**（TypeScript 检查与 Vite 构建完成）；构建提示部分 bundle 超过 500 KB，属于已有体积警告，本轮没有改构建配置。
- `git diff --check`：**PASS**，仅有 CRLF 转换提示。

## 6. 阶段 A4：PostgreSQL 安全结论

`docker-compose.yml:2-11` 定义的 `postgres` 服务使用 `postgres-data` 持久卷。`docker-compose.yml:101-106` 将该 Postgres 配置为应用数据库，同时默认 Task Store 仍使用 Redis；后端启动日志也确认应用连接到 Postgres 的 `longxin_app` schema。因此这个 healthy 容器属于运行应用的持久化基础设施，不能当成一次性测试库。

主机环境没有 `LXSCOPE_TASK_TEST_DATABASE_URL`、`DATABASE_URL`、`POSTGRES_URL` 或相关测试 URL。没有运行 SQL、迁移或任何写操作。PostgreSQL 专项为 **BLOCKED**，不是数据库测试通过。

安全准备方案：另行提供与应用/生产网络和数据卷隔离的一次性 PostgreSQL 集群；创建空的 `lxscope_task_test` 基础库及仅供测试的账号，并确保该账号只在该隔离集群内有测试所需的 `CREATEDB`/删除临时库权限；在运行测试的本地进程中设置 `LXSCOPE_TASK_TEST_DATABASE_URL=postgresql+asyncpg://<test-user>:<secret>@<isolated-host>:<port>/lxscope_task_test`。不要复用 `lxscope2-postgres-1` 的数据卷或应用账号。

## 7. 阶段 A5：浏览器阻塞

本轮按请求没有调用 Computer Use，也没有使用浏览器自动化。因而九场景的模板展示、预览、创建、编辑、Preflight、节点详情、完整输出和产物下载均为 **NOT TESTED**。`/task` 返回 HTTP 200 仅验证静态路由，不是浏览器交互验收。

## 8. 故障与剩余条件

### 已确定的问题/阻塞

1. Task API 需要有效 Logto Bearer；本轮没有合法 CLI 会话。接口返回 401，服务和路由本身可达。
2. 无法在当前身份下核验 Agent/Session、模型、知识库权限和 Workspace；S02 的真实创建及运行保持 BLOCKED。
3. 已运行 PostgreSQL 是有持久卷的应用服务，不具备一次性测试库凭据；两项 PostgreSQL 集成测试安全跳过。
4. 本轮不进行浏览器操作，九场景真实 UI 交互仍待后续验收。
5. 没有发现后端 5xx、Task 错误日志或自动回归失败。

### 继续真实验收所需的最少条件

- 由用户在受信任环境中提供/启用合法 Logto 验收会话；无需在聊天中粘贴令牌。若使用本地受控命令行，请由本地测试进程安全读取短期令牌。
- 一个已授权的 Agent/Session 和有效模型配置；一个当前用户可访问、包含测试策划模板及项目委托资料的测试知识库；可读写的该用户 Workspace。
- 若需要 PostgreSQL 集成通过，提供与运行应用数据库隔离的一次性测试集群及 `LXSCOPE_TASK_TEST_DATABASE_URL`。
- 之后另行完成浏览器交互验收；本报告不替代浏览器部分。

## 9. 下一步建议

在上述身份和测试数据准备好后，先用只读 Task API 确认用户、模板目录、Agent/Session、知识库列表及 Workspace/工具 Preflight；随后由用户授权的测试会话创建 S02 v2 Task，并逐步核对证据快照、Claim–Evidence 校验和 DOCX 下载。PostgreSQL 测试仅在隔离的一次性数据库上运行。浏览器验收单独执行，不将 HTTP 200 作为交互通过证据。

本轮到此停止，不自动进入 P7-B，不提交代码。
