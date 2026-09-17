# 项目地址与运行边界

## 代码地址

| 组件                   | 本项目内路径                       | 角色                                                  |
| ---------------------- | ---------------------------------- | ----------------------------------------------------- |
| 新销售运营中心前端     | `sales-console/apps/admin-web`     | 销售人员登录后管理客户、充值、报表和升级              |
| 新销售运营中心后端     | `sales-console/services/sales-api` | 鉴权、业务规则、签名、客户回调和升级编排              |
| 客户系统接入边界       | 新项目定义的 Bearer API            | 普通用户/管理员所在的客户系统通过接口提交申请和上报   |
| lxScope 主体           | `src`                              | Agentscope/lxScope 运行时与后端风格参考               |
| AgentScope Web UI 参考 | `examples/web_ui/frontend`         | React、pnpm、Node 20、ESLint、Prettier 和视觉结构参考 |

## 本地地址

| 服务         | 地址                            |
| ------------ | ------------------------------- |
| 销售 API     | `http://127.0.0.1:44100`        |
| 销售 Web     | `http://127.0.0.1:44101`        |
| API 健康检查 | `http://127.0.0.1:44100/health` |

前端 Vite 开发服务器将 `/api` 代理到 `44100`。生产环境建议由 Nginx 或同类网关托管静态文件，并将 `/api` 反向代理到 API 服务。

## 外部系统边界

客户系统由 `configured_ip + port + protocol + customer API Token` 标识。销售中心向客户系统调用：

- `/integration/sales/v1/ping`：连通性/版本探测
- `/integration/sales/v1/upgrades/app`：龙信业务应用升级
- `/integration/sales/v1/upgrades/core`：AgentScope 平台整体升级

客户系统向销售中心调用：

- 在线充值申请、轮询、确认到账
- 使用量与余额上报
- 历史离线充值码同步
- 发布包下载
- 连接验证

客户系统调用新销售中心时使用 `Authorization: Bearer <customer-api-token>`；销售人员前端使用 HttpOnly Cookie。两类身份不可混用。新销售中心不读取或调用历史销售总台的任何内部资源。

新接口的 canonical 基础路径为 `/api/v1`；字段使用 `snake_case`，时间使用 UTC ISO 8601，金额使用字符串。详见 `docs/API-CONTRACT.yaml`。
