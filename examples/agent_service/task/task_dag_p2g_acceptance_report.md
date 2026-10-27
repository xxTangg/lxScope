# 龙信Scope Task P2-G：DAG、节点重跑、断点执行端到端验收报告

生成日期：2026-10-19  
分支：`integrate/dev_l`  
阶段开始 HEAD：`e63c3b80278dba2db5ddfd8e195b988481cabd1e`

## 验收结论

- TaskService 与 FastAPI Task 路由的 P2-G 行为验收通过：DAG 开关、A→B/C 并行→D 汇合→E、局部重跑、成功节点重算、Pause/Continue/Step/Stop 均有真实 TaskService 路径的确定性 API 测试。
- 本阶段针对重跑语义做了最小修复：失败 Run 中允许选择 `SUCCEEDED` 节点重算，并清除其全部后继结果；Run 中有任何在途/等待输入节点时拒绝重跑。Tool/Python 仍要求 `side_effects_verified=true`。
- Task DAG 专项：**49 passed**。完整 Task 套件：**40 failed, 320 passed, 3 skipped, 21 subtests passed**。40 个失败与阶段开始的 40 个失败逐项一致，不能据此宣称旧业务场景全量通过。
- P2-H 真实后端验收目前 **BLOCKED**：本机没有配置隔离 Redis/PostgreSQL 测试 URL；未连接任何真实 Redis/PostgreSQL，也未使用 Memory/fakeredis 代替真实后端验收。
- 生产就绪：**否**。本报告只确认 TaskService/API 本地调用链行为；真实持久化并发、租约和跨进程恢复尚待 P2-H。

## 1. 工作区快照与范围

阶段开始先记录 Git 状态。工作区当时已有 P2-A 至 P2-E Task 改动，以及多处白名单外既有修改。下列快照是阶段开始基线，不把它们归到 P2-G。

### 阶段开始 `git diff --name-only`

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
"sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
sales-console/services/sales-api/src/app.ts
sales-console/services/sales-api/src/types.ts
tests/task_postgres_sop_test.py
```

### 阶段开始 `git status --short`

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
 M "sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
 M sales-console/services/sales-api/src/app.ts
 M sales-console/services/sales-api/src/types.ts
 M tests/task_postgres_sop_test.py
?? .pnpm-store/
?? examples/agent_service/.playwright-mcp/
?? examples/agent_service/blobs/
?? examples/agent_service/task/_dag.py
?? output/
?? tests/task_dag_config_test.py
?? tests/task_dag_control_test.py
?? tests/task_dag_retry_test.py
?? tests/task_dag_scheduler_test.py
?? tests/task_dag_store_test.py
?? tests/task_dag_test.py
?? tmp/
```

阶段期间本次实际改动限于：

- `examples/agent_service/task/_service.py`：失败 Run 的成功节点重算；在途节点检查优先于 Run 状态检查。
- `examples/agent_service/task/_router.py`：节点重跑路由摘要改为涵盖失败或成功节点。
- `tests/task_dag_retry_test.py`：增加成功节点重算和在途后继拒绝测试；把非法节点测试改为确实不存在的节点。
- `tests/task_dag_p2g_acceptance_test.py`：新增 11 项 TaskService+Task API 端到端用例。
- `examples/agent_service/task/task_dag_p2g_acceptance_report.md`：本报告。

本阶段没有修改 `main.py`、AgentScope 主框架、其他业务模块、九大模板、项目依赖、全局配置或 Migration。白名单外状态在最终快照中与阶段开始快照一致；下方列出的白名单外文件仍是阶段开始前已存在的修改。

## 2. 实际 API 调用链

测试在进程内创建 FastAPI 应用并挂载 `task_router`，把 `get_current_user_id` 覆盖为固定测试身份，`app.state.task_service` 注入真实 `TaskService`。执行器是本地确定性假执行器；它只记录节点调用、输入、并行状态和受控失败，不调用模型、MCP 或生产服务。Task 数据由 `TaskStore` 保存，因此这些测试验收 TaskService/API 逻辑，不验收真实 Redis/PostgreSQL。

实际路由链如下：

1. `POST /tasks/` → `TaskService.create_task` 保存自定义 DAG 或线性 Task。
2. `POST /tasks/{task_id}/runs` → `TaskService.create_run` 执行 Task 校验并派发；DAG 关闭时返回 HTTP 409，开启后返回 HTTP 202。
3. `GET /tasks/runs/{run_id}` → `TaskService.get_run` 读取持久化的 Run/Node 状态。
4. 重跑通过 `POST /tasks/runs/{run_id}/nodes/{node_id}/retry`，随后 `POST /tasks/runs/{run_id}/continue`。
5. 控制通过 `POST /tasks/runs/{run_id}/pause|continue|step|stop`；生命周期通过 `GET /tasks/runs/{run_id}/events` SSE 回放。

路由源码位置：`examples/agent_service/task/_router.py` 中 create task、create run、retry node 与四种控制路由。端到端用例位于 `tests/task_dag_p2g_acceptance_test.py`。

## 3. DAG 完整执行

测试构建 `A → B、C → D → E`，D 的 `branch_b`、`branch_c` 绑定 B、C 的结构化结果，`max_concurrency=2`。

| 观察点 | API/Run 结果 | Node 状态证据 |
|---|---|---|
| Scheduler 关闭 | POST Run 返回 409；执行器调用数为 0 | 没有创建可执行 DAG Run |
| Scheduler 显式开启 | POST Run 返回 202，engine=`dag` | Run 从 queued 开始 |
| A 完成后 | GET Run 显示 B、C 同时 running | A=succeeded；D、E=pending；执行器同时持有 B、C |
| B、C 完成 | D 进入 running 时通过 GET Run 检查父节点 | B、C=succeeded；D=running；E=pending |
| 汇合输入 | 检查 D 的 `ExecutionContext.named_inputs` | `branch_b={"node_id":"B","value":"result:B"}`，`branch_c={"node_id":"C","value":"result:C"}` |
| E 完成 | Run 终态 succeeded，最终输出包含 E 输出 | A、B、C、D、E 均 succeeded，各执行一次 |

额外 API 校验拒绝了循环依赖、非祖先输入绑定和超资源容量要求，均为 HTTP 422。资源调度用例把 B、C 设为竞争同一容量为 1 的 `database` 资源，并在 B 阻塞期间验证 C 保持 pending；峰值资源使用量为 1。线性旧 Task 在 DAG 开关关闭时仍经原 SOP 路径完成，`engine=sop`。

## 4. 失败节点与成功节点局部重跑

### 失败节点 C

实际 API 流程：创建 A/B/C/D/E Task → POST Run → 假执行器令 C 首次失败 → POST C retry → 用相同 `request_id` 重复 POST → 用旧版本提交冲突请求 → POST Continue → GET Run。

- 失败时：A、B=succeeded，C=failed，D、E=pending；D/E 没有被调用。
- 重跑后 Run ID 不变；A/B 输出和 attempt 保留，C 回到 pending；原失败 attempt 仍保存在 `attempt_history`。
- 重复重跑命令返回相同版本，不重复产生 attempt；过期 `expected_run_version` 返回 `TASK_RUN_CAS_CONFLICT`。
- Continue 后 A/B 各调用一次，C 共两次，D/E 各一次；C 的 attempt history 为自动失败、人工成功；Run 最终 succeeded。
- 对已成功 Run 再重跑 C 返回 `TASK_RUN_STATE_CONFLICT`。
- Tool 副作用节点未确认时，API 返回 409；确认 `side_effects_verified=true` 后才接受。对成功祖先重跑时，如果任一下游仍 running/awaiting_input，也以 409 拒绝且状态版本不变。

### 成功节点 B 的重算

在 `A→B→C→D` 与 `A→X→D` 场景中，D 首次失败。API 重跑已成功的 B 后，B/C/D 清空为 pending，旧成功/失败 attempt 留在历史；A 与成功旁支 X 保持成功。继续后 A/X 各执行一次，B/C/D 各执行两次。重跑事件明确列出失效后继 C、D。

## 5. Pause、Continue、Step、Stop 与状态重建

| 操作场景 | 状态变化与 API 结果 |
|---|---|
| 线性 DAG A→B；A 在途时 Pause | API 返回 `pause_requested`；A 到安全检查点后 A=succeeded、B=pending，Run=queued/control_mode=paused。重建 TaskService 后 Continue，A 不重跑，B 执行，Run succeeded。 |
| 并行 DAG；B、C 在途时 Pause | API 重复同一请求幂等；旧版本的新请求返回 409。放行在途节点后 B、C=succeeded、D=pending，Run 保存为 paused。新 TaskService 从同一存储重建后 Continue，B/C 不重跑，D 完成。 |
| Step；B、C 同为 ready | 每个 Step 请求只派发一个节点：第一次 B，第二次 C；每次保存后暂停。相同请求重复提交不增加版本或调用数。第三步派发 D 并结束；`run.step_completed` 记录分别为 `[B]`、`[C]`。 |
| Pause 请求期间节点异常 | A 失败，B 保持 pending，Run 进入 failed；Continue 返回 409。先经节点重跑 API 再 Continue，A 第二次成功后 B 才执行。 |
| Stop 时 A 在途 | Run 变为 canceled/control_mode=stopped；重复 Stop 幂等；晚到的 A 结果不能覆盖终态，B 不派发。Continue 返回 409；`run.canceled` 事件标记 `side_effects_rolled_back=false`。 |

现有 `TaskService` 直接测试也覆盖暂停后恢复、Step 失败、Stop 终态和迟到执行结果栅栏；本阶段新增 API 用例另外验证了 HTTP 层冲突码与请求幂等。

## 6. 测试结果

### 本阶段执行

- DAG 专项（`tests/task_dag_*.py`）：**49 passed, 1 warning**。
- 完整 Task 套件（`tests/task_*.py`）：**40 failed, 320 passed, 3 skipped, 1 warning, 21 subtests passed**，51.57s。
- `python -m compileall -q examples/agent_service/task tests`：通过。
- `git diff --check`：通过。
- 唯一 warning 是现有 Task 路由使用 Starlette 已弃用的 `HTTP_422_UNPROCESSABLE_ENTITY` 常量；不是测试失败。

### 阶段开始与阶段结束逐项对比

阶段开始完整套件：**40 failed, 307 passed, 3 skipped, 21 subtests passed**，56.31s。阶段结束失败集合仍为同一 40 个 nodeid。解析两次 `--tb=long` 日志后逐项比较：异常类型 40/40 相同；失败断言行 40/40 相同；traceback `.py` 路径序列 40/40 相同。唯一代码栈行号差异是 `TaskS02ContentRepairSOPTest.test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` 的 `_service.py` 调用行从 5377 顺延到 5378，测试行与 `_sop.py` 帧未变。一个旧失败的错误上下文包含随机 Run ID，只有该动态 ID 不同。判定：**未发现新增失败或栈路径回归**。

这 40 项仍是既有失败，集中在旧 SOP/输入恢复/AI verifier 与 S02、S07 产物和验收断言。它们不是通过本阶段修复的；九大模板、权限逻辑也不能仅凭“失败数相同”宣称验收通过。对应失败的异常、断言和项目栈帧逐项记录如下；堆栈列给出基线与阶段末相同的 Task/测试代码帧，三方库内部帧路径也已纳入比较。

| 测试名称（pytest nodeid） | 异常类型 | 失败断言/触发行 | 项目堆栈帧（基线与阶段末） |
|---|---|---|---|
| `tests/task_capability_contract_test.py::test_tool_schema_failure_is_persisted_and_stops_run` | `KeyError` | `assert node_run.metadata["output_schema_validation"]["status"] == "failed"` | 相同：tests\task_capability_contract_test.py:647 → tests\task_capability_contract_test.py:640 |
| `tests/task_input_protocol_test.py::TaskInputAPITest::test_api_rejects_stale_answers_and_streams_waiting_then_completion` | `AssertionError` | `self.assertEqual(completed.json()["status"], "succeeded")` | 相同：tests\task_input_protocol_test.py:271 |
| `tests/task_input_protocol_test.py::TaskInputAPITest::test_redis_round_trip_resumes_after_rebuilding_service` | `AssertionError` | `self.assertEqual(completed.status.value, "succeeded")` | 相同：tests\task_input_protocol_test.py:333 |
| `tests/task_mvp_pipeline_test.py::TestTaskMvpPipeline::test_agent_tool_python_agent_pipeline_without_mcp` | `AssertionError` | `self.assertEqual(completed.status.value, "succeeded")` | 相同：tests\task_mvp_pipeline_test.py:195 |
| `tests/task_p7b_scenario_runtime_test.py::ScenarioRuntimeTest::test_s04_decimal_costs_and_materials_are_deterministic_drafts` | `AssertionError` | `self.assertEqual(checked.result["status"], "passed")` | 相同：tests\task_p7b_scenario_runtime_test.py:232 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1OutputContractTest::test_previous_result_is_typed_only_when_validated_value_exists` | `AssertionError` | `with self.assertRaisesRegex(ValueError, "没有通过输出契约校验"):` | 相同：tests\task_p7r1_output_contract_test.py:1310 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1OutputContractTest::test_s02_final_prompt_avoids_duplicate_report_and_keeps_both_protocols` | `AssertionError` | `self.assertIn("Task 服务端会从协议区块生成来源证据台账", prompted.prompt)` | 相同：tests\task_p7r1_output_contract_test.py:193 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1OutputContractTest::test_s07_final_uses_the_shared_strict_claim_schema` | `AssertionError` | `self.assertIn(` | 相同：tests\task_p7r1_output_contract_test.py:235 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_invalid_output_is_bounded_and_server_status_is_authoritative` | `AssertionError` | `self.assertEqual(executor.calls, ["结构化输入", "结构化输入"])` | 相同：tests\task_p7r1_output_contract_test.py:2386 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_finalizer_builds_citation_index_then_runs_full_validation` | `AssertionError` | `self.assertIsNotNone(parked.pending_input)` | 相同：tests\task_p7r1_output_contract_test.py:1991 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_sop_rejected_repair_keeps_body_and_fails_closed` | `AssertionError` | `self.assertIn(` | 相同：tests\task_p7r1_output_contract_test.py:2224 |
| `tests/task_p7r1_output_contract_test.py::TaskP7R1ServiceOutputContractTest::test_validated_result_is_persisted_and_bound_to_next_node` | `AssertionError` | `self.assertEqual(completed.status.value, "succeeded")` | 相同：tests\task_p7r1_output_contract_test.py:2333 |
| `tests/task_project_planning_rag_test.py::TaskProjectPlanningRagTest::test_failed_final_ledger_does_not_create_docx` | `AssertionError` | `self.assertEqual(run.status, RunStatus.FAILED)` | 相同：tests\task_project_planning_rag_test.py:1936 |
| `tests/task_project_planning_rag_test.py::TaskProjectPlanningRagTest::test_incomplete_new_protocol_fails_closed_through_task_service_and_sop` | `AssertionError` | `self.assertEqual(run.status, RunStatus.FAILED)` | 相同：tests\task_project_planning_rag_test.py:1807 |
| `tests/task_project_planning_rag_test.py::TaskProjectPlanningRagTest::test_input_selected_project_material_is_saved_in_run_and_ledger` | `KeyError` | `run.node_runs[-1].metadata["evidence_ledger"]["passed"]` | 相同：tests\task_project_planning_rag_test.py:1530 |
| `tests/task_project_planning_rag_test.py::TaskProjectPlanningRagTest::test_new_occurrence_protocol_runs_through_task_service_and_sop` | `KeyError` | `self.assertTrue(final.metadata["evidence_ledger"]["passed"])` | 相同：tests\task_project_planning_rag_test.py:1739 |
| `tests/task_project_planning_rag_test.py::TaskProjectPlanningRagTest::test_s02_custom_copy_runs_rag_review_and_word_writer` | `KeyError` | `run.node_runs[4].metadata["citation_verification"]["passed"]` | 相同：tests\task_project_planning_rag_test.py:1374 |
| `tests/task_s02_content_repair_test.py::TaskS02ContentRepairSOPTest::test_s02_v2_repairs_only_the_mixed_occurrence_then_runs_full_ledger` | `RuntimeError` | `async for _event in final_step.reply_stream(None, state):` | 基线：tests\task_s02_content_repair_test.py:432 → examples\agent_service\task\_sop.py:115 → examples\agent_service\task\_service.py:5377；阶段末：tests\task_s02_content_repair_test.py:432 → examples\agent_service\task\_sop.py:115 → examples\agent_service\task\_service.py:5378 |
| `tests/task_s02_lightweight_template_test.py::test_s02_v5_taskservice_sop_generates_run_linked_draft_docx` | `AssertionError` | `assert run.verification_report["delivery_profile"] == "s02_lightweight_draft"` | 相同：tests\task_s02_lightweight_template_test.py:343 |
| `tests/task_s02_v3_structured_output_test.py::test_s02_v3_taskservice_sop_verifier_and_artifact_gate` | `KeyError` | `assert final_node.metadata["s02_v3_structured_output"]["passed"] is True` | 相同：tests\task_s02_v3_structured_output_test.py:1254 |
| `tests/task_s02_v3_structured_output_test.py::test_real_run_missing_empty_arrays_passes_full_run_and_writes_docx` | `KeyError` | `output_metadata = final_node.metadata["s02_v3_structured_output"]` | 相同：tests\task_s02_v3_structured_output_test.py:1280 |
| `tests/task_s02_v3_structured_output_test.py::test_s02_v3_required_business_field_still_fails_and_blocks_docx` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v3_structured_output_test.py:1316 |
| `tests/task_s02_v3_structured_output_test.py::test_real_run_kind_conflict_fails_service_run_and_blocks_docx` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v3_structured_output_test.py:1367 |
| `tests/task_s02_v3_structured_output_test.py::test_s02_v3_invalid_inner_json_stops_before_artifact_writer` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v3_structured_output_test.py:1387 |
| `tests/task_s02_v3_structured_output_test.py::test_s02_v3_rejects_redacted_legacy_run_protocol_before_artifact_writer` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v3_structured_output_test.py:1407 |
| `tests/task_s02_v3_structured_output_test.py::test_s02_v3_policy_failure_does_not_retry_or_create_artifact` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v3_structured_output_test.py:1427 |
| `tests/task_s02_v4_delivery_test.py::test_s02_v4_and_v41_historical_templates_keep_versioned_contracts` | `AssertionError` | `assert current is not None and current.template_version == "5.0.0"` | 相同：tests\task_s02_v4_delivery_test.py:297 |
| `tests/task_s02_v4_delivery_test.py::test_s02_v4_taskservice_sop_demo_draft_writes_marked_docx_after_ledger` | `KeyError` | `assert run.verification_report["evidence_ledger_passed"] is True` | 相同：tests\task_s02_v4_delivery_test.py:889 |
| `tests/task_s02_v4_delivery_test.py::test_s02_v41_taskservice_sop_demo_writes_marked_docx_after_safe_downgrade` | `AssertionError` | `assert "DEMO/SYNTHETIC · 演示草稿 · 未完成完整证据核验" in content` | 相同：tests\task_s02_v4_delivery_test.py:938 |
| `tests/task_s02_v4_delivery_test.py::test_s02_v41_adapter_pass_does_not_override_ledger_failure_or_write_docx` | `AttributeError` | `monkeypatch.setattr(` | 相同：tests\task_s02_v4_delivery_test.py:981 |
| `tests/task_s02_v4_delivery_test.py::test_s02_v41_strict_association_failure_blocks_taskrun_and_docx` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v4_delivery_test.py:1019 |
| `tests/task_s02_v4_delivery_test.py::test_s02_v4_strict_missing_source_blocks_success_status_and_docx` | `AssertionError` | `assert run.status is RunStatus.FAILED` | 相同：tests\task_s02_v4_delivery_test.py:1030 |
| `tests/task_s07_rag_integration_test.py::S07CitationOutputContractTest::test_invalid_quote_is_rejected_before_model_verifier_or_artifact` | `AssertionError` | `self.assertEqual(failed.status.value, "failed")` | 相同：tests\task_s07_rag_integration_test.py:320 |
| `tests/task_s07_rag_integration_test.py::S07CitationOutputContractTest::test_valid_citation_reaches_deterministic_citation_tool` | `KeyError` | `draft_step.metadata["output_contract_validation"]["status"],` | 相同：tests\task_s07_rag_integration_test.py:381 |
| `tests/task_s07_rag_integration_test.py::S07TaskPreflightTest::test_preflight_keeps_inaccessible_knowledge_base_as_permission_denial` | `AssertionError` | `self.assertEqual(template.template_version, "4.0.0")` | 相同：tests\task_s07_rag_integration_test.py:451 |
| `tests/task_s07_rag_integration_test.py::S07TaskPreflightTest::test_preflight_reports_document_not_found_as_data_error_not_permission` | `AssertionError` | `self.assertEqual(template.template_version, "4.0.0")` | 相同：tests\task_s07_rag_integration_test.py:451 |
| `tests/task_s07_rag_integration_test.py::S07TaskPreflightTest::test_preflight_reports_unindexed_document_separately` | `AssertionError` | `self.assertEqual(template.template_version, "4.0.0")` | 相同：tests\task_s07_rag_integration_test.py:451 |
| `tests/task_s07_rag_integration_test.py::S07TaskPreflightTest::test_report_rag_is_task_native_and_does_not_discover_session_tool` | `AssertionError` | `self.assertEqual(template.template_version, "4.0.0")` | 相同：tests\task_s07_rag_integration_test.py:451 |
| `tests/task_template_output_test.py::test_structured_schema_failure_is_persisted_and_fails_run` | `AssertionError` | `assert failed.status is RunStatus.FAILED` | 相同：tests\task_template_output_test.py:780 → tests\task_template_output_test.py:632 |
| `tests/task_template_version_test.py::test_s07_v5_uses_ai_acceptance_and_keeps_v4_history` | `AssertionError` | `assert all(step.output_contract is None for step in historical.steps)` | 相同：tests\task_template_version_test.py:131 |

## 7. Redis/PostgreSQL P2-H 检查与 BLOCKED 项

源码检查证据：

- `examples/agent_service/task/_redis_store.py:277` 的 `transition_run` 使用 WATCH/事务提交 Run 快照与事件；`:340` 的 `claim_run` 原子更新 owner、lease 和 fence epoch。
- `examples/agent_service/task/_postgres_store.py:587` 锁定 Run 行；`:685` 在事务内 CAS 更新状态并写事件；`:750` 通过行锁领取执行权。
- `examples/agent_service/task/_service.py:2350` 在读取 queued/running DAG Run 时触发懒恢复；`:3169` 续租；`:3242` 领取执行权；`:3260` 附近把进程中断时仍 running 的节点置为失败并要求人工确认，避免重放未知副作用。
- `tests/task_dag_store_test.py` 当前用 Memory 与 fakeredis 验证 100 个 CAS 更新、事件序号、竞争 claim、过期 lease fencing、重复 command 和禁止旁路写入。它们不等于真实数据库验收。
- `tests/task_postgres_sop_test.py` 有 3 个真实 PostgreSQL 集成用例，其中 `test_dag_cas_parallel_updates_claim_fencing_and_restart` 覆盖 100 路更新、事件序号、claim、过期租约 fencing 和 store 重建。当前三个用例均因 `LXSCOPE_TASK_TEST_DATABASE_URL` 未配置而 skip。
- 当前没有真实 Redis 集成测试文件；Redis URL 未配置。P2-H 需新增隔离 Redis 的集成测试，不能把 fakeredis 用例当作替代。

P2-H 真实 Redis 和 PostgreSQL 均需覆盖：100 路 CAS 与事件顺序；两 worker 竞争同一 Run；lease 未过期时拒绝第二 owner；lease 过期后 fence epoch 增加并拒绝旧 owner 写入；运行节点期间续租；Run 快照与事件原子提交；重复 command 幂等且只生成一次事件；终态阻止旧 owner 覆写；服务重建后读取事件/状态并恢复 queued Run；running 节点中断后不自动重放外部副作用；租户/用户隔离；旧的 `update_run`/`append_event` 旁路写被拒绝。PostgreSQL 还需覆盖迁移升级后的旧数据、约束与人工输入跨实例恢复。Redis 还需覆盖 WATCH 冲突重试耗尽与重启后的列表/事件读取。

### P2-H 隔离环境命令模板

PostgreSQL 测试会创建并删除随机 `lxscope_task_test_<uuid>` 数据库。必须使用与生产隔离的 PostgreSQL 集群；URL 数据库名需以 `lxscope_task_test` 开头，账号需要仅在该隔离集群内拥有创建/删除临时数据库权限。

```powershell
$env:LXSCOPE_TASK_TEST_DATABASE_URL = 'postgresql+asyncpg://<test-user>:<secret>@<isolated-postgres>:5432/lxscope_task_test'
& .\.venv\Scripts\python.exe -m pytest tests/task_postgres_sop_test.py -vv
Remove-Item Env:LXSCOPE_TASK_TEST_DATABASE_URL
```

真实 Redis 当前没有 env-gated 集成测试；在 P2-H 增加该测试文件后，隔离 Redis DB（专用实例/DB index，禁止复用生产数据）使用的目标命令为：

```powershell
$env:LXSCOPE_TASK_TEST_REDIS_URL = 'redis://:<secret>@<isolated-redis>:6379/15'
& .\.venv\Scripts\python.exe -m pytest tests/task_dag_redis_integration_test.py -vv
Remove-Item Env:LXSCOPE_TASK_TEST_REDIS_URL
```

第二条命令在当前分支尚不能执行，因为 `tests/task_dag_redis_integration_test.py` 尚未创建。P2-H 需先实现 URL 隔离和清理安全的真实 Redis 测试，再连接专用实例运行。当前没有连接任何数据库，也没有运行 SQLite/Memory 代替真实后端的测试。

## 8. P2-H 准入判断

P2-G 的 TaskService/API 行为验收通过；阶段末停止在 P2-G。**P2-H 当前执行 BLOCKED**，待提供隔离 PostgreSQL、隔离 Redis 和真实 Redis 集成测试后再执行。P2-H 完成前不具备生产就绪结论。

## 9. 阶段末 Git 快照

以下状态在删除本阶段测试日志后取得。与阶段开始快照对照时，所有白名单外的 tracked 修改和未跟踪目录原样保留；本阶段新增项仅在 Task 模块和 `tests/task_*.py`。

### 阶段末 `git diff --name-only`

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
"sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
sales-console/services/sales-api/src/app.ts
sales-console/services/sales-api/src/types.ts
tests/task_postgres_sop_test.py
```

### 阶段末 `git status --short`

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
 M "sales-console/docs/\347\256\241\347\220\206\347\263\273\347\273\237\345\257\271\346\216\245\350\265\204\346\226\231\344\270\216\345\274\200\345\217\221\346\270\205\345\215\225.md"
 M sales-console/services/sales-api/src/app.ts
 M sales-console/services/sales-api/src/types.ts
 M tests/task_postgres_sop_test.py
?? .pnpm-store/
?? examples/agent_service/.playwright-mcp/
?? examples/agent_service/blobs/
?? examples/agent_service/task/_dag.py
?? examples/agent_service/task/task_dag_p2g_acceptance_report.md
?? output/
?? tests/task_dag_config_test.py
?? tests/task_dag_control_test.py
?? tests/task_dag_p2g_acceptance_test.py
?? tests/task_dag_retry_test.py
?? tests/task_dag_scheduler_test.py
?? tests/task_dag_store_test.py
?? tests/task_dag_test.py
?? tmp/
```
