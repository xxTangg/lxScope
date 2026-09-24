import { FileText, Plus, Search, Trash2 } from 'lucide-react';
import * as React from 'react';

import type { TaskListFilter } from './task-view-model';
import { formatTaskDate, getTaskStatusLabel, isTaskInFilter, isTaskRunning } from './task-view-model';
import type { TaskRecord, TaskRunRecord } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface TaskSidebarProps {
	tasks: TaskRecord[];
	selectedTaskId: string | null;
	run: TaskRunRecord | null;
	width?: number;
	onSelect: (task: TaskRecord) => void;
	onCreate: () => void;
	onDelete: (task: TaskRecord) => void;
	disabled?: boolean;
}

const FILTERS: Array<{ id: TaskListFilter; label: string }> = [
	{ id: 'all', label: '全部' },
	{ id: 'running', label: '进行中' },
	{ id: 'completed', label: '已完成' },
	{ id: 'cancelled', label: '已取消' },
];

function statusClass(task: TaskRecord): string {
	if (task.generation_status === 'failed' || task.last_run_status === 'failed' || task.last_run_status === 'timed_out') {
		return 'border-red-200 bg-red-50 text-red-700';
	}
	if (task.last_run_status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (isTaskRunning(task)) return 'border-blue-200 bg-blue-50 text-blue-700';
	if (task.last_run_status === 'canceled') return 'border-slate-200 bg-slate-100 text-slate-600';
	return 'border-slate-200 bg-slate-50 text-slate-600';
}

export function TaskSidebar({
	tasks,
	selectedTaskId,
	run,
	width = 280,
	onSelect,
	onCreate,
	onDelete,
	disabled = false,
}: TaskSidebarProps) {
	const [filter, setFilter] = React.useState<TaskListFilter>('all');
	const [query, setQuery] = React.useState('');
	const counts = React.useMemo(
		() => ({
			all: tasks.length,
			running: tasks.filter((task) => isTaskInFilter(task, 'running')).length,
			completed: tasks.filter((task) => isTaskInFilter(task, 'completed')).length,
			cancelled: tasks.filter((task) => isTaskInFilter(task, 'cancelled')).length,
		}),
		[tasks],
	);
	const visibleTasks = React.useMemo(() => {
		const normalizedQuery = query.trim().toLocaleLowerCase();
		return tasks.filter((task) => {
			if (!isTaskInFilter(task, filter)) return false;
			if (!normalizedQuery) return true;
			return (task.title + ' ' + task.goal).toLocaleLowerCase().includes(normalizedQuery);
		});
	}, [filter, query, tasks]);

	return (
		<aside
			className="flex min-w-[220px] max-w-[420px] shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04),0_4px_12px_rgba(16,24,40,0.05)]"
			style={{ width: width + 'px' }}
		>
			<div className="flex items-center justify-between gap-3 px-4 pb-3 pt-4">
				<div>
					<div className="text-base font-semibold text-slate-900">我的任务</div>
					<div className="mt-1 text-[11px] text-slate-500">{tasks.length} 个任务</div>
				</div>
				<Button
					size="sm"
					variant="outline"
					onClick={onCreate}
					disabled={disabled}
					className="h-8 shrink-0 border-slate-200 px-2.5 text-xs"
				>
					<Plus className="size-3.5" />
					新建任务
				</Button>
			</div>

			<div className="grid grid-cols-4 border-b border-slate-100 px-3">
				{FILTERS.map((item) => (
					<button
						key={item.id}
						type="button"
						onClick={() => setFilter(item.id)}
						aria-pressed={filter === item.id}
						className={'relative flex min-w-0 items-center justify-center gap-1 px-1 py-2.5 text-[11px] transition-colors ' +
							(filter === item.id ? 'font-semibold text-blue-700' : 'text-slate-500 hover:text-slate-800')}
					>
						<span className="truncate">{item.label}</span>
						<span className="text-[10px] text-slate-400">{counts[item.id]}</span>
						{filter === item.id && <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-blue-600" />}
					</button>
				))}
			</div>

			<div className="px-3 py-3">
				<div className="relative">
					<Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
					<Input
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						placeholder="搜索任务名称或描述..."
						aria-label="搜索任务名称或描述"
						className="h-9 border-slate-200 bg-slate-50 pl-9 text-xs shadow-none focus-visible:bg-white"
					/>
				</div>
			</div>

			<div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 pb-3">
				{visibleTasks.length === 0 ? (
					<div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">
						<FileText className="mx-auto mb-3 size-5 text-slate-400" />
						{tasks.length === 0 ? '还没有任务' : '没有匹配的任务'}
						{tasks.length === 0 && <div className="mt-1 text-xs">点击“新建任务”开始规划</div>}
					</div>
				) : (
					visibleTasks.map((task) => {
						const selected = task.id === selectedTaskId;
						const matchingRun = run?.task_id === task.id ? run : null;
						return (
							<div
								key={task.id}
								className={'group relative rounded-xl border p-3 transition-colors ' +
									(selected
										? 'border-blue-300 bg-blue-50/70 shadow-sm'
										: 'border-slate-200/80 bg-white hover:border-blue-200 hover:bg-slate-50/70')}
							>
								<button
									type="button"
									className="w-full text-left"
									onClick={() => onSelect(task)}
									disabled={disabled}
								>
									<div className="flex items-start gap-2.5 pr-5">
										<div className={'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg ' +
											(selected ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500')}>
											<FileText className="size-3.5" />
										</div>
										<div className="min-w-0 flex-1">
											<div className="truncate text-sm font-semibold text-slate-800">{task.title}</div>
											<div className="mt-1 line-clamp-2 text-[11px] leading-4 text-slate-500">
												{task.goal || '未填写任务目标'}
											</div>
										</div>
									</div>
									<div className="mt-3 flex items-center justify-between gap-2">
										<Badge variant="outline" className={'h-5 border px-2 text-[10px] font-medium ' + statusClass(task)}>
											{getTaskStatusLabel(task)}
										</Badge>
										<span className="truncate text-[10px] text-slate-500">
											{task.nodes.length} 个节点 · {formatTaskDate(task.updated_at)}
										</span>
									</div>
									<div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-500">
										<span>调用 {matchingRun ? matchingRun.node_runs.length : '—'} 次</span>
										<span className="text-slate-300">·</span>
										<span>产出 {matchingRun ? matchingRun.artifacts.length : '—'} 项</span>
										<span className="text-slate-300">·</span>
										<span>检索 — 片段</span>
									</div>
								</button>
								<Button
									variant="ghost"
									size="icon-xs"
									className="absolute right-2 top-2 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
									onClick={() => onDelete(task)}
									disabled={disabled}
									aria-label={'删除' + task.title}
								>
									<Trash2 />
								</Button>
							</div>
						);
					})
				)}
			</div>
		</aside>
	);
}