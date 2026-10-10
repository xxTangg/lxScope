import { AlertCircle, BookOpen, Loader2, RefreshCw, Sparkles } from 'lucide-react';
import * as React from 'react';

import { taskApi } from '@/api';
import type { TaskTemplateDefinition } from '@/api/task';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';

import { TaskTemplatePreview } from './task-template-preview';

interface TaskTemplateGalleryProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onUseTemplate: (template: TaskTemplateDefinition) => void;
	onOpenFreeTask: () => void;
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : '模板目录加载失败，请稍后重试。';
}

export function TaskTemplateGallery({
	open,
	onOpenChange,
	onUseTemplate,
	onOpenFreeTask,
}: TaskTemplateGalleryProps) {
	const [templates, setTemplates] = React.useState<TaskTemplateDefinition[]>([]);
	const [selectedTemplateId, setSelectedTemplateId] = React.useState<string | null>(null);
	const [isLoading, setIsLoading] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const requestIdRef = React.useRef(0);
	const selectedTemplate = templates.find((item) => item.template_id === selectedTemplateId) ?? null;

	const loadTemplates = React.useCallback(async () => {
		const requestId = ++requestIdRef.current;
		setIsLoading(true);
		setError(null);
		try {
			const records = await taskApi.listTemplates();
			if (requestId !== requestIdRef.current) return;
			setTemplates(records);
			setSelectedTemplateId((current) =>
				current && records.some((item) => item.template_id === current)
					? current
					: (records[0]?.template_id ?? null),
			);
		} catch (requestError) {
			if (requestId === requestIdRef.current) setError(errorMessage(requestError));
		} finally {
			if (requestId === requestIdRef.current) setIsLoading(false);
		}
	}, []);

	React.useEffect(() => {
		if (open) void loadTemplates();
		else requestIdRef.current += 1;
	}, [loadTemplates, open]);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!flex !h-[min(88vh,900px)] !w-[min(1180px,calc(100%-2rem))] !max-w-[1180px] !grid-rows-[auto_minmax(0,1fr)] gap-3 p-5">
				<DialogHeader className="pr-8">
					<div className="flex flex-wrap items-center justify-between gap-3">
						<div>
							<DialogTitle className="flex items-center gap-2 text-lg">
								<BookOpen className="size-4 text-blue-600" />业务模板
							</DialogTitle>
							<DialogDescription className="mt-1">
								从标准流程开始。模板由服务端定义，实例化时不会调用 AI Planner。
							</DialogDescription>
						</div>
						<div className="flex items-center gap-2">
							<Button variant="outline" size="sm" onClick={onOpenFreeTask}>
								<Sparkles />自由任务（AI 规划）
							</Button>
							<Button variant="ghost" size="icon-sm" onClick={() => void loadTemplates()} disabled={isLoading} aria-label="刷新模板目录">
								{isLoading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
							</Button>
						</div>
					</div>
				</DialogHeader>

				<div className="grid min-h-0 gap-4 overflow-hidden lg:grid-cols-[minmax(310px,0.78fr)_minmax(0,1.22fr)]">
					<section className="min-h-0 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50/60 p-3">
						<div className="mb-3 flex items-center justify-between px-1">
							<div className="text-xs font-semibold text-slate-800">九个预置场景</div>
							{!isLoading && <span className="text-[10px] text-slate-500">{templates.length} 个模板</span>}
						</div>
						{isLoading && templates.length === 0 ? (
							<div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500">
								<Loader2 className="size-4 animate-spin" />加载模板目录…
							</div>
						) : error ? (
							<div className="rounded-xl border border-red-200 bg-red-50 p-4 text-xs text-red-700">
								<div className="flex items-start gap-2"><AlertCircle className="mt-0.5 size-4 shrink-0" />{error}</div>
								<Button variant="outline" size="sm" className="mt-3" onClick={() => void loadTemplates()}>重试</Button>
							</div>
						) : templates.length === 0 ? (
							<div className="py-10 text-center text-xs text-slate-500">模板目录为空。</div>
						) : (
							<div className="space-y-2">
								{templates.map((template, index) => {
									const selected = template.template_id === selectedTemplateId;
									const hasUnsupported = (template.unsupported_capabilities ?? []).length > 0;
									return (
										<button
											key={template.template_id}
											type="button"
											onClick={() => setSelectedTemplateId(template.template_id)}
											aria-pressed={selected}
											className={'w-full rounded-xl border p-3 text-left transition-colors ' + (selected ? 'border-blue-300 bg-blue-50 shadow-sm' : 'border-slate-200 bg-white hover:border-blue-200 hover:bg-slate-50')}
										>
											<div className="flex items-start gap-2.5">
												<div className={'flex size-7 shrink-0 items-center justify-center rounded-lg text-[10px] font-semibold ' + (selected ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-600')}>
													{String(index + 1).padStart(2, '0')}
												</div>
												<div className="min-w-0 flex-1">
													<div className="flex flex-wrap items-center gap-1.5">
														<span className="text-xs font-semibold text-slate-800">{template.title}</span>
														<Badge variant="outline" className="text-[9px]">v{template.template_version}</Badge>
													</div>
													<p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">{template.description}</p>
													<div className="mt-2 flex flex-wrap items-center gap-1.5">
														<Badge variant="outline" className="text-[9px]">{template.steps.length} 节点</Badge>
														<Badge variant="outline" className={hasUnsupported ? 'border-amber-200 bg-amber-50 text-amber-800 text-[9px]' : 'text-[9px]'}>{hasUnsupported ? '含未支持能力' : '待校验工具'}</Badge>
													</div>
												</div>
											</div>
										</button>
									);
								})}
							</div>
						)}
					</section>

					<section className="min-h-0 overflow-hidden rounded-xl border border-slate-200 bg-white p-4">
						{selectedTemplate ? (
							<TaskTemplatePreview template={selectedTemplate} onUse={onUseTemplate} />
						) : (
							<div className="flex h-full items-center justify-center text-center text-sm text-slate-500">
								选择一个模板查看流程详情。
							</div>
						)}
					</section>
				</div>
			</DialogContent>
		</Dialog>
	);
}
