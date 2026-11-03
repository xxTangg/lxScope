# Longxin 管理员端扩展

此目录是基于 GitHub `main` 版 AgentScope 示例服务追加的龙信产品层，不属于
`src/agentscope` 通用核心。

## 当前模块

- `plan_billing/`：套餐目录、本地套餐订单、管理员审批/拒绝、系统额度分配和账本适配。
- `销售总台对接资料回传-2026-09-15.md`：按销售总台模板整理的当前实现状态和联调资料。

`plan_billing` 只通过 `storage`、`auth` 两个应用层依赖工作。为了兼容已经存在的
管理员成员/额度页面，它读取旧的 `longxin:admin:v1` 用户、系统和账本键；订单自身
使用独立的 `longxin:plan-billing:v1` 命名空间。

## 接入方式

`examples/agent_service/main.py` 负责装配：

```python
app.state.plan_billing_service = PlanBillingService(storage, auth)
app.include_router(plan_billing_router)
```

这层装配是唯一需要让宿主应用知道龙信套餐扩展的地方。AgentScope 核心不依赖
套餐、订单、销售总台或支付实现。

## 重要边界

- 普通用户订单只落本地，不直接调用销售总台。
- 管理员审批使用本地系统 Token 池；系统充值仍通过原有 Sales Hub adapter。
- 目录中的价格是演示配置，生产定价、支付和退款规则必须由产品/销售确认。
- 当前模块使用旧管理员数据键作为兼容适配，后续可替换为独立 `AccountStore`、
  `BillingStore` 和事务实现。

## 升级与备份配置

升级服务挂载在 `/admin/upgrades`，同时提供销售总台调用的
`/integration/sales/v1/upgrades/{artifact_type}`。发布包和备份默认存放在
`LONGXIN_DATA_DIR`（默认 `data/`）下，不写入源码目录。管理员实际执行升级前，
需要配置：

```text
LONGXIN_DATA_DIR=/var/lib/longxin
LONGXIN_APP_TARGET_DIR=/opt/longxin/app
LONGXIN_CORE_TARGET_DIR=/opt/longxin/core
LONGXIN_UPGRADE_RESTART_COMMAND=<部署环境的重启命令>
LONGXIN_UPGRADE_HEALTHCHECK_URL=http://127.0.0.1:8001/health
```

每个 `.tar.gz` 必须包含 `manifest.json`，至少声明类型和版本，例如：

```json
{"artifact_type":"app","version":"3.0.4"}
```

服务会拒绝绝对路径、`..` 路径穿越、软链接、设备文件、类型/版本不符、缺少
manifest、大小不符和 SHA-256 不符的发布包；升级前复制当前目标到 `backups/`，
重启或健康检查失败时自动恢复该备份。
