# lxScope 项目级可观测性

## 定义

项目级可观测性是通过系统外部产生的遥测数据，推断系统内部状态，并回答“哪里异常、为什么异常、影响范围是什么、是否已经恢复”。它不是某个 Skill、MCP 或单一组件的监控页面。

当前服务按照日志（Logs）、指标（Metrics）和链路（Traces）三类数据构建观测基础，并通过请求上下文把三者关联起来。

## 观测范围

```text
用户请求
  → 鉴权与 API
  → 会话与 Agent 运行
  → 模型调用
  → Tool / Skill / MCP / RAG
  → Redis / PostgreSQL / Qdrant / Workspace
  → 流式响应与用户体验
```

Skill、MCP、知识库和工作区都只是被观测对象。任何一个对象的观测数据都不能替代整条请求链路。

## 已接入能力

- HTTP 请求统一生成或透传 `X-Request-ID`；
- OTel SDK 可选配置，响应在可用时返回 `X-Trace-ID`；
- HTTP、Agent、模型和 Tool 的成功/失败及耗时事件；
- 应用层有界事件存储，向管理员端提供请求、模型、Agent、Tool 和异常聚合；
- `GET /observability/metrics` 提供管理员可访问的 Prometheus 文本；
- `GET /admin/observability/overview` 提供管理员端运行分析总览；
- `GET /admin/observability/{model|agent|tool}` 提供运行组件详情和失败钻取；
- `OTEL_EXPORTER_OTLP_ENDPOINT` 可将链路导出到 OTLP/HTTP Collector；
- 观测失败不会阻塞主业务启动或请求处理；
- 观测模块只记录元数据和摘要，不记录 Prompt、工具参数、工具结果或凭据。

## 关联字段

| 字段 | 用途 |
| --- | --- |
| `request_id` | 关联一次 HTTP 请求及其响应 |
| `trace_id` | 关联跨组件 Trace 和 Span |
| `session_id` | 关联一次会话内的 Agent 执行 |
| `agent_id` | 标识参与执行的 Agent |
| `model` / `tool` | 作为受控的分析维度，不作为用户或会话级标签 |

不要把用户 ID、会话 ID 或请求 ID 直接作为高频 Metrics 标签；详细上下文应放在受控日志或 Trace 中。

## 运行配置

```text
LXSCOPE_OBSERVABILITY_ENABLED=true
LXSCOPE_OBSERVABILITY_MAX_EVENTS=50000
OTEL_SERVICE_NAME=lxscope-agent-service
OTEL_EXPORTER_OTLP_ENDPOINT=
```

没有配置 OTLP 后端时，服务仍然可以使用本地指标和结构化诊断；配置了 OTLP 后，部署方需要根据数据脱敏和访问策略确认 Trace 导出范围。

Skill PostgreSQL 事件表属于一个专项历史数据源，使用 `SKILL_OBSERVABILITY_DATABASE_URL` 配置，不是项目级可观测性后端的前提条件。

管理员端总览按以下层次组织：

```text
可观测性 / 运行分析
├─ 项目运行总览：请求量、成功率、平均响应时间、错误趋势
├─ 用户用量：每个用户的 Input / Output / Total Token
├─ 模型调用：调用次数、成功率、耗时、Token
├─ Agent 执行：执行次数、成功率、耗时
├─ Tool / MCP：调用次数、成功率、耗时
├─ 失败与异常：组件、错误类型、用户、会话和关联请求
└─ Skill 专项分析：独立页面查看 Skill 调用、同步和失败数据
```

Skill 专项页面使用 `/admin/observability/skills`，不会把 Skill 生命周期指标混入项目总览；旧的 `/admin/analytics/skills` 路径保留为兼容入口。

当前应用层事件存储默认保留最近 50,000 条事件，进程重启后清空；它是管理员实时分析的轻量投影，不替代生产环境的持久化日志、Trace 或指标后端。

## 排障目标

一个完整的观测闭环至少应能回答：

1. 请求是否到达服务并通过鉴权？
2. 请求在 Agent、模型、Tool、RAG 或外部依赖的哪一步失败或变慢？
3. 失败是局部请求问题，还是某个组件的整体趋势？
4. 修复后，错误率、延迟和成功率是否恢复？
