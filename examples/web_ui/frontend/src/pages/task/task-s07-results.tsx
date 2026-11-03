import {
	ArrowLeft,
	BadgeCheck,
	BookOpenText,
	FileText,
	ListChecks,
	TriangleAlert,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS07ReportRun } from './task-s07-result-adapter';
import type { S07QualityItem, S07ReportIssue } from './task-s07-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS07ResultsProps {
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
		<section className={`flex h-full min-h-[270px] min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 lg:h-[290px] lg:min-h-[290px] ${TASK_RESULT_CARD_CLASS}`}>
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

function QualityStatus({ item }: { item: S07QualityItem }) {
	const success = item.status === '通过';
	const concern = item.status === '发现问题' || item.status === '未通过' || item.status === '待人工复核' || item.status === '建议复核';
	const style = success
		? 'bg-emerald-50 text-emerald-700'
		: concern
			? 'bg-amber-50 text-amber-800'
			: 'bg-slate-100 text-slate-600';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${style}`}>{item.status}</span>;
}

function IssueMeta({ item }: { item: S07ReportIssue }) {
	return (
		<div className="mt-1 flex flex-wrap gap-1.5">
			<span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${item.kind === 'AI建议' ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-800'}`}>{item.kind}</span>
			{item.priority && <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{item.priority}</span>}
			{item.status && <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{item.status}</span>}
		</div>
	);
}

export function TaskS07Results({ run, onViewExecution, onViewFullResult }: TaskS07ResultsProps) {
	const result = React.useMemo(() => adaptS07ReportRun(run), [run]);
	const hasCards = result.hasStructuredContent;

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">报告生成成果</h1>
					<p className="mt-1 text-xs text-slate-500">报告概览 · 章节结构 · 质量检查 · 问题与修改建议</p>
				</div>
				<Button type="button" variant="outline" size="sm" onClick={onViewExecution}>
					<ArrowLeft />
					返回执行过程
				</Button>
			</header>

			{hasCards ? (
				<div className="grid min-w-0 gap-4 lg:grid-cols-2 lg:auto-rows-[290px]">
					{result.moduleData.overview && (
						<ResultCard
							icon={<FileText className="size-4" aria-hidden="true" />}
							title="报告生成概览"
						>
							<div className="space-y-2.5">
								{result.reportName && <h3 className="line-clamp-2 break-words text-sm font-semibold leading-5 text-slate-900">{result.reportName}</h3>}
								<dl className="grid grid-cols-2 gap-x-3 gap-y-2">
									{result.projectName && <div className="min-w-0"><dt className="text-[10px] text-slate-500">所属项目</dt><dd className="mt-0.5 line-clamp-2 break-words text-xs leading-4 text-slate-700">{result.projectName}</dd></div>}
									{result.reportType && <div className="min-w-0"><dt className="text-[10px] text-slate-500">报告类型</dt><dd className="mt-0.5 line-clamp-2 break-words text-xs leading-4 text-slate-700">{result.reportType}</dd></div>}
									{result.chapterCount !== null && <div><dt className="text-[10px] text-slate-500">实际生成章节</dt><dd className="mt-0.5 text-xs font-medium text-slate-800">{result.chapterCount} 章</dd></div>}
									{result.formats.length > 0 && <div><dt className="text-[10px] text-slate-500">成果文件格式</dt><dd className="mt-0.5 text-xs font-medium text-slate-800">{result.formats.join('、')}</dd></div>}
								</dl>
								{result.statuses.length > 0 && (
									<div className="flex flex-wrap gap-1.5 pt-0.5">
										{result.statuses.map((status) => <span key={status} className="rounded-md border border-blue-100 bg-blue-50/70 px-2 py-1 text-[10px] font-medium text-blue-800">{status}</span>)}
									</div>
								)}
							</div>
						</ResultCard>
					)}

					{result.moduleData.chapters && (
						<ResultCard
							icon={<BookOpenText className="size-4" aria-hidden="true" />}
							title="报告章节结构"
						>
							<ol className="space-y-1.5">
								{result.chapters.map((chapter, index) => (
									<li key={`${chapter.title}-${index}`} className="flex min-w-0 items-start gap-2 rounded-lg px-1 py-0.5">
										<span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-slate-100 text-[10px] font-semibold text-slate-600">{index + 1}</span>
										<div className="min-w-0 flex-1">
											{chapter.summary ? (
												<details className="group">
													<summary className="cursor-pointer line-clamp-2 break-words text-xs font-medium leading-4 text-slate-800">{chapter.title}</summary>
													<p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">{chapter.summary}</p>
												</details>
											) : <p className="line-clamp-2 break-words text-xs font-medium leading-4 text-slate-800">{chapter.title}</p>}
										</div>
									</li>
								))}
							</ol>
							{result.remainingChapters > 0 && <p className="mt-2 text-[10px] text-slate-500">另有 {result.remainingChapters} 章，请查看完整报告。</p>}
						</ResultCard>
					)}

					{result.moduleData.quality && (
						<ResultCard
							icon={<BadgeCheck className="size-4" aria-hidden="true" />}
							title="报告质量检查"
						>
							<ul className="space-y-1.5">
								{result.qualityItems.slice(0, 3).map((item, index) => (
									<li key={`${item.title}-${index}`} className="flex min-w-0 items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-1.5">
										{item.status === '发现问题' || item.status === '未通过'
											? <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-amber-600" aria-hidden="true" />
											: <ListChecks className="mt-0.5 size-3.5 shrink-0 text-blue-600" aria-hidden="true" />}
										<div className="min-w-0 flex-1">
											<div className="flex flex-wrap items-start justify-between gap-1.5">
												<p className="min-w-0 flex-1 line-clamp-1 text-[11px] font-semibold leading-4 text-slate-800">{item.title}</p>
												<div className="flex shrink-0 items-center gap-1.5">
													<span className="text-[10px] text-slate-500">{item.source}</span>
													<QualityStatus item={item} />
												</div>
											</div>
											<p className="mt-0.5 line-clamp-1 break-words text-[10px] leading-4 text-slate-700">{item.detail}</p>
										</div>
									</li>
								))}
							</ul>
							{result.qualityItems.length > 3 && <p className="mt-1 text-[10px] text-slate-500">另有 {result.qualityItems.length - 3} 项检查，请查看完整结果。</p>}
						</ResultCard>
					)}

					{result.moduleData.issues && (
						<ResultCard
							icon={<TriangleAlert className="size-4" aria-hidden="true" />}
							title="问题清单与修改建议"
						>
							<ul className="space-y-2">
								{result.issues.slice(0, 3).map((item, index) => (
									<li key={`${item.title}-${index}`} className="min-w-0 border-b border-slate-100 pb-2 last:border-0">
										<p className="line-clamp-2 break-words text-[11px] font-semibold leading-4 text-slate-800">{item.title}</p>
										<IssueMeta item={item} />
										<p className="mt-1 line-clamp-2 break-words text-[10px] leading-4 text-slate-600">{item.detail}</p>
									</li>
								))}
							</ul>
							{result.issues.length > 3 && <p className="mt-1 text-[10px] text-slate-500">另有 {result.issues.length - 3} 项，请查看完整结果。</p>}
						</ResultCard>
					)}
				</div>
			) : (
				<section className={`rounded-2xl border border-slate-200 bg-white p-4 ${TASK_RESULT_CARD_CLASS}`}>
					<div className="mb-3 flex items-center gap-2.5">
						<span className="flex size-8 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><FileText className="size-4" aria-hidden="true" /></span>
						<h2 className="text-sm font-semibold text-slate-900">报告完整结果</h2>
					</div>
					{result.markdown ? <MarkdownResult text={result.markdown} /> : <EmptyNote>本次运行没有可读取的 Markdown 报告，请查看现有成果文件或原始运行结果。</EmptyNote>}
				</section>
			)}

			{hasCards && result.markdownSupplement && (
				<details className={`rounded-2xl border border-slate-200 bg-white px-4 py-3 ${TASK_RESULT_CARD_CLASS}`}>
					<summary className="cursor-pointer text-xs font-medium text-slate-700">展开未结构化的 Markdown 内容</summary>
					<div className="mt-3 border-t border-slate-100 pt-3"><MarkdownResult text={result.markdownSupplement} /></div>
				</details>
			)}

			<section className={`rounded-2xl border border-slate-200 bg-slate-50/70 p-4 ${TASK_RESULT_CARD_CLASS}`}>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><FileText className="size-4 text-blue-600" aria-hidden="true" />报告文件与完整结果</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次智能报告生成的关键成果摘要，完整章节正文、质量检查意见及修改建议请查看成果文件。</p>
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
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行没有可预览的报告文件或完整结果。</p>
				) : null}
			</section>
		</div>
	);
}
