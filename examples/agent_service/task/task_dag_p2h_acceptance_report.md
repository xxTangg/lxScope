# 龙信Scope Task P2-H：真实 Redis/PostgreSQL 并发与故障恢复验收报告

- 日期：2026-10-19
- 分支：`integrate/dev_l`
- 阶段开始 HEAD：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`
- P2-H 未提交；未启用生产 DAG；未进入后续阶段。

## 最终判定

| 验收项 | 结果 | 说明 |
|---|---|---|
| DAG 功能验收 | **PASS** | P2-G 的 Task API/TaskService DAG 用例在完整 Task 回归中通过；本次 DAG 专项的 60 项（既有 49 项及 11 项真实 Redis/恢复项）通过。 |
| Redis 持久化并发 | **PASS** | 真实 Redis 7 上的 CAS、WATCH 冲突、租约 fencing、事件序号、幂等及服务进程恢复用例通过。Redis 服务器重启后的磁盘持久化未验收，详见风险。 |
| PostgreSQL 持久化并发 | **PASS** | 真实 PostgreSQL 17 上的三项 PostgreSQL 集成用例通过，含 CAS、事务回滚、claim/fencing、迁移兼容、Service 重建与续跑。 |
| 多进程恢复 | **PASS** | 6 个真实独立子进程恢复/竞争用例通过；未知副作用节点进入人工核验，不自动重放。 |
| 生产启用 | **CONDITIONAL GO** | 仅表示 P2-H TaskStore 并发和 Worker 恢复验收通过。DAG 保持默认关闭；Redis 磁盘恢复、PostgreSQL HA/故障切换等运行条件完成验证前不得开启生产 DAG。 |

## 隔离测试环境与安全边界

启动前检查发现工作区已有长期运行的 Compose 服务：`lxscope2-postgres-1`（PostgreSQL 16，挂载持久数据卷）及 `lxscope2-redis-1`（Redis 7，挂载持久数据卷）。本阶段没有连接它们，也没有读取或清理其中数据。

本阶段每轮使用单独临时容器：

- PostgreSQL：`postgres:17-alpine`，测试库 `lxscope_task_test`，监听仅绑定 `127.0.0.1:25432`。测试按需创建并删除随机 `lxscope_task_test_<uuid>` 数据库。无宿主机卷。
- Redis：`redis:7-alpine`，密码认证，使用逻辑库 15，监听仅绑定 `127.0.0.1:26379`。测试只写带随机 ID 的 `lxscope:task` 测试键，并对自己拥有的键调用 `UNLINK`；没有执行 `FLUSHDB` 或 `FLUSHALL`。无宿主机卷。
- 两个容器使用随机测试凭证，标记为 `codex.task=p2h-acceptance`，各轮结束后仅删除本阶段创建的临时容器。最后检查没有遗留带该标签的运行容器。
- Redis 容器使用 `--save "" --appendonly no`，以避免测试实例写入任何长期数据。由此可以验收 Redis 运行期间的 Task 快照/事件一致性和 Task Service/Worker 进程重启恢复，不能据此声称 Redis 服务进程崩溃后的 RDB/AOF 磁盘恢复已通过。
- 测试执行器与验收器均为确定性本地适配器；没有连接真实模型、生产 MCP、生产 Redis 或生产 PostgreSQL。

复现时必须先单独准备隔离实例，再显式设置以下变量；PostgreSQL 账号只应在隔离实例内拥有创建/删除 `lxscope_task_test_*` 数据库的权限：

```powershell
$env:LXSCOPE_TASK_TEST_DATABASE_URL = 'postgresql+asyncpg://<test-user>:<secret>@127.0.0.1:<pg-port>/lxscope_task_test'
$env:LXSCOPE_TASK_TEST_REDIS_URL = 'redis://:<secret>@127.0.0.1:<redis-port>/15'
$env:LXSCOPE_TASK_TEST_REDIS_ISOLATED = '1'
```

完整 Task 套件命令（PowerShell 将文件名展开后传给 pytest）：

```powershell
$taskFiles = Get-ChildItem tests -Filter 'task_*.py' | Sort-Object Name | ForEach-Object { $_.FullName }
& .\.venv\Scripts\python.exe -m pytest @taskFiles -q --tb=long
```

PostgreSQL 测试执行 SQL Migration 兼容测试并创建/删除随机测试数据库；不得对开发或生产 URL 设置 `LXSCOPE_TASK_TEST_DATABASE_URL`。Redis 集成测试未设置隔离确认变量时不会运行。

## 实际调用链

### DAG/API 与 TaskService

P2-G 的 API 验收测试仍随完整 Task 套件运行：测试挂载 `task_router` 并注入 `TaskService`，经 HTTP 路由创建、启动和控制 Task Run；确定性执行器记录调用次序、并行节点及绑定输入。P2-G 的 49 项 DAG 专项用例全部通过。本阶段没有修改 API 或 DAG 业务实现。

### PostgreSQL

`TaskService.create_task` 创建 Task → `TaskService.create_run` 写入 DAG Run → `PostgresTaskStore.save_run` 创建快照与 `run.created` 事件 → `PostgresTaskStore.transition_run` 在事务内锁定 Run、检查 `expected_version`、更新快照并追加事件。测试使用两个 Store 实例竞争 claim，再通过新 Store/新 TaskService 读取已有 Run，并 Continue 未完成节点。

事务故障测试在事件 `INSERT` 前注入异常，随后从真实 PostgreSQL 重新读取 Run 和事件，确认 Run 状态/版本与事件集合都保持回滚前值。

### Redis 与 Worker 恢复

Redis 存储用例通过真实 `RedisTaskStore.save_run`、`transition_run`、`claim_run`、事件读取接口验证原子状态更新。恢复用例用 `multiprocessing` 的 `spawn` 启动独立 Python 子进程：一个进程保存 queued/running 检查点后退出，另一进程创建真实 `TaskService` 并调用 `get_run`；TaskService 懒恢复路径执行 claim、fencing 和 DAG 节点检查点。

节点成功返回但成功检查点尚未写入时，测试子进程由 `os._exit(86)` 强制退出。新 Worker 恢复后将节点标记为 `recovery_required=true`、`recovery_policy=manual_verification`，没有再次执行带外部副作用标记的节点。

## DAG、Redis 与 PostgreSQL 结果

### DAG 与旧路径

- 原有 DAG 专项：P2-G 基线 **49 passed**；本次重新运行仍为 49 项全部通过。
- P2-H 新增的真实 Redis 存储和多进程用例共 **11 passed**。
- DAG 文件合计 **60 passed**。完整 Task 套件也重新运行 P2-G 的 API、Run 控制与重跑回归。
- DAG 开关仍由 Task 本地环境变量 `LXSCOPE_TASK_DAG_ENABLED` 显式开启；未设置为 `1/true/yes/on` 时关闭。证据见 `examples/agent_service/task/_service.py:771` 与 `:893-902`。P2-H 未更改该默认行为。
- 旧 SOP/人工输入、AI 验收、产物、九大模板及权限相关测试均包含在完整 `tests/task_*.py` 回归中；其中 40 项旧失败与基线逐项一致，不能据此声称所有旧业务断言已通过。

### Redis：11 项通过

`tests/task_dag_redis_integration_test.py` 的 5 项真实 Redis 用例全部通过：

1. 100 个并发 CAS 更新均提交；Run `run_version=100`、`event_sequence=101`，事件序号连续，且观测到 CAS 冲突。
2. 强制真实 WATCH 失效 16 次后，重试上限按预期报冲突；Run 快照、版本和 `run.created` 事件保持不变。
3. 同一 Owner 续租不增加 fencing epoch；租约有效时拒绝其他 Owner，过期后新 Owner 获得新 epoch；旧 Owner 及终态迟到写入均被拒绝。
4. 重复 `request_id` 不重复写事件；快照版本与事件序号一致；关闭并重建 Redis 客户端后可读取原 Run 与事件；旧 `update_run`/`append_event` 接口不能绕过 DAG 版本检查。
5. 两个独立进程竞争同一 Run claim，只有一个成功。

`tests/task_dag_p2h_recovery_test.py` 的 6 项真实子进程用例全部通过：queued 与已持久化的 pre-node running Run 各 1 项；节点成功后、checkpoint 前退出 1 项；节点执行中强制终止 1 项；两进程同时恢复 1 项；过期 Owner 的迟到写回 1 项。运行中/结果未知的节点保持人工核验要求，外部副作用执行计数未被增加。

Redis 测试用例没有重启 Redis 服务器进程，磁盘 AOF/RDB 恢复与 Redis 节点故障切换仍未验证。`PASS` 只覆盖真实 Redis 服务运行期间的 Task 持久化、并发、幂等、客户端/Worker/TaskService 重建路径。

### PostgreSQL：3 项通过

`tests/task_postgres_sop_test.py` 在隔离 PostgreSQL 上 **3 passed**：

- 100 个调用者同时以同一 `expected_version` 竞争，只有一个 CAS 能提交该版本，其余 99 个收到冲突；随后用 CAS 返回版本完成总计 100 次提交。最终 `run_version=100`、`event_sequence=101`、事件序号为 `1..101`。
- 在事件插入前注入异常后，快照版本、控制状态和事件记录全部回滚；重复 `command_id` 返回原结果且不重复产生事件。
- 两个 Store 竞争执行权时只有一个 Owner 获胜；同 Owner 续租保持 epoch；租约有效时拒绝其他 Owner，过期后 epoch 增加，旧 Owner 写入被拒；`SUCCEEDED` 终态拒绝迟到失败写入。
- 新 `PostgresTaskStore`/新 `TaskService` 能读取既有 Run，并从 paused checkpoint Continue：保留成功节点 A，只执行 pending 节点 B，Run ID 不变并最终成功。
- `test_existing_database_upgrade_preserves_data_and_constraints` 从旧 Migration 状态升级，旧 Task/Run 数据保留，状态约束生效，重复启动不重复应用 Migration。没有修改任何 Migration 文件。

PostgreSQL 验收运行于本地单节点 PostgreSQL 17 容器；没有覆盖真实生产拓扑的备份恢复、主从故障切换、连接池限额和网络分区。

## Run/Node 状态与恢复策略记录

| 场景 | 状态变化/观察结果 |
|---|---|
| Redis 100 次 CAS | 新建 Run：`version=0/event_sequence=1` → 100 次提交后 `version=100/event_sequence=101`；100 个事件序号无重复/缺口。 |
| PostgreSQL 100 次 CAS | 同上；事务失败注入后仍保留 `version=100/event_sequence=101`，没有半写事件。 |
| claim/租约续期 | 第一个 Owner 获得 `fence_epoch=1`；相同 Owner 续租仍为 1；Lease 过期后替代 Owner 获得 epoch 2；旧 Owner 的状态转换失败。 |
| queued/pre-node 服务退出 | 种子进程只写入持久化状态后退出；新 TaskService 通过 `get_run` 懒恢复，节点从 `pending` 执行到 `succeeded`，Run 到 `succeeded`，执行计数为 1。 |
| 节点成功但 checkpoint 未写 | 执行器副作用标记计数为 1，节点检查点仍为 `running`；进程退出后恢复成 `recovery_required/manual_verification`，计数保持 1，不自动重放。 |
| Worker 在节点中被杀 | 节点检查点为 `running`；Lease 过期后新 Worker 把该节点留给人工核验，不重放；副作用计数仍为 1。 |
| 并发恢复/迟到 Owner | 两个恢复进程只有一个 `run.claimed` 和一次节点执行；新 Owner 拿到 epoch 2 后，旧进程的写回被拒绝。 |
| PostgreSQL 新 Service Continue | 成功节点 A 保留；pending 节点 B 仅执行一次；Run 从可继续状态收敛到 `succeeded`。 |

对 queued Run 的自动/懒恢复通过 TaskService 调用验证。对已开始执行而结果不确定的 Tool/Python/外部副作用节点，测试验证系统要求人工核验，没有宣称自动恢复安全。

## 测试汇总与基线差异

| 测试阶段 | 结果 |
|---|---|
| 真实 Redis 集成 + 子进程恢复 + PostgreSQL SOP | 最终完整套件中新增 Redis/恢复 11 项通过；PostgreSQL 集成 3 项通过。 |
| DAG/SOP 专项（9 个 `task_dag_*.py` 文件、PostgreSQL SOP、输入协议） | **67 passed, 2 failed, 1 warning, 2 subtests passed**，327.26 秒。2 个失败是既有人工输入恢复失败，nodeid 与 P2-G 基线相同。 |
| 完整 `tests/task_*.py`（36 个文件） | **40 failed, 334 passed, 0 skipped, 1 warning, 23 subtests passed**，378.13 秒。 |
| 静态检查 | 新增/修改的 3 个 Python 测试文件 `compileall` 通过；`git diff --check` 通过。 |

P2-G 完整 Task 套件基线为 **40 failed, 320 passed, 3 skipped, 21 subtests passed**。P2-H 完整套件的失败 nodeid 集合与基线集合相同，40/40；异常类型、失败断言对应源码行、项目 Python 堆栈路径序列均逐项一致，40/40。对比依据是 P2-G 报告中的 40 行失败清单及本阶段完整 `--tb=long` 日志；新增 P2-H 文件和报告已纳入白名单。

通过数增加 14、跳过数减少 3：既有 3 个 PostgreSQL 集成测试从 P2-G 的跳过转为真实 PostgreSQL 通过，新增 11 个真实 Redis/多进程测试通过；PostgreSQL 原有的 2 个输入模式子测试本次实际运行并通过。未发现新增回归。唯一 warning 是既有 FastAPI/Starlette `HTTP_422_UNPROCESSABLE_ENTITY` 弃用提示。

完整回归中仍有 40 项既有失败，集中在 Task AI verifier/旧 SOP、S02/S07 模板断言及人工输入恢复等。虽然差异为零，仍需在处理这些历史失败后再声明旧业务套件全通过。

## P2-H 风险与生产前置条件

1. Redis AOF/RDB、Redis 服务进程重启后的磁盘恢复、Redis HA/故障切换未验收。本次隔离 Redis 禁用了 RDB/AOF，不能把 Task Service 重建结果等同于 Redis 节点重启恢复。
2. PostgreSQL 使用单节点容器；生产 TLS、认证/授权边界、备份恢复、主从/故障切换、网络分区和真实生产连接池配置未验收。
3. 100 路 CAS 与多 Worker 争用在本地服务端通过；生产负载、Redis 延迟、PostgreSQL 高并发连接上限及容量数据仍需在隔离 staging 环境测量。
4. 任何 `recovery_required=true` 的副作用节点都需要业务侧核验和明确恢复决策。未知结果不能自动重跑。
5. 持续保持 `LXSCOPE_TASK_DAG_ENABLED` 默认关闭；仅在配置并验证 Redis 磁盘持久化/恢复、PostgreSQL 备份与故障切换、监控告警和回滚预案后，再单独审批生产启用。

## Git 快照、阶段改动与范围核对

阶段开始时先读取并记录了 `git diff --name-only` 与 `git status --short`。下列已修改的主框架以外文件及未跟踪目录在 P2-H 开始时已存在，本阶段未编辑、删除或清理：`examples/agent_service/admin_api.py`、`examples/web_ui/frontend/**`、`sales-console/**`、`.pnpm-store/`、`examples/agent_service/.playwright-mcp/`、`examples/agent_service/blobs/`、`output/`、`tmp/`。`tests/task_postgres_sop_test.py` 在阶段开始前已有工作区改动，本次只在此白名单测试文件内扩展真实数据库断言。

P2-H 实际编辑范围：

- `tests/task_postgres_sop_test.py`：扩充真实 PostgreSQL CAS、事件事务回滚、幂等命令、claim/lease/fencing、终态保护及 Service 重建/Continue 验收；加入测试专用确定性 verifier。
- `tests/task_dag_redis_integration_test.py`：新增真实 Redis WATCH/CAS、幂等、租约、事件与多进程 claim 用例。
- `tests/task_dag_p2h_recovery_test.py`：新增真实 Redis 下的独立子进程退出、恢复、强制终止、并发恢复和迟到写回 fencing 用例。
- `examples/agent_service/task/task_dag_p2h_acceptance_report.md`：本报告。

未修改任何 Task 生产实现、AgentScope 主框架、`main.py`、其他业务模块、九大业务模板、项目依赖、全局配置或 Migration。

### 阶段开始时 `git diff --name-only`

这些 tracked 文件均为阶段开始前已有工作区改动；本阶段结束时 tracked 路径清单与其一致：

```text
examples/agent_service/admin_api.py
examples/agent_service/task/_models.py
examples/agent_service/task/_postgres_store.py
examples/agent_service/task/_redis_store.py
examples/agent_service/task/_router.py
examples/agent_service/task/_service.py
examples/agent_service/task/_store.py
examples/web_ui/frontend/src/api/admin.ts
examples/web_ui/frontend/src/features/longxin-admin/RechargeMethodsCard.tsx
sales-console/docs/API-CHECKLIST.md
sales-console/docs/API-CONTRACT.yaml
sales-console/docs/DATA-MODEL.md
sales-console/docs/管理系统对接资料与开发清单.md
sales-console/services/sales-api/src/app.ts
sales-console/services/sales-api/src/types.ts
tests/task_postgres_sop_test.py
```

### 阶段结束 `git status --short`

```text
 M examples/agent_service/admin_api.py
 M examples/agent_service/task/_models.py
 M examples/agent_service/task/_postgres_store.py
 M examples/agent_service/task/_redis_store.py
 M examples/agent_service/task/_router.py
 M examples/agent_service/task/_service.py
 M examples/agent_service/task/_store.py
 M examples/web_ui/frontend/src/api/admin.ts
 M examples/web_ui/frontend/src/features/longxin-admin/RechargeMethodsCard.tsx
 M sales-console/docs/API-CHECKLIST.md
 M sales-console/docs/API-CONTRACT.yaml
 M sales-console/docs/DATA-MODEL.md
 M sales-console/docs/管理系统对接资料与开发清单.md
 M sales-console/services/sales-api/src/app.ts
 M sales-console/services/sales-api/src/types.ts
 M tests/task_postgres_sop_test.py
?? .pnpm-store/
?? examples/agent_service/.playwright-mcp/
?? examples/agent_service/blobs/
?? examples/agent_service/task/_dag.py
?? examples/agent_service/task/task_dag_p2g_acceptance_report.md
?? examples/agent_service/task/task_dag_p2h_acceptance_report.md
?? output/
?? tests/task_dag_config_test.py
?? tests/task_dag_control_test.py
?? tests/task_dag_p2g_acceptance_test.py
?? tests/task_dag_p2h_recovery_test.py
?? tests/task_dag_redis_integration_test.py
?? tests/task_dag_retry_test.py
?? tests/task_dag_scheduler_test.py
?? tests/task_dag_store_test.py
?? tests/task_dag_test.py
?? tmp/
```

P2-H 新增的 untracked 文件都位于 Task 白名单内。阶段结束重新执行了 `git diff --name-only` 和 `git status --short`；白名单外既有状态与阶段开始快照相同。没有提交代码，临时日志已删除，本阶段创建的 Redis/PostgreSQL 容器已清理。

## P2-H 完成判断

真实 Redis 与 PostgreSQL TaskStore 并发、事件原子性、租约 fencing、服务/Worker 多进程恢复验收通过；P2-G DAG/API 回归通过，完整 Task 失败集合与旧基线一致。可以结束 P2-H 验收并进入交付评审。生产 DAG 仍保持关闭；本报告不构成生产开启授权，Redis 磁盘恢复与数据库 HA 前置条件仍须完成。
