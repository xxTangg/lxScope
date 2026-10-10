import { AlertCircle, Loader2, Play, X } from 'lucide-react';
import * as React from 'react';

import { ApiError } from '@/api/client';
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

function parseInputs(template: TaskTemplateDefinition, draft: TemplateInputDraft): Record<string, unknown> {
	const inputs: Record<string, unknown> = {};
	for (const [key, property] of Object.entries(template.input_schema.properties)) {
		const raw = draft[key] ?? '';
		const trimmed = raw.trim();
		if (property.type === 'boolean') {
			if (!trimmed) {
				if (template.input_schema.required.includes(key)) throw new Error(`请选择“${key}”。`);
				continue;
			}
			inputs[key] = trimmed === 'true';
			continue;
		}
		if (!trimmed) {
			if (template.input_schema.required.includes(key)) throw new Error(`请填写“${key}”。`);
			continue;
		}
		if (property.type === 'array') {
			const itemType = property.items?.type ?? 'string';
			const values = trimmed.split(/\r?\n|[,，]/).map((value) => value.trim()).filter(Boolean);
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

function nextStepForCapabilityFailure(code: string | undefined): string | null {
	if (code === 'TEMPLATE_CAPABILITY_MISSING') {
		return '请切换到已配置相应只读 Tool 的 Agent 会话，或请管理员检查服务端 capability_tool_map 后重试。';
	}
	if (code === 'TEMPLATE_CAPABILITY_UNSAFE') {
		return '当前会话匹配到的 Tool 未通过只读安全校验。请管理员接入只读 Tool 后重试。';
	}
	return null;
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
	onInstantiate: (template: TaskTemplateDefinition, inputs: Record<string, unknown>, sourceContext: TaskContext) => Promise<void>;
}) {
	const [draft, setDraft] = React.useState<TemplateInputDraft>({});
	const [error, setError] = React.useState('');
	const [nextStep, setNextStep] = React.useState<string | null>(null);
	const [isSubmitting, setIsSubmitting] = React.useState(false);
	const open = Boolean(template);

	React.useEffect(() => {
		if (!template) return;
		setDraft(defaultDraft(template));
		setError('');
		setNextStep(null);
		setIsSubmitting(false);
	}, [template]);

	const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!template) return;
		setError('');
		setNextStep(null);
		let inputs: Record<string, unknown>;
		try {
			inputs = parseInputs(template, draft);
		} catch (validationError) {
			setError(validationError instanceof Error ? validationError.message : '请检查模板输入。');
			return;
		}
		setIsSubmitting(true);
		try {
			await onInstantiate(template, inputs, sourceContext);
			onOpenChange(false);
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : '模板实例化失败。');
			const code = requestError instanceof ApiError ? requestError.code : undefined;
			setNextStep(nextStepForCapabilityFailure(code));
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!max-h-[88vh] !w-[min(700px,calc(100%-2rem))] !max-w-[700px] overflow-y-auto">
				{template && (
					<>
						<DialogHeader>
							<DialogTitle>使用模板：{template.title}</DialogTitle>
							<DialogDescription>
								填写本场景需要的输入。服务端会校验模板版本和当前会话 Tool，并直接创建标准流程，不调用 AI Planner。
							</DialogDescription>
						</DialogHeader>

						<form onSubmit={handleSubmit} className="space-y-4">
							{!error && (
								<div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
									<div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs font-semibold text-slate-700">
										<span>模板依赖</span>
										{template.required_capabilities.map((capability) => (
											<Badge key={capability} variant="outline" className="bg-white font-mono text-[9px]">{capability}</Badge>
										))}
									</div>
									<div className="text-[10px] leading-5 text-slate-500">这些是模板声明的能力依赖。实例化结果由服务端根据当前会话中的真实 Tool 校验。</div>
								</div>
							)}

							<div className="space-y-3">
								{Object.entries(template.input_schema.properties).map(([key, property]) => {
									const required = template.input_schema.required.includes(key);
									const label = property.description || key;
									const value = draft[key] ?? '';
									return (
										<label key={key} className="block space-y-1.5">
											<span className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-slate-800">
												{label}
												{required ? <Badge variant="outline" className="border-red-200 bg-red-50 text-[9px] text-red-700">必填</Badge> : <Badge variant="outline" className="text-[9px]">可选</Badge>}
												<span className="font-mono text-[9px] text-slate-400">{key} · {property.type}</span>
												{property.minimum != null && <span className="text-[9px] text-slate-400">最小 {property.minimum}</span>}
												{property.maximum != null && <span className="text-[9px] text-slate-400">最大 {property.maximum}</span>}
												{property.minItems != null && <span className="text-[9px] text-slate-400">至少 {property.minItems} 项</span>}
												</span>
											{property.type === 'boolean' ? (
												<select
													value={value}
													disabled={isSubmitting}
													onChange={(event) => setDraft((previous) => ({ ...previous, [key]: event.target.value }))}
													className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
												>
													<option value="">{required ? '请选择' : '不提供'}</option>
													<option value="false">否</option>
													<option value="true">是</option>
												</select>
											) : property.type === 'array' ? (
												<Textarea
													value={value}
													disabled={isSubmitting}
													onChange={(event) => setDraft((previous) => ({ ...previous, [key]: event.target.value }))}
													placeholder="每行一项，也可用逗号分隔"
													className="min-h-20 resize-y"
												/>
											) : (
												<Input
													type={property.type === 'integer' || property.type === 'number' ? 'number' : 'text'}
													step={property.type === 'integer' ? 1 : property.type === 'number' ? 'any' : undefined}
													min={property.minimum ?? undefined}
													max={property.maximum ?? undefined}
													value={value}
													disabled={isSubmitting}
													onChange={(event) => setDraft((previous) => ({ ...previous, [key]: event.target.value }))}
													placeholder={`填写${label}`}
												/>
											)}
										</label>
									);
								})}
							</div>

							{error && (
								<Alert variant="destructive">
									<AlertCircle />
									<AlertDescription>
										<div>{error}</div>
										{nextStep && (
											<div className="mt-2 space-y-2">
												<p>{nextStep}</p>
											</div>
										)}
									</AlertDescription>
								</Alert>
							)}

							<DialogFooter>
								<Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
									<X />取消
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
