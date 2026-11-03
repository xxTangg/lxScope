# P7-B 九场景统一演示数据

> **全部内容均为 `DEMO/SYNTHETIC` 虚构演示资料。** 项目、单位、人员、专家、票据、会议和费用规则都不对应真实主体；不得用于正式立项、专家聘任、报销、签发、档案入库或其他生产决策。

> **运行状态说明（2026-10-12）：** 本目录内较早的 P7-A-R 检查结果记录了当时自动化环境的 401 状态，不代表当前用户浏览器。用户已报告 S01 与 S04 完成演示运行；S02 项目来源分类和 S03 专业字段解析已在当前 Task 工作区修复，仍需重载后端并由用户重新运行确认。完整字段填写、JSON 粘贴方式和 S02/S03/S07 复验步骤见 `../P7-B-nine-scenarios-operation-guide.md`。

## 数据包

- `manifest.json`：项目统一背景、跨场景 ID、数据性质和文件清单。
- `inputs/`：按当前系统模板版本及输入协议准备的实例化请求体；只包含模板允许的输入字段。
- `knowledge-sources/`：供用户上传到其有权访问的**演示/测试知识库**的 Markdown 源文件。文件中的 `demo_source_id` 只是本演示包内部的标记；上传、索引后必须以 Task API 实际返回的知识库 ID、文档 ID、chunk、哈希和来源名为准。

## 统一项目背景

- 项目 ID：`DEMO-LXSCOPE-2025-001`
- 项目名称：`[DEMO/SYNTHETIC] 澄湖科创园综合能源与设备台账优化咨询项目`
- 项目单位：虚构的“澄湖科创园建设协调组”
- 会议 ID：`DEMO-MTG-2025-001`
- 会议时间：`2025-11-06 09:00`（虚构的历史演练日期）
- 参与规模：8 人；专家候选 ID 为 `DEMO-EXPERT-001` 至 `DEMO-EXPERT-003`，仅为用户输入的虚构候选记录。
- 演示立项边界：建筑面积 42,000 m²、设备台账 286 项、2024 年用电基线 2,160,000 kWh；预算上限 480,000 元、周期 5 个月、节能目标建议值 8%。这些数字仅存在于虚构项目资料，不代表已审计或已核实的真实事实。
- S04 费率：规则 `DEMO-FEE-RULE-2025-01`，仅用于演示计算，不是权威标准。8 人、四项费率的预期合计为 3,560.00 元（场地 800 + 餐饮 1,440 + 材料 360 + 交通 960）。

## 九场景输入映射

| 场景 | 模板版本 | 输入文件 | 主要数据 |
|---|---|---|---|
| S01 项目立项 | `s01_project_init` v2.0.0 | `inputs/s01-project-init.v2.json` | 项目简报；运行时需用户选择有权访问的演示 KB |
| S02 项目策划 | `s02_project_planning` v2.0.0 | `inputs/s02-project-planning.v2.json` | 项目画像、专业领域；源资料 ID 留空以便在所选授权 KB 内检索 |
| S03 专家智能匹配 | `s03_expert_matching` v3.0.0 | `inputs/s03-expert-matching.v3.json` | 三条虚构专家候选记录及用户声明的回避状态 |
| S04 会前准备 | `s04_meeting_preparation` v3.0.0 | `inputs/s04-meeting-preparation.v3.json` | 同一会议 ID/日期/人数及演示费率 |
| S05 会议纪要 | `s05_meeting_minutes` v3.0.0 | `inputs/s05-meeting-minutes.v3.json` | 与 S04 同一会议的已转写虚构文本；Task 不执行 ASR |
| S06 意见整理 | `s06_opinion_processing` v3.0.0 | `inputs/s06-opinion-processing.v3.json` | 虚构的已识别意见和显式分类关键词；Task 不执行 OCR |
| S07 报告生成 | `s07_report_generation` v3.0.0 | `inputs/s07-report-generation.v3.json` | 章节和规则；按当前 Schema 必填的源资料 ID 是明确占位符，必须替换为服务端返回的授权文档 ID 后才能实例化 |
| S08 归档移交 | `s08_archive_transfer` v3.0.0 | `inputs/s08-archive-transfer.v3.json` | 与项目相关的虚构文件元数据；不读取实际文件或写入档案系统 |
| S09 报销结算 | `s09_expense_settlement` v3.0.0 | `inputs/s09-expense-settlement.v3.json` | 两条虚构结构化票据和演示限额；不验真、不提交 ERP |

## 知识库文件

建议只在本地开发/演示实例中由有权限的用户新建或选择一个明确标记为演示用途的知识库，然后上传 `knowledge-sources/` 下的文件并等待索引状态变为可检索。早期报告中的未认证 401 是当时检查环境的快照；本轮没有重新查询用户浏览器会话或知识库，也没有上传、替换或重建索引。若当前用户的演示知识库已包含所需文件且状态为可检索，无需为本轮代码修复重新上传。

- `DEMO-SOURCE-001-project-charter.md`：虚构项目委托/立项背景及数字边界，供 S01/S02/S07 使用。
- `DEMO-SOURCE-002-planning-template.md`：演示策划模板章节和编写提示；不是正式制度。
- `DEMO-SOURCE-003-demo-review-rules.md`：演示核对清单；明确不是法律、标准或正式审批规则。
- `DEMO-SOURCE-004-report-reference.md`：虚构参考材料及限制，供 S07 演示检索；不是已验证的历史案例。

上传后不要把本文件里的 `demo_source_id` 当成数据库 `document_id`。在当前用户有权访问的 Task 知识库选择器中选中已索引知识库；若要用 `source_material_ids` 限定文件，必须填写服务端返回的真实文档 ID。S01 输入协议不要求 source ID；S02 的空 source ID 表示在 Task 所选、当前用户已授权的知识库范围检索，不扩大权限。S07 输入文件中的 `__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__` 只是提示占位符，未经替换绝不能提交或实例化。

## 在应用中使用

1. 用目标演示用户登录本地开发应用，并确认当前页面属于开发/演示环境，不要连接生产环境。
2. 在知识库模块中打开或创建明确标记为 Demo/Test 的知识库；上传上列四个 Markdown 文件并等待解析/索引成功。知识库 ID、文档 ID 必须从 UI/API 实际响应获取，不能复制虚构 ID。
3. 在 Task 模块中选择系统模板，使用相应 JSON 文件的 `inputs` 填写模板参数。实例化请求格式为 `{"template_version":"...","inputs":{...}}`，不要把演示说明字段作为额外模板输入；S07 需先将占位符替换为该用户可访问的真实演示文档 ID。
4. 模板实例化创建的是 Task 副本，不会启动运行。开始运行后，如果模板的 `InputStep` 再次询问同一业务输入，应将对应 JSON 中的值按 UI 提示重新填入运行输入；这不是绕过或改写 Task 输入协议。
5. **S01**：在 Task 的知识库选择器中选择第 2 步的授权演示 KB；执行 Preflight，通过后运行。核对两个 RAG 节点的实际状态、返回证据中的 KB/文档/片段/哈希和 Agent 输出的来源引用。无 KB、无授权、未索引或无匹配结果时应保留真实失败状态。
6. **S04**：选择有效 Agent/Session 和当前用户 Workspace；Preflight 通过后运行。核对费用节点为 `calculated_from_user_supplied_rates`，金额合计 3,560.00 CNY，最终 DOCX 确实写入 Workspace，产物列表及授权下载接口返回同一 TaskRun 关联的文件。
7. 所有输出继续标注为演示草稿。不得把专家初排表述为正式回避审查/聘任决定，也不得把 S04 通知标记为已发送。

如果界面未提供合法会话、Agent/Session、演示 KB 或 Workspace，不要手工伪造请求身份字段或复用其他用户的 KB ID；按本阶段报告“需用户手动操作”部分处理。
