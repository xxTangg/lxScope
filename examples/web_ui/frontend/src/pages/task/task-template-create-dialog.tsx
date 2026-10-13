import { AlertCircle, ClipboardPaste, Loader2, Play, X } from 'lucide-react';
import * as React from 'react';

import type { TaskContext, TaskTemplateDefinition, TaskTemplateInputProperty } from '@/api/task';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { getTaskTemplateDemoInput } from './task-template-demo-inputs';

type TemplateInputDraft = Record<string, string>;

function parseScalar(type: TaskTemplateInputProperty['type'], raw: string): unknown {
	if (type === 'integer') {
		const value = Number(raw);
		if (!Number.isInteger(value)) throw new Error('请输入整数。');
		return value;
	}
	if (type === 'number') {
		const value = Number(raw);
		if (!Number.isFinite(value)) throw new Error('请输入有效数字。');
		return value;
	}
	if (type === 'boolean') return raw === 'true';
	return raw;
}

function parseInputs(
	template: TaskTemplateDefinition,
	draft: TemplateInputDraft,
): Record<string, unknown> {
	const inputs: Record<string, unknown> = {};
	for (const [key, property] of Object.entries(template.input_schema.properties)) {
		const raw = draft[key] ?? '';
		const trimmed = raw.trim();
		if (property.type === 'boolean') {
			if (!trimmed) {
				if (template.input_schema.required.includes(key))
					throw new Error(`请选择“${key}”。`);
				continue;
			}
			inputs[key] = trimmed === 'true';
			continue;
		}
		if (!trimmed) {
			if (template.input_schema.required.includes(key)) throw new Error(`请填写“${key}”。`);
			continue;
		}
		if (property.type === 'string' && key.endsWith('_json')) {
			let parsedJson: unknown;
			try {
				parsedJson = JSON.parse(trimmed);
			} catch {
				throw new Error(`“${key}”需要填写有效的 JSON 数组或对象。`);
			}
			if (!parsedJson || typeof parsedJson !== 'object') {
				throw new Error(`“${key}”请直接填写 JSON 数组或对象，不要再套一层字符串引号。`);
			}
		}
		if (property.type === 'array') {
			const itemType = property.items?.type ?? 'string';
			const values = trimmed
				.split(/\r?\n|[,，]/)
				.map((value) => value.trim())
				.filter(Boolean);
			if (property.minItems != null && values.length < property.minItems) {
				throw new Error(`“${key}”至少需要 ${property.minItems} 项。`);
			}
			inputs[key] = values.map((value) => parseScalar(itemType, value));
			continue;
		}
		inputs[key] = parseScalar(property.type, trimmed);
	}
	return inputs;
}

function defaultDraft(template: TaskTemplateDefinition): TemplateInputDraft {
	return Object.fromEntries(
		Object.keys(template.input_schema.properties).map((key) => [key, '']),
	);
}

function formatDemoValue(
	key: string,
	property: TaskTemplateInputProperty,
	value: unknown,
): string | null {
	if (property.type === 'string' && key.endsWith('_json')) {
		let parsed = value;
		if (typeof parsed === 'string') {
			try {
				parsed = JSON.parse(parsed) as unknown;
			} catch {
				return null;
			}
		}
		if (!parsed || typeof parsed !== 'object') return null;
		return JSON.stringify(parsed, null, 2) ?? null;
	}

	if (property.type === 'array') {
		if (!Array.isArray(value)) return null;
		if (
			value.some(
				(item) =>
					typeof item !== 'string' &&
					typeof item !== 'number' &&
					typeof item !== 'boolean',
			)
		)
			return null;
		return value.map(String).join('\n');
	}
	if (property.type === 'integer' || property.type === 'number') {
		return typeof value === 'number' ? String(value) : null;
	}
	if (property.type === 'boolean') return typeof value === 'boolean' ? String(value) : null;
	if (property.type === 'string') return typeof value === 'string' ? value : null;
	return null;
}

function containsServerPlaceholder(value: unknown): boolean {
	if (typeof value === 'string') {
		return value.includes('__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__');
	}
	if (!value || typeof value !== 'object') return false;
	return Object.values(value).some(containsServerPlaceholder);
}

export function TaskTemplateCreateDialog({
	template,
	sourceContext,
	onOpenChange,
	onInstantiate,
}: {
	template: TaskTemplateDefinition | null;
	sourceContext: TaskContext;
	onOpenChange: (open: boolean) => void;
	onInstantiate: (
		template: TaskTemplateDefinition,
		inputs: Record<string, unknown>,
		sourceContext: TaskContext,
	) => Promise<void>;
}) {
	const [draft, setDraft] = React.useState<TemplateInputDraft>({});
	const [error, setError] = React.useState('');
	const [isSubmitting, setIsSubmitting] = React.useState(false);
	const open = Boolean(template);
	const demoInput = React.useMemo(
		() => (template ? getTaskTemplateDemoInput(template.template_id) : null),
		[template],
	);
	const demoEntries = React.useMemo(() => {
		if (!template || !demoInput) return [];
		return Object.entries(demoInput.inputs).flatMap(([key, value]) => {
			const property = template.input_schema.properties[key];
			if (!property || containsServerPlaceholder(value)) return [];
			const formatted = formatDemoValue(key, property, value);
			return formatted === null ? [] : [{ key, formatted }];
		});
	}, [demoInput, template]);
	const hasSkippedPlaceholder = Boolean(
		demoInput && Object.values(demoInput.inputs).some(containsServerPlaceholder),
	);

	React.useEffect(() => {
		if (!template) return;
		setDraft(defaultDraft(template));
		setError('');
		setIsSubmitting(false);
	}, [template]);

	const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!template) return;
		setError('');
		let inputs: Record<string, unknown>;
		try {
			inputs = parseInputs(template, draft);
		} catch (validationError) {
			setError(
				validationError instanceof Error ? validationError.message : '请检查模板输入。',
			);
			return;
		}
		setIsSubmitting(true);
		try {
			await onInstantiate(template, inputs, sourceContext);
			onOpenChange(false);
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : '模板实例化失败。');
		} finally {
			setIsSubmitting(false);
		}
	};

	const fillDemoEntry = (key: string) => {
		const entry = demoEntries.find((item) => item.key === key);
		if (!entry) return;
		setDraft((previous) => ({ ...previous, [key]: entry.formatted }));
		setError('');
	};

	const fillAllDemoEntries = () => {
		setDraft((previous) => ({
			...previous,
			...Object.fromEntries(demoEntries.map(({ key, formatted }) => [key, formatted])),
		}));
		setError('');
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!max-h-[88vh] !w-[min(700px,calc(100%-2rem))] !max-w-[700px] overflow-y-auto">
				{template && (
					<>
						<DialogHeader>
							<DialogTitle>使用模板：{template.title}</DialogTitle>
							<DialogDescription>
								填写本场景需要的输入即可创建固定流程，不调用 AI Planner。Tool/MCP
								能力只在实际运行前检查。
							</DialogDescription>
						</DialogHeader>

						<form onSubmit={handleSubmit} className="space-y-4">
							{demoInput && (
								<div className="rounded-xl border border-blue-100 bg-blue-50/70 p-3">
									<div className="flex flex-wrap items-center justify-between gap-2">
										<div className="text-xs font-semibold text-blue-900">
											演示输入 · 数据版本 v{demoInput.template_version}
										</div>
										<Button
											type="button"
											variant="outline"
											size="sm"
											disabled={isSubmitting || demoEntries.length === 0}
											onClick={fillAllDemoEntries}
										>
											<ClipboardPaste />
											填入全部可用项（{demoEntries.length}）
										</Button>
									</div>
									<p className="mt-1 text-[11px] leading-5 text-blue-800">
										可逐项填入或编辑。JSON 字段会显示为普通 JSON
										内容，提交时按模板要求作为字符串传入。
									</p>
									{hasSkippedPlaceholder && (
										<p className="mt-1 text-[11px] leading-5 text-amber-800">
											示例中的服务端授权资料 ID
											占位值已跳过；请在对应字段选择当前用户有权访问的真实资料。
										</p>
									)}
								</div>
							)}
							{!error && (
								<div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
									<div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs font-semibold text-slate-700">
										<span>模板依赖</span>
										{template.required_capabilities.map((capability) => (
											<Badge
												key={capability}
												variant="outline"
												className="bg-white font-mono text-[9px]"
											>
												{capability}
											</Badge>
										))}
									</div>
									<div className="text-[10px] leading-5 text-slate-500">
										这些能力会随模板 Task 保存；服务端仅在实际运行前检查
										Tool/MCP 是否可用。
									</div>
								</div>
							)}

							<div className="space-y-3">
								{Object.entries(template.input_schema.properties).map(
									([key, property]) => {
										const required =
											template.input_schema.required.includes(key);
										const label = property.description || key;
										const value = draft[key] ?? '';
										const hasDemoValue = demoEntries.some(
											(entry) => entry.key === key,
										);
										const inputId = `template-input-${template.template_id}-${key}`;
										const isJsonText =
											property.type === 'string' && key.endsWith('_json');
										const isLongText =
											isJsonText ||
											property.type === 'array' ||
											(property.type === 'string' &&
												(key === 'project_brief' ||
													key === 'transcript_text'));
										return (
											<div key={key} className="space-y-1.5">
												<div className="flex flex-wrap items-center justify-between gap-2">
													<label
														htmlFor={inputId}
														className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-slate-800"
													>
														{label}
														{required ? (
															<Badge
																variant="outline"
																className="border-red-200 bg-red-50 text-[9px] text-red-700"
															>
																必填
															</Badge>
														) : (
															<Badge
																variant="outline"
																className="text-[9px]"
															>
																可选
															</Badge>
														)}
														<span className="font-mono text-[9px] text-slate-400">
															{key} · {property.type}
														</span>
														{property.minimum != null && (
															<span className="text-[9px] text-slate-400">
																最小 {property.minimum}
															</span>
														)}
														{property.maximum != null && (
															<span className="text-[9px] text-slate-400">
																最大 {property.maximum}
															</span>
														)}
														{property.minItems != null && (
															<span className="text-[9px] text-slate-400">
																至少 {property.minItems} 项
															</span>
														)}
													</label>
													{hasDemoValue && (
														<Button
															type="button"
															variant="ghost"
															size="sm"
															className="h-7 px-2 text-[10px] text-blue-700"
															disabled={isSubmitting}
															onClick={() => fillDemoEntry(key)}
														>
															填入示例
														</Button>
													)}
												</div>
												{property.type === 'boolean' ? (
													<select
														id={inputId}
														value={value}
														disabled={isSubmitting}
														onChange={(event) =>
															setDraft((previous) => ({
																...previous,
																[key]: event.target.value,
															}))
														}
														className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
													>
														<option value="">
															{required ? '请选择' : '不提供'}
														</option>
														<option value="false">否</option>
														<option value="true">是</option>
													</select>
												) : isLongText ? (
													<Textarea
														id={inputId}
														value={value}
														disabled={isSubmitting}
														onChange={(event) =>
															setDraft((previous) => ({
																...previous,
																[key]: event.target.value,
															}))
														}
														placeholder={
															property.type === 'array'
																? '每行一项，也可用逗号分隔'
																: isJsonText
																	? '填写 JSON 数组或对象'
																	: `填写${label}`
														}
														className={
															property.type === 'array' || isJsonText
																? 'min-h-24 resize-y font-mono text-xs'
																: 'min-h-20 resize-y'
														}
													/>
												) : (
													<Input
														id={inputId}
														type={
															property.type === 'integer' ||
															property.type === 'number'
																? 'number'
																: 'text'
														}
														step={
															property.type === 'integer'
																? 1
																: property.type === 'number'
																	? 'any'
																	: undefined
														}
														min={property.minimum ?? undefined}
														max={property.maximum ?? undefined}
														value={value}
														disabled={isSubmitting}
														onChange={(event) =>
															setDraft((previous) => ({
																...previous,
																[key]: event.target.value,
															}))
														}
														placeholder={`填写${label}`}
													/>
												)}
											</div>
										);
									},
								)}
							</div>

							{error && (
								<Alert variant="destructive">
									<AlertCircle />
									<AlertDescription>
										<div>{error}</div>
									</AlertDescription>
								</Alert>
							)}

							<DialogFooter>
								<Button
									type="button"
									variant="ghost"
									onClick={() => onOpenChange(false)}
									disabled={isSubmitting}
								>
									<X />
									取消
								</Button>
								<Button type="submit" disabled={isSubmitting}>
									{isSubmitting ? <Loader2 className="animate-spin" /> : <Play />}
									{isSubmitting ? '校验并创建…' : '校验并创建 Task'}
								</Button>
							</DialogFooter>
						</form>
					</>
				)}
			</DialogContent>
		</Dialog>
	);
}
