import {
	ArrowLeft,
	CalendarDays,
	Check,
	CircleAlert,
	Clock3,
	FileText,
	ListChecks,
	MessageSquareText,
	UsersRound,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS05MeetingRun } from './task-s05-result-adapter';
import type { S05ActionStatus } from './task-s05-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS05ResultsProps {
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
		<section className={`flex min-h-[270px] min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 lg:h-[320px] lg:min-h-[320px] ${TASK_RESULT_CARD_CLASS}`}>
			<header className="mb-3 flex h-8 shrink-0 items-center gap-2.5">
				<span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">{icon}</span>
				<h2 className="min-w-0 truncate text-sm font-semibold text-slate-900">{title}</h2>
			</header>
			<div className="min-h-0 flex-1 overflow-hidden">{children}</div>
		</section>
	);
}

function EmptyNote({ children }: { children: React.ReactNode }) {
	return <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-500">{children}</p>;
}

function ActionStatus({ status }: { status: S05ActionStatus }) {
	if (!status) return null;
	const styles = status === 'complete'
		? 'bg-emerald-50 text-emerald-700'
		: status === 'incomplete'
			? 'bg-red-50 text-red-700'
			: 'bg-amber-50 text-amber-800';
	const label = status === 'complete' ? '已完成' : status === 'incomplete' ? '未完成' : '待处理';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${styles}`}>{label}</span>;
}

function Supplement({ title, text }: { title: string; text: string }) {
	return (
		<details className={`rounded-2xl border border-slate-200 bg-white px-4 py-3 ${TASK_RESULT_CARD_CLASS}`}>
			<summary className="cursor-pointer text-xs font-medium text-slate-700">查看未结构化的{title}原文</summary>
			<div className="mt-3 border-t border-slate-100 pt-3">
				<Markdown
					animated={false}
					className="prose prose-slate prose-sm max-w-none break-words leading-5 prose-headings:my-2 prose-headings:text-sm prose-p:my-2 prose-p:text-xs prose-li:my-1 prose-li:text-xs prose-th:text-xs prose-td:text-xs prose-table:w-full prose-table:table-fixed"
				>
					{text}
				</Markdown>
			</div>
		</details>
	);
}

export function TaskS05Results({ run, onViewExecution, onViewFullResult }: TaskS05ResultsProps) {
	const result = React.useMemo(() => adaptS05MeetingRun(run), [run]);
	const supplements = [
		{ key: 'overview', title: '会议执行概览', text: result.markdownSections.overview, hasData: result.overviewFields.length > 0 },
		{ key: 'minutes', title: '智能会议纪要', text: result.markdownSections.minutes, hasData: Boolean(result.minutesSummary || result.discussionPoints.length) },
		{ key: 'opinions', title: '专家观点与意见', text: result.markdownSections.opinions, hasData: result.opinions.length > 0 },
		{ key: 'outcomes', title: '会议结论与会后待办', text: result.markdownSections.outcomes, hasData: Boolean(result.conclusions.length || result.actions.length) },
	].filter((item): item is typeof item & { text: string } => Boolean(item.text) && !item.hasData);

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">会议召开成果</h1>
					<p className="mt-1 text-xs text-slate-500">会议概览 · 智能纪要 · 专家观点 · 会议结论与待办</p>
				</div>
				<Button type="button" variant="outline" size="sm" onClick={onViewExecution}>
					<ArrowLeft />
					返回执行过程
				</Button>
			</header>

			{result.hasStructuredContent ? (
				<>
					<div className="grid min-w-0 gap-4 lg:grid-cols-2">
						<ResultCard
							icon={<CalendarDays className="size-4" aria-hidden="true" />}
							title="会议执行概览"
						>
							{result.overviewFields.length ? (
								<>
									<dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
										{result.overviewFields.map((field) => (
											<div key={field.label} className="min-w-0">
												<dt className="text-[10px] text-slate-500">{field.label}</dt>
												<dd className="mt-0.5 line-clamp-2 break-words text-xs leading-5 text-slate-800">{field.value}</dd>
											</div>
										))}
									</dl>
									{result.transcriptSource && (
										<p className="mt-2 flex items-start gap-1.5 rounded-lg bg-slate-50 px-2 py-1.5 text-[10px] leading-4 text-slate-600">
											<FileText className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
											{result.transcriptSource}
										</p>
									)}
								</>
							) : <EmptyNote>本次结果未提取到可识别的会议基本信息。</EmptyNote>}
						</ResultCard>

						<ResultCard
							icon={<MessageSquareText className="size-4" aria-hidden="true" />}
							title="智能会议纪要"
						>
							{result.minutesDraftLabel && <span className="mb-2 inline-flex rounded-md bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-800">{result.minutesDraftLabel}</span>}
							{result.minutesSummary ? (
								<p className="rounded-xl bg-blue-50/60 px-3 py-2.5 text-xs leading-5 text-slate-700">{result.minutesSummary}</p>
							) : <EmptyNote>本次结果未提取到可确认的会议摘要。</EmptyNote>}
							{result.discussionPoints.length > 0 && (
								<div className="mt-3">
									<h3 className="mb-1.5 text-[11px] font-semibold text-slate-800">关键讨论要点</h3>
									<ul className="space-y-1.5">
										{result.discussionPoints.map((point, index) => (
											<li key={`${point}-${index}`} className="flex min-w-0 gap-2 text-xs leading-4 text-slate-700">
												<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-blue-500" />
												<span className="line-clamp-2 min-w-0">{point}</span>
											</li>
										))}
									</ul>
								</div>
							)}
						</ResultCard>

						<ResultCard
							icon={<UsersRound className="size-4" aria-hidden="true" />}
							title="专家观点与意见"
						>
							{result.opinions.length ? (
								<ul className="space-y-2">
									{result.opinions.map((opinion, index) => (
										<li key={`${opinion.speaker ?? 'meeting'}-${index}`} className="rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
											<div className="mb-1 flex flex-wrap items-center gap-1.5">
												<span className="text-[11px] font-semibold text-slate-800">{opinion.speaker ?? '会议观点摘要'}</span>
												{opinion.type && <span className="rounded-md bg-white px-1.5 py-0.5 text-[10px] text-slate-600">{opinion.type}</span>}
												{opinion.dissent && <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">存在不同意见</span>}
												{!opinion.speaker && <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">未标注发言人</span>}
											</div>
												<p className="line-clamp-2 break-words text-xs leading-4 text-slate-700">{opinion.text}</p>
										</li>
									))}
								</ul>
							) : <EmptyNote>本次结果未提取到可确认的专家或会议观点。</EmptyNote>}
						</ResultCard>

						<ResultCard
							icon={<ListChecks className="size-4" aria-hidden="true" />}
							title="会议结论与会后待办"
						>
							{result.conclusions.length > 0 && (
								<div>
									<h3 className="mb-1.5 text-[11px] font-semibold text-slate-800">会议结论 / 讨论建议</h3>
									<ul className="space-y-1.5">
										{result.conclusions.map((item, index) => (
											<li key={`${item.text}-${index}`} className="flex min-w-0 items-start gap-2">
												<span className={`mt-0.5 shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${item.kind === 'decision' ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-800'}`}>
													{item.kind === 'decision' ? '会议结论' : '讨论建议'}
												</span>
												<p className="line-clamp-2 min-w-0 text-xs leading-4 text-slate-700">{item.text}</p>
												</li>
										))}
									</ul>
								</div>
							)}
							{result.actions.length > 0 && (
								<div className={result.conclusions.length ? 'mt-2.5 border-t border-slate-100 pt-2.5' : ''}>
									<h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-800"><CalendarDays className="size-3.5 text-slate-400" aria-hidden="true" />会后待办</h3>
									<ul className="space-y-1.5">
										{result.actions.map((action, index) => (
											<li key={`${action.text}-${index}`} className="flex min-w-0 items-start gap-2">
												{action.status === 'complete' ? <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" aria-hidden="true" /> : action.status === 'incomplete' ? <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-red-600" aria-hidden="true" /> : <Clock3 className="mt-0.5 size-3.5 shrink-0 text-slate-400" aria-hidden="true" />}
												<div className="min-w-0 flex-1">
													<div className="flex min-w-0 items-start justify-between gap-2">
														<p className="line-clamp-2 min-w-0 text-xs leading-4 text-slate-700">{action.text}</p>
														<ActionStatus status={action.status} />
													</div>
													{(action.owner || action.deadline) && (
														<p className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] leading-4 text-slate-500">
															{action.owner && <span>负责人：{action.owner}</span>}
															{action.deadline && <span>截止：{action.deadline}</span>}
														</p>
														)}
													</div>
												</li>
										))}
									</ul>
								</div>
							)}
							{!result.conclusions.length && !result.actions.length && <EmptyNote>本次结果未识别到明确结论或会后待办。</EmptyNote>}
						</ResultCard>
					</div>

					{supplements.map((item) => <Supplement key={item.key} title={item.title} text={item.text} />)}
				</>
			) : (
				<section className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm ${TASK_RESULT_CARD_CLASS}`}>
					<div className="mb-3 flex items-center gap-2.5">
						<span className="flex size-8 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><FileText className="size-4" aria-hidden="true" /></span>
						<h2 className="text-sm font-semibold text-slate-900">会议召开完整结果</h2>
					</div>
					{result.markdown ? (
						<Markdown
							animated={false}
							className="prose prose-slate prose-sm max-w-none break-words leading-6 prose-headings:my-3 prose-headings:text-sm prose-p:my-2 prose-li:my-1 prose-table:w-full prose-table:table-fixed prose-th:break-words prose-td:break-words"
						>
							{result.markdown}
						</Markdown>
					) : <EmptyNote>本次运行没有可读取的会议业务结果或 Markdown 内容。</EmptyNote>}
				</section>
			)}

			<section className={`rounded-2xl border border-slate-200 bg-slate-50/70 p-4 ${TASK_RESULT_CARD_CLASS}`}>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
							<FileText className="size-4 text-blue-600" aria-hidden="true" />
							会议成果文件与完整结果
						</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次会议召开的关键成果摘要，完整会议纪要、发言内容、专家意见及会后事项请查看任务成果文件。</p>
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
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行未生成成果文件或可预览的完整结果。</p>
				) : null}
			</section>
		</div>
	);
}
