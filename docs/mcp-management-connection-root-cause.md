# 管理系统中 MCP 显示“无法连接”的根因总结

## 一、结论

本次 MCP 显示 `ClosedResourceError`、`MCP 无法连接`，根因不在 AgentScope 主代码，也不是 MCP 服务配置本身被改坏，而是在管理系统的 Task/管理端调用 MCP 时复用了已经失效的有状态 MCP 连接。

管理系统缓存中的 MCP 客户端仍然被判断为“已连接”，但底层 AnyIO stream 或 MCP session 已经被关闭。后续请求继续使用这个失效客户端，就会出现：

```text
ClosedResourceError
MCP 无法连接
```

强制刷新后暂时恢复，是因为刷新过程重新创建或重新连接了 MCP 客户端；但如果后续请求再次复用旧的失效连接，问题就会再次出现。

## 二、实际触发链路

```text
管理端/Task 请求 MCP
        ↓
复用缓存中的 MCPClient
        ↓
客户端表面上仍是 connected
        ↓
底层 stream 已被关闭
        ↓
list_tools() 或 tool() 触发 ClosedResourceError
        ↓
前端显示“MCP 无法连接”
```

问题的关键是“连接状态和底层资源状态不一致”。仅检查一个连接状态字段，不能证明 MCP 的底层通信资源仍然可用。

## 三、为什么主分支源码可以正常添加 MCP

主分支正常，说明 AgentScope 原有 MCP 生命周期流程本身是可用的。主流程通常会在合适的请求边界创建、连接、使用和关闭 MCP 客户端。

管理系统的 Task 流程属于额外的调用入口。它没有完全复用原有生命周期边界，而是把 MCP 客户端带入了跨请求或跨执行过程的缓存状态，因此更容易出现：

- MCP 已被前一次请求关闭，但缓存对象仍被保留；
- 页面刷新后旧客户端对象仍被继续使用；
- Task 页面和管理端页面使用不同的连接状态判断；
- MCP 的 `list_tools()` 失败后，系统仍尝试使用原对象执行工具。

因此，“主代码没有问题”和“管理系统中 MCP 无法连接”可以同时成立。

## 四、强制刷新为什么有效

强制刷新会重新初始化页面或请求上下文，间接触发新的 MCP 连接创建。新的客户端拥有新的底层 stream，所以短时间内可以正常：

1. 建立连接；
2. 获取工具列表；
3. 执行 MCP 工具。

但如果管理系统没有在每次 Task/MCP 请求前检查连接是否真正可用，后续又可能继续拿到旧连接，因此普通刷新后仍会复现。

## 五、管理系统侧的修复原则

本次修复遵循以下边界：

- 不修改 `src/agentscope` 主代码；
- Task 通过 AgentScope 的公共 Toolkit/MCPClient 能力调用 MCP；
- Task 请求边界负责检查和恢复失效连接；
- 发现 `list_tools()` 失败时，关闭旧客户端并重新连接；
- 重新连接成功后重新获取工具列表，再执行 ToolStep；
- 管理端探测使用新建的短生命周期 MCPClient，避免污染缓存连接。

核心处理逻辑是：

```text
Task 请求开始
    ↓
检查 MCP 客户端是否真的能 list_tools()
    ↓
成功：继续执行
失败：close → connect → list_tools()
    ↓
成功：继续执行
失败：明确返回 MCP 连接错误
```

## 六、Task Planner 相关的隐性问题

连接问题之外，Task 生成节点也会放大故障表现：

- AgentStep 可能只生成“请调用某工具”的文字，而没有真正生成 ToolStep；
- ToolStep 可能出现空 `tool_name`；
- 工具参数可能不符合 MCP 的 `input_schema`；
- 最终 AgentStep 以前只能看到上一节点输出，看不到天气、地理等全部结果；
- MCP 工具权限不足或外部网络 TLS 失败时，页面也可能统一显示为 MCP 不可用。

这些问题与 MCP 连接生命周期不同，但都会让用户感觉“管理系统调用 MCP 不稳定”。

## 七、当前 Task 管理层的改进

管理系统现在采用 AgentScope 能力的接口化适配方式：

- 使用 AgentScope 的模型和结构化输出生成 TaskPlan；
- 使用 AgentScope Toolkit 提供真实工具列表和参数 schema；
- 保存计划前校验工具名称、参数和节点顺序；
- 计划不合法时自动让模型修复一次；
- ToolStep 执行前使用 AgentScope 公共 MCP 能力检查连接；
- 最终 AgentStep 接收全部前序节点结果。

## 八、需要区分的三类错误

### 1. 连接生命周期错误

典型表现：

```text
ClosedResourceError
stream closed
MCP 无法连接
```

处理：重新建立 MCP session，不能只刷新页面状态。

### 2. 权限错误

典型表现：

```text
Permission required for mcp__xxx__tool
```

处理：检查当前 Agent/Session 的 PermissionContext，必要时允许该工具。它不是 MCP 断连。

### 3. 外部网络错误

典型表现：

```text
Client network socket disconnected before secure TLS connection was established
```

处理：检查容器网络、代理、DNS、出口策略和第三方 API。它不是 Task Planner 或 MCP 生命周期错误。

## 九、最终判断

本次“管理系统添加 MCP 后无法连接”的真正原因是：

> 管理系统的 Task/管理端调用路径复用了生命周期已经结束的 MCP 有状态客户端，导致客户端状态与底层 stream 状态不一致；同时，旧的 Task Planner 和结果传递机制放大了错误表现。

因此正确修复方向是：

1. 管理系统请求边界增加 MCP 健康探测和重连；
2. Task 复用 AgentScope 的 Toolkit/MCP 公共能力；
3. Planner 增加 ToolStep/schema/节点顺序校验；
4. Task 向最终 Agent 传递全部前序结果；
5. 权限错误、网络错误和生命周期错误分别处理，不能都显示成“无法连接”。
