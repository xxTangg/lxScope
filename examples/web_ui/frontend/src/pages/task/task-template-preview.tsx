import { AlertCircle, Check, LockKeyhole, WandSparkles } from 'lucide-react';

import type { TaskTemplateDefinition } from '@/api/task';
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

export function TaskTemplatePreview({
	template,
	onUse,
}: {
	template: TaskTemplateDefinition;
	onUse: (template: TaskTemplateDefinition) => void;
}) {
	const unsupported = template.unsupported_capabilities ?? [];
	return (
		<div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
			<div>
				<div className="flex flex-wrap items-center gap-2">
					<h2 className="text-lg font-semibold text-slate-900">{template.title}</h2>
					<Badge variant="outline">v{template.template_version}</Badge>
					<Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
						{template.steps.length} 个逻辑节点
					</Badge>
					<Badge
						variant="outline"
						className={
							unsupported.length
								? 'border-amber-200 bg-amber-50 text-amber-800'
								: 'border-slate-200 bg-slate-50 text-slate-600'
						}
					>
						{unsupported.length ? '含未支持能力' : '待校验会话工具'}
					</Badge>
				</div>
				<p className="mt-2 whitespace-pre-line text-sm leading-6 text-slate-600">
					{template.description}
				</p>
			</div>

			<section className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
				<div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-800">
					<WandSparkles className="size-3.5 text-blue-600" />
					能力依赖
				</div>
				<div className="flex flex-wrap gap-1.5">
					{template.required_capabilities.map((capability) => (
						<Badge
							key={capability}
							variant="outline"
							className="bg-white font-mono text-[10px]"
						>
							{capability}
						</Badge>
					))}
				</div>
				<p className="mt-2 text-[11px] leading-5 text-slate-500">
					目录 API 不提供当前会话的能力就绪结果；选择使用后由服务端绑定真实 Tool 并校验。
				</p>
			</section>

			{unsupported.length > 0 && (
				<section className="rounded-xl border border-amber-200 bg-amber-50/70 p-3">
					<div className="mb-2 flex items-center gap-2 text-xs font-semibold text-amber-900">
						<AlertCircle className="size-3.5" />
						当前不支持的能力
					</div>
					<ul className="space-y-2">
						{unsupported.map((item) => (
							<li
								key={item.capability_key}
								className="text-[11px] leading-5 text-amber-900"
							>
								<span className="font-mono font-semibold">
									{item.capability_key}
								</span>
								<span className="ml-2">{item.reason}</span>
							</li>
						))}
					</ul>
				</section>
			)}

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

			<div className="sticky bottom-0 -mx-1 flex justify-end border-t border-slate-100 bg-white/95 px-1 py-3 backdrop-blur">
				<Button onClick={() => onUse(template)}>使用此模板</Button>
			</div>
		</div>
	);
}
