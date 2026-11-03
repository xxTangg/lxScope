import {
	ArrowLeft,
	Check,
	ClipboardList,
	FileCheck2,
	FileText,
	ReceiptText,
	TriangleAlert,
	WalletCards,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS09ReimbursementRun } from './task-s09-result-adapter';
import type { S09CheckItem, S09TodoItem } from './task-s09-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS09ResultsProps {
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

function CheckStatus({ item }: { item: S09CheckItem }) {
	const style = item.status === '已完成'
		? 'bg-emerald-50 text-emerald-700'
		: item.status === '发现异常'
			? 'bg-red-50 text-red-700'
			: item.status === '未执行'
				? 'bg-slate-100 text-slate-600'
				: 'bg-amber-50 text-amber-800';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${style}`}>{item.status}</span>;
}

function TodoStatus({ item }: { item: S09TodoItem }) {
	const style = item.status === '已整理'
		? 'bg-emerald-50 text-emerald-700'
		: item.status === '未执行'
			? 'bg-slate-100 text-slate-600'
			: 'bg-amber-50 text-amber-800';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${style}`}>{item.status}</span>;
}

export function TaskS09Results({ run, onViewExecution, onViewFullResult }: TaskS09ResultsProps) {
	const result = React.useMemo(() => adaptS09ReimbursementRun(run), [run]);
	const hasCards = result.hasStructuredContent;
	const maximumCategoryAmount = Math.max(0, ...result.expenseCategories.map((item) => item.amountMinor ?? 0));

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">报销结算成果</h1>
					<p className="mt-1 text-xs text-slate-500">费用汇总 · 分类明细 · 票据核对 · 报销材料与待办</p>
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
							icon={<WalletCards className="size-4" aria-hidden="true" />}
							title="报销结算概览"
						>
							<div className="space-y-3">
								{result.amount && (
									<div className="rounded-xl border border-blue-100 bg-blue-50/50 px-3 py-2.5">
										<p className="text-[10px] text-slate-500">{result.amountLabel}</p>
										<p className="mt-1 break-words text-2xl font-semibold tracking-tight text-slate-900">{result.amount}</p>
									</div>
								)}
								<dl className="grid grid-cols-2 gap-x-3 gap-y-2">
									{result.projectId && <div className="min-w-0"><dt className="text-[10px] text-slate-500">项目标识</dt><dd className="mt-0.5 break-words text-xs font-medium leading-4 text-slate-800">{result.projectId}</dd></div>}
									{result.expenseCategory && <div className="min-w-0"><dt className="text-[10px] text-slate-500">报销类别</dt><dd className="mt-0.5 break-words text-xs font-medium leading-4 text-slate-800">{result.expenseCategory}</dd></div>}
									{result.accountingPeriod && <div className="min-w-0"><dt className="text-[10px] text-slate-500">会计期间</dt><dd className="mt-0.5 break-words text-xs font-medium leading-4 text-slate-800">{result.accountingPeriod}</dd></div>}
									{result.invoiceCount !== null && <div><dt className="text-[10px] text-slate-500">票据记录</dt><dd className="mt-0.5 text-xs font-semibold text-slate-800">{result.invoiceCount} 笔</dd></div>}
								</dl>
								{result.duplicateCount !== null && result.duplicateCount > 0 && <p className="flex items-start gap-1.5 text-[11px] leading-4 text-amber-800"><TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />含 {result.duplicateCount} 条重复标记，金额不可视为可报销金额。</p>}
							</div>
						</ResultCard>
					)}

					{result.moduleData.expenses && (
						<ResultCard
							icon={<ReceiptText className="size-4" aria-hidden="true" />}
							title="费用明细与分类"
						>
							<div className="space-y-3">
								{result.expenseCategories.some((item) => item.amount) ? (
									<>
										<p className="text-[10px] text-slate-500">按费用类别汇总</p>
										<div className="space-y-3">
											{result.expenseCategories.slice(0, 4).map((item) => (
												<div key={item.name} className="min-w-0">
													<div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
														<span className="min-w-0 break-words font-medium text-slate-700">{item.name} <span className="font-normal text-slate-400">{item.count} 笔</span></span>
														<span className="shrink-0 font-semibold tabular-nums text-slate-900">{item.amount}</span>
													</div>
													<div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
														<div className="h-full rounded-full bg-blue-500" style={{ width: `${maximumCategoryAmount > 0 ? Math.max(4, Math.round(((item.amountMinor ?? 0) / maximumCategoryAmount) * 100)) : 0}%` }} />
													</div>
												</div>
											))}
										</div>
									</>
								) : (
									<>
										<p className="text-[10px] text-slate-500">票据金额或重复状态不足以安全汇总，以下仅列类别与数量。</p>
										<ul className="space-y-2">
											{result.expenseCategories.slice(0, 4).map((item) => (
												<li key={item.name} className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-2 text-xs">
													<span className="min-w-0 break-words font-medium text-slate-700">{item.name}</span><span className="shrink-0 text-slate-500">{item.count} 笔</span>
												</li>
											))}
										</ul>
									</>
								)}
								{result.expenseCategories.length > 4 && <p className="text-[10px] text-slate-500">另有 {result.expenseCategories.length - 4} 类，请查看完整结果。</p>}
							</div>
						</ResultCard>
					)}

					{result.moduleData.checks && (
						<ResultCard
							icon={<FileCheck2 className="size-4" aria-hidden="true" />}
							title="票据与凭证核对"
						>
							<div className="space-y-2">
								{result.checkItems.map((item, index) => (
									<div key={`${item.title}-${index}`} className="flex min-w-0 items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
										{item.status === '已完成' ? <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" aria-hidden="true" /> : <TriangleAlert className={`mt-0.5 size-3.5 shrink-0 ${item.status === '发现异常' ? 'text-red-600' : 'text-amber-600'}`} aria-hidden="true" />}
										<div className="min-w-0 flex-1">
											<div className="flex flex-wrap items-start justify-between gap-1.5"><p className="min-w-0 flex-1 break-words text-[11px] font-semibold leading-4 text-slate-800">{item.title}</p><CheckStatus item={item} /></div>
											<p className="mt-0.5 line-clamp-2 break-words text-[10px] leading-4 text-slate-600">{item.detail}</p>
										</div>
									</div>
								))}
							</div>
						</ResultCard>
					)}

					{result.moduleData.materials && (
						<ResultCard
							icon={<ClipboardList className="size-4" aria-hidden="true" />}
							title="报销材料与结算待办"
						>
							<div className="space-y-2">
								{result.draftOutputAvailable && <div className="flex items-start gap-2 rounded-xl border border-blue-100 bg-blue-50/50 px-2.5 py-2"><FileText className="mt-0.5 size-3.5 shrink-0 text-blue-600" aria-hidden="true" /><div className="min-w-0"><p className="text-[11px] font-semibold leading-4 text-slate-800">报销草稿内容已整理</p><p className="mt-0.5 text-[10px] leading-4 text-slate-600">仍需按财务流程复核。</p></div></div>}
								{result.todoItems.map((item, index) => (
									<div key={`${item.title}-${index}`} className="flex min-w-0 items-start justify-between gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
										<div className="min-w-0"><p className="line-clamp-2 break-words text-[11px] font-semibold leading-4 text-slate-800">{item.title}</p><p className="mt-0.5 line-clamp-2 break-words text-[10px] leading-4 text-slate-600">{item.detail}</p></div>
										<TodoStatus item={item} />
									</div>
								))}
								{!result.draftOutputAvailable && !result.todoItems.length && <EmptyNote>本次结果未返回报销材料或待办事项。</EmptyNote>}
							</div>
						</ResultCard>
					)}
				</div>
			) : (
				<section className={`rounded-2xl border border-slate-200 bg-white p-4 ${TASK_RESULT_CARD_CLASS}`}>
					<div className="mb-3 flex items-center gap-2.5">
						<span className="flex size-8 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><FileText className="size-4" aria-hidden="true" /></span>
						<h2 className="text-sm font-semibold text-slate-900">报销结算完整结果</h2>
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
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><FileText className="size-4 text-blue-600" aria-hidden="true" />报销材料与完整结果</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次报销结算的关键成果摘要，完整费用明细、凭证核对记录、报销材料及待办说明请查看任务成果文件。</p>
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
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行没有可预览的报销文件或完整结果。</p>
				) : null}
			</section>
		</div>
	);
}
