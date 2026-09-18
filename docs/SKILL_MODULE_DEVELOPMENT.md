# Skill 模块开发文档

> 适用范围：Longxin AgentScope Web UI 的技能中心、管理员技能管理、会话技能面板与工作区同步。
>
> 当前实现原则：尽量不修改 AgentScope 底层，功能集中在 `examples/agent_service` 业务层和 `examples/web_ui/frontend` 前端层。

## 1. 背景与目标

Skill 模块需要同时解决三件事：

1. 管理员能够安装、发布、隐藏、删除技能，并按全体用户或指定用户控制可见范围。
2. 用户侧的技能中心和会话右侧技能面板只能展示当前登录用户实际有权限使用的技能。
3. 发布范围变化、技能删除后，已经存在的 AgentScope 工作区不能继续保留失效技能；新发布的技能要能够进入当前会话。

## 2. 当前遇到的主要困难

### 2.1 技能存在多个数据层

技能不是只存在一张“技能表”中，而是经过多个阶段：

```text
ClawHub 技能源
    ↓ 下载
管理员技能库记录
    ↓ 管理员发布
发布目录（Redis）
    ↓ 同步
用户技能库记录
    ↓ 当前会话装载
Agent 工作区 skills/ 文件
    ↓ AgentScope toolkit
模型实际可调用技能
```

删除管理员技能库记录，只能删除“来源记录”。已经解压到工作区的 `SKILL.md` 文件不会自动消失，因此旧技能仍可能被模型读取。

### 2.2 新建会话不一定等于新建工作区

当前 `LocalWorkspaceManager` 使用按 agent 的工作区隔离策略。同一个 agent 下新建会话，通常仍复用原来的工作区目录。因此：

- 旧会话安装过的技能会出现在新会话中；
- 删除技能库记录不会自动清理旧工作区文件；
- 只刷新页面或新建会话无法解决工作区残留。

### 2.3 前端面板和模型实际工具列表是两套数据

前端右侧“当前技能”面板展示 `/resources/published?kind=skill` 返回的当前用户可见目录；模型在后端组装 toolkit 时读取会话工作区技能。工作区中的 Markdown 只用于补充详情内容，不再决定面板是否展示该技能。

这样可以避免管理员刚发布技能时，用户库已有记录但旧会话工作区还没有解压文件，导致“技能中心能看到、当前技能面板看不到”的问题。面板是只读展示，技能的安装、发布、隐藏和删除统一由管理员界面负责。

### 2.4 全体可见、指定用户和当前用户的含义容易混淆

发布范围定义如下：

| 范围 | 含义 |
| --- | --- |
| `all` | 对所有 active 用户可见，包括管理员 |
| `selected` | 仅 `user_ids` 中的用户可见 |
| `none` | 不向任何用户展示 |

会话最终使用的是当前用户的可见结果，而不是管理员目录中的全部技能。当前用户接口 `/resources/published?kind=skill` 已经完成用户范围过滤。

### 2.5 管理员技能列表中的体验问题

之前存在以下问题：

- 指定用户使用原生多选框，选择范围不直观；
- 技能卡片描述过长，页面高度不一致；
- 已安装技能没有详情查看入口；
- 技能中心管理员页面和管理员技能管理页面没有同步发布范围控件；
- 删除技能只删了技能库记录，没有同步清理工作区；
- 用户列表请求曾使用 `page_size=500`，但后端上限为 `100`。

## 3. 已完成的解决方案

### 3.1 管理员发布范围

文件：[`examples/web_ui/frontend/src/pages/admin/skills.tsx`](../examples/web_ui/frontend/src/pages/admin/skills.tsx)

已支持：

- “全部展示”和“全部隐藏”批量操作；
- `不展示给用户`、`全部用户`、`指定用户`三种范围；
- 指定用户弹窗选择、全选、清空；
- 发布状态徽标；
- 技能卡片固定高度；
- 点击名称或描述打开详情抽屉；
- 已安装技能删除确认。

发布数据统一通过：

```text
POST /admin/resources
GET  /admin/resources?kind=skill
```

发布目录由管理员业务层保存，MCP 和 Skill 共用同一套发布模型。

### 3.2 技能中心管理员页面同步范围控制

文件：[`examples/web_ui/frontend/src/pages/skill/index.tsx`](../examples/web_ui/frontend/src/pages/skill/index.tsx)

管理员在“已安装的技能”页面也可以直接修改发布范围。该页面和“技能管理”页面调用同一个 `/admin/resources` 接口，因此不会出现两个页面状态不一致的问题。

管理员删除技能使用：

```text
DELETE /admin/skills/{skill_id}
```

该接口会同时处理：

1. 撤销发布；
2. 删除管理员技能库记录；
3. 清理已登记用户项目工作区中的同名技能。

### 3.3 当前用户会话技能过滤

文件：[`examples/web_ui/frontend/src/pages/chat/ChatViewport.tsx`](../examples/web_ui/frontend/src/pages/chat/ChatViewport.tsx)

会话面板不再对管理员角色直接放开全部技能，而是统一使用当前用户的发布结果：

```text
当前用户 /resources/published?kind=skill 结果 = 会话面板可见技能
```

右侧面板不提供增加和删除按钮。管理员对范围的编辑只能在管理员页面完成，避免用户直接修改会话工作区后绕过发布范围。

发布目录通过焦点恢复和约 3 秒间隔刷新，避免管理员修改范围后已打开的用户页面长期停留在旧数据。

### 3.4 会话前置工作区校准

文件：[`examples/agent_service/main.py`](../examples/agent_service/main.py)

在现有 `longterm_memory_factory` 中增加业务层校准逻辑 `_sync_current_user_skills`。它在 AgentScope toolkit 组装前执行，不修改 AgentScope 核心代码。

校准流程：

1. 查询当前用户的发布范围；
2. 查询当前工作区技能；
3. 对管理员控制且当前用户不可见的技能执行 `workspace.remove_skill`；
4. 对可见但缺失的内置技能，从产品内置目录重新加入；
5. 对可见但缺失的下载技能，从当前用户技能库和对应 Skill Hub 下载并安装；
6. AgentScope 后续组装 toolkit 时读取的就是校准后的工作区。

用户自己上传、且不属于管理员发布目录的本地技能不会被这段逻辑误删。

### 3.5 删除后清理已有工作区

文件：[`examples/agent_service/admin_api.py`](../examples/agent_service/admin_api.py)

管理员删除技能时，业务层会遍历：

```text
用户 → agent → session → workspace
```

然后调用现有工作区 API 删除匹配的技能。该逻辑只使用 AgentScope 已有的：

- `workspace_service.resolve(...)`
- `workspace.list_skills(...)`
- `workspace.remove_skill(...)`

没有修改 `src/agentscope` 的工作区实现、toolkit 实现或技能加载器。

## 4. 关键接口与文件职责

| 模块 | 职责 |
| --- | --- |
| `examples/agent_service/admin_api.py` | 发布范围、用户范围、删除清理、管理员资源接口 |
| `examples/agent_service/main.py` | 应用层会话前置技能校准、工作区与 Hub 连接 |
| `examples/web_ui/frontend/src/pages/admin/skills.tsx` | 管理员技能管理界面 |
| `examples/web_ui/frontend/src/pages/skill/index.tsx` | 技能中心及管理员已安装技能列表 |
| `examples/web_ui/frontend/src/pages/chat/ChatViewport.tsx` | 当前会话技能面板的数据连接与只读展示 |
| `examples/web_ui/frontend/src/hooks/usePublishedResources.ts` | 发布目录刷新与轮询 |
| `examples/web_ui/frontend/src/hooks/useWorkspace.ts` | 当前工作区技能读取、安装、删除 |
| `examples/web_ui/frontend/src/api/admin.ts` | 管理员发布、删除 API 客户端 |
| `examples/web_ui/frontend/src/i18n/locales/zh.json` | 中文界面文案 |
| `examples/web_ui/frontend/src/i18n/locales/en.json` | 英文界面文案 |

## 5. 重要行为说明

### 5.1 发布后的生效时机

- 已打开页面：发布目录通常在焦点恢复或约 3 秒内的下一次轮询后更新；
- 当前会话：下一次会话运行前会进行工作区校准；
- 正在执行中的模型回合：不会被强制中断，仍可能使用本回合开始时已经组装的 toolkit；
- 历史消息：不会被回写或删除，历史回答中的技能名称仍会保留。

### 5.2 删除后的生效时机

管理员删除接口会清理已登记的用户工作区。对于已经开始运行的回合，删除不会中断正在执行的模型调用；下一回合开始前会重新校准。

### 5.3 `gongwen-writting` 的来源

`gongwen-writting` 是下载技能自身 `SKILL.md` 中定义的技能名称，属于 ClawHub 下载技能，不是 AgentScope 内置技能。类似“邮箱和令牌为空、需要注册”的提示，也是该技能自身规则或内容产生的。

## 6. 开发与部署

### 6.1 开发模式热加载

开发 Compose 已配置源码挂载：

```powershell
docker compose -p lxscope-dev-l `
  -f docker-compose.yml `
  -f docker-compose.dev.yml up -d
```

开发模式下：

- Vite 前端支持 HMR；
- 容器内 Uvicorn 开启 Python reload；
- Redis、工作区、Qdrant 等数据卷保持不变。

修改依赖或 Docker 配置时才需要：

```powershell
docker compose -p lxscope-dev-l `
  -f docker-compose.yml `
  -f docker-compose.dev.yml up -d --build
```

### 6.2 生产模式

生产模式前端是构建后的静态镜像，后端代码也在镜像中。普通重启不会带入新的代码，应该使用：

```powershell
docker compose up -d --build
```

仅修改代码但使用开发 Compose 时，通常不需要重新构建。

## 7. 验证清单

### 管理员范围验证

1. 安装一个 ClawHub 技能。
2. 在管理员技能管理中设置“全部用户”。
3. 用普通用户和管理员分别打开新会话。
4. 确认两边都能在技能面板和模型可用技能中看到它。
5. 改为“指定用户”，只选择一个用户。
6. 确认被选用户可见，其他用户不可见。
7. 改为“不展示给用户”。
8. 确认下一次会话运行前，工作区中不再有该管理员管理技能。

### 删除验证

1. 删除管理员已安装技能。
2. 确认管理员技能库记录消失。
3. 确认发布目录中没有对应资源。
4. 已存在项目下一次运行前不再加载该技能。
5. 历史聊天文字仍然存在，这是预期行为。

### 静态检查

当前已完成：

- Python 文件 AST 语法检查；
- 中文、英文语言包 JSON 解析检查；
- `git diff --check` 检查；
- 确认没有修改 `src/agentscope`。

完整前端构建需要容器内依赖已经安装；如果本机没有 `node_modules`，应使用开发容器或先安装依赖后执行：

```powershell
pnpm --dir examples/web_ui build:frontend
```

## 8. 当前限制与后续建议

1. 当前页面同步是“焦点恢复/约 3 秒轮询”，不是 WebSocket 级别的即时推送；模型侧则在每次会话运行前校准。
2. 正在执行的模型回合不会被取消；如有强制撤销需求，需要增加运行中断机制。
3. 前端用户列表请求上限为 100，用户规模超过 100 时应补充分页或服务端搜索。
4. 如果技能已经在旧版本中被删除、且发布目录也没有留下记录，系统无法仅凭目录判断它是否是用户自行上传的技能；后续可以增加删除墓碑或技能来源标记。
5. Skill Hub 暂时不可用时，新技能无法自动下载进入工作区，但发布目录和用户技能库记录仍可保留，下一次校准可以重试。

## 9. 设计原则总结

- 发布范围是权限数据，不等于工作区文件；两者必须分别同步。
- 会话面板展示范围和模型 toolkit 范围必须使用同一份当前用户权限结果。
- 删除技能必须同时处理来源记录、用户库记录、发布目录和工作区副本。
- 新建会话不能假设工作区为空。
- 优先在产品业务层调用已有 AgentScope API，避免为业务权限需求修改 AgentScope 核心。
