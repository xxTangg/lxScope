import {
	ArrowLeft,
	BookOpen,
	ChevronDown,
	CircleHelp,
	FileText,
	FolderOpen,
	Lightbulb,
	Target,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS02ProjectPlanningRun } from './task-s02-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Button } from '@/components/ui/button';


interface TaskS02ResultsProps {
	run: TaskRunRecord;
	onViewExecution: () => void;
	onViewFullResult: () => void;
}

function ResultCard({
	icon: Icon,
	title,
	children,
	className = '',
}: {
	icon: typeof BookOpen;
	title: string;
	children: React.ReactNode;
	className?: string;
}) {
	return (
		<section className={`flex h-[360px] min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 ${TASK_RESULT_CARD_CLASS} ${className}`}>
			<div className="mb-3 flex shrink-0 items-center gap-2.5">
				<span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
					<Icon className="size-4" aria-hidden="true" />
				</span>
				<h2 className="text-sm font-semibold text-slate-900">{title}</h2>
			</div>
			<div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden pr-1">{children}</div>
		</section>
	);
}

function EmptyNote({ children }: { children: React.ReactNode }) {
	return <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-500">{children}</p>;
}

export function TaskS02Results({ run, onViewExecution, onViewFullResult }: TaskS02ResultsProps) {
	const result = React.useMemo(() => adaptS02ProjectPlanningRun(run), [run]);
	const visibleFields = result.overviewFields;

	return (
		<div className="mx-auto w-full max-w-[1600px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">项目策划成果</h1>
					<p className="mt-1 text-xs text-slate-500">项目概览 · 策划报告 · 建议计划 · 参考资料</p>
				</div>
				<Button type="button" variant="outline" size="sm" onClick={onViewExecution}>
					<ArrowLeft />
					返回执行过程
				</Button>
			</header>

			<div className="grid min-w-0 gap-4 lg:grid-cols-2">
				<ResultCard icon={Target} title="项目策划概览">
					{result.projectName && (
						<div className="mb-3 rounded-xl bg-blue-50/70 px-3.5 py-3">
							<div className="text-[10px] font-medium uppercase tracking-wide text-blue-700">项目名称</div>
							<div className="mt-1 text-base font-semibold leading-6 text-slate-900">{result.projectName}</div>
						</div>
					)}
					{visibleFields.length > 0 && (
						<dl className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
							{visibleFields.map((field) => (
								<div key={field.label} className="min-w-0">
									<dt className="text-[11px] text-slate-500">{field.label}</dt>
									<dd className="mt-0.5 line-clamp-2 text-xs leading-5 text-slate-800">{field.value}</dd>
								</div>
							))}
						</dl>
					)}
					{result.overviewExcerpt && (
						<p className="mt-2.5 border-l-2 border-blue-200 pl-3 text-xs leading-5 text-slate-600">{result.overviewExcerpt}</p>
					)}
					{!result.projectName && visibleFields.length === 0 && !result.overviewExcerpt && (
						<EmptyNote>本次运行未提供可识别的项目概况信息。</EmptyNote>
					)}
				</ResultCard>

				<ResultCard icon={FileText} title="智能策划大纲">
					{result.outline.length ? (
						<div className="divide-y divide-slate-100 rounded-xl border border-slate-100">
							{result.outline.map((section, index) => (
								<details key={`${section.number}-${section.title}-${index}`} className="group px-3 first:rounded-t-xl last:rounded-b-xl">
									<summary className="flex cursor-pointer list-none items-center gap-3 py-3 [&::-webkit-details-marker]:hidden">
										<span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-slate-600">
											{section.number}
										</span>
										<span className="min-w-0 flex-1 text-xs font-medium leading-5 text-slate-800">{section.title}</span>
										<ChevronDown className="size-4 shrink-0 text-slate-400 transition-transform group-open:rotate-180" aria-hidden="true" />
									</summary>
									<p className="pb-3 pl-10 text-xs leading-5 text-slate-600">
										{section.summary ?? '本章节暂未提取到摘要，请查看完整报告。'}
									</p>
								</details>
							))}
						</div>
					) : (
						<EmptyNote>未能从本次报告识别章节标题，完整策划大纲请查看成果报告。</EmptyNote>
					)}
				</ResultCard>

				<ResultCard icon={Lightbulb} title="AI 策划建议与工作计划">
					<h3 className="mb-2 text-xs font-semibold text-slate-800">策划建议</h3>
					{result.recommendations.length ? (
						<ul className="space-y-2.5">
							{result.recommendations.map((recommendation, index) => (
								<li key={`${recommendation}-${index}`} className="flex gap-2.5 text-xs leading-5 text-slate-700">
									<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-blue-500" />
									<span>{recommendation}</span>
								</li>
							))}
						</ul>
					) : (
						<EmptyNote>本次报告未提取到明确的策划建议。</EmptyNote>
					)}
					<h3 className="mb-2 mt-4 border-t border-slate-100 pt-3 text-xs font-semibold text-slate-800">工作计划</h3>
					{result.workPlan.length ? (
						<ol className="space-y-2.5">
							{result.workPlan.map((item, index) => (
								<li key={`${item.title}-${index}`} className="flex gap-3">
									<span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-blue-50 text-[10px] font-semibold text-blue-700">{index + 1}</span>
									<div className="min-w-0 flex-1">
										<div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
											<p className="text-xs font-medium text-slate-800">{item.title}</p>
											{item.timing && <span className="text-[10px] text-slate-500">{item.timing}</span>}
										</div>
										{item.detail && <p className="mt-0.5 text-xs leading-5 text-slate-600">{item.detail}</p>}
									</div>
								</li>
							))}
						</ol>
					) : (
						<EmptyNote>本次结果未提供明确的阶段或时间安排。</EmptyNote>
					)}
				</ResultCard>

				<ResultCard icon={FolderOpen} title="参考资料与待核实事项">
					<h3 className="mb-2 text-xs font-semibold text-slate-800">参考资料</h3>
					{result.references.length ? (
						<ul className="divide-y divide-slate-100">
							{result.references.map((reference) => (
								<li key={reference.name} className="flex items-start gap-2.5 py-2 first:pt-0 last:pb-0">
									<FileText className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden="true" />
									<div className="min-w-0">
										<p className="break-words text-xs font-medium text-slate-800">{reference.name}</p>
										{reference.usage && <p className="mt-0.5 text-[11px] leading-4 text-slate-500">{reference.usage}</p>}
									</div>
								</li>
							))}
						</ul>
					) : (
						<EmptyNote>本次运行未提取到可展示的资料名称；来源记录可在执行详情中查看。</EmptyNote>
					)}
					<p className="mt-2 flex gap-2 text-[10px] leading-4 text-slate-500">
						<CircleHelp className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
						资料来源表示本次运行检索或读取过相关资料，不代表资料内容已完成事实核验。
					</p>
					<h3 className="mb-2 mt-4 border-t border-slate-100 pt-3 text-xs font-semibold text-slate-800">待核实事项</h3>
					{result.pendingItems.length ? (
						<ul className="space-y-2.5">
							{result.pendingItems.map((item, index) => (
								<li key={`${item}-${index}`} className="flex gap-2.5 text-xs leading-5 text-slate-700">
									<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
									<span>{item}</span>
								</li>
							))}
						</ul>
					) : (
						<EmptyNote>本次结果未识别到明确的待核实事项。</EmptyNote>
					)}
				</ResultCard>
			</div>

			<section className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
							<FileText className="size-4 text-blue-600" aria-hidden="true" />
							完整 Word 报告
						</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次项目策划的核心成果摘要，完整章节、详细分析与参考资料请查看 Word 报告。</p>
					</div>
					{result.docxArtifacts.length > 0 && (
						<Button type="button" variant="outline" size="sm" onClick={onViewFullResult}>
							<FileText />
							打开预览与下载
						</Button>
					)}
				</div>
				{result.docxArtifacts.length ? (
					<ul className="mt-3 grid gap-2 sm:grid-cols-2">
						{result.docxArtifacts.map((artifact) => (
							<li key={artifact.id} className="flex min-w-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
								<FileText className="size-4 shrink-0 text-slate-400" aria-hidden="true" />
								<span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700" title={artifact.name}>{artifact.name}</span>
								<span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">DOCX</span>
							</li>
						))}
					</ul>
				) : (
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行未生成 DOCX 报告。</p>
				)}
			</section>
		</div>
	);
}
