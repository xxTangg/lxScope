# Skill 模块可观测性编写前置准备与设计边界

> 本文是 Skill 作为单个业务能力的专项观测设计，不是 lxScope 项目级可观测性的总定义。项目级范围、数据关联和接入边界见 [PROJECT_OBSERVABILITY.md](./PROJECT_OBSERVABILITY.md)。

> 状态：前置设计文档
>
> 目的：在 Skill 功能验证通过后，为可观测性编码明确观测范围、已有组件、接入位置、解耦边界和验收标准。
>
> 本文档只描述准备工作和设计方案，不代表已经完成可观测性代码实现。

## 1. 背景与目标

Skill 模块已经包含技能安装、发布范围控制、用户可见性过滤、会话工作区同步、技能加载和删除清理等能力。

可观测性工作的目标不是重新实现 Skill，也不是简单增加几条日志，而是让系统在运行过程中能够回答：

1. 某个用户的 Skill 为什么可见或不可见？
2. 某个会话启动时，Skill 是否成功同步到工作区？
3. Skill 是在哪个阶段失败的？
4. 删除或撤销发布后，工作区是否仍有残留？
5. Skill Hub、Redis、工作区等依赖是否导致了失败或变慢？
6. 一次请求、会话、工作区同步和 Agent 调用之间能否被关联起来？

可观测性最终应服务于三类工作：

- 故障定位：快速找到失败环节和失败原因；
- 运行评估：了解成功率、耗时、规模和资源使用情况；
- 业务审计：追踪管理员对 Skill 发布、隐藏和删除等高风险操作。

## 2. Skill 当前业务链路

### 2.1 发布和使用链路

```text
管理员安装或选择 Skill
        ↓
管理员技能库记录
        ↓
管理员设置发布范围
        ↓
发布目录
        ↓
当前用户可见 Skill 列表
        ↓
会话运行前工作区同步
        ↓
AgentScope 组装 toolkit
        ↓
模型实际使用 Skill
```

### 2.2 删除链路

```text
管理员删除 Skill
        ↓
撤销发布
        ↓
删除管理员技能库记录
        ↓
删除用户侧已同步记录
        ↓
遍历并清理已有工作区
        ↓
下一次会话不再加载该 Skill
```

这两条链路必须分别观测。只观测管理员操作，无法判断工作区是否真正完成同步；只观测工作区，也无法解释权限来源。

## 3. 当前已有的可观测基础

当前项目已经存在一些可复用能力，但它们还没有组合成完整的 Skill 可观测体系。

| 已有组件 | 当前用途 | 对 Skill 可观测性的价值 | 当前限制 |
| --- | --- | --- | --- |
| `request_id_middleware` | 生成并返回 `X-Request-ID` | 关联一次 HTTP 请求和响应 | 尚未自动覆盖所有后台同步和 Agent 运行链路 |
| `agentscope._logging.logger` / Python logging | 输出异常和运行日志 | 记录 Skill 恢复、下载、安装、清理失败 | 当前主要记录失败，成功、耗时和上下文信息不足 |
| `AdminService._audit` | 保存管理员操作审计事件 | 可记录发布、撤销、删除等高风险操作 | 当前 Skill 发布和删除路径尚未形成完整的 Skill 专属审计事件 |
| AgentScope `TracingMiddleware` | 生成 Agent、模型和工具调用 Span | 可复用 OpenTelemetry 链路能力 | 当前业务服务入口需要确认是否启用 Provider、Exporter 和该 Middleware |
| `WorkspaceBase` / `WorkspaceService` | 列出、添加、安装、删除 Skill | 是工作区同步观测的实际边界 | 现有 API 本身不提供 Skill 业务统计 |
| `ClawSkillHub` | 下载远程 Skill | 可观测远程依赖的耗时和失败 | 当前主要有异常日志，缺少统一的成功率和耗时统计 |
| 健康检查接口 | 判断服务存活 | 可扩展为基础依赖或 Skill 能力检查 | 当前没有 Skill 专属 readiness 或一致性检查 |
| Skill 相关测试 | 验证加载、归档、toolkit 等功能 | 可作为可观测性回归测试基础 | 当前没有验证日志、指标和 Span 是否产生的测试 |

### 3.1 当前已经能看到的 Skill 运行信息

目前主要能看到以下失败信息：

- 内置 Skill 恢复失败；
- 已发布 Skill 下载或安装失败；
- 删除 Skill 时枚举用户、Agent、Session 失败；
- 删除 Skill 时清理工作区失败；
- HTTP 错误及部分管理接口的 `request_id`。

### 3.2 当前缺少的能力

当前代码中尚未形成以下完整指标体系：

- Skill 同步成功率；
- Skill 同步耗时；
- 每次同步中添加、删除、恢复、下载的数量；
- 发布范围和工作区状态不一致的次数；
- Skill 下载、安装、加载的分阶段结果；
- Skill 专属的依赖失败统计；
- Skill 管理操作的统一审计结果；
- 从请求到会话、工作区同步、toolkit 加载和模型调用的完整链路。

特别需要注意：OpenTelemetry 依赖和 tracing 代码存在，并不等于当前 Skill 服务已经自动产生完整 Trace。需要在编码前确认运行时 Provider、Exporter 和 Middleware 是否实际启用。

## 4. 本次可观测性的观测范围

### 4.1 P0：必须观测的核心链路

以下内容直接影响用户是否能使用 Skill，应作为第一阶段范围：

1. Skill 发布；
2. 当前用户可见性判断；
3. 会话运行前的工作区同步；
4. Skill 下载和安装；
5. Skill 删除和工作区清理；
6. toolkit 最终加载结果。

### 4.2 P1：依赖和性能

第二阶段可覆盖：

1. Skill Hub 请求耗时和失败；
2. Redis 读取发布目录或技能记录的耗时和失败；
3. Workspace resolve、list、add、remove、install 的耗时和失败；
4. 单次同步涉及的用户、Agent、Session、Workspace 数量；
5. 发布目录同步的耗时和失败。

### 4.3 P2：运营分析和管理视图

后续可增加：

1. 各 Skill 的实际调用次数（需要存在明确的调用信号）；
2. 各 Skill 的安装成功率；
3. 各版本 Skill 的失败率；
4. 用户可见 Skill 数量分布；
5. 被撤销后仍然残留的 Skill 数量；
6. 管理员操作趋势和异常操作统计。

P2 不应阻塞第一阶段。先确保故障能够被定位，再考虑运营分析和可视化面板。

### 4.4 Skill“使用”的统计口径

Skill 的不同生命周期状态不能直接都称为“使用”。建议明确区分：

```text
published
  → visible
  → provisioned
  → loaded
  → invoked
  → completed
```

| 状态 | 含义 | 是否算实际使用 |
| --- | --- | ---: |
| `published` | 管理员已将 Skill 放入发布目录 | 否 |
| `visible` | 当前用户根据发布范围可以看到 Skill | 否 |
| `provisioned` | Skill 已同步或安装到用户工作区 | 否 |
| `loaded` | Skill 已加载到当前 Agent 的 toolkit，具备使用条件 | 否，属于技术可用 |
| `invoked` | 模型或运行时有明确的 Skill 调用、选择或引用信号 | 是 |
| `completed` | Skill 已参与本次处理并完成 | 是，属于有效使用 |

第一阶段建议把 `loaded` 作为主要观测口径，称为“Skill 已加载并可用”。

只有系统存在明确的调用事件时，才统计 `invoked` 和 `completed`，并将其称为“实际使用”。不能仅凭 Skill 已发布、用户可见、已安装或已加载，就断言用户真正使用了 Skill。

对于当前主要以 `SKILL.md` 指令形式存在的 Skill，如果模型读取了指令但没有产生独立的调用事件，系统可能无法准确证明模型是否真正遵循了该 Skill。这种场景应记录为“已加载”或“可用”，不要直接计入实际使用次数。

## 5. 指标设计

### 5.1 推荐指标清单

指标名称是逻辑名称，最终采用 OpenTelemetry Metrics、Prometheus 或其他后端时可以映射为具体实现名称。

| 指标 | 类型 | 建议维度 | 说明 |
| --- | --- | --- | --- |
| `skill_reconcile_total` | Counter | `result`、`trigger` | 工作区 Skill 同步次数 |
| `skill_reconcile_duration` | Histogram | `result`、`trigger` | 单次同步耗时 |
| `skill_reconcile_operation_total` | Counter | `operation`、`result` | 添加、删除、恢复、下载、安装等操作次数 |
| `skill_visible_resource_total` | Gauge 或日志字段 | 无高基数维度 | 当前用户可见 Skill 数量 |
| `skill_sync_mismatch_total` | Counter | `mismatch_type` | 发布目录、用户库和工作区不一致次数 |
| `skill_download_total` | Counter | `hub`、`result` | 从 Skill Hub 下载次数 |
| `skill_install_total` | Counter | `source`、`result` | 安装到工作区次数 |
| `skill_cleanup_total` | Counter | `result`、`reason` | 删除或撤销后的工作区清理次数 |
| `skill_load_total` | Counter | `result` | toolkit 加载 Skill 的结果；表示技术可用，不等于实际使用 |
| `skill_invoked_total` | Counter | `result`、`source` | 有明确调用或选择信号时，统计 Skill 实际调用次数 |
| `skill_completed_total` | Counter | `result` | 有明确完成信号时，统计 Skill 参与处理并完成的次数 |
| `skill_dependency_duration` | Histogram | `dependency`、`operation` | Skill Hub、Redis、Workspace 等依赖耗时 |
| `skill_dependency_failure_total` | Counter | `dependency`、`error_code` | 依赖失败次数 |

### 5.2 指标维度约束

指标维度必须保持低基数，避免把大量业务 ID 直接作为指标标签。

适合作为指标维度的字段：

- `result`：`success`、`failed`、`skipped`；
- `operation`：`list`、`add`、`remove`、`download`、`install`、`load`；
- `trigger`：`session_start`、`admin_delete`、`publication_change`；
- `source`：`builtin`、`skill_hub`、`user_library`；
- `dependency`：`redis`、`workspace`、`skill_hub`；
- 稳定的错误码。

不建议作为指标标签的字段：

- `user_id`；
- `session_id`；
- `workspace_id`；
- 完整 `request_id`；
- Skill 名称或任意用户输入文本；
- Prompt、Skill 文件内容、Token、密码和外部凭据。

这些字段如确有排障需要，应放在受控的日志或 Trace 属性中，并遵守数据脱敏和访问权限要求。

## 6. 日志事件设计

日志用于回答“某一次具体操作发生了什么”。建议采用结构化字段，不依赖只能人工阅读的长句。

### 6.1 推荐事件

```text
skill.publication.updated
skill.visibility.evaluated
skill.reconcile.started
skill.reconcile.completed
skill.reconcile.failed
skill.workspace.added
skill.workspace.removed
skill.download.completed
skill.install.completed
skill.load.completed
skill.cleanup.completed
skill.cleanup.failed
```

### 6.2 通用字段

建议统一包含：

```text
event_name
occurred_at
result
error_code
request_id
trace_id
user_id（按安全策略决定是否记录）
agent_id
session_id
workspace_id
skill_id
skill_name（按安全策略决定是否记录）
source
operation
duration_ms
```

其中 `request_id` 和 `trace_id` 的职责不同：

- `request_id`：应用层请求关联号，便于查接口请求和响应；
- `trace_id`：OpenTelemetry 链路关联号，便于串起多个 Span。

### 6.3 日志内容边界

不得把以下内容写入普通运行日志：

- 完整 Prompt；
- Skill 的完整 Markdown 正文；
- 用户上传文件内容；
- 模型返回的完整敏感数据；
- Token、密码、Bearer Token、Cookie；
- 不必要的个人信息。

日志中记录摘要、错误码、数量和资源 ID 即可。

## 7. Trace 链路设计

如果运行时已经启用 AgentScope OpenTelemetry tracing，建议将 Skill 同步作为一条独立业务链路接入，而不是修改 AgentScope 核心 Skill 加载器。

### 7.1 推荐 Span 层次

```text
HTTP request
  └─ chat/session turn
      └─ skill.reconcile
          ├─ published_resources
          ├─ workspace.list_skills
          ├─ workspace.remove_skill
          ├─ skill_hub.download
          ├─ workspace.install_skill
          └─ toolkit.skill_load
```

管理员删除 Skill 时，可以形成另一条链路：

```text
HTTP admin delete
  └─ skill.delete
      ├─ publication.withdraw
      ├─ user_library.cleanup
      ├─ workspace.enumerate
      └─ workspace.remove_skill
```

### 7.2 Span 属性建议

建议记录操作类型、来源、结果、耗时、数量和稳定错误码。不要把完整业务正文放入 Span 属性。

Span 结束时必须明确：

- 成功：`OK`；
- 业务跳过：记录 `skipped` 原因；
- 异常：记录错误类型、错误码并标记失败。

## 8. 基于已有组件进行编码

### 8.1 复用原则

可观测性实现应优先复用以下组件：

| 目标 | 优先复用的组件 | 接入位置 |
| --- | --- | --- |
| 请求关联 | `request_id_middleware` | HTTP 请求入口 |
| 普通运行日志 | `agentscope._logging.logger` 或 Python logging | 业务边界和异常边界 |
| 管理行为审计 | `AdminService._audit` | Skill 发布、撤销、删除等管理操作 |
| Skill 工作区操作 | `WorkspaceBase`、`WorkspaceService` | 现有 list/add/install/remove 调用周围 |
| 远程 Skill 下载 | `ClawSkillHub` | download 调用周围 |
| Agent/模型/工具链路 | AgentScope `TracingMiddleware` | 现有 AgentScope middleware 机制 |
| 任务关联 | `TaskService`、`TaskExecutor` | 任务创建、运行、完成和失败边界 |

### 8.2 建议的解耦边界

Skill 业务代码不应直接依赖具体监控平台。建议增加一个轻量的业务观测接口或适配层，由它负责：

1. 统一事件名称；
2. 统一字段命名；
3. 统一成功、跳过、失败状态；
4. 统一耗时记录方式；
5. 将日志、指标和 Trace 映射到具体实现。

业务层只表达“发生了什么”，不关心“输出到哪个平台”。

### 8.3 业务层接入位置

优先在以下现有流程边界接入：

- `examples/agent_service/main.py` 的 `_sync_current_user_skills`：同步总入口、阶段结果、耗时和数量；
- `examples/agent_service/admin_api.py` 的 `publish_resource`：发布范围变更；
- `examples/agent_service/admin_api.py` 的 `remove_installed_skill`：撤销发布、删除记录和清理工作区；
- `examples/agent_service/admin_api.py` 的 `_remove_skill_from_workspaces`：用户、Agent、Session、Workspace 遍历及清理结果；
- `examples/agent_service/admin_api.py` 的 `published_resources`：用户可见性结果和查询耗时；
- 现有 Workspace 和 Skill Hub API 调用周围：依赖耗时和失败原因；
- AgentScope toolkit 组装边界：最终 Skill 是否被加载。

不建议修改以下核心行为：

- AgentScope 核心 Skill loader；
- Workspace 的文件结构和删除语义；
- 发布范围的权限判断规则；
- Skill 的业务数据模型，除非观测确实需要且经过评估。

## 9. 审计、日志、指标和 Trace 的职责划分

| 类型 | 重点回答的问题 | 是否适合高频同步事件 |
| --- | --- | --- |
| 审计 | 谁在什么时候对哪个资源做了什么管理操作 | 不适合记录所有内部步骤 |
| 日志 | 某一次操作具体发生了什么 | 适合记录失败和关键阶段 |
| 指标 | 整体成功率、失败次数、耗时趋势如何 | 适合高频聚合统计 |
| Trace | 一次请求经过了哪些组件、在哪一步变慢或失败 | 适合跨组件排障 |

Skill 工作区同步不能全部写入审计事件，否则会把内部运行记录和管理员行为混在一起。建议：

- 管理员发布、撤销、删除：审计；
- 每次同步、下载、安装、清理：日志和指标；
- 跨请求、跨组件调用：Trace。

## 10. 编码前的准备工作清单

### 10.1 功能基线

- [ ] Skill 安装功能验证通过；
- [ ] 全体用户、指定用户、不展示三种范围验证通过；
- [ ] 用户侧可见性验证通过；
- [ ] 新会话和复用工作区场景验证通过；
- [ ] 权限撤销后工作区能清理；
- [ ] Skill 删除后已有工作区能清理；
- [ ] 用户自有 Skill 不会被误删；
- [ ] Skill Hub 不可用时失败边界明确；
- [ ] 相关单元测试和接口测试通过。

### 10.2 观测契约

- [ ] 确定事件名称；
- [ ] 确定成功、失败、跳过状态；
- [ ] 确定错误码分类；
- [ ] 确定日志字段；
- [ ] 确定指标名称和低基数维度；
- [ ] 确定 Trace 的父子 Span；
- [ ] 确定 `request_id`、`trace_id`、`session_id` 的关联关系；
- [ ] 确定敏感字段脱敏规则；
- [ ] 确定日志和指标的保存周期及访问权限。

### 10.3 运行环境确认

- [ ] 确认当前服务是否启用 OpenTelemetry Provider；
- [ ] 确认 Trace 是否有 Exporter；
- [ ] 确认是否需要 Metrics Provider；
- [ ] 确认部署环境是否已有日志采集和指标采集系统；
- [ ] 确认本地开发环境能否查看日志和测试 Span；
- [ ] 确认观测组件不可用时不能阻塞 Skill 主流程。

## 11. 推荐实施顺序

```text
1. 固化 Skill 功能测试基线
        ↓
2. 确定观测对象、事件、指标和错误码
        ↓
3. 盘点并确认已有日志、审计、request_id、Tracing 能力
        ↓
4. 定义解耦的观测接口或适配层
        ↓
5. 先接入 Skill 同步和删除清理这两条 P0 链路
        ↓
6. 增加成功、失败、耗时和数量信息
        ↓
7. 接入 Trace 和 Metrics 导出
        ↓
8. 用故障场景验证是否可以定位问题
        ↓
9. 再考虑仪表盘、运营统计和 P2 指标
```

第一阶段不要求一次完成完整监控平台。只要能够在本地或测试环境中通过日志、测试导出器或已有观测后端确认关键事件和链路已经产生，即可进入下一阶段。

## 12. 验收标准

### 12.1 功能不受影响

- 可观测性失败不能阻塞 Skill 安装、发布、同步和删除主流程；
- 观测代码不能改变 Skill 权限判断结果；
- 观测代码不能改变用户工作区 Skill 的增删逻辑；
- 不能因为记录日志而泄露 Prompt、凭据或 Skill 内容。

### 12.2 故障可定位

至少能够通过一次 `request_id` 或 `trace_id` 找到：

- 用户或会话范围；
- Skill 标识；
- 当前操作阶段；
- 操作结果；
- 耗时；
- 错误码和依赖；
- 是否已经影响 toolkit 加载。

### 12.3 指标可统计

至少能够统计：

- Skill 同步次数和成功率；
- Skill 同步耗时；
- Skill 下载、安装和删除失败次数；
- 工作区一致性异常次数；
- Skill Hub 和 Workspace 依赖失败次数。

### 12.4 可测试

至少覆盖以下场景：

1. Skill 同步成功；
2. 内置 Skill 恢复失败；
3. 远程 Skill 下载失败；
4. 工作区安装失败；
5. 权限撤销后 Skill 被删除；
6. 管理员删除后清理多个工作区；
7. 工作区不可用但管理员删除主流程仍能完成；
8. 观测输出失败但业务流程仍能完成。

## 13. 非目标

本阶段不包含：

- 重新设计 Skill 权限模型；
- 修改 AgentScope 核心 Skill loader；
- 重新设计 Workspace 存储结构；
- 新建独立的监控平台；
- 立即开发复杂的前端监控大盘；
- 记录全部 Prompt、Skill 正文或模型原始输出；
- 用审计事件替代运行日志、指标和 Trace。

## 14. 结论

当前 Skill 模块已经具备日志、请求关联、审计、工作区 API 和 OpenTelemetry tracing 等基础，但还缺少围绕 Skill 生命周期的统一观测指标。

后续编码应遵循以下原则：

1. 先观测 Skill 同步、权限一致性和删除清理这几条核心链路；
2. 优先复用现有日志、审计、request ID、Workspace、Skill Hub 和 AgentScope tracing；
3. 通过轻量观测接口或适配层解耦业务代码与具体监控后端；
4. 指标使用低基数标签，详细上下文放在受控日志或 Trace 中；
5. 可观测性异常不能影响 Skill 主流程；
6. 先完成故障定位能力，再扩展运营统计和可视化大盘。
