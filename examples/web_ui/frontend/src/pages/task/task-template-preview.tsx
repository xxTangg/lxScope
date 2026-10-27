import { Check, ChevronDown, ChevronUp, LockKeyhole, WandSparkles } from 'lucide-react';
import * as React from 'react';

import { getTaskCapabilityDisplayName } from './task-template-labels';
import type { TaskTemplateDefinition, TaskToolSchema } from '@/api/task';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

const STEP_KIND_LABELS: Record<TaskTemplateDefinition['steps'][number]['kind'], string> = {
	agent: 'AgentStep',
	tool: 'ToolStep',
	python: 'CodeStep',
	input: '用户输入',
};

const ARTIFACT_FORMAT_LABELS = {
	markdown: 'Markdown',
	docx: 'DOCX',
	xlsx: 'XLSX',
} as const;

const TOOL_NAME_LABELS: Record<string, string> = {
	TeamDelete: '删除团队',
	'mcp__amap-maps__maps_regeocode': '地址反查',
	'mcp__amap-maps__maps_geo': '地址转坐标',
	'mcp__amap-maps__maps_ip_location': 'IP 定位',
	'mcp__amap-maps__maps_weather': '天气查询',
	'mcp__amap-maps__maps_search_detail': '地点详情',
	'mcp__amap-maps__maps_bicycling': '骑行路线',
	'mcp__amap-maps__maps_direction_walking': '步行路线',
	'mcp__amap-maps__maps_direction_driving': '驾车路线',
	'mcp__amap-maps__maps_direction_transit_integrated': '公交路线',
	'mcp__amap-maps__maps_distance': '距离测算',
	'mcp__amap-maps__maps_text_search': '地点搜索',
	'mcp__amap-maps__maps_around_search': '周边搜索',
	'mcp__markitdown__convert_to_markdown': '文档转 Markdown',
	'mcp__browser-use__browser_close': '关闭浏览器',
	'mcp__browser-use__browser_resize': '调整窗口',
	'mcp__browser-use__browser_console_messages': '查看控制台消息',
	'mcp__browser-use__browser_handle_dialog': '处理网页弹窗',
	'mcp__browser-use__browser_evaluate': '网页交互',
	'mcp__browser-use__browser_file_upload': '上传文件',
	'mcp__browser-use__browser_drop': '拖放文件',
	'mcp__browser-use__browser_find': '查找页面内容',
	'mcp__browser-use__browser_fill_form': '填写表单',
	'mcp__browser-use__browser_press_key': '按键操作',
	'mcp__browser-use__browser_type': '输入文字',
	'mcp__browser-use__browser_navigate': '访问网页',
	'mcp__browser-use__browser_navigate_back': '返回上一页',
	'mcp__browser-use__browser_network_requests': '查看网络请求',
	'mcp__browser-use__browser_network_request': '查看网络请求',
};

const MAX_VISIBLE_OPTIONAL_TOOLS = 8;

function getToolDisplayName(toolName: string): string {
	if (TOOL_NAME_LABELS[toolName]) return TOOL_NAME_LABELS[toolName];
	return toolName
		.replace(/^mcp__[^_]+__/, '')
		.replace(/^maps_/, '')
		.replaceAll('_', ' ');
}

export function TaskTemplatePreview({
	template,
	tools,
	isToolsLoading,
	onUse,
}: {
	template: TaskTemplateDefinition;
	tools: TaskToolSchema[];
	isToolsLoading: boolean;
	onUse: (template: TaskTemplateDefinition) => void;
}) {
	const [selectedToolNames, setSelectedToolNames] = React.useState<string[]>([]);
	const [showAllOptionalTools, setShowAllOptionalTools] = React.useState(false);
	React.useEffect(() => {
		setSelectedToolNames([]);
		setShowAllOptionalTools(false);
	}, [template.template_id, template.template_version]);

	const requiredCapabilities = [...new Set(template.required_capabilities ?? [])];
	const optionalTools = tools.filter(
		(tool, index) => tools.findIndex((candidate) => candidate.name === tool.name) === index,
	);
	const visibleOptionalTools = showAllOptionalTools
		? optionalTools
		: optionalTools.slice(0, MAX_VISIBLE_OPTIONAL_TOOLS);
	const selectedOptionalCount = optionalTools.filter((tool) =>
		selectedToolNames.includes(tool.name),
	).length;
	const toggleTool = (toolName: string) => {
		setSelectedToolNames((current) =>
			current.includes(toolName)
				? current.filter((name) => name !== toolName)
				: [...current, toolName],
		);
	};

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
				<div>
					<div className="flex flex-wrap items-center gap-2">
						<h2 className="text-lg font-semibold text-slate-900">{template.title}</h2>
						<Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
							{template.steps.length} 个逻辑节点
						</Badge>
						<Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
							待校验会话工具
						</Badge>
					</div>
					<p className="mt-2 whitespace-pre-line text-sm leading-6 text-slate-600">
						{template.description}
					</p>
				</div>

				<section className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
					<div className="flex flex-wrap items-center justify-between gap-2">
						<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
							<WandSparkles className="size-3.5 text-blue-600" />
							AI 能力配置
						</div>
						<span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600">
							已选择 {selectedOptionalCount} 项可选工具
						</span>
					</div>
					<div className="mt-2.5 flex flex-wrap items-center gap-1.5">
						{requiredCapabilities.map((capability) => (
							<span
								key={capability}
								aria-label={`${getTaskCapabilityDisplayName(capability)}，必需且已选中`}
								className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-1 text-[10px] font-medium text-blue-800"
							>
								<LockKeyhole className="size-3" aria-hidden="true" />
								{getTaskCapabilityDisplayName(capability)}
							</span>
						))}
						{visibleOptionalTools.map((tool) => {
							const checked = selectedToolNames.includes(tool.name);
							const displayName = getToolDisplayName(tool.name);
							return (
								<label
									key={tool.name}
									className={`inline-flex max-w-full cursor-pointer items-center break-all rounded-full border px-2 py-1 text-[10px] font-medium transition-colors ${checked ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600 hover:border-blue-200 hover:text-blue-700'}`}
								>
									<input
										type="checkbox"
										checked={checked}
									aria-label={`选择可选工具 ${displayName}`}
										onChange={() => toggleTool(tool.name)}
										className="sr-only peer"
									/>
									<span className="mr-1 hidden peer-checked:inline-flex" aria-hidden="true">
										<Check className="size-3" />
									</span>
									<span className="peer-focus-visible:rounded-sm peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-blue-500">
										{displayName}
									</span>
								</label>
							);
						})}
						{optionalTools.length > MAX_VISIBLE_OPTIONAL_TOOLS && (
							<button
								type="button"
								className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-white px-2 py-1 text-[10px] font-medium text-blue-700 hover:bg-blue-50"
								onClick={() => setShowAllOptionalTools((current) => !current)}
							>
								{showAllOptionalTools ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
								{showAllOptionalTools ? '收起' : `查看更多 ${optionalTools.length - MAX_VISIBLE_OPTIONAL_TOOLS} 项`}
							</button>
						)}
						{isToolsLoading && <span className="text-[10px] text-slate-400">正在读取工具目录…</span>}
						{!isToolsLoading && optionalTools.length === 0 && (
							<span className="text-[10px] text-slate-400">当前会话暂无可选工具</span>
						)}
					</div>
					<p className="mt-2 text-[10px] leading-4 text-slate-400">可选勾选仅用于前端展示，不会改变模板实例或真实执行配置。</p>
				</section>

			<section>
				<div className="mb-2 text-xs font-semibold text-slate-800">流程预览</div>
				<ol className="space-y-2">
					{template.steps.map((step, index) => (
						<li
							key={step.step_key}
							className="rounded-xl border border-slate-200 bg-white p-3"
						>
							<div className="flex items-start gap-3">
								<div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-slate-600">
									{index + 1}
								</div>
								<div className="min-w-0 flex-1">
									<div className="flex flex-wrap items-center gap-1.5">
										<span className="text-xs font-semibold text-slate-800">
											{step.title}
										</span>
										<Badge
											variant="outline"
											className={
												step.required
													? 'border-blue-200 bg-blue-50 text-blue-700'
													: 'border-slate-200 bg-slate-50 text-slate-600'
											}
										>
											{step.required ? (
												<LockKeyhole className="mr-1 size-3" />
											) : (
												<Check className="mr-1 size-3" />
											)}
											{step.required ? '必需' : '可选'}
										</Badge>
										<Badge variant="outline" className="text-[10px]">
											{STEP_KIND_LABELS[step.kind]}
										</Badge>
										{step.editable ? (
											<Badge variant="outline" className="text-[10px]">
												可编辑内容
											</Badge>
										) : (
											<Badge variant="outline" className="text-[10px]">
												模板锁定
											</Badge>
										)}
										{step.capability_key && (
											<Badge
												variant="outline"
												className="font-mono text-[10px]"
											>
												{step.capability_key}
											</Badge>
										)}
										{step.artifact && (
											<Badge variant="outline" className="text-[10px]">
												文件输出：
												{ARTIFACT_FORMAT_LABELS[step.artifact.format]}
												{step.artifact.filename
													? ` · ${step.artifact.filename}`
													: ''}
											</Badge>
										)}
										{step.output_schema && (
											<Badge variant="outline" className="text-[10px]">
												结构化输出校验
											</Badge>
										)}
									</div>
									<p className="mt-1 text-[11px] leading-5 text-slate-500">
										{step.output_requirement}
									</p>
								</div>
							</div>
						</li>
					))}
				</ol>
			</section>

			<div className="rounded-xl border border-slate-200 bg-white p-3">
				<div className="text-xs font-semibold text-slate-800">最终输出</div>
				<p className="mt-1 text-[11px] leading-5 text-slate-600">
					{template.output_requirement}
				</p>
			</div>
			</div>

			<div className="flex shrink-0 justify-end border-t border-slate-100 bg-white px-1 py-3">
				<Button type="button" className="min-w-32" onClick={() => onUse(template)}>
					使用此模板
				</Button>
			</div>
		</div>
	);
}
