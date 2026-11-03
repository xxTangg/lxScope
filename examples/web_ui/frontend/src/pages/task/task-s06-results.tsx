import {
	ArrowLeft,
	ClipboardList,
	FileText,
	ListChecks,
	MessageSquareText,
	Tags,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS06OpinionRun } from './task-s06-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS06ResultsProps {
	run: TaskRunRecord;
	onViewExecution: () => void;
	onViewFullResult: () => void;
}

function ResultCard({
	icon,
	title,
	children,
}: {
	icon: React.ReactNode;
	title: string;
	children: React.ReactNode;
}) {
	return (
		<section className={`flex h-full min-h-[260px] min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 lg:h-[320px] lg:min-h-[320px] ${TASK_RESULT_CARD_CLASS}`}>
			<header className="mb-3 flex h-8 shrink-0 items-center gap-2.5">
				<span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">{icon}</span>
				<h2 className="min-w-0 truncate text-sm font-semibold text-slate-900">{title}</h2>
			</header>
			<div className="min-h-0 flex-1">{children}</div>
		</section>
	);
}

function EmptyNote({ children }: { children: React.ReactNode }) {
	return <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-500">{children}</p>;
}

function MarkdownResult({ text }: { text: string }) {
	return (
		<Markdown animated={false} className="prose prose-slate prose-sm max-w-none break-words leading-5 prose-headings:my-3 prose-headings:text-sm prose-p:my-2 prose-p:text-xs prose-li:my-1 prose-li:text-xs prose-th:text-xs prose-td:text-xs prose-pre:overflow-x-auto prose-pre:rounded-lg prose-table:block prose-table:max-w-full prose-table:overflow-x-auto">
			{text}
		</Markdown>
	);
}

function FollowUpStatus({ status }: { status: '待确认' | '待补充' | '存在分歧' | '已核实' | null }) {
	if (!status) return null;
	const styles = status === '已核实'
		? 'bg-emerald-50 text-emerald-700'
		: 'bg-amber-50 text-amber-800';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${styles}`}>{status}</span>;
}

export function TaskS06Results({ run, onViewExecution, onViewFullResult }: TaskS06ResultsProps) {
	const result = React.useMemo(() => adaptS06OpinionRun(run), [run]);
	const categoryMax = Math.max(1, ...result.categories.map((item) => item.count ?? 0));

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">专家意见整理成果</h1>
					<p className="mt-1 text-xs text-slate-500">意见总览 · 分类分布 · 核心摘要 · 待确认事项</p>
				</div>
				<Button type="button" variant="outline" size="sm" onClick={onViewExecution}>
					<ArrowLeft />
					返回执行过程
				</Button>
			</header>

			{result.hasStructuredContent ? (
				<div className="grid min-w-0 gap-4 lg:grid-cols-2 lg:auto-rows-[320px]">
					<ResultCard
						icon={<ClipboardList className="size-4" aria-hidden="true" />}
						title="意见整理概览"
					>
						{result.moduleData.overview ? (
							<div className="space-y-3">
								{result.projectName && (
									<div className="min-w-0 rounded-xl bg-slate-50 px-3 py-2.5">
										<p className="text-[10px] text-slate-500">项目名称</p>
										<p className="mt-1 line-clamp-2 break-words text-xs font-medium leading-5 text-slate-800">{result.projectName}</p>
									</div>
								)}
								{result.stats.length > 0 && (
									<dl className="grid grid-cols-2 gap-2">
										{result.stats.map((stat) => (
											<div key={stat.label} className="min-w-0 rounded-xl border border-slate-100 bg-white px-3 py-2">
												<dt className="text-[10px] text-slate-500">{stat.label}</dt>
												<dd className="mt-0.5 truncate text-lg font-semibold leading-6 text-slate-900" title={stat.value}>{stat.value}</dd>
											</div>
										))}
									</dl>
								)}
							</div>
						) : <EmptyNote>本次结果没有可核对的意见统计或整理概览。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<Tags className="size-4" aria-hidden="true" />}
						title="专家意见分类"
					>
						{result.categories.length ? (
							<ul className="space-y-3">
								{result.categories.map((category) => (
									<li key={category.name} className="min-w-0">
										<div className="flex items-center justify-between gap-3">
											<p className="min-w-0 truncate text-xs font-medium text-slate-800">{category.name}</p>
											{category.count !== null && <span className="shrink-0 text-[11px] font-semibold tabular-nums text-slate-600">{category.count} 条</span>}
										</div>
										{category.count !== null ? (
											<div className="mt-1.5 h-1.5 rounded-full bg-slate-100">
												<div className="h-full rounded-full bg-blue-500" style={{ width: `${category.count === 0 ? 0 : Math.max(8, category.count / categoryMax * 100)}%` }} />
											</div>
										) : category.example && <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">{category.example}</p>}
									</li>
								))}
							</ul>
						) : <EmptyNote>本次结果未包含可识别的分类结果。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<MessageSquareText className="size-4" aria-hidden="true" />}
						title="核心意见摘要"
					>
						{result.coreOpinions.length ? (
							<ul className="space-y-3">
								{result.coreOpinions.map((item, index) => (
									<li key={`${item.title}-${index}`} className="flex min-w-0 gap-2.5">
										<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-blue-500" />
										<div className="min-w-0">
											<p className="text-[11px] font-semibold text-slate-800">{item.title}</p>
											<p className="mt-0.5 line-clamp-3 break-words text-xs leading-5 text-slate-600">{item.text}</p>
										</div>
									</li>
								))}
							</ul>
						) : <EmptyNote>未从本次结果中可靠提取核心意见摘要，完整原文请查看补充内容或任务结果。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<ListChecks className="size-4" aria-hidden="true" />}
						title="待确认问题与处理建议"
					>
						{result.followUps.length ? (
							<ul className="space-y-2.5">
								{result.followUps.map((item, index) => (
									<li key={`${item.title}-${index}`} className="flex min-w-0 items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
										<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
										<div className="min-w-0 flex-1">
											<div className="flex flex-wrap items-start justify-between gap-1.5">
												<p className="min-w-0 text-[11px] font-semibold leading-4 text-slate-800">{item.title}</p>
												<FollowUpStatus status={item.status} />
											</div>
											<p className="mt-0.5 line-clamp-2 break-words text-[10px] leading-4 text-slate-600">{item.detail}</p>
										</div>
									</li>
								))}
							</ul>
						) : <EmptyNote>本次结果未明确列出待确认问题或后续处理建议。</EmptyNote>}
					</ResultCard>
				</div>
			) : (
				<section className={`rounded-2xl border border-slate-200 bg-white p-4 ${TASK_RESULT_CARD_CLASS}`}>
					<div className="mb-3 flex items-center gap-2.5">
						<span className="flex size-8 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><FileText className="size-4" aria-hidden="true" /></span>
						<h2 className="text-sm font-semibold text-slate-900">专家意见整理完整结果</h2>
					</div>
					{result.markdown ? <MarkdownResult text={result.markdown} /> : <EmptyNote>本次运行没有可读取的 Markdown 结果，请通过原始结果或成果文件查看。</EmptyNote>}
				</section>
			)}

			{result.hasStructuredContent && result.markdownSupplement && (
				<details className={`rounded-2xl border border-slate-200 bg-white px-4 py-3 ${TASK_RESULT_CARD_CLASS}`}>
					<summary className="cursor-pointer text-xs font-medium text-slate-700">展开未结构化的 Markdown 业务内容</summary>
					<div className="mt-3 border-t border-slate-100 pt-3"><MarkdownResult text={result.markdownSupplement} /></div>
				</details>
			)}

			<section className={`rounded-2xl border border-slate-200 bg-slate-50/70 p-4 ${TASK_RESULT_CARD_CLASS}`}>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><FileText className="size-4 text-blue-600" aria-hidden="true" />成果文件与完整结果</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次专家意见整理的关键成果摘要，完整意见原文、分类记录、处理建议及详细依据请查看任务成果文件。</p>
					</div>
					{result.hasFullResult && (
						<Button type="button" variant="outline" size="sm" onClick={onViewFullResult}>
							<FileText />
							{result.artifacts.length ? '打开预览与下载' : '查看完整结果'}
						</Button>
					)}
				</div>
				{result.artifacts.length ? (
					<ul className="mt-3 grid gap-2 sm:grid-cols-2">
						{result.artifacts.map((artifact) => (
							<li key={artifact.id} className="flex min-w-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
								<FileText className="size-4 shrink-0 text-slate-400" aria-hidden="true" />
								<span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700" title={artifact.name}>{artifact.name}</span>
								<span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">{artifact.format.toUpperCase()}</span>
							</li>
						))}
					</ul>
				) : !result.hasFullResult ? (
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行没有可预览的完整结果或成果文件。</p>
				) : null}
			</section>
		</div>
	);
}
