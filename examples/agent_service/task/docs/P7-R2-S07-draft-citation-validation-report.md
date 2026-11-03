# P7-R2 S07 草稿引用校验修复报告

## 运行诊断

诊断对象：TaskRun `1cb4cfca82d14f738746e15ba71cee87`，节点 `4ce37977e24e488d86b8b26ddd587a3d`（“生成章节草稿”）。本次运行已完成授权资料检索并保存六条 Run 证据。失败发生在草稿生成/验收，不是 RAG 工具缺失或知识库权限失败。

草稿存在真实问题：七个要求章节之外新增了独立的“人工复核与交接说明”；C5 把两份来源混作一个引用；C6 未在正文使用；部分 quote 只有标题、工作表标签、文件名或句子残片。Run 记录的三轮模型验收对候选来源是否必须全部引用、引用映射的要求不一致。最终节点失败，未生成产物。

来源身份、原文逐字匹配和语义支持是不同检查。授权证据 ID 来自该 Run 并不证明 quote 能支持正文结论；当前确定性校验负责来源授权、身份、哈希、连续原文及引用编号一致性。语义支持仍要求人工复核，不会被报告成自动通过。

## 修复

- 增加通用 `citations` 节点输出契约。S07 的章节草稿和最终草稿均在继续执行前，对本 Run 的服务端授权 Evidence 快照执行确定性校验；快照缺失时失败关闭。来源、权限、哈希、quote 或编号校验失败不会交给模型重写或放宽。
- 更新通用引用提示和验收上下文：只需引用实际使用的来源，不必覆盖所有检索候选；一个编号不能混合多个来源；quote 应为来源正文中相关的连续摘录；模型验收不得重复推翻服务端已完成的来源完整性检查，也不得声称自动验证语义支持。
- 发布 S07 `4.0.0`，给章节草稿与最终草稿声明该输出契约。v1、v2、v3 模板及其历史 Task 语义保留；旧 Task 不会自动获得 v4 校验配置。
- 将 S07 v4 的证据策略登记为与 v3 相同的来源类型及人工复核规则。
- 修正两个测试中“当前 S07 版本仍是 v3”的断言；历史兼容测试继续显式覆盖 v3。

校验器不会证明某条原文在语义上支持某项结论，也不能自动保证章节内容符合全部业务要求。章节数量和报告完整性仍由生成要求及步骤验收处理；证据语义状态保留为 `requires_human_review`。

## 修改文件

- `examples/agent_service/task/_models.py`：通用节点输出契约的类型与来源快照约束。
- `examples/agent_service/task/_template_models.py`：限制模板输出契约只用于 Agent 节点。
- `examples/agent_service/task/_citation_verifier.py`：共享引用规范与生成提示。
- `examples/agent_service/task/_agentscope_executor.py`：把服务端校验状态传给步骤验收，避免重复提出来源校验要求。
- `examples/agent_service/task/_service.py`：在 Agent 输出验收阶段执行当前 Run 的确定性引用校验并持久化状态。
- `examples/agent_service/task/_template_registry.py`、`examples/agent_service/task/templates/s07_report_generation.v4.json`：注册 S07 v4，保留旧版本。
- `examples/agent_service/task/_scenario_evidence_policies.py`：为 S07 v4 复用既有证据策略。
- `tests/task_s07_rag_integration_test.py`、`tests/task_template_version_test.py`、`tests/task_p7r1_output_contract_test.py`：验证授权快照、错误 quote 失败关闭、合法 quote 传递至下游校验及模板历史兼容。
- `tests/task_p7b_demo_data_compatibility_test.py`、`tests/task_nine_scenarios_test.py`：将“当前 S07”版本期望更新为 v4。

本轮没有修改 Task 白名单以外的文件，也没有提交或暂存文件。工作区原有 Task、前端和销售台未提交内容均保留。

## 验证结果

- 定向 S07、模板版本、输出契约测试：`47 passed, 4 subtests passed`。
- 九场景、S07、模板兼容及输出契约回归：`54 passed, 13 subtests passed`。
- 全量 Task 专项测试：`247 passed, 2 skipped, 17 subtests passed`。
- Task 源码与 Task 专项测试 `compileall`：通过。
- `git diff --check`：通过；Git 仅提示若干现有 LF/CRLF 转换提示。
- 前端未修改，本轮未运行 `pnpm build`。

自动化通过使用 Task 专项测试中的合成授权 Evidence 与测试 Gateway；这不是实时知识库、真实登录浏览器或真实模型联调。真实运行验收仍待用户在部署环境中完成。

## 重新验收

Task 后端 Python 代码、模板 Registry 和模板 JSON 均已变更。若使用 `docker-compose.dev.yml`，仓库通过 `.:/app` 挂载；为确保模板 JSON 与 Python 代码都加载，重启 `agentscope` 服务即可：`docker compose -f docker-compose.yml -f docker-compose.dev.yml restart agentscope`。若使用基础 `docker-compose.yml`，后端代码烘焙在镜像内且没有仓库挂载，需要重建并重建容器：`docker compose up -d --build agentscope`。两种方式都不需要重建前端镜像。

随后从模板库新建 S07 v4 Task 并启动全新 Run；已有 v1–v3 Task 和失败 Run 保持原样，不会自动迁移到 v4。

重点检查“生成章节草稿”是否仅保留七个要求章节、所有引用编号与唯一 `task-citations` 区块一致、每条 quote 来自当前 Run 授权来源原文，且人工复核状态清晰。旧 Run `1cb4cfca82d14f738746e15ba71cee87` 是失败记录，不会被本次代码修改追溯重算。
