# lxScope 项目 Skill 全面说明

> 本文档汇总当前 lxScope 项目中与 Agent Skill 相关的架构、加载流程、调用规范、问题定位、应用层改造、运行时诊断和面试经验。
>
> 本文讨论的是项目自身的 AgentScope Skill 系统，不是 Codex 的 Skill 系统。

## 1. Skill 的定位

Skill 是一组面向模型的可复用任务指令，通常由一个目录组成：

~~~
skill-name/
├── SKILL.md       # 必需：元数据和操作指令
├── scripts/       # 可选：脚本
├── references/    # 可选：参考资料
└── assets/        # 可选：模板和其他资源
~~~

Skill 不是普通工具，也不是 MCP Server。

Skill 主要描述：

- 什么时候应该使用；
- 应该按照什么步骤执行；
- 可以使用哪些脚本和资源；
- 如何验证最终结果。

真正执行动作的仍然是普通工具、MCP 工具或 Skill 引用的脚本。

## 2. SKILL.md 格式

一个最小的 Skill 至少需要在根目录放置 SKILL.md：

~~~markdown
---
name: paper-innov-compare
description: Compare research papers and produce a structured report.
---

# Paper Innovation Comparison

## When to use

Use this Skill when the user asks to compare research papers.

## Workflow

1. Inspect the input documents.
2. Run the supplied analysis script.
3. Generate and verify the final report.
~~~

要求：

1. name 是模型调用 Skill 时使用的精确名称；
2. description 用于帮助模型判断是否匹配任务；
3. name 和 description 缺失时，Skill 不会进入有效列表；
4. 脚本、参考文件和资源路径必须真实存在；
5. 不能依赖“第一个 Skill”或列表顺序。

如果 SKILL.md 声明了 scripts/create_compare_docx.py，但目录中没有这个文件，则 Skill 包本身是不完整的。自行创建替代脚本可以作为 fallback，但应明确标记为兼容性替代方案，不能宣称完全遵循原 Skill。

## 3. 当前项目中的 Skill 来源

当前项目的 Skill 主要来自以下层次：

| 来源 | 说明 |
| --- | --- |
| 内置 Skill | 位于 examples/agent_service/builtin_skills/，随产品部署提供 |
| 用户 Skill 库 | 管理员安装或从 Skill Hub 下载后形成的用户资源 |
| Workspace Skill | 当前 Agent 实际可用的 Skill，存放在 Workspace Skill 分区 |
| Skill Hub | 用于浏览和下载远程 Skill |

当前仓库内置的业务 Skill 包括：

- work-report
- project-initiation
- customer-solution
- research-report
- presentation-review
- executive-brief
- job-presentation
- roadmap-plan
- solution-comparison
- speech-story
- training-course
- data-report

其中，内置 Skill 由应用启动时扫描 builtin_skills 目录，并作为新 Workspace 的种子内容。

## 4. 当前项目的 Skill 生命周期

Skill 不能只用“有没有这个文件”来描述。当前项目应该区分以下状态：

~~~text
published
    ↓
visible
    ↓
provisioned
    ↓
exposed
    ↓
invoked
    ↓
completed
~~~

| 状态 | 含义 | 是否代表模型实际使用 |
| --- | --- | ---: |
| published | 管理员已经发布 Skill | 否 |
| visible | 当前用户根据发布范围可以看到 Skill | 否 |
| provisioned | Skill 已同步或安装到 Workspace | 否 |
| exposed | Skill 名称和描述已经进入 Agent 可用上下文 | 否 |
| invoked | 模型调用了 Skill 工具 | 是 |
| completed | Skill 工具成功返回 | 是 |

核心原则：

> Skill 已安装、已可见或已进入 Toolkit，都不能直接证明模型真正使用了 Skill。

## 5. 当前项目的加载链路

当前项目的应用级流程如下：

~~~text
用户发起会话
    ↓
会话前同步当前用户有权限的 Skill
    ↓
Skill 进入当前 Agent 的 Workspace
    ↓
Workspace.list_skills()
    ↓
AgentScope Toolkit 组装
    ↓
模型看到 Skill 名称、描述和目录
    ↓
模型判断任务是否匹配
    ↓
调用 Skill(skill="精确名称")
    ↓
Skill viewer 返回 SKILL.md 正文
    ↓
模型读取辅助文件或执行脚本
    ↓
生成和验证最终结果
~~~

### 5.1 会话前同步

入口位于：

~~~text
examples/agent_service/main.py
~~~

应用层的 _sync_current_user_skills 负责：

1. 查询当前用户可见的 Skill；
2. 删除当前用户已经无权使用的受管 Skill；
3. 恢复缺失的内置 Skill；
4. 从 Skill Hub 下载并安装缺失的远程 Skill；
5. 最后再次列出 Workspace Skill，确认同步结果。

这个阶段解决的是“当前用户有哪些 Skill 可以使用”，不代表模型已经实际调用了某个 Skill。

### 5.2 Workspace 解析

Workspace 会扫描当前 Agent 的 Skill 分区，读取每个 Skill 根目录的 SKILL.md，并解析：

- name；
- description；
- Markdown 正文；
- Skill 目录；
- 更新时间或索引信息。

解析结果会形成 AgentScope 的 Skill 对象，并传递给 Toolkit。

### 5.3 Toolkit 暴露

当前项目使用 AgentScope 2.0 的 skills_or_loaders 机制。Toolkit 会：

1. 收集当前可用 Skill；
2. 生成 Skill 列表 Prompt；
3. 注册内置 Skill viewer；
4. 在工具 schema 中暴露名为 Skill 的工具。

典型调用形式：

~~~json
{
  "skill": "paper-innov-compare"
}
~~~

Skill viewer 返回 Skill 的 Markdown 正文，但不负责执行 Skill 中的脚本。

## 6. AgentScope 官方流程与当前项目差异

AgentScope 不同版本的 Skill 实现存在差异，不能把所有版本的行为混为一谈。

### 6.1 官方教程中的通用流程

官方教程中，典型步骤是：

1. 准备包含 SKILL.md 的 Skill 目录；
2. 注册到 Toolkit；
3. Toolkit 生成 Skill Prompt；
4. Agent 自动将 Skill Prompt 追加到 system prompt；
5. 为 Agent 提供文本读取工具或 Shell；
6. 模型读取 SKILL.md；
7. 模型根据 Skill 指令调用工具和脚本。

这个流程强调的是“Skill 是文件化指令”，并不意味着所有 AgentScope 版本都必须调用一个名为 Skill 的工具。

### 6.2 AgentScope 2.0 的 Skill viewer

AgentScope 2.0 的实现增加了 SkillLoader 和内置 Skill viewer。当前 lxScope 的底层代码已经包含这一设计：

- Toolkit 中存在 SkillViewer；
- 工具名称是 Skill；
- Skill 有可用时才会暴露 Skill viewer；
- Skill viewer 根据精确名称返回完整 Markdown。

当前项目的默认 Toolkit Prompt 还要求模型优先调用 Skill viewer。

因此要区分：

| 问题 | 结论 |
| --- | --- |
| Skill 是否是普通工具 | 不是 |
| 当前项目是否有 Skill 工具 | 有，名称为 Skill |
| 所有 AgentScope 版本是否强制调用 Skill 工具 | 不是 |
| 当前项目默认 Prompt 是否要求调用 Skill | 是 |
| 当前项目是否在运行时阻止 Read 绕过 | 目前没有 |

## 7. 自动加载和自动调用的区别

当前项目接到任务时，会自动完成以下事情：

- 同步当前用户有权限的 Skill；
- 将 Skill 放入 Workspace；
- 通过 Toolkit 发现 Skill；
- 将 Skill 名称和描述暴露给模型。

但当前项目不会保证每个任务都调用 Skill。

实际调用仍然由模型判断：

~~~text
接到任务
    ↓
看到 Skill 名称和描述
    ↓
判断任务是否匹配
    ├── 不匹配：不调用 Skill
    └── 匹配：调用 Skill(skill="...")
~~~

所以准确说法是：

> 项目会自动准备和暴露 Skill，但不会强制每个任务都调用 Skill。

## 8. Skill 的正确调用顺序

推荐的 AgentScope 2.0 应用层顺序是：

~~~text
1. 模型发现任务匹配某个 Skill
2. 调用 Skill(skill="精确名称")
3. 阅读 Skill viewer 返回的完整指令
4. 使用 Skill 目录中的 scripts、references 和 assets
5. 调用 Bash、PowerShell、Read、MCP 等工具
6. 验证最终产物
~~~

例如：

~~~text
Skill("paper-innov-compare")
    ↓
阅读返回的 SKILL.md 正文
    ↓
运行 scripts/paper_summ.py
    ↓
生成报告
    ↓
检查报告是否符合 Skill 要求
~~~

不推荐的顺序是：

~~~text
Read("paper-innov-compare/SKILL.md")
    ↓
跳过 Skill viewer
~~~

Read、Bash 和 PowerShell 仍然可以用于：

- 读取 Skill 引用的参考文件；
- 执行 Skill 中的脚本；
- 处理输入文件；
- 验证输出文件。

它们不应替代当前项目约定的 Skill viewer 调用。

## 9. 今日发现的通用 Skill Bug

### 9.1 问题表现

模型可能声称“使用了某个 Skill”，但调用记录中没有：

~~~text
Skill(skill="...")
~~~

而是直接：

~~~text
Read(".../SKILL.md")
~~~

### 9.2 根因

项目同时存在两条路径：

~~~text
预期路径：
模型 → Skill viewer → SKILL.md 正文

绕过路径：
模型 → Read 或 Shell → 直接读取 SKILL.md
~~~

当前项目的 Prompt 要求模型调用 Skill，但 Read 和 Shell 仍可以访问 Workspace 文件，因此 Prompt 约束没有被运行时权限强制执行。

这不是 Skill loader 无法加载，而是：

> 模型行为约定和工具访问权限之间存在不一致。

### 9.3 影响

- 无法仅凭“Skill 已安装”判断 Skill 是否被使用；
- 无法仅凭最终回答判断 Skill 是否真正生效；
- 直接读取可能绕过统一审计；
- Skill 的调用名称、版本和结果更难关联；
- Skill 文件缺失时，模型可能自行构造替代方案；
- 任务结果可能看起来正确，但调用流程并不合规。

## 10. 当前项目已经完成的应用层改造

本项目已完成应用层改造，未修改 AgentScope 核心 Skill loader、Workspace 或 Toolkit。

### 10.1 应用层调用说明

文件：

~~~text
examples/agent_service/skill_observability.py
~~~

应用层提示模型：

1. 任务匹配 Skill 时，先调用内置 Skill；
2. 使用精确 Skill 名称；
3. 阅读 Skill viewer 返回的指令；
4. 再处理 scripts、references 和 assets；
5. 不使用 Read、Bash 或 PowerShell 替代根目录 SKILL.md 的 Skill 调用。

### 10.2 同步摘要

_sync_current_user_skills 现在返回 SkillReconcileSummary，记录：

- 同步前 Skill 数量；
- 当前用户可见数量；
- 同步后 Skill 数量；
- 删除数量；
- 恢复数量；
- 安装数量；
- 失败数量；
- 错误码；
- 同步耗时；
- 最终可用 Skill 名称。

### 10.3 SkillUsageMiddleware

应用层新增 SkillUsageMiddleware，用于：

- 向会话系统提示追加 Skill 调用协议；
- 记录 Skill 是否暴露给模型；
- 观察名称为 Skill 的工具调用；
- 记录 Skill viewer 最终返回状态；
- 不记录 Skill Markdown 正文和模型完整输出。

## 11. 运行时诊断事件

当前应用层记录以下事件：

| 事件 | 含义 |
| --- | --- |
| skill.reconcile.started | 开始同步 Workspace Skill |
| skill.reconcile.completed | 同步完成，包含数量、耗时和结果 |
| skill.reconcile.failed | 同步发生异常 |
| skill.exposed | Skill 已暴露给模型 |
| skill.invoked | 模型调用了 Skill viewer |
| skill.completed | Skill viewer 返回最终状态 |

诊断重点：

~~~text
skill.exposed 有记录
但 skill.invoked 没有记录
    ↓
Skill 已经可用，但本轮没有调用 Skill
~~~

如果有 skill.invoked：

~~~text
skill.invoked
    ↓
skill.completed result=success
~~~

说明模型至少完成了 Skill viewer 的读取过程。

诊断日志不应该记录：

- Skill Markdown 正文；
- Prompt 全文；
- 模型完整输入和输出；
- 用户上传文件内容；
- 密码、Token、Cookie 和 Authorization；
- 与排障无关的个人信息。

## 12. 当前限制

当前改造是应用层协议和诊断增强，不是底层强制访问控制，因此仍存在以下限制：

1. 不能从底层阻止模型直接使用 Read 或 Shell 读取 SKILL.md；
2. skill.invoked 缺失可以发现绕过，但不能阻止绕过；
3. 模型是否真正遵循 Skill 内容，不能仅凭工具调用完全证明；
4. Skill 脚本缺失时，应用层只能记录失败；
5. 当前项目不会因为任务匹配就强制调用 Skill；
6. Prompt 规则与工具权限仍然不是同一层面的约束。

如果未来需要严格合规，可以在应用权限层增加 Skill 路径策略：

- 禁止普通文件工具读取 Skill 根目录的 SKILL.md；
- 允许普通工具读取 scripts、references 和 assets；
- 对 Skill viewer 调用做名称校验；
- 对绕过行为返回明确错误；
- 将 Skill 调用和普通文件读取纳入同一条审计链路。

这类严格拦截属于访问控制增强，需要谨慎评估对文件工具和已有用户 Skill 的影响。

## 13. 测试

应用层测试位于：

~~~text
tests/skill_observability_test.py
~~~

覆盖内容：

- 应用层 Prompt 要求先调用 Skill；
- 普通 Read 调用不会被中间件改写；
- Skill 调用会原样转发给真正的 Skill viewer；
- Skill 调用结果可以被诊断；
- 同步失败时可以形成 partial 状态。

AgentScope 核心 Skill viewer 测试位于：

~~~text
tests/toolkit_skill_test.py
~~~

测试重点包括：

- Skill 有效时，Toolkit 暴露 Skill 工具；
- Skill viewer 可以按名称返回 Skill 正文；
- 不存在的 Skill 返回错误；
- 工具组和 Skill 的可见性处理。

## 14. 项目文件职责

| 文件 | 职责 |
| --- | --- |
| examples/agent_service/main.py | 用户 Skill 同步、内置 Skill 初始化、会话前校准 |
| examples/agent_service/skill_observability.py | 应用层调用协议、同步摘要、运行时诊断 |
| examples/agent_service/builtin_skills/ | 产品内置 Skill |
| src/agentscope/workspace/ | AgentScope Workspace Skill 读取和安装 |
| src/agentscope/tool/_toolkit.py | Toolkit 组装和 Skill viewer 注册 |
| src/agentscope/tool/_builtin/_skill.py | Skill viewer 的具体实现 |
| tests/skill_observability_test.py | 应用层 Skill 协议和诊断测试 |
| tests/toolkit_skill_test.py | AgentScope 核心 Skill viewer 测试 |
| docs/SKILL_MODULE_DEVELOPMENT.md | Skill 管理、发布、同步和调用说明 |

## 15. 面试中的经验总结

这个项目可以用来回答 Agent 面试中的几个典型问题。

### 15.1 如何判断 Agent 是否真的使用了 Skill

不能只看：

- Skill 是否安装；
- Skill 是否出现在列表；
- Skill 是否进入 Toolkit；
- 最终回答中是否出现 Skill 名称。

更可靠的判断是：

~~~text
模型是否调用了 Skill 工具
    ↓
Skill viewer 是否成功返回
    ↓
后续工具是否按照 Skill 指令执行
~~~

至少应该把 invoked 和 completed 作为明确的运行时事件。

### 15.2 Prompt 能不能保证模型遵循流程

不能完全保证。

Prompt 适合提供行为指导，但不能替代：

- 工具权限；
- 参数校验；
- 路径控制；
- 运行时审计；
- 失败检测。

因此更完整的 Agent 系统需要：

~~~text
Prompt 指导
    +
Toolkit 暴露
    +
工具权限
    +
Middleware 诊断
    +
调用事件审计
~~~

### 15.3 为什么不直接修改底层 AgentScope

底层 loader 负责通用 Skill 发现、解析和 viewer 能力；用户权限、发布范围、Workspace 同步和产品诊断属于应用层职责。

将业务逻辑放在应用层的好处：

- 减少对上游框架的侵入；
- 便于跟随 AgentScope 升级；
- 可以按用户和组织定义 Skill 可见性；
- 可以单独调整日志、指标和审计；
- 不破坏其他 AgentScope 使用场景。

### 15.4 如何描述本项目的技术亮点

可以概括为：

> 我把 Skill 的生命周期拆分为发布、可见、同步、暴露、调用和完成，发现项目中存在“Skill 已经可用但模型绕过 Skill viewer 直接读取文件”的流程问题。在不修改 AgentScope 核心 loader 的前提下，我在应用层增加了会话前 Skill 同步、系统提示协议和 Middleware 诊断，使用 skill.exposed、skill.invoked 和 skill.completed 区分技术可用性和实际使用，并保留了后续通过工具权限做强制拦截的扩展空间。

## 16. 日常排障清单

当用户反馈“Skill 没生效”时，按以下顺序检查：

1. Skill 目录是否存在；
2. 根目录是否存在 SKILL.md；
3. SKILL.md 是否包含 name 和 description；
4. 当前用户是否有发布权限；
5. Skill 是否同步到当前 Agent Workspace；
6. Workspace.list_skills 是否能列出该 Skill；
7. Toolkit 是否暴露 Skill viewer；
8. system prompt 是否包含 Skill 名称和描述；
9. 是否出现 skill.exposed；
10. 是否出现 skill.invoked；
11. Skill viewer 是否返回 success；
12. Skill 引用的脚本和资源是否真实存在；
13. 最终产物是否通过 Skill 要求的验证步骤。

最后要区分：

~~~text
没有 skill.exposed
    → 暴露链路有问题

有 skill.exposed，没有 skill.invoked
    → 模型没有选择 Skill，或绕过了 Skill viewer

有 skill.invoked，但 completed 失败
    → Skill 名称、Skill 内容或 Workspace 文件有问题

completed 成功，但最终结果错误
    → 需要检查 Skill 指令、脚本执行和结果验证
~~~

