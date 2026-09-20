# Skill 模块可观测性正式编码规格

> 本文只描述 Skill 专项事件和历史分析。Skill、MCP、RAG、模型和 Tool 的统一项目级观测由 [PROJECT_OBSERVABILITY.md](./PROJECT_OBSERVABILITY.md) 定义。

> 状态：基于当前代码的增量实施规格
>
> 当前基线：用户 Token 使用聚合、应用层调用约定、同步摘要日志、Skill 调用诊断、可选 PostgreSQL 事件表、管理员查询 API 和可观测性分析页已经进入代码；相关专项测试和前端生产构建已通过，真实 PostgreSQL 联调、请求/Trace 关联、OpenTelemetry Metrics 和管理审计仍需继续补齐。
>
> 原则：复用 AgentScope 现有 Skill viewer、Middleware、Workspace、日志和 OpenTelemetry 能力；改动集中在 `examples/agent_service` 产品业务层，不修改 AgentScope 核心 Skill loader。

## 1. 本次需求

当前阶段补齐三件事：

1. 应用层明确要求模型在使用 Skill 前先调用内置 `Skill` 工具；
2. 测试确认 `Skill` 工具和 Skill 指令已进入会话；
3. 记录 Skill 同步成功或失败、加载数量和耗时，便于诊断；
4. 在现有用户 Token 使用记录基础上，增加管理员按周期查看每个用户 Token 消费的分析能力。

本次增量同时增加一张应用层 PostgreSQL 观测事件表，为后续“用户设置 → 分析 → Skill”页面提供历史数据来源；MCP 暂不写入这张表。

在此基础上，保留后续接入 request ID、Trace、Metrics 和管理审计的扩展边界。

## 2. 当前代码基线

### 2.1 已完成

| 能力 | 当前实现 | 代码位置 | 状态 |
| --- | --- | --- | --- |
| 应用层 Skill 调用约定 | `APPLICATION_SKILL_INSTRUCTIONS` 要求先调用内置 `Skill` 工具，再使用 Skill 引用的脚本和资源 | `examples/agent_service/skill_observability.py` | 已完成 |
| 指令进入系统提示词 | `SkillUsageMiddleware.on_system_prompt` 在存在可用 Skill 时追加应用层约定 | `examples/agent_service/skill_observability.py` | 已完成 |
| Skill 暴露诊断 | 首次处理系统提示词时输出 `skill.exposed`、可见数量、实际列入提示词数量和 `Skill` 工具可用性 | `SkillUsageMiddleware.on_system_prompt` | 已完成 |
| Skill 实际调用诊断 | `on_acting` 只拦截名称为 `Skill` 的内置工具调用 | `SkillUsageMiddleware.on_acting` | 已完成 |
| Skill 调用结果诊断 | 输出 `skill.invoked` 和 `skill.completed`，不记录返回的 Markdown 正文 | `SkillUsageMiddleware.on_acting` | 已完成 |
| 同步摘要 | `SkillReconcileSummary` 保存同步结果、数量和耗时 | `examples/agent_service/skill_observability.py` | 已完成 |
| 同步入口接入 | `_sync_current_user_skills` 返回同步摘要，并在开始和结束时输出诊断 | `examples/agent_service/main.py` | 已完成 |
| Session 关联 | `longterm_memory_factory` 将 `session_id` 传给同步摘要 | `examples/agent_service/main.py` | 已完成 |
| Middleware 注入 | 会话创建时加入 `SkillUsageMiddleware` | `examples/agent_service/main.py` | 已完成 |
| PostgreSQL 观测事件表 | 以追加事件保存 `reconcile`、`exposed`、`invoked`、`completed` 的状态、数量和耗时 | `examples/agent_service/skill_observability_store.py`、`migrations/0001_skill_observability_events.sql` | 已完成 |
| 事件写入解耦 | 通过 `SkillObservationSink` 注入，未配置 PostgreSQL 时使用 no-op，写入失败不阻塞 Skill 主流程 | `examples/agent_service/skill_observability_store.py` | 已完成 |
| 用户 Token 使用聚合 | 复用现有 Redis 消息中的模型 usage，按用户、周期统计输入、输出、总 Token、消息数和会话数 | `examples/agent_service/auth.py`、`token_usage_analytics.py` | 已完成 |
| Skill 分析查询 API | 管理员按时间范围查询事件聚合、使用链路、同步耗时和高频 Skill | `examples/agent_service/skill_analytics_api.py`、`skill_observability_store.py` | 已完成 |
| 可观测性前端可视化 | 账号菜单增加“分析”入口，优先展示每个用户 Token 消费，同时展示 Skill 趋势、生命周期、成功/失败和最近同步快照 | `examples/web_ui/frontend/src/pages/admin/analytics.tsx` | 已完成 |
| 用户 Skill 失败诊断 | 按同步、暴露、调用、执行阶段聚合失败，并展示受控错误码和最近失败用户/会话 | `skill_observability_store.py`、`skill_analytics_api.py`、`analytics.tsx` | 已完成 |
| 基础及会话测试 | 覆盖应用层提示、真实 Toolkit/Agent 输入、真实 Skill viewer、普通工具透传和 partial 结果 | `tests/skill_observability_test.py` | 已通过 6 项专项测试 |

### 2.2 部分完成

| 能力 | 当前情况 | 还需补齐 |
| --- | --- | --- |
| 确认 `Skill` 工具进入会话 | 测试已通过真实 `Agent._prepare_model_input()` 断言工具 schema 中存在 `Skill` | 已通过 |
| 确认 Skill 指令进入会话 | 测试已通过真实 Agent 输入断言 Skill 名称、描述和应用层约定进入系统提示词 | 已通过 |
| 自动化测试执行 | 已安装工作区运行时依赖并执行专项测试 | 相关测试通过，完整项目测试尚未执行 |
| 结构化日志与事件落库 | 日志字段固定，并可通过 `SkillObservationSink` 写入 PostgreSQL；管理员查询 API 已提供 | 尚未统一 request ID 和 trace ID；尚未完成真实 PostgreSQL 联调 |

### 2.3 尚未实现

- Skill 专属 OpenTelemetry Metrics；
- Skill 业务 Span；
- 当前服务入口启用 `TracingMiddleware`；
- request ID 到 Skill 同步链路的上下文传播；
- Skill 发布、撤销和删除的专属管理审计；

这些内容属于后续增强，不影响当前三项要求和当前管理员分析页的主体实现，但在称为“生产级完整可观测性”之前必须补齐。

## 3. 使用口径

```text
published
  → visible
  → provisioned
  → exposed
  → invoked
  → completed
```

| 状态 | 定义 | 当前检测方式 | 是否算实际使用 |
| --- | --- | --- | ---: |
| `published` | 管理员已保存发布记录 | 管理接口结果 | 否 |
| `visible` | 当前用户通过发布范围过滤 | `published_resources` | 否 |
| `provisioned` | 同步后 Workspace 能列出 Skill | `_sync_current_user_skills` 最终 `list_skills` | 否 |
| `exposed` | Skill 已进入本轮可用上下文，应用层协议也进入系统提示词 | `on_system_prompt` | 否，表示技术可用 |
| `invoked` | 模型调用内置 `Skill` 工具并提供 Skill 名称 | `on_acting` | 是 |
| `completed` | 内置 `Skill` viewer 返回最终结果 | `on_acting` | 是 |

不得把已发布、可见、安装或暴露直接统计为实际使用。

## 4. 应用层调用约定

### 4.1 当前约定

模型匹配到可用 Skill 时，必须按以下顺序执行：

```text
识别匹配的 Skill
    ↓
调用 Skill(skill="精确名称")
    ↓
读取 Skill viewer 返回的完整指令
    ↓
按照指令使用其中引用的脚本、文件和工具
```

`Read`、`Bash` 和 `PowerShell` 可以读取 Skill 指令引用的辅助资源，但不能替代内置 `Skill` 工具直接读取根目录 `SKILL.md`。

### 4.2 与 AgentScope 现有能力的关系

AgentScope Toolkit 本身已经提供内置 `Skill` viewer 和默认 Skill 使用说明。当前应用层约定是产品级补充约束：

- 不替换内置 `Skill` viewer；
- 不修改 Skill loader；
- 不修改 Toolkit；
- 不修改 Workspace；
- 只通过现有 `on_system_prompt` Hook 追加产品约定；
- 只通过现有 `on_acting` Hook 观察真实 `Skill` 工具调用。

## 5. 当前同步诊断字段

`SkillReconcileSummary` 当前记录：

| 字段 | 含义 |
| --- | --- |
| `result` | `success`、`partial`、`failed` 或 `skipped` |
| `error_code` | 当前主要错误或跳过原因 |
| `before_count` | 同步前 Workspace Skill 数量 |
| `visible_count` | 当前用户可见 Skill 数量 |
| `after_count` | 同步完成后 Workspace Skill 数量 |
| `removed_count` | 因权限撤销而删除的数量 |
| `restored_count` | 恢复的内置 Skill 数量 |
| `installed_count` | 下载并安装到 Workspace 的数量 |
| `failure_count` | 单个 Skill 操作失败数量 |
| `skill_names` | 同步后 Skill 名称集合，供会话 Middleware 判断是否需要生效 |
| `duration_seconds` | 同步总耗时，单位为秒 |

### 5.1 当前日志事件

```text
skill.reconcile.started
skill.reconcile.completed
skill.reconcile.failed
skill.exposed
skill.invoked
skill.completed
```

### 5.2 当前结果规则

- 正常完成且无子操作失败：`success`；
- 整体完成但存在某个恢复、下载或安装失败：`partial`；
- 同步主流程抛出异常：`failed`；
- 账号不可用或没有有效应用上下文：`skipped`。

### 5.3 PostgreSQL 事件表

表名：`skill_observability_events`

当前列分为四类：

| 类别 | 字段 | 用途 |
| --- | --- | --- |
| 事件定位 | `event_id`、`event_name`、`result`、`occurred_at` | 区分事件和时间范围查询 |
| 链路关联 | `user_id`、`agent_id`、`session_id`、`skill_name` | 按用户、会话、Skill 聚合 |
| 同步指标 | `before_count`、`visible_count`、`after_count`、`removed_count`、`restored_count`、`installed_count`、`failure_count`、`duration_seconds` | 支撑同步状态、数量和耗时分析 |
| 会话暴露指标 | `skill_count`、`listed_skill_count`、`skill_tool_available` | 判断 Skill 是否进入实际会话 |

表中明确不设置 Prompt、Skill Markdown、模型输出和凭据字段。当前写入入口由 `SkillObservationSink` 注入，分析页通过独立查询 API 读取聚合结果，不直接依赖 Middleware。

## 6. 解耦边界

### 6.1 当前结构

```text
examples/agent_service/
├─ main.py
│  ├─ 调用现有权限、Storage、Workspace 和 Skill Hub
│  └─ 产生 SkillReconcileSummary
└─ skill_observability.py
   ├─ 应用层 Skill 调用协议
   ├─ 同步摘要模型
   ├─ 诊断日志
   └─ SkillUsageMiddleware
├─ skill_observability_store.py
│  ├─ SkillObservationEvent
│  ├─ SkillObservationSink
│  ├─ PostgreSQL repository
│  └─ no-op / best-effort sink
└─ migrations/0001_skill_observability_events.sql
   └─ skill_observability_events 表和查询索引
└─ skill_analytics_api.py
   └─ 管理员查询 API：/admin/analytics/skills

examples/web_ui/frontend/src/pages/admin/
└─ analytics.tsx
   └─ Skill 趋势、生命周期、排行和同步快照
```

当前结构已经把应用层 Skill 观测从 `main.py` 主流程中分离出来。PostgreSQL 只是可选落点，不替换现有 Redis Storage；后续查询 API 和前端可以只依赖事件表，不依赖 AgentScope 核心对象。

### 6.2 必须保持的边界

- 观测代码不能修改 Skill 权限判断；
- 观测代码不能修改 Workspace 增删语义；
- Middleware 必须原样返回系统提示词之外的原业务结果；
- `on_acting` 必须原样 yield 所有下游 ToolChunk 和 ToolResponse；
- 日志失败不能阻塞 Skill 主流程；
- PostgreSQL 未配置时仍保持 stdout 日志行为；
- PostgreSQL 写入失败时降级为诊断日志，不阻塞 Skill 主流程；
- 不记录 Skill Markdown 正文、Prompt 或模型完整输出；
- 不把用户、Session、Skill 名称作为未来 Metrics 标签。

## 7. 正式测试要求

### 7.1 已有单元测试

当前测试代码覆盖：

1. 应用层提示要求先调用 `Skill`；
2. 非 Skill 工具不受 Middleware 影响；
3. Skill 工具调用能够透传；
4. 同步摘要能够从 success 转为 partial；
5. 同步耗时能够产生。

### 7.2 编码完成前必须增加的集成测试

#### A. Toolkit 工具测试（已补代码）

组装真实 Toolkit 后断言：

```text
工具 schema 中存在 name == "Skill"
```

#### B. Skill 指令测试（已补代码）

准备一个测试 Skill，生成 Agent 系统提示词后断言：

- 存在 AgentScope Skill 使用说明；
- 存在测试 Skill 名称；
- 存在测试 Skill 描述；
- 存在应用层调用约定；
- 不需要在日志中输出测试 Skill Markdown 正文。

#### C. Skill 调用测试（已补代码）

调用真实 `Skill` viewer 后断言：

- 调用输入使用精确 Skill 名称；
- viewer 返回成功；
- Middleware 不修改返回值；
- 产生 invoked 和 completed 诊断；
- 诊断中不包含 Markdown 正文。

#### D. 同步摘要测试（仍需补齐）

覆盖：

- 无变化同步；
- 权限撤销删除；
- 内置 Skill 恢复；
- Skill Hub 安装；
- 单个安装失败得到 partial；
- 主流程失败得到 failed；
- 数量和耗时符合实际结果。

### 7.3 测试执行环境

当前已使用工作区 Python 3.12 环境安装项目核心依赖、`service`、`observability-postgres`、`pytest` 和 `PyYAML`。Skill 可观测性、事件 Sink 和 Toolkit/Skill 专项测试共 18 项已通过；这不等同于完整项目测试全部通过，完整测试仍应在标准开发环境中执行。

## 8. 后续可观测性增强

### 8.1 P0：完成当前三项要求

- 保留应用层调用约定；
- 已补真实 Toolkit、系统提示词和 Skill viewer 集成测试代码；
- 补同步摘要业务测试；
- 验证 `skill.exposed`、`skill.invoked` 和 `skill.completed`；
- 已在工作区依赖环境通过 Skill 可观测性和 Toolkit/Skill 专项测试；
- 补同步摘要业务测试并继续执行完整项目测试。

### 8.2 P1：关联和 OpenTelemetry

- 将 request ID 通过安全上下文传入 Skill 日志；
- 启用现有 `TracingMiddleware`；
- 增加 `skill.reconcile` 业务 Span；
- 将同步次数、耗时和变更数量映射为 OpenTelemetry Metrics；
- 未配置 Provider 时自动 no-op；
- Observer 失败不得影响业务。

### 8.3 P2：管理审计和展示

- Skill 发布、撤销和删除接入现有 `_audit`；
- 已基于 `skill_observability_events` 增加 Skill 分析查询 API；
- 已在账号菜单增加 Skill 分析入口和图表；页面本身仍受管理员路由保护；
- 分析页已增加失败阶段、失败原因、失败率和最近失败记录；
- MCP 仅预留后续独立事件类型，不在本阶段写入；
- 不在普通 `/health` 中扫描全部 Workspace。

## 9. 后续 Metrics 规划

当前代码输出诊断日志，以下 Metrics 尚未实现：

| 指标 | 类型 | 单位 | 低基数标签 |
| --- | --- | --- | --- |
| `skill_reconcile_total` | Counter | `1` | `result`、`trigger` |
| `skill_reconcile_duration_seconds` | Histogram | `s` | `result`、`trigger` |
| `skill_reconcile_changes_total` | Counter | `1` | `change`、`result`、`source` |
| `skill_visible_count` | Histogram | `1` | 可选 `scope_type` |
| `skill_exposed_total` | Counter | `1` | `source` |
| `skill_invocations_total` | Counter | `1` | `result`、`source` |
| `skill_dependency_failures_total` | Counter | `1` | `dependency`、`operation`、`error_code` |

禁止使用 `user_id`、`session_id`、`request_id`、`trace_id`、`skill_name` 作为 Metrics 标签。

## 10. 完成标准

### 10.1 当前三项要求完成标准

- 应用层提示明确要求先调用 `Skill`；
- 仅在存在可用 Skill 时追加应用层提示；
- 真实 Toolkit 中存在内置 `Skill` 工具；
- 完整系统提示词中存在 Skill 列表和应用层约定；
- Skill viewer 能根据精确名称返回对应 Skill；
- 同步日志包含结果、前后数量、变化数量和耗时；
- Skill 调用日志区分 invoked 和 completed；
- 日志不包含 Skill Markdown 正文和敏感数据；
- 自动化测试在完整依赖环境通过；
- 没有修改 AgentScope 核心 Skill loader、Toolkit 和 Workspace。

### 10.2 生产级完整可观测性完成标准

除上述内容外，还需要：

- request ID 和 trace ID 可关联；
- Skill Trace 和 Metrics 已接入；
- 管理员发布和删除已有审计记录；
- Observer 故障不影响业务；
- 测试环境能够展示至少一条成功链路和一条失败链路。

## 11. 实施顺序

```text
当前已有应用层约定和同步日志
        ↓
补真实 Toolkit / 系统提示词集成测试
        ↓
补同步成功、partial、failed 场景测试
        ↓
在完整依赖环境执行并保留结果
        ↓
再接 request ID、TracingMiddleware 和 OTel Metrics
        ↓
最后补管理审计和可视化证据
```

不应在完整会话验证之前先建设监控大盘。
