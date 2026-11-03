import { AlertCircle, Loader2, Play, X } from 'lucide-react';
import * as React from 'react';

import { getTaskCapabilityDisplayName } from './task-template-labels';
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
function emptyInputValue(property: TaskTemplateInputProperty): unknown {
	switch (property.type) {
		case 'string':
			return '';
		case 'array':
			return Array.from(
				{ length: Math.max(0, Math.floor(property.minItems ?? 0)) },
				() => property.items ? emptyInputValue(property.items) : '',
			);
		case 'integer':
			return Math.ceil(property.minimum ?? 0);
		case 'number':
			return property.minimum ?? 0;
		case 'boolean':
			return false;
	}
}

function getDeferredTemplateInputs(template: TaskTemplateDefinition): Record<string, unknown> {
	return Object.fromEntries(
		template.input_schema.required.flatMap((key) => {
			const property = template.input_schema.properties[key];
			return property ? [[key, emptyInputValue(property)]] : [];
		}),
	);
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
	const [error, setError] = React.useState('');
	const [isSubmitting, setIsSubmitting] = React.useState(false);
	const open = Boolean(template);
	const deferredInputs = template ? getDeferredTemplateInputs(template) : null;

	React.useEffect(() => {
		if (!template) return;
		setError('');
		setIsSubmitting(false);
	}, [template]);

	const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!template) return;
		setError('');
		const inputs = deferredInputs ?? {};
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

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!max-h-[88vh] !w-[min(700px,calc(100%-2rem))] !max-w-[700px] overflow-y-auto">
				{template && (
					<>
						<DialogHeader>
							<DialogTitle>使用模板：{template.title}</DialogTitle>
							<DialogDescription>
								创建时无需填写业务资料；执行任务时会再让你补充本场景所需信息。
							</DialogDescription>
						</DialogHeader>

						<form onSubmit={handleSubmit} className="space-y-4">
							<div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
								<div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs font-semibold text-slate-700">
									<span>模板依赖</span>
									{template.required_capabilities.map((capability) => (
										<Badge key={capability} variant="outline" className="bg-white text-[10px] font-medium">
											{getTaskCapabilityDisplayName(capability)}
										</Badge>
									))}
								</div>
								<div className="text-[10px] leading-5 text-slate-500">
									这些能力会随模板 Task 保存；服务端仅在实际运行前检查 Tool/MCP 是否可用。
								</div>
							</div>

							<div className="rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3 text-xs leading-5 text-blue-900">
								{template.title}流程和工具配置会随任务创建。具体业务信息将在执行阶段填写，不会用演示数据预填。
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
									{isSubmitting ? '创建中…' : '创建 Task'}
								</Button>
							</DialogFooter>
						</form>
					</>
				)}
			</DialogContent>
		</Dialog>
	);
}
