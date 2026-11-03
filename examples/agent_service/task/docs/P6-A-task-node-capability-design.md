# P6-A：Task 九场景节点能力分析与扩展设计

> 分析基线：`integrate/dev_l`，P5.1 基准提交 `fdc219312fdf4a0a4f1a04598e4ae6667422bb66`（`fdc2193`）。
> 本文仅分析和设计；本阶段未修改执行器、业务源码或模板数据。

## 1. 结论与边界

Task 目前将一份流程编译为有序 `TaskNode` 列表，再由 Task 适配层将节点交给 AgentScope 的官方 `SOPEngine` 顺序运行。节点种类只有 `agent`、`tool`、`python`、`input`，九个系统模板仅用前三者中的 `agent`、`tool`、`input`，节点数为 6–8。模板 `capability_key` 只是逻辑依赖名；它不能证明当前会话有真实 Tool、数据源或业务权限。

因此，九个场景的流程骨架可以由现有节点表达，业务闭环却普遍受真实 Tool/MCP、文件资产入口和业务协议缺失限制。当前 `InputStep` 能让运行暂停、收集自由文本并恢复同一 run，但不具备审批人的身份认证、授权、决定审计、回执关联或文件上传能力。不能把人工审批、外部回执、条件分支或并行伪装成输入节点、工具节点或提示词约定。

现阶段建议不新增 `Rule`、`Condition`、`Parallel`、`Subflow`、`Output` 节点类型。先用带明确契约的只读 Tool 完成业务规则/数据查询；将正式 `HumanApproval` 作为 S03 首个必要的通用扩展，但必须先解决 Task 路由可用的审批身份/权限来源和审计持久化；外部事件 `Wait` 单独排在后续。产物可沿用现有 Task artifact 能力，补齐模板配置与结构化输出约束即可。

本文的“没有支持/缺失”指当前 Task 模型、Task 适配层及 Task 文档没有定义相应通用协议；不否定运行会话可能动态提供某个业务 Tool。现有能力及模板声明不得被表述为已经接入具体业务系统。

## 2. 现有源码能力分析

### 2.1 节点模型与输入/输出

| 能力 | 当前实现 | 边界与源码依据 |
|---|---|---|
| 节点类型和配置 | `StepType` 为 `agent/tool/python/input`；`TaskNode.config` 按类型承载具体配置，并可设置 `order`、知识库、验收条件、`max_attempts`、最终 artifact 等。 | `examples/agent_service/task/_models.py:64-81, 196-246`。没有 `depends_on`、边、条件表达式、审批字段、Wait 事件字段或并行组。 |
| 节点交接 | TaskService 按顺序执行；本步输入主要是前一步文本输出，Task 适配器同时把历史、知识库和用户回答放入执行上下文。单步结果由 `StepExecutionResult` 承载文本、任意结构化 `result`、metadata、状态/问题。 | `examples/agent_service/task/_service.py:1095-1203`、`_executor.py:18-45`。结构化结果有运行时载体，但没有跨节点 schema 声明和强类型契约。 |
| 人工补充信息 | `TaskInputQuestion` 是 key/label/required/options；提交为 `dict[str,str]`，最多 16 项，每个回答最多 20,000 字符。Input step 暂停后由相同 run 接收关联 request id 的答案。 | `_models.py:104-165`、`_service.py:1003-1093`、`_sop.py:59-117`。前端 `task-input-form.tsx:10-89` 使用文本框和选项按钮，没有文件上传、审批决定或审批人身份字段。 |
| 失败/校验 | Task verifier 可依据节点验收条件形成 `StepVerdict`；SOP 对 verifier 未通过的步骤按 `max_attempts` 重试。执行器抛出的异常会由 TaskService 将 run 置为失败；`retry_run` 创建新 run，而非在原节点内通用重试。 | `_models.py:166-193, 246`、`_sop.py:59-150`、`_service.py:705-794, 626-680`；AgentScope `_engine.py:109-153`。不能把 `max_attempts` 描述成 Tool/网络异常重试保证。 |
| Python | Python step 通过选定 Workspace 的 Python 命令执行，有单次超时上限；这不是受限规则 DSL，也不是 Rule 引擎。 | `_models.py:213-225`、`_agentscope_executor.py:130-157, 662-724`。不建议将业务规则塞入任意 Python 代码配置。 |
| 最终产物 | `ArtifactConfig` 支持 Markdown、DOCX、XLSX；TaskService 可把最终节点结果交给 artifact writer 写入 Workspace 并记录 artifact 元数据。DOCX/XLSX 的结构规划在绑定会话时可调用模型，异常时回退到基于 Markdown 的确定性规格。 | `_models.py:73-99, 241`、`_service.py:1339-1410, 1528-1536`、`_artifact_spec.py:13-30, 183`。模板 `TemplateStep` 当前没有 artifact 字段，模板编译不传此配置；也没有最终 JSON 输出 schema 校验。 |

### 2.2 执行引擎与官方 AgentScope 依赖

`TaskSOPStep` 继承 AgentScope `SOPStepBase`，通过注入的 Task executor 执行普通节点，并把 Task 特定输出写入继承的 step state；Input 节点则使用官方 SOP 的 `AWAITING` phase 停放。见 `examples/agent_service/task/_sop.py:28-117`。官方接口约定子类实现单次 `reply_stream`，见 `src/agentscope/sop/_schema.py:65-102`。

`TaskService._build_sop_engine` 把节点列表转换成一组 `TaskSOPStep`，再构造官方 `SOP(steps=steps)` 和 `SOPEngine`（`_service.py:1095-1203`）。官方 `SOPEngine.reply_stream` 使用 `for index, step in enumerate(self.sop.steps)` 顺序遍历，并只将紧邻前一步的 submission 交给下一步；验证循环按步的 `max_attempts` 重试（`src/agentscope/sop/_engine.py:76-155`）。它不是 DAG 调度器，没有通用条件跳转、并行 fan-out/join 或子流程调用。Task 状态适配使用官方 `SOPStepRunState`/`SOPPhase`，见 `src/agentscope/sop/_state.py:23-115`。

本设计只考虑 Task 模块内通过 `TaskSOPStep`、Task 自有状态/API/store 做增量适配；不修改 `src/agentscope/**`，不覆写官方调度器，也不通过 monkey patch 注入能力。

### 2.3 服务、Planner、模板编译与校验

* `TaskService.create_task` 可直接保存给定节点；`generate_task` 才调用注入的 Planner（`_service.py:143-225`）。自由创建的 `POST /{task_id}/generate` 继续走 Planner；模板实例化在 `TaskTemplateService.instantiate` 中进行输入校验、能力绑定、节点编译后调用 `create_task`，不调用 Planner（`_template_service.py:72-146`；`_router.py:213-229, 330-354`）。
* AgentScope Planner 使用会话模型生成结构化 `TaskPlanDraft`，做一次修复尝试及节点校验；它负责自由任务规划，不应成为模板默认执行路径。见 `_agentscope_planner.py:75-180`、`_plan_validation.py:75-188`。
* 模板只允许四种现有节点类型，最多 8 步；固定 Registry 当前包含九个系统模板。`TemplateStep` schema 在 `_template_models.py:85-117, 189-236`，Registry 在 `_template_registry.py:76-171`。模板结构校验器维护系统模板必需步骤、相对顺序、配置及编辑策略，见 `_template_validation.py:60-119, 234-381`。
* Compiler 按 `capability_key` 在当前 Task 会话可用 Tool 中绑定实际 Tool schema，并拒绝不可读写策略不符合模板约束的工具；它把模板 kind 编译到已有 `TaskNode`，不添加 StepType。见 `_template_compiler.py:74-115, 182-313`。Capability key 不是 Tool 注册名，也不构成业务接通证明。

### 2.4 工具调用、权限与副作用

`AgentScopeTaskExecutor` 从所选会话的 Toolkit/MCP 列出实际可用工具（`_agentscope_executor.py:159-216`）。运行 Tool step 时按节点配置渲染参数、用 Tool schema 校验，并由 `PermissionEngine(session.state.permission_context)` 检查权限；拒绝权限、需要交互确认的外部 Tool、注入状态的 Tool 不会在后台 Task 中直接执行（`_agentscope_executor.py:582-660`）。

这是对当前 Tool 调用的会话权限检查，不是业务审批协议：没有审批人角色、审批决定/拒绝理由、一次性批准凭据、正式审计链或副作用幂等合同。模板只读约束也不能替代业务规则正确性。凡是通知发送、归档写入、ERP 提交等副作用，真实接口和授权闭环未验收前必须保持阻断。

### 2.5 持久化、恢复与用户隔离

创建 run 时，TaskService 将 Task 的节点定义、revision 和上下文复制到 `TaskRunRecord`，随后异步调度执行（`_service.py:489-625, 984-1001`）。每次 SOP 推进都会保存 `engine.state` 的 JSON 快照和 pending input（`_service.py:796-895, 1215-1337`）。Postgres/Redis store 保存模型 JSON 快照（`_postgres_store.py:142-180, 261-285`；`_redis_store.py:22-53, 117-163`）；store 查询以 `user_id` 为作用域（`_store.py:23-162`）。模板副本与系统模板由 Registry 定义分离，模板实例化产生新的 Task，不写回模板 JSON（`_template_service.py:72-146`）。

已发现的恢复边界：等待 Input 的 run 有持久化的 SOP state 和 request correlation，可由 `answer_run` 恢复同一 run；普通 queued/running 执行使用进程内 `asyncio.create_task`。TaskService/服务启动流程中未发现启动时扫描并重投 queued/running run 的 recovery worker（`_service.py:984-1001`；`examples/agent_service/main.py:828-868`）。这意味着当前检查点不是通用崩溃恢复/分布式队列保证。正式审批或外部事件恢复前必须设计原子状态变更及重放/幂等语义，不能仅依赖 pending input 快照。

### 2.6 前端流程编辑器

前端 Task API 类型以四种节点为联合类型，包含 Task/Run 和 pending input 数据；见 `examples/web_ui/frontend/src/api/task.ts:20-170`。`task-flow-editor.tsx` 展示节点类型、允许增加可选节点，限制最多 8 个；模板模式锁定受保护配置、禁用不可允许的重排/删除操作（`examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx:31-60, 119-318, 630-680`）。Input 表单只提交字符串回答（`task-input-form.tsx:10-89`）。UI 不提供审批角色授权、外部回执监听、条件边编辑、并行 join、嵌套 Task 或文件上传协议。

## 3. S01–S09 场景与节点能力映射

以下步骤名取自 `examples/agent_service/task/templates/s0*.v1.json`；各 JSON 中的 `kind` 是当前实际节点类型。能力缺口对照 `examples/agent_service/task/docs/task-template-capability-matrix.md`。`capability_key` 为逻辑键，不代表相应 Tool/MCP 已注册或已连接。

| 场景 | 当前逐步映射（步骤：节点类型） | 可复用部分及实际缺口 | 节点数/高风险 |
|---|---|---|---|
| **S01 项目立项** | `collect_project_input`：input；`extract_project_profile`：agent；`search_similar_projects`：tool；`check_project_rules`：tool；`draft_initiation_recommendation`：agent；`review_project_facts`：input；`produce_initiation_card`：agent。`s01_project_init.v1.json:27-108` | 可直接用 Input 收集资料、Agent 形成草稿、Tool 执行检索/规则调用。缺少已核验的相似项目库、立项规则及项目读取授权契约。输入复核不是审批。输出为建议卡/草稿；不写项目台账。 | **7，可在上限内**。项目规则可能影响正式立项；保持只读/建议，不自动立项。 |
| **S02 项目策划** | `collect_planning_inputs`：input；`retrieve_planning_template`：tool；`draft_planning_outline`：agent；`draft_planning_document`：agent；`verify_planning_citations`：tool；`review_planning_outline`：input（可选）；`produce_planning_output`：agent。`s02_project_planning.v1.json:27-108` | 结构可复用。缺少策划模板/检索和引用核验 Tool 契约。Input 复核不构成签字；现有 Artifact writer 可支持最终文件，但模板没有 artifact 配置。 | **7，可在上限内**。定位为草稿输出，不得宣称来源已核验。 |
| **S03 专家智能匹配** | `collect_matching_requirements`：input；`extract_project_profile`：agent（可选）；`lookup_expert_candidates`：tool；`check_expert_conflicts`：tool；`rank_expert_candidates`：tool；`explain_matching_reasons`：agent；`collect_human_review`：input；`produce_expert_shortlist`：agent。`s03_expert_matching.v1.json:28-121` | 可复用输入、画像提取、查询/冲突/评分 Tool 槽位、理由文本和候选清单生成。专家库、回避数据/规则、评分算法均无业务契约；敏感专家字段还需最小化授权。最后的 Input 只收集意见，不是审批。 | **8，已达上限**。推荐结果需要正式审核；审批能力应替换原 Input 槽位，不加第 9 节点。 |
| **S04 会前准备** | `collect_meeting_parameters`：input；`calculate_meeting_costs`：tool；`generate_meeting_materials`：tool；`check_meeting_materials`：tool；`collect_material_corrections`：input（可选）；`prepare_notification`：agent；`send_meeting_notification`：tool。`s04_meeting_preparation.v1.json:29-110` | 可复用草稿、校验 Tool 槽位和修订信息收集；缺费用、物料生成/检查及通知 Tool。正式审批、通知授权、幂等发送和外部回执协议缺失；发送节点在真实能力验收前必须阻断。 | **7，可在上限内**。通知是外部副作用；不能仅凭可用 Tool 或 Input 同意就发送。 |
| **S05 会议召开与纪要** | `collect_audio_asset`：input；`transcribe_meeting_audio`：tool；`segment_meeting_topics`：agent；`extract_meeting_actions`：agent；`review_minutes_draft`：input；`produce_meeting_minutes`：agent。`s05_meeting_minutes.v1.json:26-97` | 纪要草稿步骤可复用；缺受控音频资产引用/访问及 ASR Tool。Input 仅为复核意见，不是正式签发。 | **6，可在上限内**。若用于正式会议记录需签发/留痕规则；不能编造无 ASR 的转写。 |
| **S06 意见整理** | `collect_opinion_images`：input；`recognize_opinion_text`：tool；`extract_opinion_points`：agent；`classify_opinion_points`：tool；`summarize_opinion_dimensions`：agent；`review_opinion_summary`：input；`export_opinion_summary`：tool。`s06_opinion_processing.v1.json:26-109` | 文本整理/总结结构可复用；缺图片资产入口、OCR/手写识别、分类与导出 Tool 契约。Input 不是图片上传或正式审核。需记录识别置信度/原文证据，低置信不能让 Agent 猜测。 | **7，可在上限内**。导出前需明确数据脱敏和访问控制。 |
| **S07 报告生成** | `collect_report_materials`：input；`retrieve_report_evidence`：tool；`draft_report_chapters`：agent；`check_report_citations`：tool；`check_report_consistency`：tool；`check_report_compliance`：tool；`collect_report_review`：input；`produce_report_draft`：agent。`s07_report_generation.v1.json:29-120` | 可复用草稿流程；缺 RAG、引用、一致性、合规 Tool 和文件输出配置。复核 Input 不是正式签字；最终仍应标记草稿。 | **8，已达上限**。若增加正式签字，必须替换或重构现有步骤，不能加第 9 节点。 |
| **S08 归档移交** | `collect_archive_files`：input；`extract_archive_metadata`：tool；`classify_archive_materials`：agent；`check_archive_completeness`：tool；`prepare_transfer_manifest`：agent；`write_archive_system`：tool。`s08_archive_transfer.v1.json:27-94` | 可复用清单生成；缺文件资产入口、元数据/完整性工具及真实档案系统契约。写授权、幂等写入和接收回执都缺失；当前必须阻断写入。 | **6；审批+回执两个未来节点理论上共 8**，仍须有系统契约、授权和幂等语义。 |
| **S09 报销结算** | `collect_invoice_assets`：input；`recognize_invoice_fields`：tool；`verify_and_deduplicate_invoices`：tool；`check_expense_rules`：tool；`prepare_expense_draft`：agent；`submit_expense_to_erp`：tool。`s09_expense_settlement.v1.json:28-101` | 草稿编排可复用；缺发票资产、OCR/验真去重/费用规则 Tool 和 ERP 接口。审批、ERP 写授权、幂等提交、财务回执协议缺失；提交步骤当前必须阻断。 | **6；审批+回执两个未来节点理论上共 8**，正式提交前必须双重门控并确认幂等。 |

**跨场景输入缺口：** Task Input 当前只接受字符串，不接受受控文件引用。S05/S06/S08/S09，以及可能需要附件的 S01/S02/S07，须定义 Task 资产 ID、用户所有权、内容类型/大小限制、病毒扫描/存储位置和下载权限。不能接受任意本地路径或未经授权 URL 作为替代。

## 4. 七项通用节点能力缺口矩阵

| 能力 | 是否必需 / 影响场景 | 当前源码支持 | 任务模型 / SOP 适配改动 | AgentScope 主代码 | 建议优先级与官方 Task 替换性 |
|---|---|---|---|---|---|
| **Rule** | 业务规则确定性验证有必要，但不一定需要独立节点；关联 S01/S03/S04/S06/S07/S09。 | Tool step 可调用外部确定性规则工具；Task verifier 是执行验收，不是业务规则引擎。Python 不是安全的规则 DSL。 | 首选只定义只读、版本化、带证据的 Tool 契约，不加 StepType。若规则必须脱离外部 Tool，可在后续 Task 模块新增受限声明式 Rule config/结果类型及本地解释器，不能接收任意代码。 | 否。 | **P6-B 不作为新节点**；先接 Tool。将来若确有规则 DSL 需求为中优先级。契约独立、纯输入输出，易映射到官方新 Task API。 |
| **HumanApproval** | 正式决策必需：首个 S03；成熟业务还涉及 S04/S05/S07/S08/S09。 | 不支持。Input 只是自由文本收集；Tool 交互确认被后台执行拒绝。Task 端点当前身份为 `user_id`，没有审批角色/政策协议。 | 必须新增 Task 专属审批配置、pending/decision 状态、通过/拒绝/理由和审计事件；TaskSOPStep 以 AWAITING 停放并由受权 API 恢复同一 run。拒绝需成为明确终态，绝不能继续副作用节点。需 store 原子/幂等写入。审批 actor 从服务端认证上下文取得，不能信任请求体。 | 否。 | **最高优先级但有阻塞门槛**：先确定可从现有认证上下文/已批准的 Task 内政策得到真实审批人权限和审计保证。官方 Task 替换性高：能力集中在 Task 模型、路由、存储和 SOP adapter。 |
| **Condition** | 当前九场景不依赖分支才能形成安全直线流程；若要求拒绝后返工/分支，关联 S03/S04/S07/S08/S09。 | 不支持。Node 无边/条件；官方 SOPEngine 只按顺序遍历。 | 需引入控制流图/边、变量作用域、跳过状态、断点恢复与验证；仅扩展节点配置不够。可先把拒绝定义成终止结果。 | 否；但不能只写 Task 条件字段而由顺序引擎忽略。需 Task 私有调度器或官方等价能力。 | **后置**。若决策分支成为硬需求，再独立立项。图结构标准化后可转接未来官方 Task 图引擎。 |
| **Wait** | 外部回执等待涉及 S04/S08/S09。 | 只有人类 Input 的等待/恢复，不支持外部事件来源、回调关联或超时策略。 | 后续需要 Task Wait config/state、可信事件源和 Task 专属回调 API、事件签名/归属校验、correlation/idempotency、超时与取消、持久化恢复。 | 否。 | **中后优先级**，依赖各业务系统先给事件/回执契约。接口化后可适配官方异步等待原语。 |
| **Parallel** | 九场景当前线性模板可工作；没有明确首批必须并行的闭环。 | 不支持 fan-out/join、分支级状态或并发取消。 | 需要并行组/依赖模型、结果汇合规则、部分失败/重试、资源限制和恢复语义；不能由多个连续 Tool 节点模拟并行。 | 否。 | **低优先级/暂缓**，等待量化耗时/吞吐需求。适合未来官方 DAG/parallel 支持后替换。 |
| **Subflow** | 当前模板不要求运行时嵌套 SOP。 | 不支持子 run 的创建、状态传播、权限传递、取消或版本锁定。 | 若为模板复用，可在编译期展开为普通节点并受 8 节点限制；运行时子流程则需 Task 父子 run 模型及生命周期传播。 | 否。 | **低优先级/暂缓**；优先考虑编译期复用。未来可映射官方 Task/Subflow contract。 |
| **Output** | 结构化结果、可下载产物对 S01/S02/S03/S05/S06/S07 有用；独立节点不是必需。 | 已有最终节点 ArtifactConfig、Artifact writer 和 Markdown/DOCX/XLSX；每节点结果有 `Any` 载体。模板不能定义 artifact，API/UI 未做强 schema 输出验证。 | 首选在 Task 模板步骤上加可选 artifact/output schema 元数据；compiler 将 artifact 绑定到最终 TaskNode，TaskService 用受限 JSON Schema 校验最终 `result` 或输出对象，并给出可回归错误。可保持现有节点类型。 | 否。 | **中优先级**；小范围 Task 模型/编译器扩展，清晰映射到未来官方 artifact/output 契约。 |

### 推荐优先级

1. **P6-B-1：审批协议前置定义 + S03 HumanApproval Task adapter。** 仅在审批身份、决策、拒绝行为、审计存储契约有可验证来源后开发；否则先作为阻塞项，不做假审批 UI。
2. **P6-B-2：Output 模板配置与 schema 校验（若首个闭环验收确需结构化/文件产物）。** 复用现有 artifact writer，不新增 Output StepType。
3. **P6-B-3：接入经过确认的只读业务 Tool/MCP 契约**，优先 S03 专家检索、冲突、评分；这属于配置/业务连接器前置，不代表新增节点。
4. **后续独立阶段：Wait**，按外部事件合同推进。
5. **暂缓：Condition、Parallel、运行时 Subflow。** 除非业务验收明确需要，否则不增加通用调度复杂度。

## 5. Task 模块内的设计方案

### 5.1 设计约束

* 节点定义需显式区分 `kind`、配置、输入/输出约束与副作用标记；不能只靠 prompt/step title 表达控制流语义。
* 新增协议使用可向后兼容的可选字段/联合类型，并保证旧 Task/Run 快照可反序列化。已有四种节点的 JSON 和模板版本不能无故改格式。
* 运行时状态必须能 checkpoint/restore；审批决定、授权主体、关联 run/node/revision、决定时间和不可变证据摘要应作为 Task-owned audit 数据持久化，不能只放在 UI 或 Agent 文本中。
* 所有业务写入 Tool 仍需有明确的服务端权限与幂等合同。审批不是万能权限令牌；“已审批”也不能自动绕过 AgentScope PermissionEngine 的 Tool 保护。
* 在身份/审计政策和数据库原子操作未确定前，HumanApproval 为设计，不作为已支持能力。不要改共享认证代码或新增未授权的角色推断。

### 5.2 各类扩展落点

* `Rule`：先复用 `tool` 配置，声明业务 Tool 的版本、schema、证据结构和只读属性。若未来有本地 Rule DSL，独立校验并对未知字段/运算符 fail-closed；禁止 eval/Python 代码。
* `HumanApproval`：新增 Task-owned 类型和状态，不复用 `InputStepConfig`。通过 `SOPPhase.AWAITING` 暂停，Task API 在验证 run/node/request/revision 与服务端 approver policy 后提交决定；拒绝记录为业务终态并抑制后续执行。需处理同一决定重复提交、并发审批、run 取消和审批窗口过期。
* `Condition`：若有业务必需的返工循环，需显式等待官方可替换的控制流契约或单独实现 Task 调度器设计；当前顺序 SOP 中不能假装条件已生效。
* `Wait`：审批节点可以提供可信用户事件恢复，但不代表一般外部 Wait。外部 Wait 需要业务系统 callback/provider 及签名、owner、correlation、超时、去重和重放策略。
* `Output`：模板节点可配置受限 output schema 和最终 ArtifactConfig；通过 JSON Schema 或现有仓库验证规范强校验形状、大小和媒体类型。由 Task artifact writer 保存，不新增调度节点。

## 6. 与 AgentScope SOP 的兼容性

1. Task 继续用官方 `SOP` 表示一个已编译的线性步骤数组，`TaskSOPStep` 继续实现 `SOPStepBase`；所有新状态定义于 `examples/agent_service/task/**`。
2. HumanApproval 适合现有官方 `SOPPhase.AWAITING` 的暂停/恢复边界：Task step 在无决定时持久化专属 pending state，在 API 接收合法决定后恢复同一 official engine state。必须通过官方 state revalidation/构造路径恢复；不能私自改 AgentScope 类。
3. 官方引擎逐步顺序遍历，不支持条件跳跃、并行或通用外部事件。不要在 SOP 节点返回带有“分支”文字的自然语言来模拟这些机制。
4. `max_attempts` 目前属于 SOP step verification retry。新节点若有业务级拒绝/超时/重复投递策略，应定义独立状态和协议，避免改变现有 retry 含义。
5. 官方 Task 后续替换时，稳定对外协议应是 Task 的节点配置、输入输出 schema、run/node 状态、审批/等待事件、审计和权限语义；SOP adapter 仅是当前实现适配层。该边界便于将来把线性执行交还 AgentScope，而不改变业务 API。

## 7. 首个闭环：S03 专家智能匹配

### 7.1 八节点内的候选流程

| 顺序 | 步骤 | 当前/建议类型 | 职责与安全条件 |
|---:|---|---|---|
| 1 | `collect_matching_requirements` | 现有 `input` | 收集领域、地区、级别、时间、回避条件等结构化字段；字段定义、必填和敏感信息边界需由业务确认。 |
| 2 | `extract_project_profile`（可选） | 现有 `agent` | 仅从输入/获授权资料抽取画像；输出需保留来源，不补造事实。可选节点可删除以增加编辑空间。 |
| 3 | `lookup_expert_candidates` | 现有 `tool` | 调用真实专家目录；要求分页/数量上限、字段 allow-list、数据时间戳和来源。无真实服务时明确失败/预览，不生成假候选。 |
| 4 | `check_expert_conflicts` | 现有 `tool` | 对每位候选返回 `clear/conflict/unknown`、规则版本和证据摘要；`unknown` 必须阻断推荐或人工标注未核实。 |
| 5 | `rank_expert_candidates` | 现有 `tool` | 按经业务批准的确定性评分算法返回分项、权重/版本、输入特征和排序；不可由 Agent 临时编分。 |
| 6 | `explain_matching_reasons` | 现有 `agent` | 将候选证据、冲突状态和分数转为易读说明；每条理由引用来源字段，不得新增事实。 |
| 7 | `collect_human_review` 改为正式审批槽位 | **需新增 Task-only `HumanApproval`** | 需真实 reviewer policy；审批决定、理由、候选快照摘要和 reviewer server identity 入审计。拒绝是明确终态；当前阶段不支持“退回某步重跑”。 |
| 8 | `produce_expert_shortlist` | 现有 `agent` | 仅在批准后生成最终名单/说明；可用已有 artifact writer 生成文件，但模板需先允许配置 artifact/schema。不得在拒绝/未审批状态执行。 |

该方案保持 8 个槽位，不加第 9 节点；审批替换当前收集意见的 Input。若要求“退回补充后继续”，则需 Condition/循环或显式重新发起审批/Task run 的产品语义，不能偷偷在节点提示词中实现。当前模板 `unsupported_capabilities` 已列 `expert.business_approval`，模板 matrix 明确该场景为 demo/configurable。

### 7.2 真闭环前置条件与当前能力复用

| 闭环部分 | 可复用现有机制 | 必须补齐/阻塞项 |
|---|---|---|
| 项目画像输入 | InputStep 字符串字段；AgentStep 可提取文本。 | 项目字段 schema、受控文件/资料引用、来源证明、敏感字段最小化。 |
| 专家数据查询 | ToolStep、当前会话真实 Tool 枚举、Tool schema 参数校验和 PermissionEngine。 | 经批准的专家目录 API/MCP、分页与隐私字段合同、数据新鲜度、真实用户访问策略。当前 Task 代码和 matrix 未声明该业务合同。 |
| 回避规则 | ToolStep 调用规则服务。 | 规则权威源、规则版本/生效日、证据结构；无结果/接口错误/身份不完整时 fail-closed。 |
| 推荐评分 | ToolStep 调用确定性评分服务。 | 已批准的评分公式、权重、版本、解释字段与公平性审查；不能默认委托 LLM 打分。 |
| 推荐理由 | AgentStep 生成自然语言并保留上一步证据。 | Grounding 格式、候选字段引用、无证据理由拦截/检测；模型只能解释已有结果。 |
| 人工审核 | SOP `AWAITING` + Task 状态持久化可提供适配基础。 | 正式 HumanApproval 配置/API/store、可信 reviewer 权限/身份、审批日志、并发/重放防护、拒绝终态行为。当前 Task API 没有审批角色协议，故此为首要阻塞。 |
| 结果输出 | 末节点和现有 ArtifactConfig/writer。 | 模板 artifact/output schema 配置、结构化结果契约、文件访问授权。 |

在真实 API、权限合同和审批政策进入代码之前，S03 只能作为可预览的模板设计/模拟闭环，不应宣称已接专家库或形成真实审批闭环。

## 8. P6-B 逐文件开发计划（本阶段不实施）

### Gate 0：业务合同（先于代码）

产品/业务需提供：专家查询 API/MCP 和字段授权、冲突规则及可解释证据、评分算法及版本、审批角色来源/授权策略、拒绝语义与审计保留要求、文件资产引用方式、最终输出 schema。若审批角色不能从现有可信身份上下文获得，必须先有 Task 范围内且经批准的身份策略；本计划不以修改共享认证模块作为默认方案。任何外部业务接口缺失均列阻塞，不能使用假 Tool 填充。

### Gate 1：审批模型与同一 run 恢复

拟修改（仅 Task 后端）：

1. `examples/agent_service/task/_models.py`：定义 approval step config、pending request/decision/audit 记录和 run/node 状态；给旧快照兼容默认值。审批 actor 只保存从可信服务端上下文解析的身份。
2. `examples/agent_service/task/_sop.py`：扩展 Task 自有 `TaskStepState`/`TaskSOPStep`，在等待时使用官方 AWAITING phase；恢复时校验 request 与 run/node/revision 对应。
3. `examples/agent_service/task/_service.py`：实现权限预检、同一 run 的幂等审批恢复、拒绝终止并禁止下游执行、锁内状态转换和事件记录；不要更改通用 Tool permission 规则。
4. `examples/agent_service/task/_store.py`、`_redis_store.py`、`_postgres_store.py`：增加审批 pending/decision/audit 的持久化和原子幂等转换；检查 Redis/Postgres 双后端及旧数据反序列化。若无法保证事件与状态一致提交，先解决 store contract 再开放写节点。
5. `examples/agent_service/task/_router.py`：在现有 `get_current_user_id` Task 路由边界下增加审批 API；从服务端身份绑定 actor，禁止客户端传入 actor id/任意审批授权。通过权限策略的集成测试证明只有被授权主体可决定。
6. `examples/agent_service/task/_template_models.py`、`_template_compiler.py`、`_template_validation.py`：以向后兼容形式增加 approval kind/config、绑定和结构校验；锁定必需步骤身份/顺序/审批配置，不改变既有四种 kind 语义。
7. `examples/agent_service/task/templates/s03_expert_matching.v1.json`：待 Gate 0 批准后，将第 7 步从 input 改成 approval。先明确 template version/schema version 与已有副本兼容政策；禁止无说明地改动模板数据格式。

拟修改（Task 前端）：

8. `examples/web_ui/frontend/src/api/task.ts`：增加 approval request/decision/status 类型及 Task 专属 API 调用，不修改 `api/client.ts` 或 `api/index.ts`。
9. `examples/web_ui/frontend/src/pages/task/task-flow-editor.tsx`、`task-run-panel.tsx`（如需）和一个独立审批面板文件：区分“补充信息”和“审批待处理”，展示真实服务端返回的候选/证据摘要、批准/拒绝/理由；页面不自行判断权限，不用模拟数据表示已审批。

需补测试文件（位置遵循现有 Task 专项测试规范）：

10. 新增 `tests/task_approval_test.py`：成功批准/拒绝、未授权 actor、伪造 actor、重复/并发决定、run/node/revision 不匹配、拒绝不执行第 8 节点、pending state 重启后恢复、旧 Task/Run 快照兼容。
11. 扩充现有 `tests/task_template_test.py`、`tests/task_sop_test.py`、`tests/task_postgres_sop_test.py`：S03 仍为 8 步、审批替代 Input、审批字段不可由模板副本非法覆盖、两种持久化后端原子性、Tool 缺失时无伪造可运行任务、模板隔离和用户隔离。

**若 Gate 0 缺少可信审批身份/审计 store 能力，以上审批文件不应进入实施；先交付阻塞报告和接口决策。** 本计划不允许修改 `src/agentscope/**`、非 Task 后端、共享 API client、导航、`main.py`、认证机制或构建配置。若实现确需上述边界外改动，应停止并提交范围变更评审，而不是自行越界。

### Gate 2：输出及真实工具

审批最小闭环通过后，按业务验收需要再扩展 `_template_models.py`、`_template_compiler.py`、`_template_validation.py`、`_models.py` 为末节点 ArtifactConfig/output schema，并更新 Task API/page 类型和表单；复用现有 Task artifact writer。把 S03 的查询/冲突/评分接到 Gate 0 批准的真实 Tool/MCP，添加契约测试/权限测试。不要新增 Rule 或 Output 调度节点。

Wait 需等业务提供可信回执 provider 后另开阶段；Condition/Parallel/运行时 Subflow 不进入首个 P6-B。

## 9. 风险、阻塞项与验收标准

### 风险与阻塞

1. **审批身份无合同（首要阻塞）：** 当前 Task endpoint 以 `user_id` 作为所有者身份，但没有 reviewer role/policy。没有可信授权来源时，S03 无法满足正式审批要求。
2. **业务数据源未声明接通：** capability matrix 将专家库、回避规则、评分算法标为待核验。ToolStep 结构不等于真实业务 Tool 存在。
3. **Input 不是资产/审批/回执：** 字符串问答不能安全代替音频、图片、归档文件、发票上传或审核签名。
4. **执行恢复不是任务队列：** awaiting input 可恢复，queued/running 尚无发现的启动恢复 worker。审批/Wait 的持久化必须覆盖断进程与重复事件场景。
5. **官方 SOP 控制流线性：** 分支、并行、子流程无安全扩展点仅靠节点配置实现；任何此类支持都需单独调度模型设计。
6. **副作用安全：** S04/S08/S09 只有在业务授权、幂等、失败对账/回执机制具备后才能开放。

### 后续 P6-B 验收标准

* 仍不修改 `src/agentscope/**`、非 Task 后端或共享前端/认证/导航/构建配置；仅在批准的 Task 白名单文件中变更。
* S03 模板不超过 8 节点；正式审批是专用节点/API/state，拒绝或未审批不得进入最终名单/写操作。
* 只有服务端可认证且被授权的 reviewer 能审批；请求体伪造 actor、越权、跨用户、跨 run、旧 revision、重复或并发决定均拒绝或返回幂等既有决定；全程可审计。
* 等待/恢复在 Redis 和 Postgres 后端均可靠；旧 Task/Run 数据可读取；测试覆盖服务重启、重复投递和下游抑制。
* 专家 Tool 返回来源、规则版本、冲突状态和评分证据；`unknown` fail-closed。Tool 缺失或接口失败不会产出假候选/“已审批”结果。
* 自由任务仍使用原 Planner；模板实例化不调用 Planner；现有四种节点和九个模板维持兼容；8 节点上限、必需节点/顺序校验继续生效。
* 若增加文件资产/output schema：跨用户资产读取被拒绝，输出契约有正/负测试，artifact 仍走既有 Task writer；前端明确区分草稿和批准结果。
* Task 专项后端测试、必要的 Redis/Postgres 集成测试、前端类型检查/build 和关键浏览器交互均通过；验收报告逐项记录命令和结果，不能用 mocked Tool 测试声称业务系统已连通。

## 10. 本阶段交付状态

本文件记录的是源码分析与后续设计，不包含新节点实现、执行器变更、模板变更或测试结果宣称。P6-A 至此结束；P6-B 未开始。
