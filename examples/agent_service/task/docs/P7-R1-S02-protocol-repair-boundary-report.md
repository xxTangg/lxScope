# P7-R1 S02 协议修复边界回归报告

## 范围

本次针对 TaskRun `98606e54f934407096a7906fa8e84e9b` 的 S02 最终节点失败进行修复。仅修改 Task 后端、Task 专项测试和 Task 文档；未提交代码。

## 根因

失败 Run 的首轮输出有一个未完成的 `task-claim-evidence` 区块。服务端将报告正文锁定，并记录 `body_sha256=523687d3fed24d4dfea676448dd7b9ac5d750dff32abcd84e45a799af1006567`、`separator=""`。正文已经以两个换行结束。

第二轮 Agent 返回的紧凑协议不能安全物化：`CL-33` / `O-8-3` 将整张“待核实事项”表登记为 `kind=unverified`，但该表中有多行带 `[C3]` 引用。一个待核实 occurrence 不能同时带正文引用；而且单个 occurrence 覆盖了状态不同的多行内容。物化器拒绝该响应是正确的失败关闭行为。

随后 `_service.py` 的回退分支固定在正文后增加 `\n\n`。这与锁定清单中的空分隔符不符，导致最终指纹检查把协议响应拒绝误报为“正文与前一轮失败输出不一致”。

## 修复

- `_evidence_contract.py` 新增安全封装函数，使用服务端清单记录的原始分隔符，检查正文、SHA-256、锚点、引用集合及大小限制。该封装只让不合格响应进入普通校验器；不会把响应标记为已修复或通过。
- `_service.py` 在协议无法物化时调用上述函数，并把结果记为 `wrapped_for_validation`。普通 Claim–Evidence、引用、权限和来源校验仍决定任务是否通过；未能安全封装时保留拒绝状态。
- 通用 Claim–Evidence 生成及修复提示明确要求每个表格行/段落单独锚定；有引用事实与待核实事项必须分开；`kind=unverified` 的 occurrence 不得包含引用标记。
- 没有放宽来源身份、授权主体、哈希、逐字摘录或引用编号检查，没有生成虚构 Evidence 或产物。

## 回归验证

新增测试覆盖双换行正文、混合引用与待核实 occurrence、拒绝物化、无损封装、普通校验继续失败，以及 TaskService/SOP 的端到端失败关闭路径。已有成功修复测试仍验证合法协议能进入完整引用与来源校验。

| 检查 | 结果 |
|---|---|
| P7-R1 输出契约、S02 引用协议、证据链定向测试 | PASS：66 passed，6 subtests passed |
| Task 全量专项测试 | PASS：249 passed，2 skipped，17 subtests passed |
| Task Python 编译检查 | PASS |
| `git diff --check` | PASS；仅有仓库已有的 LF/CRLF 转换提示 |
| Ruff | NOT RUN：当前虚拟环境没有安装 `ruff`，未添加依赖 |
| 真实浏览器/真实登录 Run | NOT TESTED：本轮只执行自动化回归，未访问用户会话 |

PostgreSQL 两项测试仍为 skipped，不代表真实数据库集成通过。

## 本次修改文件

- `examples/agent_service/task/_evidence_contract.py`
- `examples/agent_service/task/_service.py`
- `tests/task_p7r1_output_contract_test.py`
- `examples/agent_service/task/docs/P7-R1-S02-protocol-repair-boundary-report.md`

本轮实际编辑仅在 Task 白名单内。工作区中其他既有 Task、前端及销售台未提交修改均予保留；没有暂存或提交文件。

## 真实复验说明

Run `98606e54f934407096a7906fa8e84e9b` 已终止，修复不会改写该 Run。完成后端重载/更新后，需要从 S02 模板新建 Run。新报告仍须让表格中每个不同状态的行有独立 occurrence；本次修复不会替旧正文拆分混合表格。若后端校验失败，新 Run 应显示具体 Claim/引用结构错误，而不再把分隔符差异误报成正文改写。

本项目 `docker-compose.dev.yml` 将源码挂载至 `/app` 且启用 `UVICORN_RELOAD=true`。开发环境支持热重载；为确保长驻服务加载新模块，可执行：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart agentscope
```

基础 `docker-compose.yml` 使用镜像内代码且关闭 reload；该运行方式需要重建后端镜像：

```powershell
docker compose -f docker-compose.yml up -d --build agentscope
```

本次未修改前端，因此不需要重建 Web UI。
