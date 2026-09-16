# 数据模型草案

## Staff

销售人员账号。密码只保存 `passwordHash + passwordSalt`，接口不返回密码字段。第一阶段角色为 `admin`，后续扩展 `operator`、`auditor`。

## Customer

客户系统连接与运营信息：

- `id`：销售中心内部 ID
- `name`：客户名称
- `systemId`：客户系统 ID
- `environment`：`test` / `production` / `unclassified`
- `protocol`、`ip`、`port`：客户系统地址
- `apiToken`：客户接口访问令牌，严禁写入前端日志
- `status`：`active` / `disabled`
- `lastReport`：最近一次客户系统汇总上报
- `lastSeenIP`、`lastSeenAt`：最近上报来源

## RechargeOrder

充值业务订单：

- `method`：`online` 或 `offline`
- `status`：`pending` → `approved` / `rejected`；签发充值码后为 `issued`
- `requestID`：管理系统提交申请时的链路关联 ID，审批结果和轮询数据原样带回
- `requestedAt`：管理系统提交申请时的 UTC 时间
- `requestedAmount`：客户申请金额
- `amount`：销售审核确认金额
- `code`：审批通过后生成的 `LXRC2.<payload>.<signature>` 签名充值码，并在客户轮询响应中返回
- `expiresAt`：充值码过期时间，默认自审批签发起 1 小时
- `decisionReason`：销售人员批准或拒绝时填写的原因
- `delivered`、`deliveryAttempts`、`lastDeliveryAt`：客户系统轮询和确认状态
- `ackOperationID`、`redemptionOperationID`、`ledgerID`：客户系统 ACK 的本地操作和账本标识
- `createdAt`、`processedAt`、`deliveredAt`：订单时间线

## UsageReport

客户系统周期快照：`poolTokens`、`totalRecharged`、`cumulativeConsumed`、`cumulativeCredits`、`appVersion`、`reportedAt`。销售中心使用最后一次报告判断在线状态；报告不是实时监控，也不是单笔交易凭证。

## ReleaseMeta

发布包元数据：`type`、`version`、`file`、`size`、`sha256`、`uploadedAt`、`uploadedBy`。实际压缩包位于数据目录 `releases/`，而不是源码目录。
