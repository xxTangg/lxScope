# Task R6：SOP 真实存储、并发与断点恢复验收报告

**日期：** 2026-10-22  
**分支 / 基准提交：** `integrate/dev_l` / `e63c3b80278dba2db5ddfd8e195b988481cabd1e`  
**结论：** 隔离单节点 Redis、PostgreSQL 与 TaskService SOP 多进程验收通过；生产准入 **NO-GO**。

## 1. 范围与工作区保护

R6 开始前保存的工作区快照位于 `%TEMP%\lxscope-task-r6-start-20261022-212340`，包括完整 `git status --short`、`git diff --name-only`、未暂存 patch 和 HEAD。开始时工作区已有 86 条 status 项及 39 个 tracked diff 路径，含本次白名单以外的既有前端、Sales Console、配置和其他文件改动。它们均未清理或覆盖。

本阶段没有修改 Task 生产实现。新增项只有：

- `tests/task_sop_r6_integration_test.py`：真实 PostgreSQL/Redis 与独立进程验收。
- `examples/agent_service/task/task_unified_execution_r6_acceptance_report.md`：本报告。

最终 `git status --short` 相对阶段开始快照只新增上述两个未跟踪文件；阶段开始已有的状态项仍在。`git diff --name-only` 仍显示原有 39 个路径，没有因 R6 新增 tracked diff。差异核对使用开始快照文件，不把先前已有改动归到 R6。

## 2. 隔离测试环境与安全边界

测试使用本机 loopback 上新建、随机命名的临时 Docker 网络和独立容器：PostgreSQL 17、Redis 7。未连接仍在运行的项目 PostgreSQL/Redis 容器，也未连接开发或生产服务。测试 URL 只通过进程环境变量提供，不写入源码或报告。

PostgreSQL 防护：测试仅接受 loopback 主机和 `lxscope_task_test*` 管理库 URL；每个参数化测试创建随机 `lxscope_task_test_r6_<uuid>` 数据库，结束后只删除该随机数据库。Redis 防护：要求 `LXSCOPE_TASK_TEST_REDIS_ISOLATED=1`、loopback 主机和专用 DB `/15`；使用随机用户、Run 和 key 前缀，清理只对本测试登记的 key 执行 `UNLINK`。没有执行 `FLUSHDB` 或 `FLUSHALL`。

PostgreSQL 测试 URL 环境变量：`LXSCOPE_TASK_TEST_DATABASE_URL`（格式为 `postgresql+asyncpg://<test-user>:<redacted>@127.0.0.1:<isolated-port>/lxscope_task_test`）。Redis 环境变量：`LXSCOPE_TASK_TEST_REDIS_URL`（`redis://:<redacted>@127.0.0.1:<isolated-port>/15`）及 `LXSCOPE_TASK_TEST_REDIS_ISOLATED=1`。报告不包含临时凭据。

Redis 持久化另用专用临时容器启用 AOF 与 `appendfsync always`，触发 Redis server 进程关闭并由容器内循环重启，然后从新 TaskService 进程读取 Run 并继续。此项证明服务进程重启后 AOF 状态可读，不代表宿主机/磁盘丢失恢复、Redis 集群故障转移、备份恢复或生产 HA。

## 3. 实际调用链与执行命令

新增验收测试由 `TaskService` 建立 Task/Run 并调用 Task 层的 `pause_run`、`step_run`、`continue_run`、`retry_node`、`submit_input` 和恢复/claim 路径；状态和事件通过真实 `PostgresTaskStore` 或 `RedisTaskStore` 读取。独立 `multiprocessing` 子进程使用同一隔离存储竞争 Run、恢复执行或在节点执行中退出。这里是 TaskService/store 真实后端链路，不将单独调度器或 Memory store 结果算作存储验收；本报告也不把未做的 HTTP UI 浏览器操作表述为 R6 证据。

执行的核心命令：

```powershell
# 先设置上节说明的隔离 URL 和隔离标志；值不写入仓库
.\.venv\Scripts\python.exe -m pytest tests/task_sop_r6_integration_test.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests/task_postgres_sop_test.py tests/task_dag_redis_integration_test.py tests/task_dag_p2h_recovery_test.py -q --tb=short
# 完整 Task 套件：收集 tests 下全部 task_*.py
$taskTests = Get-ChildItem tests -File -Filter 'task_*.py' | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest @taskTests -q --tb=short
```

真实后端单独验收：`tests/task_sop_r6_integration_test.py` 的 PostgreSQL/Redis 参数化场景 **4 passed**（两个测试场景 × 两种存储）。既有 P2-H PostgreSQL/Redis 与恢复专项 **14 passed，2 subtests passed**。完整 Task 套件最终 **358 passed，43 failed，1 warning，32 subtests passed**；运行耗时 368.52 秒。唯一 warning 是 DAG P2-G API 测试触发的 Starlette `HTTP_422_UNPROCESSABLE_ENTITY` 弃用提示。完整套件中的 43 项为基线失败，逐项差异见第 8 节。

## 4. SOP Run、NodeRun、Attempt 与控制验收

JSONL 证据写在本机 `%TEMP%\lxscope-task-r6-trace-20261022-215938.jsonl`；记录包含 Run/Node ID、engine、状态、控制模式、版本、事件序号、fencing epoch、快照指纹、attempt 历史及合成 executor 调用次数。它由本次测试生成，不是持久化业务数据。

| 验收 | 真实结果与代表性证据 | 结果 |
|---|---|---|
| SOP Pause、Step、跨进程 Continue | PostgreSQL Run `2d4639d5a0b44fb1875d4fcf558a4634`：A 执行一次后暂停，`queued/paused`，A succeeded/attempt 1，B/C pending；Step 后仅 B succeeded、C pending，调用序列为 A、B；新 TaskService/Worker Continue 后同一 Run succeeded，A/B/C 均 attempt 1，版本/事件序号从 7/8 → 14/15 → 21/22。Redis 对应 Run `31bc3d48a1254efeb4ebedf7b40070c5` 得到相同断点结果。 | PASS |
| 节点失败局部重跑 | PostgreSQL Run `f7174d071e0848c9aa4a0dbeeb962126`：首轮 A/B succeeded、C failed attempt 1、D pending；重跑后同 Run succeeded，A/B 仍 attempt 1，C 历史为 failed/succeeded 且 attempt 2，D attempt 1；合成调用计数 A=1/B=1/C=2/D=1。Redis Run `d9b85637ad1943d59e01980c46ef0411` 结果一致。重复 request_id 返回幂等结果；陈旧版本冲突被拒绝。 | PASS |
| 副作用安全门禁 | Tool 节点 retry 未携带 `side_effects_verified=True` 时被拒绝；明确确认后才能进入重试路径。崩溃恢复中副作用结果未知的节点采用人工核验标记，不自动再次执行。 | PASS（测试门禁）；真实业务副作用仍需人工确认策略 |
| 人工 InputStep 重启 | PostgreSQL Run `970c72c40975424293651dcec5aa812d` 在 `awaiting_input` 重启前保留 ask attempt 1、deliver pending；新 Service 提交答案后同一 Run succeeded，deliver 执行一次。Redis Run `9097db6e0b1248e0a6b622d5c4e65d22` 同样通过。 | PASS |
| Stop 与终态保护 | PostgreSQL Run `fb5abb25a6324e71a389dc0de2758a22` / Redis Run `576ccd9e294d4f779306c2c1450b16e5`：节点 X 执行中收到 Stop，Run 为 canceled/control `stopped`，Y 不执行；Continue 被拒绝。 | PASS |
| SOP 节点身份和快照 | Run 使用 `engine=sop`、稳定 `node_snapshot_fingerprint` 与有序节点快照；检查结果保存/恢复后未改写成功前驱。测试中的版本变化检查基于合成 Task/Run 元数据，不是 S01–S09 注册模板的真实升级迁移验收。 | PASS（合成快照场景） |

## 5. PostgreSQL 并发、事务与 fencing

- **CAS：** 100 个并发竞争更新后，`run_version=100`、`event_sequence=101`，没有丢失更新；Run `139f7ae84e7d40bc89f8fb60e3e81ab8`。另一个参数化场景通过服务入口对同一 SOP Run 执行并发变更。
- **原子性：** 通过隔离数据库中的事件插入故障注入，Run/Node CAS 事务回滚；状态仍为原始版本 0、事件序号 1。没有出现 Run 已变但事件缺失的部分提交。
- **执行权竞争：** 两个独立子进程竞争恢复同一 Run，只有一个取得 claim；节点只执行一次。代表 Run `625087814ad240fa97fecc6263d7919d`。
- **租约与 fencing：** 同 Owner 续租不降低 epoch；租约过期后新 Owner 获得 epoch 2；旧 Owner 的迟到写回被拒绝。记录 Run `9e1302fbdb5e4371ad5aba248660bc40`，测试最终 epoch=2。
- **幂等及终态：** 重复控制/重试请求按 request_id 幂等；版本冲突不覆盖更新状态；Stop 的 canceled 终态不能通过 Continue 或迟到执行结果恢复。
- **重建：** 独立 TaskService 实例从 PostgreSQL 读取现有 Run、NodeRun、Attempt、事件后恢复同一 SOP Run。

结果：该临时单节点 PostgreSQL 实测通过。未验证 PostgreSQL 主从切换、连接中断期间不确定提交、磁盘故障、备份恢复、生产负载及跨区域一致性。

## 6. Redis 并发与持久化

- 使用真实 Redis 7 client/store；100 次 SOP CAS 并发最终 version=100、event_sequence=101；WATCH/CAS 冲突按预期重试/报告冲突，没有观察到版本覆盖。
- 两个独立进程竞争单一 Run，只有一个执行节点；租约过期后 fencing epoch 提升，旧 Owner 写回拒绝。
- Run 状态和有序事件序号一起读取；暂停、Step、Continue、失败重试、输入恢复和 Stop 的事件/状态与 PostgreSQL 场景一致。
- AOF 服务重启用例：Run `3be19399e28e448587e850ca60f6cb36` 在重启前为 SOP `queued/paused`，A succeeded、B pending；Redis server `run_id` 改变且恢复 PONG。新 TaskService 继续后同一 Run succeeded，A/B attempt 均为 1，只有 B 由恢复 Worker 执行，最终 version/event_sequence=14/15。Redis `INFO persistence` 显示 AOF enabled。
- Redis 测试只清除随机测试 key，没有清库。AOF 的测试数据目录是临时 tmpfs；未验证宿主机重启/存储介质故障、集群 failover、复制延迟、RDB/AOF 文件损坏或备份还原。
- 未单独注入持续 WATCH 冲突直至 Redis CAS 重试预算耗尽；本阶段验证并发冲突收敛、版本冲突处理和 fencing，重试耗尽边界仍需后续专项用例。

## 7. 独立进程故障恢复矩阵

| 场景 | 证据与观察 | 结果 |
|---|---|---|
| queued Run 由进程退出后的另一 Worker 恢复 | child Worker 在 A/B 完成、C 尚待执行时退出；独立 Service 从持久化 Run 继续；成功前驱 attempt 保持 1。 | PASS |
| 节点执行期间强制退出 | PG Run `359a4223f2e147aabc56d4d89bf3a4a6`；A/B succeeded，C running attempt 1，version/event=10/10，子进程退出码 86。 | PASS（检测到不确定执行） |
| 外部副作用发生、checkpoint 未提交 | 合成 marker 记录 A/B/C 各发生一次。恢复将 C 标记 failed 且 `recovery_required=true`，Run failed，C 没有被自动重放。 | PASS（fail closed） |
| 两 Worker 同时恢复 | 两独立进程竞争一个 Run，只一方得到执行权，节点计数为 1。 | PASS |
| Pause 后服务重启 | 恢复仍处于 paused；不自动继续，只有显式 Step/Continue 才派发。 | PASS |
| InputStep 等待时重启 | `awaiting_input` 状态和问题快照保留，提交答案后同一 Run 继续，后续节点执行一次。 | PASS |
| Step 后服务重启 | Step 只派发 B；保存暂停检查点后新 Service Continue 只执行 C。 | PASS |
| 快照不一致 | mismatch Run `e776277d18fc49cdb58bf990f2f037c7` 在执行前 failed/拒绝，节点仍 pending、attempt 0，Executor 调用数 0。 | PASS |
| 模板版本变化 | 对 Run 快照版本与当前 Task 元数据版本分离的合成场景，恢复使用原 Run 的节点快照。未执行真实 S01–S09 注册模板版本升级或其外部依赖。 | 部分覆盖；真实模板迁移 BLOCKED |

Crash-recovery 本身的人工核验门禁通过 synthetic test 验证；业务 Tool/MCP 的副作用 exactly-once 不可能仅由本地状态存储保证，未连接外部服务，也未宣称 exactly-once。

## 8. 43 项失败基线与 DAG 回归

完整 Task 套件基线文件：`%TEMP%\lxscope-task-control-baseline-20261022-165208\pytest-task-final-2.txt`。本次完整运行文件：`%TEMP%\lxscope-task-r6-full-final-20261022-215938.txt`。逐项比较脚本：`%TEMP%\compare_task_failure_signatures_r6_v4.py`。

| 对比维度 | 基线 | R6 最终 | 比较 |
|---|---:|---:|---|
| 失败 nodeid | 43 | 43 | 完全一致、顺序一致 |
| 新增 / 消失 nodeid | — | — | 0 / 0 |
| 测试 header/name | 43/43 | 43/43 | 完全一致 |
| 异常类型 | 43/43 | 43/43 | 完全一致 |
| 断言行 | 43/43 | 43/43 | 完全一致 |
| 项目堆栈帧 | 43/43 | 43/43 | 完全一致 |
| 归一化完整签名 | 43/43 | 43/43 | 完全一致 |

结论：当前 43 项失败均可归类为 R0–R5 基线已存在；本轮无新回归。完整运行统计为 **43 failed、358 passed、1 warning、32 subtests passed**。这不是全套通过，43 项失败仍需单独处理。

DAG 回归：全套中 `tests/task_dag_*.py` 没有新增失败；既有 P2-H Redis/PostgreSQL/多进程专项 14 passed、2 subtests passed。故 DAG 回归 **PASS**。九大场景模板未编辑；本轮没有接入模型、MCP 或数据库依赖去端到端运行 S01–S09，模板依赖门禁与真实业务产物仍不是本报告证明的生产行为。

## 9. 文件修改与 Git 差异

R6 新增文件为上述测试与报告，均在用户允许范围。生产 Task 源码修改：**0**。没有提交代码。

最终核对命令：

```powershell
git diff --name-only
git status --short
```

阶段开始时的 39 个 tracked diff 路径仍为既有修改；R6 没有新 tracked diff。最终 status 中仅比初始快照增加本测试和本报告两个未跟踪文件。工作区中其他预存改动仍保留。新增测试文件通过 `py_compile`，并检查无行尾空白。

## 10. 剩余风险与准入判断

| 项目 | 状态 |
|---|---|
| SOP Redis 持久化并发（隔离单节点，含 AOF server-process restart） | **PASS** |
| SOP PostgreSQL 持久化并发（隔离单节点 PostgreSQL 17） | **PASS** |
| SOP 独立多进程故障恢复 | **PASS** |
| SOP Pause / Step / Continue / Stop | **PASS** |
| SOP 节点局部重跑 | **PASS** |
| DAG 回归 | **PASS** |
| 生产准入 | **NO-GO** |

NO-GO 原因：验收只覆盖隔离单节点环境，未验证生产拓扑与容量、Redis/PostgreSQL HA/故障切换和备份恢复、网络分区/不确定提交、真实业务 Tool/MCP 副作用与人工对账、S01–S09 注册模板升级迁移、权限与审计生产配置；且 Task 完整测试仍有 43 项基线失败。生产功能保持关闭。进入后续阶段前须先制定并执行生产拓扑验收、真实外部副作用人工确认/补偿策略、S01–S09 注册模板恢复验证和基线失败处置；本阶段不自动进入 R7。
