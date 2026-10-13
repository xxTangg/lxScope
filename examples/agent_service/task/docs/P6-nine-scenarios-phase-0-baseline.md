# P6 九场景统一完善：阶段 0 基线与开发规划

## 检查结果

- 分支：`integrate/dev_l`。
- HEAD：`1418f2b feat(task): integrate existing rag for project planning`。
- P6-D-2F、P6-D-2G 与 P6-G-1 的实现、测试和报告都在当前工作区；它们尚未提交。本次在这些工作上增量开发。
- P6-G-1 基线 Task 全量专项测试：`162 passed, 2 skipped, 2 subtests passed`。两个 PostgreSQL 用例因没有配置 disposable PostgreSQL 测试 URL 而跳过。
- 九个系统模板均存在，当前公开版本为 `1.0.0`，各有 6–8 个线性节点；v1 没有默认 ArtifactConfig。Task 节点仍限 `agent`、`tool`、`python`、`input` 四种，最多 8 个。
- Task API 已提供模板列表/详情/实例化、Task 校验与 Preflight、运行历史、事件、节点知识图谱及有所有权校验的 Artifact 列表/下载。
- 已有 Artifact Writer 支持 Markdown、DOCX、XLSX；最终节点可以配置产物。TaskRun 以 `node_runs[].input/output/result/metadata` 保存节点数据，并保存开始/结束时间、TaskRun 错误和产物元数据。
- Task 原生能力只确认 `planning.template_retrieve` 与 `document.citation_verify`；知识库适配复用授权 KnowledgeBaseService。通用 Tool/MCP 由执行会话动态提供。仓库里未找到九场景领域 Tool/MCP 的生产注册实现；S03 有 Task-owned Schema 合约，但没有专家查询、回避或评分服务适配。
- 浏览器访问 `http://localhost:8000/task` 后转到 `/chat`，页面报告无法连接服务器；当前没有可用登录 Task 会话。真实用户隔离、动态 Toolkit 清单、KnowledgeBase、Workspace 下载和浏览器端到端验收因此处于未验证状态。
- 本机有 Python venv、前端依赖和 `pnpm`，可执行 Task 测试及前端构建。未发现 PostgreSQL/Redis/模型/知识库凭据类环境变量；这只反映当前进程环境，不能据此推断部署环境。

## 九场景能力矩阵

| 场景 | 当前可复用实现 | 生产依赖/缺口 | 本次可验证范围 |
| --- | --- | --- | --- |
| S01 项目立项 | 模板实例化、InputStep、线性 SOP、Task 产物 | `project.similar_search`、`project.rule_check` 和授权项目读取未发现生产适配；规则/历史项目库未联调 | 模板契约、Preflight 缺失阻断、合成资料下的编排测试 |
| S02 项目策划 | P6-D-2F 定向模板 RAG、P6-D-2G 项目资料证据快照、P6-G-1 Claim–Evidence、引用复核、Artifact Writer | 当前没有有效登录会话；真实 KB 和 Workspace 仍未验收；v1 无默认文件配置 | 既有 Task 夹具回归、版本兼容、离线引用/产物验证；不冒充真实 KB 联调 |
| S03 专家智能匹配 | S03 Tool Schema/参数/结果契约、冲突未知时拒绝评分、8 节点模板 | `expert.lookup/conflict_check/rank` 无生产数据服务；正式审批策略与 HumanApproval 仍阻塞 | 隔离的合成专家数据契约测试，演示结果显式标示；不发邀请、不审批 |
| S04 会前准备 | 模板、InputStep、Task Office Writer | 费用规则来源、材料生成/检查及通知服务未发现；通知有外部副作用 | 确定性规则和草稿结构的纯测试；Preflight 阻断通知调用 |
| S05 会议召开与纪要 | 模板、InputStep、Task Office Writer | ASR/音频权限/真实时间定位未联调 | 用带来源标识的虚构转写文本测试后续整理；不声称系统完成 ASR |
| S06 意见整理 | 模板、Task Office Writer | OCR/手写识别与意见分类服务未发现 | 虚构转录文本/图片元数据的隔离契约测试，低置信记录保留人工复核 |
| S07 报告生成 | 模板、通用 Claim–Evidence、引用校验和 Artifact Writer 可复用 | `report.rag_retrieve/citation_check/consistency_check/compliance_check` 没有生产适配；合规语义不能自动证明 | 通用协议和隔离证据夹具测试；输出保持草稿并要求语义人工复核 |
| S08 归档移交 | 模板、Task 文件元数据/产物机制可复用 | 正式档案系统、写入授权、幂等与回执服务缺失；存在外部写入副作用 | 待移交清单和完整性规则测试；禁止将未连接的 `archive.write` 显示为成功 |
| S09 报销结算 | 模板、可复用 Decimal/Schema/Artifact 能力 | 发票 OCR/验真、费用政策、ERP 接口及正式审批缺失；存在财务副作用 | 金额、税额、重复票据纯函数测试及待提交文件生成；不验真、不提交 ERP |

此矩阵描述的是仓库和当前本机环境可见的能力，不等同于运行部署中用户会话的动态 Tool/MCP 清单。Preflight 的实际可用状态必须按当前会话返回。

## 开发顺序

1. 为通用 Claim–Evidence 协议增加可选 EvidencePolicy，并将各场景策略声明与校验器分层；旧 TaskRun 不配置策略时保持现有兼容路径。
2. 用现有 `NodeRunRecord` 持久化面向 UI 的结构化结果、能力状态、证据和错误分类；避免增加数据库迁移。补全运行详情中完整节点输出及受控的结构化数据/证据台账展示。
3. 保持九个已发布 v1 文件只读。对需要默认文件产物的场景，以可审计新版本配置最终节点 ArtifactConfig，并测试 v1 旧 TaskRun/副本仍可按原定义校验。
4. 用 `tests/task_*` 中的合成资料和显式测试适配器覆盖九个场景的模板读取、实例化、节点限制、Preflight、失败关闭、用户隔离、证据和产物契约。副作用 Tool 不在测试中伪造生产成功。
5. 跑 Task 全量测试、Python 编译/静态检查、前端构建；只在当前 API 和登录会话恢复后执行浏览器端到端验收。
6. 生成九场景矩阵与最终总报告，逐项区分 PASS、DEMO PASS、BLOCKED、FAIL、NOT TESTED。

## 工作区保护记录

基线工作区含 P6-D-2F/G、P6-G-1 的 Task 修改，以及 Task 前端、销售台和多个未跟踪目录。它们均保留；本任务不提交、不推送、不使用 `git add .`。代码修改仍限于 Task 后端、Task 前端/API 和 `tests/task_*`；阶段记录及总报告写入 Task 文档目录。
