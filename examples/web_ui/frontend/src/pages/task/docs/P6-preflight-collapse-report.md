# Task 运行前能力检查折叠面板修改说明

## 修改内容

修改文件：

- `src/pages/task/index.tsx`
- `src/pages/task/docs/P6-preflight-collapse-report.md`

`CapabilityPreflightPanel` 使用项目已有的 Radix `Collapsible` 封装。标题行是原生键盘可操作按钮，折叠状态由组件维护，按钮状态由 Radix 提供 `aria-expanded`；箭头随展开状态旋转。展开详情仍在正常文档流中，不覆盖节点和执行轨迹区域。

## 界面行为

- 尚未检查：显示简短提示，不展示旧结果。
- 检查中：显示旋转图标和“运行前能力检查中...”。
- 检查通过：默认收起；摘要统计 `status === available` 的实际检查项。没有检查项时显示“当前无能力检查项”。
- 检查未通过：默认展开；摘要以红色标记失败，异常数按非 `available` 的检查项计算；若没有异常检查项，则按服务端 `issues` 数量显示。
- 展开详情保留原有能力、工具、接口兼容、权限、真实数据源、消息和问题列表。现在会完整显示所有 `issues`，不再截断到前八条。
- 普通渲染不会重置手动展开状态；任务 ID、Preflight 新结果版本或检查状态变化时会重新应用默认状态。切换 Task 会清空旧结果并使旧请求失效，避免迟到响应覆盖当前任务。

改动位置：面板与状态摘要约在 `src/pages/task/index.tsx:148`；检查状态和结果版本约在 `:314`；Preflight 请求处理约在 `:784`；页面挂载面板约在 `:1076`。

## 验证

- `pnpm build`：通过，包含 TypeScript 项目构建和 Vite 生产构建。
- `pnpm exec eslint src/pages/task/index.tsx`：通过。
- 前端 `package.json` 没有测试脚本，前端目录也没有现有测试/规格文件，因此没有可运行的 Task 前端专项测试。
- 浏览器打开 `http://localhost:8000/task`：页面报告“无法连接服务器”，没有可选 Task 或 Preflight 结果，无法在浏览器中验证成功/失败结果的展开、收起和任务切换。该限制未通过模拟结果掩盖。

## 边界检查

本次只修改 Task 前端目录中的页面文件和本说明。未修改 `src/agentscope/**`、后端、共享 API、认证、导航、依赖或构建配置；`examples/web_ui/frontend/src/api/task.ts` 未修改。工作区已有的 P6-D-2F、销售台和其他未提交文件均予以保留，未暂存或提交任何文件。
