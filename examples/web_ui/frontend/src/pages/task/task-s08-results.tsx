import {
	Archive,
	ArrowLeft,
	Check,
	ClipboardCheck,
	FileText,
	FolderTree,
	TriangleAlert,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS08ArchiveRun } from './task-s08-result-adapter';
import type { S08CheckStatus } from './task-s08-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS08ResultsProps {
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

function IntegrityStatus({ status }: { status: S08CheckStatus }) {
	const style = status === '已核对'
		? 'bg-emerald-50 text-emerald-700'
		: status === '未检查'
			? 'bg-slate-100 text-slate-600'
			: 'bg-amber-50 text-amber-800';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${style}`}>{status}</span>;
}

export function TaskS08Results({ run, onViewExecution, onViewFullResult }: TaskS08ResultsProps) {
	const result = React.useMemo(() => adaptS08ArchiveRun(run), [run]);
	const hasCards = result.hasStructuredContent;

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">归档移交成果</h1>
					<p className="mt-1 text-xs text-slate-500">归档概览 · 分类目录 · 完整性核对 · 移交清单</p>
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
							icon={<Archive className="size-4" aria-hidden="true" />}
							title="归档成果概览"
						>
							<div className="space-y-3">
								{result.projectName && <h3 className="line-clamp-2 break-words text-sm font-semibold leading-5 text-slate-900">{result.projectName}</h3>}
								<div className="grid grid-cols-2 gap-3">
									{result.fileCount !== null && (
										<div className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2">
											<div className="text-2xl font-semibold leading-7 text-slate-900">{result.fileCount}</div>
											<div className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-slate-500">{result.fileCountLabel ?? '文件条目'}</div>
										</div>
									)}
									{result.directoryCount !== null && (
										<div className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2">
											<div className="text-2xl font-semibold leading-7 text-slate-900">{result.directoryCount}</div>
											<div className="mt-0.5 text-[10px] leading-4 text-slate-500">实际返回分类数</div>
										</div>
									)}
								</div>
								<dl className="grid grid-cols-2 gap-x-3 gap-y-2">
									{result.projectId && <div className="min-w-0"><dt className="text-[10px] text-slate-500">项目标识</dt><dd className="mt-0.5 line-clamp-2 break-all text-xs leading-4 text-slate-700">{result.projectId}</dd></div>}
									{result.archiveCategory && <div className="min-w-0"><dt className="text-[10px] text-slate-500">归档类别</dt><dd className="mt-0.5 line-clamp-2 break-words text-xs leading-4 text-slate-700">{result.archiveCategory}</dd></div>}
									{result.archiveScope && <div className="col-span-2 min-w-0"><dt className="text-[10px] text-slate-500">归档范围</dt><dd className="mt-0.5 line-clamp-2 break-words text-xs leading-4 text-slate-700">{result.archiveScope}</dd></div>}
								</dl>
								{result.statuses.length > 0 && <div className="flex flex-wrap gap-1.5">{result.statuses.slice(0, 4).map((status) => <span key={status} className="rounded-md border border-blue-100 bg-blue-50/70 px-2 py-1 text-[10px] font-medium text-blue-800">{status}</span>)}</div>}
								{result.fileContentRead === false && <p className="text-[10px] leading-4 text-slate-500">本次仅整理提交的元数据，未读取档案文件内容。</p>}
							</div>
						</ResultCard>
					)}

					{result.moduleData.directories && (
						<ResultCard
							icon={<FolderTree className="size-4" aria-hidden="true" />}
							title="智能归档目录"
						>
							<div className="space-y-2">
								{result.directories.slice(0, 3).map((directory) => (
									<div key={directory.name} className="flex min-w-0 items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
										<span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><FolderTree className="size-3.5" aria-hidden="true" /></span>
										<div className="min-w-0 flex-1">
											<div className="flex flex-wrap items-center justify-between gap-1.5">
												<p className="break-words text-xs font-semibold leading-4 text-slate-800">{directory.name}</p>
												{directory.count !== null && <span className="shrink-0 text-[10px] text-slate-500">{directory.count} 项</span>}
											</div>
											{directory.examples.length > 0 && <p className="mt-0.5 line-clamp-1 break-all text-[10px] leading-4 text-slate-500">示例：{directory.examples[0]}</p>}
											{directory.source === '用户提供分类' && <p className="mt-0.5 text-[10px] text-slate-500">分类来自用户提交的元数据</p>}
										</div>
									</div>
								))}
								{result.directories.length === 0 && <EmptyNote>本次结果返回了 {result.directoryCount} 个分类，但未包含可展示的目录名称。</EmptyNote>}
							</div>
							{result.directories.length > 3 && <p className="mt-1 text-[10px] text-slate-500">另有 {result.directories.length - 3} 个分类，请查看完整目录。</p>}
						</ResultCard>
					)}

					{result.moduleData.integrity && (
						<ResultCard
							icon={<ClipboardCheck className="size-4" aria-hidden="true" />}
							title="归档完整性检查"
						>
							<div className="space-y-1.5">
								{result.integrityItems.slice(0, 3).map((item, index) => (
									<div key={`${item.title}-${index}`} className="flex min-w-0 items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-1.5">
										{item.status === '已核对' ? <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" aria-hidden="true" /> : <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-amber-600" aria-hidden="true" />}
										<div className="min-w-0 flex-1">
											<div className="flex flex-wrap items-start justify-between gap-1.5">
												<p className="min-w-0 flex-1 break-words text-[11px] font-semibold leading-4 text-slate-800">{item.title}</p>
												<IntegrityStatus status={item.status} />
											</div>
											<p className="mt-0.5 line-clamp-2 break-words text-[10px] leading-4 text-slate-600">{item.detail}</p>
										</div>
									</div>
								))}
								{result.integrityItems.length > 3 && <p className="text-[10px] text-slate-500">另有 {result.integrityItems.length - 3} 项，请查看完整检查结果。</p>}
							</div>
						</ResultCard>
					)}

					{result.moduleData.handover && (
						<ResultCard
							icon={<FileText className="size-4" aria-hidden="true" />}
							title="档案移交清单"
						>
							<div className="space-y-3">
								{result.handoverStatus && (() => {
									const isComplete = result.handoverStatus === '档案移交已完成';
									const isPending = /待|等待/.test(result.handoverStatus);
									const badgeClass = isComplete
										? 'border-emerald-200 bg-emerald-50/60 text-emerald-700'
										: isPending
										? 'border-amber-200 bg-amber-50/60 text-amber-800'
										: 'border-blue-100 bg-blue-50/60 text-blue-700';
									return <span className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${badgeClass}`}>{isComplete ? <Check className="size-3.5" aria-hidden="true" /> : isPending ? <TriangleAlert className="size-3.5" aria-hidden="true" /> : <FileText className="size-3.5" aria-hidden="true" />}{result.handoverStatus}</span>;
								})()}
								{result.handoverName && <p className="line-clamp-2 break-words text-sm font-semibold leading-5 text-slate-900">{result.handoverName}</p>}
								<div className="grid grid-cols-2 gap-x-3 gap-y-2">
									{result.handoverCount !== null && <div><dt className="text-[10px] text-slate-500">清单材料数量</dt><dd className="mt-0.5 text-xs font-semibold text-slate-800">{result.handoverCount} 项</dd></div>}
									{result.recipient && <div className="min-w-0"><dt className="text-[10px] text-slate-500">接收单位</dt><dd className="mt-0.5 line-clamp-2 break-words text-xs leading-4 text-slate-700">{result.recipient}</dd></div>}
									{result.handoverMethod && <div className="min-w-0"><dt className="text-[10px] text-slate-500">交接方式</dt><dd className="mt-0.5 line-clamp-2 break-words text-xs leading-4 text-slate-700">{result.handoverMethod}</dd></div>}
								</div>
								{result.handoverSummary && <p className="line-clamp-3 break-words text-xs leading-5 text-slate-600">{result.handoverSummary}</p>}
								{!result.handoverStatus && <EmptyNote>本次结果未返回实际移交状态。</EmptyNote>}
							</div>
						</ResultCard>
					)}
				</div>
			) : (
				<section className={`rounded-2xl border border-slate-200 bg-white p-4 ${TASK_RESULT_CARD_CLASS}`}>
					<div className="mb-3 flex items-center gap-2.5">
						<span className="flex size-8 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><Archive className="size-4" aria-hidden="true" /></span>
						<h2 className="text-sm font-semibold text-slate-900">归档移交完整结果</h2>
					</div>
					{result.markdown ? <MarkdownResult text={result.markdown} /> : <EmptyNote>本次运行没有可读取的 Markdown 结果，请查看现有成果文件或原始运行记录。</EmptyNote>}
				</section>
			)}

			{hasCards && result.markdownSupplement && (
				<details className={`rounded-2xl border border-slate-200 bg-white px-4 py-3 ${TASK_RESULT_CARD_CLASS}`}>
					<summary className="cursor-pointer text-xs font-medium text-slate-700">展开其他 Markdown 结果</summary>
					<div className="mt-3 border-t border-slate-100 pt-3"><MarkdownResult text={result.markdownSupplement} /></div>
				</details>
			)}

			<section className={`rounded-2xl border border-slate-200 bg-slate-50/70 p-4 ${TASK_RESULT_CARD_CLASS}`}>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><FileText className="size-4 text-blue-600" aria-hidden="true" />归档成果文件与完整结果</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次归档移交的关键成果摘要，完整档案目录、材料清单、完整性检查记录及移交说明请查看任务成果文件。</p>
					</div>
					{result.hasFullResult && (
						<Button type="button" variant="outline" size="sm" onClick={onViewFullResult}>
							<FileText />
							{result.artifacts.length ? '打开预览与下载' : '查看完整结果'}
						</Button>
					)}
				</div>
				{result.artifacts.length > 0 ? (
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
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行没有可预览的归档文件或完整结果。</p>
				) : null}
			</section>
		</div>
	);
}
