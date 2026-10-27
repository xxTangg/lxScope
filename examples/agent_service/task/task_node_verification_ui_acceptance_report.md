# Task-VIS-1 节点执行与验收结果可视化验收报告

日期：2026-10-26

## 结论

Task 节点详情现复用 Run API 已持久化的节点与 Attempt 数据，分别展示执行状态、各类验收记录、对应输入输出、失败诊断和执行历史。SOP 节点、DAG 画布节点和右侧执行轨迹可以选择同一 NodeRun 详情。没有新增后端 API、模型调用、验收执行、Run 版本更新、事件或 Artifact 写入。

## API 与持久化数据来源

- `GET /tasks/runs/{run_id}` 仍是详情读取入口。Task Router 的 `get_run` 返回 `TaskRunRecord`，并调用 `TaskService.get_run`；该入口按当前用户授权读取 Run。未添加接口或更改权限逻辑。
- `TaskRunRecord.node_runs` 提供节点状态、时间、输入、输出、结果、metadata、错误、Attempt 序号和 `attempt_history`。每个历史 `TaskNodeAttemptRecord` 自带当次状态、输入输出、结果、metadata、错误和时间，因此 Attempt 的验收信息不会借用当前 Attempt。
- `TaskNode` 冻结配置提供 Output Schema 和 Output Contract；Run 提供来源模板、版本、快照指纹、图结构、最终 `verification_report` 和 Artifact 状态。
- 后端记录位置经源码确认：`_execution_gates.py` 保存 `output_schema_validation` 与 `output_contract_validation`；TaskService 保存 `task_ai_verification`、来源/Citation、Claim–Evidence、业务校验及 Attempt 记录；`_router.py` 的 Run 读取响应直接返回 `TaskRunRecord`。

## UI 行为

- **执行概览：** 显示节点名、类型、Node ID、服务端执行状态、Attempt、开始/结束时间、耗时及错误码/信息。
- **验收详情：** 独立列出 AI 验收、Schema、Output Contract、Citation、Claim–Evidence、InputStep 及已有场景校验记录，状态严格映射为“通过、未通过、未执行、不适用、无记录”。只读映射服务端记录，不根据节点成功状态推断验收通过。
- **类型边界：** Input、Tool、Python 没有 AI 验收记录时显示“不适用”；只有 TaskService 明确持久化 `attempts: 0` 和空 verifier 记录时才显示“未执行”。Agent 失败或历史 Run 缺少结构化记录但无法确认 verifier 是否运行时显示“无记录”。Run 级验收单独展示，不与节点状态混为一谈。
- **契约与依据：** 显示冻结来源、模板版本、快照指纹、节点输出契约 ID/版本/校验模式，以及已有证据记录中的 Claim/Citation/Evidence/Document 标识。来源清单不展示知识库引用原文或 quote。
- **输入输出：** 每个 Attempt 展示其实际输入、输出和结构化结果；DAG 展示持久化 Input Binding，SOP 展示原节点顺序来源。无输出时明确显示“无有效输出”。
- **执行历史：** 每次 Attempt 单独折叠展示状态、起止时间、错误、验收记录和本次输入输出。优先使用持久化 Attempt History 中对应编号的记录，保留 manual/automatic 来源，避免用最新 Attempt 覆盖旧结果。
- **选择节点：** SOP 执行主舞台节点、DAG 画布节点和右侧时间线节点选择会联动至同一详情；DAG 按钮仅在有 Run 时启用。刷新后由现有 Run 查询重载持久化详情。
- **最终交付：** Run 级展示最终验收状态、来源台账标识、Artifact 数量及交付状态；`draft_unverified` 明确显示“草稿 · 未核验”，不显示为正式核验通过。
- **敏感信息：** 展示及复制诊断时过滤常见 Token、API Key、密码、私有推理字段；结构化 JSON 同时移除敏感字段。权限仍由现有 Run API 执行。本 UI 不增加任何数据读取权限。
- Pause、Continue、Step、Stop、Retry、人工输入和 Artifact 下载控制保持原有入口与调用路径。

## 修改文件

本阶段实际修改或新增：

- `examples/web_ui/frontend/src/api/task.ts`：补齐 `TaskNodeOutputContract`、节点输出契约及 Run 快照指纹的前端类型。
- `examples/web_ui/frontend/src/pages/task/task-run-panel.tsx`：节点/Attempt 详情、分离的 Run 验收摘要、脱敏诊断复制及选择联动。
- `examples/web_ui/frontend/src/pages/task/task-dag-canvas.tsx`：支持选择有 Run 的节点并显示选中状态；错误摘要脱敏。
- `examples/web_ui/frontend/src/pages/task/task-overview.tsx`：SOP 节点选择与 Run 详情联动；节点摘要脱敏。
- `examples/web_ui/frontend/src/pages/task/index.tsx`：在 Task 页面共享选中节点状态并接线。
- `examples/web_ui/frontend/src/pages/task/task-node-verification.ts`：新增服务端验收记录与 Attempt 的纯映射函数。
- `examples/web_ui/frontend/src/pages/task/task-run-sanitization.ts`：新增 Task Run 展示/复制内容脱敏函数。
- `examples/web_ui/frontend/tests/task-node-verification.test.mjs`：新增纯本地 Task 专属前端测试。
- `examples/agent_service/task/task_node_verification_ui_acceptance_report.md`：本报告。

没有修改 Task 后端、AgentScope 主框架、`main.py`、官方模板、全局依赖或数据库 Migration。

## 测试结果

| 检查 | 结果 | 证据 |
|---|---|---|
| Task 节点映射与敏感内容测试 | PASS：9 passed | `node --experimental-strip-types --test tests/task-node-verification.test.mjs` |
| Task Scoped ESLint | PASS | `pnpm exec eslint src/api/task.ts src/pages/task/index.tsx src/pages/task/task-dag-canvas.tsx src/pages/task/task-overview.tsx src/pages/task/task-run-panel.tsx src/pages/task/task-node-verification.ts src/pages/task/task-run-sanitization.ts tests/task-node-verification.test.mjs`，exit 0 |
| 前端 TypeScript 与生产构建 | PASS | `pnpm run build`，exit 0；Vite 提示已有应用 bundle 大于 500 kB，不是本次构建失败 |
| TaskService/Task API 后端回归 | PASS：2 passed | P2-G 的真实 Task API DAG/旧串行兼容测试 1 passed；`tests/task_capability_contract_test.py::test_preflight_and_saved_tool_results_are_user_scoped` 1 passed，确认非 Owner 无法读取 Run 且没有触发工具执行 |
| 真实浏览器交互 / 截图 | NOT TESTED | 本阶段运行的是纯映射测试、构建和现有 API 集成回归；没有声称完成浏览器操作验收 |
| 完整 Task 后端套件 | NOT RUN | 后端代码与权限/API 响应未修改，本次只运行一项 TaskService/API 回归 |

专项用例覆盖服务端节点状态映射、错误码/时间、AI/Schema/Contract 结果分离、Agent 异常与历史缺记录不被推断为验收失败、明确零次 verifier 记录显示“未执行”、适用与不适用、来源/Citation/Claim–Evidence 记录、S02 `draft_unverified` 草稿门禁状态、Attempt 1 失败与 Attempt 2 成功数据隔离、手动 Attempt 来源保留及敏感字段脱敏。TaskService 回归另验证当前用户隔离与旧串行兼容。

## 工作区保护记录

- 开发前快照：`%LOCALAPPDATA%\\Temp\\task-vis1-snapshot-20261026-172224\\git-status-short.txt`；涉及源码快照清单为同目录 `snapshot-index.txt`。
- 工作区在本阶段开始前已有大量未提交 Task、前端、测试、Docker、Sales Console 及其他文件变更。未执行 reset、clean、stash、提交或清理。
- 本阶段新增的报告、节点校验映射、脱敏模块和前端测试目录均属于 Task 白名单。快照比对还发现 `examples/agent_service/task/_errors.py` 在快照后出现了额外 tracked diff，增加 `agent_outcome_protocol` 错误分类；该文件不属于本阶段 UI 改动，未由本阶段操作修改，也未被覆盖或回退，保留原样。仅凭 Git 状态无法确认该额外 diff 的来源。
- `git diff --name-only` 会列出阶段前已经存在的大量 tracked 修改，不能作为本阶段独有变更清单；上述“修改文件”按阶段快照与本阶段代码操作区分。
- `git status --short` 阶段前快照与阶段结束状态已对照。除本阶段 Task 白名单内文件外，额外出现的 `_errors.py` diff 已单独记录；既有非 Task 修改保留，未新增白名单外路径。

## 未覆盖与风险

- 尚未在浏览器中人工点击 SOP/DAG 节点验证视觉布局和焦点操作；需由后续浏览器验收确认。
- 当前 API 对 Run Owner 返回持久化输入/输出；UI 执行常见敏感信息脱敏，但不能替代服务端未来新增字段的权限审核。未授权 Run 仍必须由服务端拒绝。
- 历史 Run 若未持久化结构化校验记录，界面会显示“无记录”，不会补算或补调用验收器。
- `draft_unverified` 的业务含义保留为草稿待核验。

## 阶段结论

前端节点执行、验收、Attempt 和输入输出可视化：**实现完成，自动化专项、Scoped ESLint、构建及一项 Task API 回归 PASS**。真实浏览器视觉验收未执行；不将其表述为 PASS。没有改变业务门禁或执行器，也未自动重跑任何 Run。
