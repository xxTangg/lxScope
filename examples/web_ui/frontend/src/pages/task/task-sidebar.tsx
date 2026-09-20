import { FileText, Plus, Trash2 } from 'lucide-react';

import type { RunStatus, TaskRecord } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface TaskSidebarProps {
	tasks: TaskRecord[];
	selectedTaskId: string | null;
	onSelect: (task: TaskRecord) => void;
	onCreate: () => void;
	onDelete: (task: TaskRecord) => void;
	disabled?: boolean;
}

function statusLabel(status: RunStatus | null | undefined): string {
	switch (status) {
		case 'queued':
			return '排队中';
		case 'running':
			return '执行中';
		case 'succeeded':
			return '已完成';
		case 'failed':
			return '失败';
		case 'canceled':
			return '已取消';
		case 'timed_out':
			return '超时';
		default:
			return '未执行';
	}
}

function taskStatusLabel(task: TaskRecord): string {
	if (task.generation_status === 'generating') return '节点生成中';
	if (task.generation_status === 'failed') return '生成失败';
	if (task.status === 'draft') return '待生成';
	return statusLabel(task.last_run_status);
}

function statusClass(status: RunStatus | null | undefined): string {
	if (status === 'succeeded') return 'bg-emerald-100 text-emerald-700';
	if (status === 'failed' || status === 'timed_out') return 'bg-red-100 text-red-700';
	if (status === 'running' || status === 'queued') return 'bg-amber-100 text-amber-700';
	return 'bg-muted text-muted-foreground';
}

function taskStatusClass(task: TaskRecord): string {
	if (task.generation_status === 'failed') return 'bg-red-100 text-red-700';
	if (task.generation_status === 'generating') return 'bg-amber-100 text-amber-700';
	if (task.status === 'draft') return 'bg-muted text-muted-foreground';
	return statusClass(task.last_run_status);
}

function formatDate(value: string): string {
	return new Date(value).toLocaleDateString('zh-CN', {
		month: 'short',
		day: 'numeric',
	});
}

export function TaskSidebar({
	tasks,
	selectedTaskId,
	onSelect,
	onCreate,
	onDelete,
	disabled = false,
}: TaskSidebarProps) {
	return (
		<aside className="flex w-[280px] shrink-0 flex-col overflow-hidden rounded-[22px] bg-card shadow-panel">
			<div className="flex items-center justify-between px-5 pt-5 pb-4">
				<div>
					<div className="text-lg font-semibold">我的任务</div>
					<div className="mt-1 text-xs text-muted-foreground">可保存、可复用的线性任务</div>
				</div>
				<Button
					size="icon-sm"
					variant="outline"
					onClick={onCreate}
					disabled={disabled}
					aria-label="新建任务"
				>
					<Plus />
				</Button>
			</div>

			<div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
				{tasks.length === 0 ? (
					<div className="rounded-2xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
						<FileText className="mx-auto mb-3 size-5" />
						还没有任务
						<div className="mt-1 text-xs">点击右上角创建第一个任务</div>
					</div>
				) : (
					tasks.map((task) => {
						const selected = task.id === selectedTaskId;
						return (
							<div
								key={task.id}
								className={`group relative mb-2 rounded-2xl border p-3 transition-colors ${
									selected
										? 'border-primary/15 bg-muted/80'
										: 'border-transparent hover:border-border hover:bg-muted/50'
								}`}
							>
								<button
									type="button"
									className="w-full text-left"
									onClick={() => onSelect(task)}
									disabled={disabled}
								>
									<div className="flex items-start gap-2">
										<FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
										<div className="min-w-0 flex-1">
											<div className="truncate text-sm font-medium">{task.title}</div>
											<div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
												{task.goal || '未填写任务目标'}
											</div>
										</div>
									</div>
									<div className="mt-3 flex items-center justify-between gap-2">
										<Badge className={taskStatusClass(task)}>
											{taskStatusLabel(task)}
										</Badge>
										<span className="text-[11px] text-muted-foreground">
											{task.nodes.length} 个节点 · {formatDate(task.updated_at)}
										</span>
									</div>
								</button>
								<Button
									variant="ghost"
									size="icon-xs"
									className="absolute top-2 right-2 opacity-0 group-hover:opacity-100"
									onClick={() => onDelete(task)}
									disabled={disabled}
									aria-label={`删除${task.title}`}
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
