# 龙信Scope Task DAG-E2-F 真实执行验收报告（中断恢复与环境收尾）

日期：2026-10-26（Asia/Shanghai）  
范围：仅恢复既有 S01 v3.0.0 非线性 DAG Run，不新增业务功能。  
结论更新：**同一 Run 的失败节点 Retry、Continue 与最终交付现已通过隔离环境真实浏览器验收。** 本报告前半部分保留首次中断时的历史快照；最终诊断与恢复事实以第 9 节为准。本轮未修改源码、模板、配置或官方业务数据，只通过真实 Task 页面操作隔离 Run，并以只读查询核验持久化结果。

> **状态时间边界：** 第 2～6 节的 Run v32 / event seq34 结果，是 Retry 被服务端接受之前的有效历史快照，不是当前最终状态。Retry 后同一 Run 到达 v55 / event seq57 并成功完成，详见第 9 节。

## 1. 环境安全检查与收尾（首次中断阶段记录）

| 资源 | 只读确认 | 本轮处理 |
|---|---|---|
| 前端 | http://localhost:8000；既有标签页元数据指向 /task/6cb85879ba7e4ff3bd8acba70c8852b4。该标签页曾显示目标 Task 与失败节点操作面板。 | 尝试一次 UI 点击后浏览器控制超时；不再操作。 |
| 独立 Task 测试服务 | PID 40292，python.exe，监听 127.0.0.1:18002，创建时间 2026-10-26 11:37:06。健康信息为 e2f-isolated-test、API/Task ready。与本轮 E2-F 浏览器测试服务对应。 | 保持运行以保留 Run 与测试执行器内存状态。 |
| PostgreSQL | 容器 task-e2f-pg-91d61622，PostgreSQL 16，监听 127.0.0.1:1911；标签 lxscope.task-e2f=true；tmpfs，无宿主机挂载。当前 Run 所在隔离库：lxscope_task_test_r6_22ffca2287ee4c8593bfd53e8c36d0ea。 | 保持运行；未清库、未删除记录。 |
| Redis | 容器 task-e2f-redis-91d61622，Redis 7，监听 127.0.0.1:9629；相同隔离标签；tmpfs，无宿主机挂载。只读 PING=true，测试 DB 15 的 DBSIZE=0。 | 保持运行；未执行 FLUSHDB/FLUSHALL。 |

浏览器服务使用 R6 _Backend 临时数据库；其 close() 会删除本次创建的临时数据库（源码：tests/task_sop_r6_integration_test.py:138-160）。为保留 Run 和重跑所需的测试执行器状态，本阶段没有停止 PID 40292，也没有停止/清理容器。当前环境收尾状态为：**隔离资源仍运行，等待恢复浏览器控制后再决定安全停止服务**。

未连接生产数据库、模型、MCP 或业务工具。当前 E2-F 执行器与知识库适配器为本地确定性合成实现。

## 2. Run 只读核对结果

通过 PostgreSQL 隔离测试库，以只读连接查询 longxin_app.task_runs 与 longxin_app.task_events；未写入数据库。

| 字段 | 核对值 |
|---|---|
| Run ID | d600e711c020419085212dbc124596b6 |
| Task ID | 6cb85879ba7e4ff3bd8acba70c8852b4 |
| 状态 / 版本 / 事件序号 | failed / run_version=32 / event_sequence=34 |
| 引擎 / 控制模式 | dag / single_step |
| 当前节点 | null（无在途节点） |
| Artifact | 0 条 |
| 最后一条事件 | seq 34 run.failed |
| Retry 请求 | 事件流中无 retry_requested 或其他 retry 事件；没有可确认的 retry request_id。 |

### 节点与 Attempt

| 节点 | Node ID | 最新状态 | Attempt / 历史 |
|---|---|---|---|
| 收集项目资料 | 0f04a5c10f62431794498bc73586eaf0 | succeeded | 1 / 1 |
| 识别立项字段 | fe2738f47c9a4b82ba79a2d5458e601b | succeeded | 1 / 1 |
| 检索相似项目 | 8588bc7f1f1540c989a48f43174a351b | succeeded | 1 / 1 |
| 核对立项规则 | 9ac29cf550404c7aa6b7531cf22f0341 | succeeded | 1 / 1 |
| 形成立项建议 | 376df46a54614a839fa6eac752763b3d | failed | 1 / 1，失败原因为“一次性合成可重试故障” |
| 核对项目事实（InputStep） | 1946898e491943b49ae803fed56fef6b | pending | 0 / 0 |
| 输出立项信息卡 | 67f469b626ef429b87642cce92bba42f | pending | 0 / 0 |

四个成功前驱的 Attempt 均仍为 1；失败节点的 Attempt 1 失败历史仍存在；两个后继均未执行。最初的“收集项目资料”InputStep 已通过浏览器提交合成演示资料并成功完成。后续“核对项目事实”仍处于 pending，尚未被跳过或作答。

### 末段事件记录

| 序号 | 事件 |
|---:|---|
| 27 | run.step_requested |
| 28 | run.claimed |
| 29 | run.started |
| 30 | step.started |
| 31 | step.attempt_rejected |
| 32 | step.rejected |
| 33 | step.failed |
| 34 | run.failed |

此前输入提交 request_id 为 ffd17844ab1343e0aa21f00afd5c75f3；单步请求 request_id 为 6ec1391f-f732-490c-bcbe-3de7df9327b6。Run 快照的控制状态是 single_step。没有 run.continued，所以本 Run 的 Continue 尚未验收。

## 3. 浏览器重跑恢复结果

1. 原 Task 页面显示 Task 6cb85879ba7e4ff3bd8acba70c8852b4、Run v32、失败节点“形成立项建议”，并显示“重跑节点”按钮及 Attempt 1 历史。
2. 因数据库确认无 Retry 事件，本轮从真实 Task 页面定位目标失败节点并尝试点击一次。
3. 浏览器输入调用在 Input.dispatchMouseEvent 阶段超时，未能确认点击是否送达。
4. 随后立即以只读 PostgreSQL 查询复核：仍是 Run v32、事件序号 34、节点 Attempt 1；没有 retry event/request_id，状态和 Artifact 数不变。**据服务端证据，重跑请求未被接受；不是“已接受但前端等待超时”。**
5. 原浏览器标签页随后无法再由浏览器控制 API 读取。新建标签页回到 /chat，Task 路由未能恢复并显示 Not Found；浏览器工具因无法可靠判定 Windows 浏览器的当前 URL 而终止本轮计算机操作。没有绕过认证，也没有调用 Retry API 或修改数据库快照。

因此本轮没有第二次 Attempt、下游恢复、最终 Markdown Artifact 或刷新恢复结果。浏览器恢复结果为 **BLOCKED**，不得将此次失败表述为重跑通过。

### 一次性故障注入

测试执行器源码 tests/task_dag_e2f_browser_server.py:194,220-225 在目标节点 order == 4 首次调用时先设置 _draft_failure_used=True，再返回确定性失败；之后不会对同节点再次注入该故障。数据库中已记录该目标节点的首次注入失败，且隔离服务进程仍运行，因此恢复所需执行器状态与 Run 数据仍在。该判断来自测试实现和首次失败证据；本轮没有执行 Attempt 2 验证。

浏览器截图曾在本会话工具输出中显示 Task 页面、Run 状态及重跑控件，但没有成功保存为独立 PNG 文件。本轮无法提供可下载的截图文件。

## 4. 控制状态与阶段结果

| 项目 | 结果 | 证据 |
|---|---|---|
| DAG 副本与真实 Task 页面 | PASS（此前已完成） | 现有 Task/Run 页面显示此 DAG Task；此前验收核对结构为 7 节点、8 边、9 字段绑定、并发度 3。 |
| 合成节点执行与并行检索前驱 | PASS（此前已完成） | 本 Run 四个前驱 succeeded，各一次；同一用户授权的合成 KB 适配器提供检索结果。 |
| Pause 安全边界 | PASS（此前已完成） | Run 事件 seq 26 run.paused；两条并行检索分支成功后暂停。 |
| Step 单节点派发 | PASS（此前已完成） | seq 27 run.step_requested，随后只启动“形成立项建议”；该节点失败后 Run 结束。 |
| Continue | BLOCKED | 无 run.continued 事件；页面控制无法继续。 |
| 失败节点局部重跑 | BLOCKED | Retry 未进入服务端，Attempt 仍为 1。 |
| 人工 InputStep 不跳过 | PASS（当前状态） | 后续“核对项目事实”保持 pending / Attempt 0。 |
| 最终 Markdown Artifact | BLOCKED | Run 仍 failed，数据库 Artifact 数为 0。 |
| 刷新后终态、历史与产物恢复 | BLOCKED | 浏览器控制失效，未完成刷新复核。 |
| Stop/终止门禁 | 未测试 | 本次 Run 未执行 Stop，未推断其结果。 |

## 5. 存储与外部依赖

### 真实隔离存储

此前本阶段实际运行记录：

- pytest -q tests/task_dag_e2f_storage_integration_test.py：**2 passed**（真实隔离 Redis/PostgreSQL 测试路径，非 Memory/SQLite/fakeredis 替代）。
- 定向能力与 S01 DAG 测试：**7 passed**。
- 测试 Harness py_compile：通过。
- R6 既有真实存储故障恢复套件：**16 passed、2 failed**。两个失败来自 test_sop_r6_integration_test.py::test_real_store_sop_subprocess_crash_fencing_and_worker_competition 的 PostgreSQL/Redis 参数化用例：合成 unknown template 不满足冻结模板契约。该历史问题没有通过放宽契约或测试断言处理。

本轮新执行的存储核对为 PostgreSQL 只读查询（Run/Node/Attempt/事件/Artifact）成功，以及 Redis PING=true, DB 15 DBSIZE=0。本轮未重新运行 pytest，因此上列通过数是本阶段此前记录，不代表这次浏览器重跑通过。

### 外部依赖边界

S01 两个检索分支使用本地合成、授权作用域的测试知识库适配器；没有调用真实外部知识库 Gateway。真实 Gateway 并发隔离、来源串扰和真实外部服务故障重试仍为 **BLOCKED**。没有连接生产模型、生产 MCP、ERP、审批或其他业务工具。

## 6. 测试与证据汇总

| 类型 | 结果 |
|---|---|
| 本轮 PostgreSQL 只读核对 | PASS；目标 Run 与事件可读，未执行写操作 |
| 本轮 Redis 健康检查 | PASS；PING true、DB15 空 |
| 本轮浏览器 Retry 请求 | BLOCKED；UI 输入超时且服务端无 Retry 事件 |
| 本轮 Run Attempt 2 / 下游 / Artifact | 未发生 |
| 本轮完整 Task pytest | 未运行；本轮无源码修复，且浏览器控制被工具终止 |
| 此前 E2-F Redis/PostgreSQL 集成测试 | 2 passed |
| 此前定向 Task DAG 测试 | 7 passed |
| 此前 R6 真实存储回归 | 16 passed、2 个既有合成模板契约失败 |

## 7. Git 状态与白名单对照

阶段开始快照位置：%TEMP%\task-dag-e2f-start-20261026-110651。快照记录分支 integrate/dev_l、HEAD e63c3b80278dba2db5ddfd8e195b988481cabd1e、基线 git status --short 112 项、git diff --name-only 52 项。

本次只读对比快照与本阶段报告前工作区状态：

- 112 项基线状态均保留；无状态位变化、无路径消失。
- git diff --name-only 为 52 项，与阶段快照完全相同；无新增或减少的 tracked diff。
- 快照之后新增的 3 个未跟踪文件均为 Task 专属 E2-F 验收测试：tests/task_dag_e2f_browser_server.py、tests/task_dag_e2f_capabilities_test.py、tests/task_dag_e2f_storage_integration_test.py。
- 本轮新增的唯一文件为本报告，位于 examples/agent_service/task/** 白名单内。
- 工作区里原有的非 Task 修改（包含 Docker 开发配置、Admin/Sales Console 等）已存在于阶段快照，本轮没有改动或清理。故整个工作区不是“白名单干净”；但本阶段没有新增白名单外变更。

下面附本报告写入后的完整 git diff --name-only 与 git status --short 输出，供核验。快照的完整原始输出保存在上列 TEMP 目录中的 diff-name-only.txt 与 status-short.txt。

## 8. P2-H / 生产准入判断（恢复前历史结论，已由第 9 节更新）

- 当时的 S01 E2-F 同 Run 局部重跑成功恢复：**BLOCKED（历史状态；后续已通过，见第 9 节）**。
- 当时的浏览器 Continue、最终 Markdown Artifact 与刷新后恢复：**BLOCKED（历史状态；后续已通过，见第 9 节）**。
- Redis/PostgreSQL 隔离测试：本阶段此前专项为 **PASS**，本轮只读健康及 Run 状态核查 **PASS**；不能替代 Attempt 2 浏览器验收。
- 外部真实知识库并发：**BLOCKED**。
- 生产准入：**NO-GO**。本报告不宣称浏览器恢复或生产就绪。
- 此节记录的是首次中断收尾时的决定。后续恢复后仍不扩展 S02～S09、不启用生产 DAG、不提交代码；隔离服务和数据库继续保留。

## 9. Retry 控制链路诊断与同一 Run 恢复（2026-10-26）

### 9.1 实际诊断结论

首次复核时 Run 为 failed / version 32 / event sequence 34，确实尚无 Retry 事件。随后使用用户确认已登录的本地隔离浏览器会话打开同一 Task，页面显示 Run v33。对同一隔离 PostgreSQL Run 做只读核对发现服务端已持久化：

| 项 | 证据 |
|---|---|
| Run / Task | `d600e711c020419085212dbc124596b6` / `6cb85879ba7e4ff3bd8acba70c8852b4` |
| Retry 事件 | seq 35 `node.retry_requested`，request_id `b35729fb-4a5e-4804-951b-1221e7e2c6da`，`manual_attempt=1` |
| Retry 后控制状态 | Run queued、`control_mode=paused`；目标节点回到 pending，历史 Attempt 1 保留；成功前驱仍 succeeded 且 Attempt 不变 |
| 失效下游 | InputStep `1946898e491943b49ae803fed56fef6b` 与最终输出节点 `67f469b626ef429b87642cce92bba42f` 被列入 `invalidated_node_ids` |

因此，服务端已接受 Retry；没有证据显示它被 HTTP 409/422 或业务门禁拒绝。Retry 语义会将 Run 置为 paused，需通过页面 Continue 或 Step 恢复派发。先前 `Input.dispatchMouseEvent` 超时本身不能证明点击没有送达；后续 event seq35 证明请求最终进入了持久化控制链路，但由于没有可取回的 HTTP access log，无法断言 seq35 与先前那次物理点击在时间上严格对应，也无法还原该请求的精确 HTTP 响应。

前端源码显示重跑处理器在发送 API 请求前对所有重跑都调用同步 `window.confirm(...)`（`examples/web_ui/frontend/src/pages/task/index.tsx` 的 `handleRetryNode`）；这可能影响自动化点击对原生确认框的处理。但缺少该次浏览器事件和 HTTP 日志，故将其记作**可能的浏览器控制交互因素**，不是已证实的根因。当前不能分类为“请求到达后被拒绝”或“Retry 成功但派发失败”：事实是 Retry 已接受并暂停排队，随后经 Continue 成功派发；准确说，首次操作来源无法完全追溯，服务端最终接受状态与恢复结果均可证实。

### 9.2 源码调用链与隔离 HTTP 验证

- Task 页面生成 `request_id`、携带 `expected_run_version` 与副作用确认字段；`handleRetryNode` 成功通过确认后调用 API Client。
- API Client 调用 `POST /tasks/runs/{run_id}/nodes/{node_id}/retry`。
- Task Router 将请求交给 `TaskService.retry_node`，接受后返回 HTTP 202；版本或状态冲突映射为 HTTP 409。
- TaskService 对目标节点、Run 版本、在途节点及副作用确认执行门禁；本次目标为无外部副作用的合成 Agent 节点，`side_effects_verified=false` 合法。接受 Retry 后重置目标与受影响下游并进入 queued/paused，等待后续控制派发。

独立 Task Router/ASGI TestClient 验证记录：

| 验证 | 结果 |
|---|---|
| 命令 | `\.venv\Scripts\python.exe -m pytest -q tests/task_dag_p2g_acceptance_test.py::TaskDagP2GApiAcceptanceTest::test_api_retry_failed_node_preserves_successful_prefix_and_is_idempotent tests/task_dag_retry_test.py::TaskDagRetryTest::test_retry_route_reports_conflicts_as_http_409` |
| 结果 | **2 passed in 12.04s**；覆盖成功前缀保留/幂等与冲突映射 |
| 独立 Router 调用 | 隔离内存测试服务中 start HTTP 202，retry HTTP 202；响应 queued、paused、目标 pending、Attempt 1 失败历史仍在 |
| 边界 | Router 调用使用新建的内存测试数据，仅验证 HTTP 契约，不是目标 Run 的 Retry，也不代表 Redis/PostgreSQL 集成结果 |

### 9.3 同 Run 浏览器恢复结果

在合法登录的本地 Task 页面中，没有再次点击 Retry（避免重复请求）。从页面点击 Continue，之后在真实 InputStep 中提交合成答案：`无补充。现有输入全部为合成演示资料；本次仅生成待业务人员复核的草稿，不构成审批、合规确认或外部系统写入。` 人工核对节点正常出现并完成，没有被跳过。之后 Run 自动完成剩余节点。

最终只读数据库核验与页面刷新结果：

| 对象 | 最终结果 |
|---|---|
| Run | 同一 Run `d600e711c020419085212dbc124596b6`，`succeeded`，version 55，event sequence 57，control mode continuous |
| 失败节点“形成立项建议” | Node `376df46a54614a839fa6eac752763b3d`，当前 succeeded，Attempt 2；Attempt 1 failed 历史保留，Attempt 2 为 manual 且 succeeded |
| 成功前驱 | 收集项目资料、识别立项字段、检索相似项目、核对立项规则仍各自 Attempt 1 成功；没有重跑或新增前驱调用 |
| 人工核对 | InputStep `1946898e491943b49ae803fed56fef6b` 成功，答案通过页面提交 |
| 后继输出 | `67f469b626ef429b87642cce92bba42f` 成功；此前未执行，Retry 后正常执行 |
| 事件与产物 | seq 56 `artifact.created`：Artifact `ed8d73294a1b4292b8988c6288b655a3`，`项目立项知识卡.md`，Markdown，3496 bytes；seq 57 `run.succeeded` |
| 刷新恢复 | 刷新并重新进入 Task 页面后仍显示 7/7 完成、Run v55、1 个 Markdown Artifact；页面预览为明确标注的合成草稿并含“待业务人员复核”说明 |

浏览器自动化工具没有把截图另存为独立 PNG 文件；页面状态和预览曾在本会话浏览器工具输出中可见。该证据是隔离环境的合成 Task 执行，不是外部模型、真实知识库、MCP、审批或业务系统集成验收。

### 9.4 日志、变更与最终判断

服务端日志无法取回：未找到 E2-F 临时日志文件，当前 Codex 线程没有附加该服务的终端会话；Uvicorn access log 并未配置为关闭，但进程输出没有可读的日志收集端。因此无法确认首次点击的 HTTP 到达时刻与返回码。浏览器控制超时也不构成请求失败证据。可确认的是：服务最终持久化了 Retry 请求，未见其被业务拒绝，随后 Continue 通过页面成功恢复并完成同一 Run。

本轮没有修改任何源码或测试；只更新本验收报告。Run 状态变化由合法 Task 页面上的 Retry/Continue/InputStep 操作产生，数据库查询只读。没有重放 Retry API、直接修改 Run JSON 或人工写入事件。隔离 Redis/PostgreSQL 服务保留且未清理；外部真实知识库并发仍 BLOCKED。

最终结论：**S01 隔离合成 Run 的浏览器 Retry→Continue→人工输入→Artifact 完整链：PASS。生产准入仍 NO-GO**，因为真实外部知识库并发和生产级外部依赖验收未完成。无需代码修复；不扩展 S02～S09，不启用生产 DAG，不提交代码。

## 附录：当前工作区状态

### 当前完整 git diff --name-only

<pre>
docker-compose.dev.yml
examples/agent_service/admin_api.py
examples/agent_service/task/_agentscope_executor.py
examples/agent_service/task/_capability_contracts.py
examples/agent_service/task/_evidence_contract.py
examples/agent_service/task/_models.py
examples/agent_service/task/_postgres_store.py
examples/agent_service/task/_redis_store.py
examples/agent_service/task/_router.py
examples/agent_service/task/_service.py
examples/agent_service/task/_sop.py
examples/agent_service/task/_store.py
examples/agent_service/task/_template_compiler.py
examples/agent_service/task/_template_registry.py
examples/agent_service/task/_template_validation.py
examples/web_ui/frontend/src/api/admin.ts
examples/web_ui/frontend/src/api/task.ts
examples/web_ui/frontend/src/features/longxin-admin/RechargeMethodsCard.tsx
examples/web_ui/frontend/src/pages/task/index.tsx
examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx
examples/web_ui/frontend/src/pages/task/task-header.tsx
examples/web_ui/frontend/src/pages/task/task-input-form.tsx
examples/web_ui/frontend/src/pages/task/task-knowledge-graph.tsx
examples/web_ui/frontend/src/pages/task/task-overview.tsx
examples/web_ui/frontend/src/pages/task/task-run-panel.tsx
examples/web_ui/frontend/src/pages/task/task-sidebar.tsx
examples/web_ui/frontend/src/pages/task/task-template-create-dialog.tsx
examples/web_ui/frontend/src/pages/task/task-template-gallery.tsx
examples/web_ui/frontend/src/pages/task/task-template-preview.tsx
sales-console/docs/API-CHECKLIST.md
sales-console/docs/API-CONTRACT.yaml
sales-console/docs/DATA-MODEL.md
"sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
sales-console/services/sales-api/src/app.ts
sales-console/services/sales-api/src/types.ts
tests/task_capability_contract_test.py
tests/task_clarification_test.py
tests/task_input_protocol_test.py
tests/task_mvp_pipeline_test.py
tests/task_nine_scenarios_test.py
tests/task_p7b_scenario_runtime_test.py
tests/task_p7r1_output_contract_test.py
tests/task_postgres_sop_test.py
tests/task_project_planning_rag_test.py
tests/task_s02_content_repair_test.py
tests/task_s02_lightweight_template_test.py
tests/task_s02_v4_delivery_test.py
tests/task_s07_rag_integration_test.py
tests/task_sop_test.py
tests/task_template_output_test.py
tests/task_template_test.py
tests/task_template_version_test.py
</pre>

### 当前完整 git status --short

<pre>
 M docker-compose.dev.yml
 M examples/agent_service/admin_api.py
 M examples/agent_service/task/_agentscope_executor.py
 M examples/agent_service/task/_capability_contracts.py
 M examples/agent_service/task/_evidence_contract.py
 M examples/agent_service/task/_models.py
 M examples/agent_service/task/_postgres_store.py
 M examples/agent_service/task/_redis_store.py
 M examples/agent_service/task/_router.py
 M examples/agent_service/task/_service.py
 M examples/agent_service/task/_sop.py
 M examples/agent_service/task/_store.py
 M examples/agent_service/task/_template_compiler.py
 M examples/agent_service/task/_template_registry.py
 M examples/agent_service/task/_template_validation.py
 M examples/web_ui/frontend/src/api/admin.ts
 M examples/web_ui/frontend/src/api/task.ts
 M examples/web_ui/frontend/src/features/longxin-admin/RechargeMethodsCard.tsx
 M examples/web_ui/frontend/src/pages/task/index.tsx
 M examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx
 M examples/web_ui/frontend/src/pages/task/task-header.tsx
 M examples/web_ui/frontend/src/pages/task/task-input-form.tsx
 M examples/web_ui/frontend/src/pages/task/task-knowledge-graph.tsx
 M examples/web_ui/frontend/src/pages/task/task-overview.tsx
 M examples/web_ui/frontend/src/pages/task/task-run-panel.tsx
 M examples/web_ui/frontend/src/pages/task/task-sidebar.tsx
 M examples/web_ui/frontend/src/pages/task/task-template-create-dialog.tsx
 M examples/web_ui/frontend/src/pages/task/task-template-gallery.tsx
 M examples/web_ui/frontend/src/pages/task/task-template-preview.tsx
 M sales-console/docs/API-CHECKLIST.md
 M sales-console/docs/API-CONTRACT.yaml
 M sales-console/docs/DATA-MODEL.md
 M "sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
 M sales-console/services/sales-api/src/app.ts
 M sales-console/services/sales-api/src/types.ts
 M tests/task_capability_contract_test.py
 M tests/task_clarification_test.py
 M tests/task_input_protocol_test.py
 M tests/task_mvp_pipeline_test.py
 M tests/task_nine_scenarios_test.py
 M tests/task_p7b_scenario_runtime_test.py
 M tests/task_p7r1_output_contract_test.py
 M tests/task_postgres_sop_test.py
 M tests/task_project_planning_rag_test.py
 M tests/task_s02_content_repair_test.py
 M tests/task_s02_lightweight_template_test.py
 M tests/task_s02_v4_delivery_test.py
 M tests/task_s07_rag_integration_test.py
 M tests/task_sop_test.py
 M tests/task_template_output_test.py
 M tests/task_template_test.py
 M tests/task_template_version_test.py
?? .pnpm-store/
?? examples/agent_service/.playwright-mcp/
?? examples/agent_service/blobs/
?? examples/agent_service/task/R0_task_control_gap_audit.md
?? examples/agent_service/task/_dag.py
?? examples/agent_service/task/_execution_gates.py
?? examples/agent_service/task/task_dag_e1_business_gate_parity_report.md
?? examples/agent_service/task/task_dag_e2_non_linear_s01_acceptance_report.md
?? examples/agent_service/task/task_dag_e2f_real_execution_acceptance_report.md
?? examples/agent_service/task/task_dag_p2f2_acceptance_report.md
?? examples/agent_service/task/task_dag_p2f3_acceptance_report.md
?? examples/agent_service/task/task_dag_p2g_acceptance_report.md
?? examples/agent_service/task/task_dag_p2h_acceptance_report.md
?? examples/agent_service/task/task_dag_template_save_compatibility_report.md
?? examples/agent_service/task/task_r9a_business_gate_acceptance_report.md
?? examples/agent_service/task/task_unified_execution_r6_acceptance_report.md
?? examples/agent_service/task/task_unified_execution_r7_nine_scenarios_acceptance_report.md
?? examples/agent_service/task/task_unified_execution_r8_browser_acceptance_report.md
?? examples/agent_service/task/task_unified_node_retry_checkpoint_acceptance_report.md
?? examples/agent_service/task/templates/s01_project_init.v3.json
?? examples/web_ui/frontend/src/pages/task/task-dag-canvas.tsx
?? examples/web_ui/frontend/src/pages/task/task-dag-utils.ts
?? examples/web_ui/frontend/src/pages/task/task-result-card-style.ts
?? examples/web_ui/frontend/src/pages/task/task-s01-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s02-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s02-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s03-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s03-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s04-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s04-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s05-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s05-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s06-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s06-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s07-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s07-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s08-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s08-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s09-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s09-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-template-labels.ts
?? examples/web_ui/frontend/src/pages/task/task_dag_p2f_acceptance_report.md
?? output/
?? tests/task_dag_config_test.py
?? tests/task_dag_control_test.py
?? tests/task_dag_e1_business_gate_test.py
?? tests/task_dag_e2_non_linear_s01_test.py
?? tests/task_dag_e2f_browser_server.py
?? tests/task_dag_e2f_capabilities_test.py
?? tests/task_dag_e2f_storage_integration_test.py
?? tests/task_dag_editing_test.py
?? tests/task_dag_p2g_acceptance_test.py
?? tests/task_dag_p2h_recovery_test.py
?? tests/task_dag_redis_integration_test.py
?? tests/task_dag_retry_test.py
?? tests/task_dag_scheduler_test.py
?? tests/task_dag_store_test.py
?? tests/task_dag_template_copy_test.py
?? tests/task_dag_test.py
?? tests/task_r7_nine_scenarios_integration_test.py
?? tests/task_sop_control_test.py
?? tests/task_sop_r6_integration_test.py
?? tests/task_sop_template_retry_test.py
?? tmp/
</pre>

## 附录：阶段开始快照的完整 Git 输出

### 阶段开始 git diff --name-only

<pre>
docker-compose.dev.yml
examples/agent_service/admin_api.py
examples/agent_service/task/_agentscope_executor.py
examples/agent_service/task/_capability_contracts.py
examples/agent_service/task/_evidence_contract.py
examples/agent_service/task/_models.py
examples/agent_service/task/_postgres_store.py
examples/agent_service/task/_redis_store.py
examples/agent_service/task/_router.py
examples/agent_service/task/_service.py
examples/agent_service/task/_sop.py
examples/agent_service/task/_store.py
examples/agent_service/task/_template_compiler.py
examples/agent_service/task/_template_registry.py
examples/agent_service/task/_template_validation.py
examples/web_ui/frontend/src/api/admin.ts
examples/web_ui/frontend/src/api/task.ts
examples/web_ui/frontend/src/features/longxin-admin/RechargeMethodsCard.tsx
examples/web_ui/frontend/src/pages/task/index.tsx
examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx
examples/web_ui/frontend/src/pages/task/task-header.tsx
examples/web_ui/frontend/src/pages/task/task-input-form.tsx
examples/web_ui/frontend/src/pages/task/task-knowledge-graph.tsx
examples/web_ui/frontend/src/pages/task/task-overview.tsx
examples/web_ui/frontend/src/pages/task/task-run-panel.tsx
examples/web_ui/frontend/src/pages/task/task-sidebar.tsx
examples/web_ui/frontend/src/pages/task/task-template-create-dialog.tsx
examples/web_ui/frontend/src/pages/task/task-template-gallery.tsx
examples/web_ui/frontend/src/pages/task/task-template-preview.tsx
sales-console/docs/API-CHECKLIST.md
sales-console/docs/API-CONTRACT.yaml
sales-console/docs/DATA-MODEL.md
"sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
sales-console/services/sales-api/src/app.ts
sales-console/services/sales-api/src/types.ts
tests/task_capability_contract_test.py
tests/task_clarification_test.py
tests/task_input_protocol_test.py
tests/task_mvp_pipeline_test.py
tests/task_nine_scenarios_test.py
tests/task_p7b_scenario_runtime_test.py
tests/task_p7r1_output_contract_test.py
tests/task_postgres_sop_test.py
tests/task_project_planning_rag_test.py
tests/task_s02_content_repair_test.py
tests/task_s02_lightweight_template_test.py
tests/task_s02_v4_delivery_test.py
tests/task_s07_rag_integration_test.py
tests/task_sop_test.py
tests/task_template_output_test.py
tests/task_template_test.py
tests/task_template_version_test.py
</pre>

### 阶段开始 git status --short

<pre>
 M docker-compose.dev.yml
 M examples/agent_service/admin_api.py
 M examples/agent_service/task/_agentscope_executor.py
 M examples/agent_service/task/_capability_contracts.py
 M examples/agent_service/task/_evidence_contract.py
 M examples/agent_service/task/_models.py
 M examples/agent_service/task/_postgres_store.py
 M examples/agent_service/task/_redis_store.py
 M examples/agent_service/task/_router.py
 M examples/agent_service/task/_service.py
 M examples/agent_service/task/_sop.py
 M examples/agent_service/task/_store.py
 M examples/agent_service/task/_template_compiler.py
 M examples/agent_service/task/_template_registry.py
 M examples/agent_service/task/_template_validation.py
 M examples/web_ui/frontend/src/api/admin.ts
 M examples/web_ui/frontend/src/api/task.ts
 M examples/web_ui/frontend/src/features/longxin-admin/RechargeMethodsCard.tsx
 M examples/web_ui/frontend/src/pages/task/index.tsx
 M examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx
 M examples/web_ui/frontend/src/pages/task/task-header.tsx
 M examples/web_ui/frontend/src/pages/task/task-input-form.tsx
 M examples/web_ui/frontend/src/pages/task/task-knowledge-graph.tsx
 M examples/web_ui/frontend/src/pages/task/task-overview.tsx
 M examples/web_ui/frontend/src/pages/task/task-run-panel.tsx
 M examples/web_ui/frontend/src/pages/task/task-sidebar.tsx
 M examples/web_ui/frontend/src/pages/task/task-template-create-dialog.tsx
 M examples/web_ui/frontend/src/pages/task/task-template-gallery.tsx
 M examples/web_ui/frontend/src/pages/task/task-template-preview.tsx
 M sales-console/docs/API-CHECKLIST.md
 M sales-console/docs/API-CONTRACT.yaml
 M sales-console/docs/DATA-MODEL.md
 M "sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
 M sales-console/services/sales-api/src/app.ts
 M sales-console/services/sales-api/src/types.ts
 M tests/task_capability_contract_test.py
 M tests/task_clarification_test.py
 M tests/task_input_protocol_test.py
 M tests/task_mvp_pipeline_test.py
 M tests/task_nine_scenarios_test.py
 M tests/task_p7b_scenario_runtime_test.py
 M tests/task_p7r1_output_contract_test.py
 M tests/task_postgres_sop_test.py
 M tests/task_project_planning_rag_test.py
 M tests/task_s02_content_repair_test.py
 M tests/task_s02_lightweight_template_test.py
 M tests/task_s02_v4_delivery_test.py
 M tests/task_s07_rag_integration_test.py
 M tests/task_sop_test.py
 M tests/task_template_output_test.py
 M tests/task_template_test.py
 M tests/task_template_version_test.py
?? .pnpm-store/
?? examples/agent_service/.playwright-mcp/
?? examples/agent_service/blobs/
?? examples/agent_service/task/R0_task_control_gap_audit.md
?? examples/agent_service/task/_dag.py
?? examples/agent_service/task/_execution_gates.py
?? examples/agent_service/task/task_dag_e1_business_gate_parity_report.md
?? examples/agent_service/task/task_dag_e2_non_linear_s01_acceptance_report.md
?? examples/agent_service/task/task_dag_p2f2_acceptance_report.md
?? examples/agent_service/task/task_dag_p2f3_acceptance_report.md
?? examples/agent_service/task/task_dag_p2g_acceptance_report.md
?? examples/agent_service/task/task_dag_p2h_acceptance_report.md
?? examples/agent_service/task/task_dag_template_save_compatibility_report.md
?? examples/agent_service/task/task_r9a_business_gate_acceptance_report.md
?? examples/agent_service/task/task_unified_execution_r6_acceptance_report.md
?? examples/agent_service/task/task_unified_execution_r7_nine_scenarios_acceptance_report.md
?? examples/agent_service/task/task_unified_execution_r8_browser_acceptance_report.md
?? examples/agent_service/task/task_unified_node_retry_checkpoint_acceptance_report.md
?? examples/agent_service/task/templates/s01_project_init.v3.json
?? examples/web_ui/frontend/src/pages/task/task-dag-canvas.tsx
?? examples/web_ui/frontend/src/pages/task/task-dag-utils.ts
?? examples/web_ui/frontend/src/pages/task/task-result-card-style.ts
?? examples/web_ui/frontend/src/pages/task/task-s01-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s02-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s02-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s03-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s03-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s04-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s04-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s05-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s05-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s06-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s06-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s07-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s07-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s08-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s08-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-s09-result-adapter.ts
?? examples/web_ui/frontend/src/pages/task/task-s09-results.tsx
?? examples/web_ui/frontend/src/pages/task/task-template-labels.ts
?? examples/web_ui/frontend/src/pages/task/task_dag_p2f_acceptance_report.md
?? output/
?? tests/task_dag_config_test.py
?? tests/task_dag_control_test.py
?? tests/task_dag_e1_business_gate_test.py
?? tests/task_dag_e2_non_linear_s01_test.py
?? tests/task_dag_editing_test.py
?? tests/task_dag_p2g_acceptance_test.py
?? tests/task_dag_p2h_recovery_test.py
?? tests/task_dag_redis_integration_test.py
?? tests/task_dag_retry_test.py
?? tests/task_dag_scheduler_test.py
?? tests/task_dag_store_test.py
?? tests/task_dag_template_copy_test.py
?? tests/task_dag_test.py
?? tests/task_r7_nine_scenarios_integration_test.py
?? tests/task_sop_control_test.py
?? tests/task_sop_r6_integration_test.py
?? tests/task_sop_template_retry_test.py
?? tmp/
</pre>
