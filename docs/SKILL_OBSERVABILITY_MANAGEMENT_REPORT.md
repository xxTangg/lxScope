# Skill 模块可观测性阶段汇报

> 本文汇报的是 Skill 专项观测能力，不代表项目级可观测性已经完成。项目级接入见 [PROJECT_OBSERVABILITY.md](./PROJECT_OBSERVABILITY.md)。

> 汇报对象：项目负责人
>
> 当前阶段：已完成用户 Token 使用聚合、应用层调用约定、基础诊断、Skill PostgreSQL 观测事件表、管理员查询 API 和前端分析页；相关专项测试与前端生产构建已通过，生产级链路关联和真实 PostgreSQL 联调仍需继续。

## 1. 本阶段解决的问题

本阶段围绕 Skill 补齐三项能力：

1. 明确模型使用 Skill 前必须调用 AgentScope 内置 `Skill` 工具；
2. 验证 Skill 工具调用不会被应用层改写，并验证 Skill 调用约定能够进入系统提示词；
3. 记录 Skill 同步结果、加载数量、变化数量和耗时，便于定位权限、下载、安装和工作区残留问题。

## 2. 当前已经完成

### 2.1 应用层调用约定

当会话存在可用 Skill 时，应用层会向模型补充以下执行顺序：

```text
匹配可用 Skill
    ↓
调用 Skill(skill="精确名称")
    ↓
读取并遵循 Skill 指令
    ↓
使用 Skill 引用的脚本、文件和工具
```

应用层没有重新实现 Skill loader，也没有修改 AgentScope 核心 Toolkit 和 Workspace。

### 2.2 Skill 使用诊断

目前能够记录：

| 事件 | 含义 |
| --- | --- |
| `skill.exposed` | Skill 已进入本轮 Agent 可用上下文 |
| `skill.invoked` | 模型调用了内置 `Skill` 工具 |
| `skill.completed` | Skill viewer 返回最终结果 |

只有 `Skill` 工具被实际调用才算实际使用。技能已发布、用户可见或已安装，不计入实际使用次数。

### 2.3 Skill 同步诊断

目前能够记录：

- 同步成功、部分成功、失败或跳过；
- 同步前工作区 Skill 数量；
- 当前用户可见 Skill 数量；
- 同步后工作区 Skill 数量；
- 删除数量；
- 恢复内置 Skill 数量；
- 下载并安装数量；
- 子操作失败数量；
- 同步总耗时。

这可以用于判断“用户为什么看不到 Skill”“权限撤销后是否清理”“Skill Hub 或安装是否失败”等问题。

### 2.4 PostgreSQL 事件表

为后续用户设置中的“分析 → Skill”页面，新增了应用层表：

```text
skill_observability_events
```

表中保存：

- `reconcile`、`exposed`、`invoked`、`completed` 事件；
- 成功、partial、失败等结果；
- 用户、Agent、Session、Skill 的受控标识；
- 可见数量、加载数量、变化数量和耗时；
- 失败原因编码。

表中不保存 Prompt、Skill Markdown、模型完整输出和凭据。

写入通过独立的 `SkillObservationSink` 注入。没有配置 PostgreSQL 时保持原有终端日志；数据库不可用时降级记录诊断日志，不阻塞 Skill 主流程。

### 2.5 用户 Token 与 Skill 前端分析页

管理员可从账号菜单的“分析”进入：

```text
/admin/analytics/skills
```

页面当前展示：

- 选定周期内的总 Token、输入 Token、输出 Token 和用户数量；
- 每个用户的 Token 明细、消息数和会话数；
- 观测事件总量、Skill 实际调用次数、调用/暴露比、平均同步耗时；
- 每日暴露、调用、完成趋势；
- 暴露 → 调用 → 完成的生命周期数量；
- 成功/失败完成数量；
- 高频 Skill；
- 最近一次同步的可见和已装载数量。
- 失败记录总量、Skill 执行失败率、失败阶段、错误码和最近失败用户/会话。

页面只消费聚合 API，不直接访问数据库，也不把 Prompt、Skill Markdown 或模型输出带到前端。Token 统计复用已有消息 usage；失败详情只展示用户、会话、Skill 和受控错误码，不展示原始异常内容。

## 3. 基于已有组件的实现方式

本次实现复用了：

- AgentScope 内置 `Skill` viewer；
- AgentScope Middleware 的 `on_system_prompt` 和 `on_acting`；
- 现有 Workspace 的 list、add、install、remove；
- 现有发布范围和用户权限结果；
- 现有应用日志。

新增内容集中在 `examples/agent_service` 应用层，不修改 AgentScope 核心 Skill 代码，保持了业务与观测逻辑的边界。

## 4. 当前验证状态

已经补充单元测试和真实会话集成测试代码，覆盖：

- 应用层提示要求先调用 `Skill`；
- 非 Skill 工具正常透传；
- Skill 工具调用正常透传；
- 真实 Toolkit 中存在内置 `Skill` 工具；
- 真实 Agent 模型输入包含 Skill 名称、描述和应用层调用约定；
- 真实 Skill viewer 返回结果未被 Middleware 改写；
- 诊断日志不包含 Skill Markdown 正文；
- 同步存在子操作失败时标记为 partial；
- 同步耗时能够生成。

已使用工作区 Python 3.12 环境补齐项目核心依赖、`service`、`observability-postgres`、`pytest` 和 `PyYAML`。当前结果为：

```text
tests/token_usage_analytics_test.py + tests/skill_observability_test.py + tests/skill_observability_store_test.py + tests/toolkit_skill_test.py
22 passed
```

另外已用异步 SQLAlchemy 测试引擎验证表初始化和事件写入流程；当前未连接真实 PostgreSQL 实例。

这代表 Skill 可观测性和 Toolkit/Skill 专项测试通过，不代表完整项目测试全部通过。

真实会话测试已经覆盖：

```text
内置 Skill 工具确实进入会话
Skill 名称和描述确实进入模型系统提示词
应用层调用约定没有覆盖 AgentScope 原有 Skill 指令
```

## 5. 当前边界

目前属于“用户 Token 聚合 + Skill 基础诊断 + 事件落库能力”，还不是完整的生产级可观测平台：

- 已有日志，但尚未接入 request ID 和 trace ID；
- 已有 Skill 调用事件，但尚未转成 OpenTelemetry Metrics；
- AgentScope 提供 TracingMiddleware，但当前服务入口尚未启用；
- Skill 发布和删除尚未增加专属管理审计；
- PostgreSQL 事件表已建立，并已增加管理员历史查询 API；
- Skill 管理员分析页已经建设，暂不包含 MCP 指标。

这些不影响当前三项功能补齐，但需要在后续生产化阶段继续完成。

## 6. 下一步顺序

```text
1. 配置测试 PostgreSQL 并验证真实表创建、事件写入和查询 API
2. 执行完整项目测试并保留结果
3. 接入 request ID 和 TracingMiddleware
4. 将同步次数、耗时和变更数量转为 OTel Metrics
5. 增加管理员发布和删除审计
6. 根据实际使用情况继续细化 Skill 分析维度
```

## 7. 验收方式

阶段验收建议提供：

1. 自动化测试结果；
2. 一条同步成功日志；
3. 一条同步失败或 partial 日志；
4. 一条 `skill.invoked` 和 `skill.completed` 日志；
5. 证明日志中不包含 Prompt、Skill Markdown 正文和凭据；
6. 证明没有修改 AgentScope 核心 Skill loader、Toolkit 和 Workspace。

## 8. 当前结论

当前代码已经建立了 Skill 使用约定和基础诊断链路，能够区分“已暴露”和“实际调用”，并通过独立 PostgreSQL 事件表保存同步结果、数量变化和耗时。

下一阶段重点是接入测试 PostgreSQL 验证端到端读取，并继续补 request ID、Trace、Metrics 和管理审计；当前 Skill 分析页已经可作为管理员查看观测数据的入口。
