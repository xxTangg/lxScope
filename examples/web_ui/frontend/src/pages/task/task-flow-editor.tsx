import {
	ArrowDown,
	ArrowUp,
	Bot,
	Code2,
	GripVertical,
	Plus,
	Trash2,
	Wrench,
	MessageSquareText,
} from 'lucide-react';
import * as React from 'react';

import { TaskInputStepEditor } from './task-input-step-editor';
import type { TaskArtifactFormat, TaskNode, TaskStepType, TaskToolSchema } from '@/api';
import type { TaskTemplateDefinition, TaskTemplateStep } from '@/api/task';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

interface TaskFlowEditorProps {
	nodes: TaskNode[];
	onChange: (nodes: TaskNode[]) => void;
	tools?: TaskToolSchema[];
	disabled?: boolean;
	templateMode?: boolean;
	template?: TaskTemplateDefinition | null;
	templateStepBindings?: Record<string, string>;
}

const STEP_LABELS: Record<TaskStepType, string> = {
	agent: 'AgentStep',
	tool: 'ToolStep',
	python: 'CodeStep',
	input: '用户输入',
};

function normalizeNode(node: TaskNode, index: number): TaskNode {
	const config = node.config;
	if (node.type === 'input' && config?.type === 'input')
		return { ...node, order: index, prompt: '', config };
	if (node.type === 'tool' && config?.type === 'tool') {
		return { ...node, order: index, prompt: '', config };
	}
	if (node.type === 'python' && config?.type === 'python') {
		return { ...node, order: index, prompt: '', config };
	}
	const prompt = config?.type === 'agent' ? config.prompt : node.prompt;
	return {
		...node,
		order: index,
		type: 'agent',
		prompt,
		config: {
			...(config?.type === 'agent' ? config : {}),
			type: 'agent',
			prompt,
		},
	};
}

function normalizeNodes(nodes: TaskNode[]): TaskNode[] {
	const normalized = nodes.map(normalizeNode);
	return normalized.map((node, index) => ({
		...node,
		artifact: index === normalized.length - 1 ? (node.artifact ?? null) : null,
	}));
}

function stepIcon(type: TaskStepType) {
	if (type === 'input') return <MessageSquareText className="size-4 text-amber-600" />;
	if (type === 'tool') return <Wrench className="size-4 text-amber-600" />;
	if (type === 'python') return <Code2 className="size-4 text-violet-600" />;
	return <Bot className="size-4 text-primary" />;
}

function templateRuntimePrompt(step: TaskTemplateStep): string {
	return [
		step.config.type === 'agent' ? step.config.prompt : '',
		`输出要求：${step.output_requirement}`,
		`失败处理说明：${step.failure_handling}`,
		`敏感信息要求：${step.sensitive_information}`,
	]
		.filter(Boolean)
		.join('\n\n');
}

function restoreOptionalStep(step: TaskTemplateStep, id: string): TaskNode | null {
	const prompt = templateRuntimePrompt(step);
	if (step.config.type === 'agent') {
		return {
			id,
			name: step.title,
			prompt,
			type: 'agent',
			config: { type: 'agent', prompt },
			order: 0,
			acceptance_criteria: step.output_requirement,
		};
	}
	if (step.config.type === 'input') {
		return {
			id,
			name: step.title,
			prompt,
			type: 'input',
			config: {
				type: 'input',
				reason: step.config.reason,
				questions: step.config.questions.map((question) => ({ ...question, options: [...question.options] })),
			},
			order: 0,
			acceptance_criteria: step.output_requirement,
		};
	}
	return null;
}

export function TaskFlowEditor({
	nodes,
	onChange,
	tools = [],
	disabled = false,
	templateMode = false,
	template = null,
	templateStepBindings = {},
}: TaskFlowEditorProps) {
	const [argumentDrafts, setArgumentDrafts] = React.useState<Record<string, string>>({});
	const bindingByNodeId = React.useMemo(
		() => new Map(Object.entries(templateStepBindings).map(([stepKey, nodeId]) => [nodeId, stepKey])),
		[templateStepBindings],
	);
	const nextOptionalStep = template?.steps.find(
		(step) =>
			!step.required &&
			!nodes.some((node) => node.id === templateStepBindings[step.step_key]) &&
			Boolean(restoreOptionalStep(step, templateStepBindings[step.step_key] ?? '')),
	) ?? null;
	const normalizeEditedNodes = (items: TaskNode[]) =>
		templateMode
			? items.map((node, index) => ({ ...node, order: index }))
			: normalizeNodes(items);

	const updateNode = (index: number, update: Partial<TaskNode>) => {
		const next = nodes.map((node, nodeIndex) =>
			nodeIndex === index ? { ...node, ...update } : node,
		);
		onChange(normalizeEditedNodes(next));
	};

	const updateStepType = (index: number, type: TaskStepType) => {
		const node = nodes[index];
		if (!node) return;
		const config =
			type === 'agent'
				? {
						type: 'agent' as const,
						prompt:
							node.config?.type === 'agent'
								? node.config.prompt
								: node.prompt || '请基于上一节点的输出继续完成任务。',
					}
				: type === 'tool'
					? {
							type: 'tool' as const,
							tool_name: node.config?.type === 'tool' ? node.config.tool_name : '',
							arguments: node.config?.type === 'tool' ? node.config.arguments : {},
						}
					: type === 'input'
						? {
								type: 'input' as const,
								reason: '请补充本步骤需要的信息。',
								questions: [
									{
										key: 'details',
										label: '请提供所需信息',
										required: true,
										options: [],
									},
								],
							}
						: {
								type: 'python' as const,
								code: node.config?.type === 'python' ? node.config.code : '',
								timeout_seconds:
									node.config?.type === 'python'
										? node.config.timeout_seconds
										: 30,
							};
		updateNode(index, {
			type,
			prompt: type === 'agent' ? config.prompt : '',
			config,
		});
	};

	const moveNode = (index: number, offset: number) => {
		if (templateMode) return;
		const target = index + offset;
		if (target < 0 || target >= nodes.length) return;
		const next = [...nodes];
		[next[index], next[target]] = [next[target], next[index]];
		onChange(normalizeNodes(next));
	};

	const removeNode = (index: number) => {
		if (templateMode) {
			const node = nodes[index];
			const stepKey = node ? bindingByNodeId.get(node.id) : undefined;
			const step = template?.steps.find((item) => item.step_key === stepKey);
			if (!step || step.required) return;
		}
		if (nodes.length <= 1) return;
		onChange(normalizeEditedNodes(nodes.filter((_, nodeIndex) => nodeIndex !== index)));
	};

	const addNode = () => {
		if (nodes.length >= 8) return;
		onChange(
			normalizeNodes([
				...nodes,
				{
					id: 'node-' + Date.now(),
					name: 'Agent 节点 ' + (nodes.length + 1),
					prompt: '请基于上一节点的输出继续完成任务。',
					type: 'agent',
					config: {
						type: 'agent',
						prompt: '请基于上一节点的输出继续完成任务。',
					},
					order: nodes.length,
				},
			]),
		);
	};

	const addOptionalTemplateStep = () => {
		if (!template || !nextOptionalStep || nodes.length >= 8) return;
		const nodeId = templateStepBindings[nextOptionalStep.step_key];
		if (!nodeId) return;
		const restored = restoreOptionalStep(nextOptionalStep, nodeId);
		if (!restored) return;
		const templateIndex = template.steps.findIndex((step) => step.step_key === nextOptionalStep.step_key);
		const insertAt = nodes.findIndex((node) => {
			const key = bindingByNodeId.get(node.id);
			return template.steps.findIndex((step) => step.step_key === key) > templateIndex;
		});
		const next = [...nodes];
		next.splice(insertAt < 0 ? next.length : insertAt, 0, restored);
		onChange(normalizeEditedNodes(next));
	};

	return (
		<section>
			<div className="mb-3 flex items-center justify-between">
				<div>
					<div className="text-base font-semibold">任务流程</div>
					<div className="mt-1 text-xs text-muted-foreground">
						按顺序执行，每一步的输出会自动传给下一步
					</div>
					{nodes.length >= 8 && (
						<div className="mt-1 text-[11px] text-amber-700">
							已达到 Task 的 8 节点上限，不能再添加步骤。
						</div>
					)}
					{templateMode && (
						<div id="task-template-flow-edit-limitations" className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-500">
							模板流程顺序固定，服务端会拒绝重排。只能编辑模板授权的名称、提示词和输入问题；必需节点不能删除，新增仅限恢复模板声明的可选步骤，其他执行约束由模板固定。
						</div>
					)}
				</div>
				<Button
					variant="outline"
					size="sm"
					onClick={templateMode ? addOptionalTemplateStep : addNode}
					disabled={disabled || nodes.length >= 8 || (templateMode && !nextOptionalStep)}
					title={nodes.length >= 8 ? 'Task 最多包含 8 个节点。' : templateMode ? (nextOptionalStep ? '只能恢复来源模板声明的可选步骤。' : '没有可恢复的可选步骤；模板流程结构固定。') : 'Task 最多包含 8 个节点。'}
				>
					<Plus />
					{templateMode ? (nextOptionalStep ? `添加可选：${nextOptionalStep.title}` : '无可添加的可选步骤') : '新增步骤'}
				</Button>
			</div>

			<div className="relative pl-8">
				<div className="absolute top-6 bottom-6 left-[13px] w-px bg-border" />
				<div className="relative mb-3 flex items-center gap-3">
					<div className="z-10 flex size-7 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
						始
					</div>
					<span className="text-sm font-medium">开始</span>
				</div>

				{nodes.map((node, index) => {
					const templateStepKey = bindingByNodeId.get(node.id);
					const templateStep = template?.steps.find((step) => step.step_key === templateStepKey);
					const templateEditable = templateMode && Boolean(templateStep?.editable);
					const fieldDisabled = disabled || (templateMode && !templateEditable);
					const configDisabled =
						disabled ||
						(templateMode &&
							(!templateEditable || (templateStep?.kind !== 'agent' && templateStep?.kind !== 'input')));
					const toolConfig = node.config?.type === 'tool' ? node.config : null;
					const pythonConfig = node.config?.type === 'python' ? node.config : null;
					const agentConfig = node.config?.type === 'agent' ? node.config : null;
					const inputConfig = node.config?.type === 'input' ? node.config : null;
					const argumentText =
						argumentDrafts[node.id] ??
						JSON.stringify(toolConfig?.arguments ?? {}, null, 2);
					return (
						<div key={node.id} className="relative mb-3">
							<div className="absolute top-6 -left-8 z-10 flex size-7 items-center justify-center rounded-full border border-border bg-card text-xs font-semibold text-muted-foreground">
								{index + 1}
							</div>
							<div className="rounded-2xl border border-border bg-background/70 p-4 transition-colors focus-within:border-primary/30">
								<div className="flex items-start gap-3">
									<GripVertical className="mt-1 size-4 shrink-0 text-muted-foreground/60" />
									<div className="min-w-0 flex-1 space-y-3">
										<div className="flex items-center gap-2">
											{stepIcon(node.type)}
											<select
												value={node.type}
												onChange={(event) =>
													updateStepType(
														index,
														event.target.value as TaskStepType,
													)
												}
												disabled={disabled || templateMode}
												className="h-8 rounded-lg border border-input bg-background px-2 text-xs font-medium text-foreground outline-none focus:border-ring focus:ring-3 focus:ring-ring/50"
											>
												<option value="agent">AgentStep</option>
												<option value="tool">ToolStep</option>
												<option value="python">CodeStep</option>
												<option value="input">用户输入</option>
											</select>
											<Input
												value={node.name}
												onChange={(event) =>
													updateNode(index, { name: event.target.value })
												}
												disabled={fieldDisabled}
												placeholder="步骤名称"
												className="h-8 border-transparent bg-transparent px-1 text-sm font-semibold shadow-none focus-visible:bg-muted/60"
											/>
										</div>
										<div className="text-[11px] text-muted-foreground">
											{STEP_LABELS[node.type]}
										</div>
										{templateMode && templateStep && (
											<div className="text-[11px] text-slate-500">
												{templateStep.required ? '模板必需节点：不能删除。 ' : '模板可选节点。 '}
												{templateStep.editable ? '可编辑模板授权的内容。' : '节点内容和执行配置由模板固定。'}
											</div>
										)}
										<label className="flex items-center gap-2 text-xs text-muted-foreground">
											<input
												type="checkbox"
												checked={node.knowledge_graph_enabled ?? false}
												onChange={(event) =>
													updateNode(index, {
														knowledge_graph_enabled:
															event.target.checked,
													})
												}
												disabled={templateMode || disabled}
												className="size-3.5 accent-primary"
											/>
											<span>允许在此步骤按需生成知识图谱</span>
										</label>
										{agentConfig && (
											<Textarea
												value={agentConfig.prompt}
												onChange={(event) =>
													updateNode(index, {
														prompt: event.target.value,
														config: {
															...agentConfig,
															prompt: event.target.value,
														},
													})
												}
												disabled={configDisabled}
												placeholder="写入 AgentStep 要完成的 Prompt"
												className="min-h-24 resize-y bg-card text-sm leading-6"
											/>
										)}
										{inputConfig && (
											<TaskInputStepEditor
												config={inputConfig}
												disabled={configDisabled}
												templateConfig={templateStep?.config.type === 'input' ? templateStep.config : undefined}
												onChange={(config) => updateNode(index, { config })}
											/>
										)}
										<div className="space-y-2">
											<label className="block text-xs text-muted-foreground">
												完成条件与验收标准
											</label>
											<Textarea
												value={node.acceptance_criteria ?? ''}
												disabled={fieldDisabled || templateMode}
												rows={2}
												placeholder="例如：目的地、日期、人数和预算均已确认，结果有明确依据"
												onChange={(event) =>
													updateNode(index, {
														acceptance_criteria: event.target.value,
													})
												}
											/>
											<label className="flex items-center gap-2 text-xs text-muted-foreground">
												最多尝试次数
												<Input
													type="number"
													min={1}
													max={10}
													className="w-20"
													value={node.max_attempts ?? 3}
													disabled={templateMode || disabled}
													onChange={(event) =>
														updateNode(index, {
															max_attempts: Math.min(
																10,
																Math.max(
																	1,
																	Number(event.target.value) || 1,
																),
															),
														})
													}
												/>
											</label>
										</div>

										{toolConfig && (
											<div className="space-y-3">
												<Input
													list={'task-tool-options-' + node.id}
													value={toolConfig.tool_name}
													onChange={(event) =>
														updateNode(index, {
															config: {
																...toolConfig,
																tool_name: event.target.value,
															},
														})
													}
													disabled={configDisabled}
													placeholder="工具名称，例如 weather 或 search"
													className="bg-card text-sm"
												/>
												<datalist id={'task-tool-options-' + node.id}>
													{tools.map((tool) => (
														<option key={tool.name} value={tool.name}>
															{tool.description}
														</option>
													))}
												</datalist>
												{toolConfig.tool_name &&
													tools.find(
														(tool) =>
															tool.name === toolConfig.tool_name,
													)?.description && (
														<div className="text-xs leading-5 text-muted-foreground">
															{
																tools.find(
																	(tool) =>
																		tool.name ===
																		toolConfig.tool_name,
																)?.description
															}
														</div>
													)}
												<Textarea
													value={argumentText}
													onChange={(event) => {
														const text = event.target.value;
														setArgumentDrafts((previous) => ({
															...previous,
															[node.id]: text,
														}));
														try {
															const parsed = JSON.parse(
																text,
															) as Record<string, unknown>;
															if (
																parsed &&
																typeof parsed === 'object' &&
																!Array.isArray(parsed)
															) {
																updateNode(index, {
																	config: {
																		...toolConfig,
																		arguments: parsed,
																	},
																});
															}
														} catch {
															// Keep the draft text until the JSON is complete.
														}
													}}
													disabled={configDisabled}
													placeholder={'参数 JSON，例如 {"city":"北京"}'}
													className="min-h-24 resize-y bg-card font-mono text-xs leading-5"
												/>
											</div>
										)}

										{pythonConfig && (
											<div className="space-y-3">
												<Textarea
													value={pythonConfig.code}
													onChange={(event) =>
														updateNode(index, {
															config: {
																...pythonConfig,
																code: event.target.value,
															},
														})
													}
													disabled={configDisabled}
													placeholder="编写 CodeStep 数据处理代码，可使用 previous_output 变量"
													className="min-h-32 resize-y bg-card font-mono text-xs leading-5"
												/>
												<label className="flex items-center gap-2 text-xs text-muted-foreground">
													<span>超时（秒）</span>
													<Input
														type="number"
														min={1}
														max={300}
														value={pythonConfig.timeout_seconds}
														onChange={(event) =>
															updateNode(index, {
																config: {
																	...pythonConfig,
																	timeout_seconds:
																		Number(
																			event.target.value,
																		) || 30,
																},
															})
														}
														disabled={configDisabled}
														className="h-8 w-24 bg-card text-sm"
													/>
												</label>
											</div>
										)}

										{index === nodes.length - 1 && !templateMode && (
											<div className="space-y-3 rounded-xl border border-dashed border-primary/30 bg-primary/[0.03] p-3">
												<div className="text-xs font-medium text-foreground">
													最终文件产物（可选）
												</div>
												<div className="grid gap-3 sm:grid-cols-2">
													<label className="space-y-1.5 text-xs text-muted-foreground">
														<span>文件格式</span>
														<select
															value={node.artifact?.format ?? ''}
															onChange={(event) => {
																const format = event.target
																	.value as TaskArtifactFormat;
																updateNode(index, {
																	artifact: format
																		? {
																				format,
																				filename:
																					node.artifact
																						?.filename ??
																					null,
																			}
																		: null,
																});
															}}
															disabled={configDisabled}
															className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground outline-none focus:border-ring focus:ring-3 focus:ring-ring/50 disabled:opacity-50"
														>
															<option value="">不生成文件</option>
															<option value="markdown">
																Markdown（.md）
															</option>
															<option value="docx">
																Word（.docx）
															</option>
															<option value="xlsx">
																Excel（.xlsx）
															</option>
														</select>
													</label>
													{node.artifact && (
														<label className="space-y-1.5 text-xs text-muted-foreground">
															<span>文件名（可选）</span>
															<Input
																value={node.artifact.filename ?? ''}
																onChange={(event) =>
																	updateNode(index, {
																		artifact: {
																			...node.artifact!,
																			filename:
																				event.target.value,
																		},
																	})
																}
																disabled={configDisabled}
																placeholder="例如：北京出行建议"
																className="h-8 bg-card text-sm"
															/>
														</label>
													)}
												</div>
												<div className="text-[11px] leading-5 text-muted-foreground">
													执行完成后，最后一个节点的输出会保存到当前
													Workspace，并关联到本次 Run。
												</div>
											</div>
										)}
									</div>
									<div className="flex shrink-0 items-center gap-1">
										<Button
											variant="ghost"
											size="icon-xs"
											onClick={() => moveNode(index, -1)}
											disabled={disabled || templateMode || index === 0}
											aria-label={templateMode ? '上移步骤（模板顺序固定）' : '上移步骤'}
											aria-describedby={templateMode ? 'task-template-flow-edit-limitations' : undefined}
											title={templateMode ? '模板流程顺序固定，服务端会拒绝重排。' : '上移步骤'}
										>
											<ArrowUp />
										</Button>
										<Button
											variant="ghost"
											size="icon-xs"
											onClick={() => moveNode(index, 1)}
											disabled={disabled || templateMode || index === nodes.length - 1}
											aria-label={templateMode ? '下移步骤（模板顺序固定）' : '下移步骤'}
											aria-describedby={templateMode ? 'task-template-flow-edit-limitations' : undefined}
											title={templateMode ? '模板流程顺序固定，服务端会拒绝重排。' : '下移步骤'}
										>
											<ArrowDown />
										</Button>
										<Button
											variant="ghost"
											size="icon-xs"
											className="text-destructive hover:text-destructive"
											onClick={() => removeNode(index)}
											disabled={fieldDisabled || nodes.length <= 1 || (templateMode && Boolean(templateStep?.required))}
											aria-label={templateMode && templateStep?.required ? '删除步骤（模板必需节点）' : '删除步骤'}
											title={templateMode && templateStep?.required ? '模板必需节点不能删除。' : '删除可选步骤'}
										>
											<Trash2 />
										</Button>
									</div>
								</div>
							</div>
						</div>
					);
				})}

				<div className="relative flex items-center gap-3">
					<div className="z-10 flex size-7 items-center justify-center rounded-full border border-border bg-card text-xs font-semibold text-muted-foreground">
						终
					</div>
					<span className="text-sm font-medium">结束</span>
				</div>
			</div>
		</section>
	);
}
