import {
	ArrowLeft,
	CalendarDays,
	Check,
	CircleAlert,
	ClipboardList,
	Coins,
	FileText,
	MapPin,
	UsersRound,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS04MeetingPreparationRun } from './task-s04-result-adapter';
import type { S04TodoItem } from './task-s04-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS04ResultsProps {
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
		<section className={`flex min-h-[260px] min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 lg:h-[280px] lg:min-h-[280px] ${TASK_RESULT_CARD_CLASS}`}>
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

function MarkdownContent({ text }: { text: string }) {
	return (
		<div className="max-h-[175px] overflow-hidden break-words">
			<Markdown
				animated={false}
				className="prose prose-slate prose-sm max-w-none break-words leading-5 prose-headings:my-1.5 prose-headings:text-[11px] prose-p:my-1.5 prose-p:text-[11px] prose-li:my-0.5 prose-li:text-[11px] prose-th:break-words prose-td:break-words prose-table:w-full prose-table:table-fixed"
			>
				{text}
			</Markdown>
		</div>
	);
}

function TodoIcon({ status }: { status: S04TodoItem['status'] }) {
	if (status === 'complete') return <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden="true" />;
	if (status === 'incomplete') return <CircleAlert className="mt-0.5 size-4 shrink-0 text-red-600" aria-hidden="true" />;
	return <CalendarDays className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden="true" />;
}

function TodoStatus({ status }: { status: S04TodoItem['status'] }) {
	const styles = status === 'complete'
		? 'bg-emerald-50 text-emerald-700'
		: status === 'incomplete'
			? 'bg-red-50 text-red-700'
			: 'bg-amber-50 text-amber-800';
	const label = status === 'complete' ? '已完成' : status === 'incomplete' ? '未完成' : '待确认';
	return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${styles}`}>{label}</span>;
}

export function TaskS04Results({ run, onViewExecution, onViewFullResult }: TaskS04ResultsProps) {
	const result = React.useMemo(() => adaptS04MeetingPreparationRun(run), [run]);
	const artifacts = result.artifacts;

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">会前准备成果</h1>
					<p className="mt-1 text-xs text-slate-500">会议安排 · 会前材料 · 专家费用 · 准备待办</p>
				</div>
				<Button type="button" variant="outline" size="sm" onClick={onViewExecution}>
					<ArrowLeft />
					返回执行过程
				</Button>
			</header>

			{result.hasStructuredContent ? (
				<div className="grid min-w-0 gap-4 lg:grid-cols-2">
					<ResultCard
						icon={<CalendarDays className="size-4" aria-hidden="true" />}
						title="会议基本信息"
					>
						{result.meetingFields.length ? (
							<dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
								{result.meetingFields.map((field) => (
									<div key={field.label} className="min-w-0">
										<dt className="text-[10px] text-slate-500">{field.label}</dt>
										<dd className="mt-0.5 line-clamp-2 break-words text-xs leading-5 text-slate-800">{field.value}</dd>
									</div>
								))}
							</dl>
						) : result.markdownSections.meeting ? (
							<MarkdownContent text={result.markdownSections.meeting} />
						) : <EmptyNote>本次结果未提取到可识别的会议信息。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<ClipboardList className="size-4" aria-hidden="true" />}
						title="会前材料清单"
					>
						{result.materials.length ? (
							<ul className="space-y-2">
								{result.materials.map((item) => (
									<li key={`${item.name}-${item.format ?? ''}`} className="flex min-w-0 items-start gap-2.5 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
										<FileText className="mt-0.5 size-4 shrink-0 text-blue-600" aria-hidden="true" />
										<div className="min-w-0 flex-1">
											<p className="truncate text-xs font-medium text-slate-800" title={item.name}>{item.name}</p>
											<p className="mt-0.5 line-clamp-1 text-[10px] leading-4 text-slate-500">{item.detail ?? (item.status === 'file' ? '本次运行已生成文件' : '内容已整理')}</p>
										</div>
										{item.format ? (
											<span className="shrink-0 rounded-md bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-600">{item.format}</span>
										) : (
											<span className="shrink-0 rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">内容已整理</span>
										)}
									</li>
								))}
							</ul>
						) : result.markdownSections.materials ? (
							<MarkdownContent text={result.markdownSections.materials} />
						) : <EmptyNote>本次结果没有可确认的材料清单。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<Coins className="size-4" aria-hidden="true" />}
						title="专家费用预算"
					>
						{result.budgetTotal || result.budgetLines.length || result.participantCount ? (
							<div className="space-y-3">
								<div className="flex flex-wrap items-end justify-between gap-2">
									{result.budgetTotal ? (
										<div>
											<p className="text-[10px] text-slate-500">费用合计 · 预算草稿</p>
											<p className="mt-0.5 text-2xl font-semibold tracking-tight text-slate-900">{result.budgetTotal}</p>
										</div>
									) : <p className="text-xs text-slate-500">本次结果未返回费用合计。</p>}
									{result.participantCount && (
										<div className="flex items-center gap-1.5 rounded-lg bg-slate-50 px-2 py-1.5 text-xs text-slate-600">
											<UsersRound className="size-3.5" aria-hidden="true" />
											{result.participantCount} 人
										</div>
									)}
								</div>
								{result.budgetLines.length > 0 && (
									<ul className="divide-y divide-slate-100 rounded-lg border border-slate-100 px-2.5">
										{result.budgetLines.map((line, index) => (
											<li key={`${line.label}-${index}`} className="flex items-center justify-between gap-3 py-1.5 text-xs">
												<div className="min-w-0">
													<p className="truncate font-medium text-slate-700" title={line.label}>{line.label}</p>
													{line.calculation && <p className="text-[10px] text-slate-500">人数 × 单价：{line.calculation}</p>}
												</div>
												{line.amount && <span className="shrink-0 font-medium tabular-nums text-slate-800">{line.amount}</span>}
												</li>
										))}
									</ul>
								)}
							</div>
						) : result.markdownSections.budget ? (
							<MarkdownContent text={result.markdownSections.budget} />
						) : <EmptyNote>本次结果未返回可确认的费用金额。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<MapPin className="size-4" aria-hidden="true" />}
						title="会前准备与待办"
					>
						{result.todos.length ? (
							<ul className="space-y-2">
								{result.todos.map((item, index) => (
									<li key={`${item.text}-${index}`} className="flex min-w-0 items-start gap-2.5">
										<TodoIcon status={item.status} />
										<p className="min-w-0 flex-1 line-clamp-2 text-xs leading-5 text-slate-700">{item.text}</p>
										<TodoStatus status={item.status} />
									</li>
								))}
							</ul>
						) : result.markdownSections.todos ? (
							<MarkdownContent text={result.markdownSections.todos} />
						) : <EmptyNote>本次结果未识别到明确的准备待办事项。</EmptyNote>}
					</ResultCard>
				</div>
			) : (
				<section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
					<div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
						<FileText className="size-4 text-blue-600" aria-hidden="true" />
						会前准备结果
					</div>
					{result.markdown ? (
						<Markdown
							animated={false}
							className="prose prose-slate prose-sm max-w-none break-words leading-6 prose-headings:my-3 prose-p:my-2 prose-li:my-1 prose-table:w-full prose-table:table-fixed prose-th:break-words prose-td:break-words"
						>
							{result.markdown}
						</Markdown>
					) : <EmptyNote>本次运行没有可结构化展示的会前准备结果或 Markdown 内容。</EmptyNote>}
				</section>
			)}

			<section className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
							<FileText className="size-4 text-blue-600" aria-hidden="true" />
							完整会前准备成果
						</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次会前准备的关键成果摘要，完整会议材料、费用明细及详细内容请查看任务成果文件。</p>
					</div>
					{result.hasFullResult && (
						<Button type="button" variant="outline" size="sm" onClick={onViewFullResult}>
							<FileText />
							{artifacts.length ? '打开预览与下载' : '查看完整结果'}
						</Button>
					)}
				</div>
				{artifacts.length ? (
					<ul className="mt-3 grid gap-2 sm:grid-cols-2">
						{artifacts.map((artifact) => (
							<li key={artifact.id} className="flex min-w-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
								<FileText className="size-4 shrink-0 text-slate-400" aria-hidden="true" />
								<span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700" title={artifact.name}>{artifact.name}</span>
								<span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
									{artifact.format.toUpperCase()}
								</span>
							</li>
						))}
					</ul>
				) : (
					<p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行未生成成果文件。</p>
				)}
			</section>
		</div>
	);
}
