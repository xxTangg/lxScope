import { Check, CircleAlert, Loader2, Play, RotateCcw, Square } from 'lucide-react';

import type { NodeRunRecord, RunStatus, TaskRunRecord } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface TaskRunPanelProps {
	run: TaskRunRecord | null;
	onCancel: () => void;
	onRetry: () => void;
}

function runStatusLabel(status: RunStatus): string {
	const labels: Record<RunStatus, string> = {
		queued: '排队中',
		running: '执行中',
		succeeded: '已完成',
		failed: '执行失败',
		canceled: '已取消',
		timed_out: '执行超时',
	};
	return labels[status];
}

function runStatusClass(status: RunStatus): string {
	if (status === 'succeeded') return 'bg-emerald-100 text-emerald-700';
	if (status === 'failed' || status === 'timed_out') return 'bg-red-100 text-red-700';
	if (status === 'running' || status === 'queued') return 'bg-amber-100 text-amber-700';
	return 'bg-muted text-muted-foreground';
}

function nodeIcon(node: NodeRunRecord) {
	if (node.status === 'running') return <Loader2 className="size-4 animate-spin text-amber-600" />;
	if (node.status === 'succeeded') return <Check className="size-4 text-emerald-600" />;
	if (node.status === 'failed') return <CircleAlert className="size-4 text-red-600" />;
	return <Play className="size-3.5 text-muted-foreground" />;
}

function isTerminal(status: RunStatus): boolean {
	return ['succeeded', 'failed', 'canceled', 'timed_out'].includes(status);
}

export function TaskRunPanel({ run, onCancel, onRetry }: TaskRunPanelProps) {
	if (!run) {
		return (
			<section className="flex min-h-0 flex-1 flex-col border-l border-border/70 bg-muted/20 p-6">
				<div className="text-base font-semibold">执行结果</div>
				<div className="flex flex-1 flex-col items-center justify-center text-center text-muted-foreground">
					<div className="mb-3 flex size-12 items-center justify-center rounded-2xl bg-card shadow-sm">
						<Play className="size-5" />
					</div>
					<div className="text-sm font-medium text-foreground">保存后执行任务</div>
					<div className="mt-1 max-w-56 text-xs leading-5">
						每个节点的状态和结果都会在这里逐步展示
					</div>
				</div>
			</section>
		);
	}

	return (
		<section className="flex min-h-0 flex-1 flex-col border-l border-border/70 bg-muted/20">
			<div className="flex items-center justify-between gap-3 px-6 pt-6 pb-4">
				<div>
					<div className="text-base font-semibold">执行结果</div>
					<div className="mt-1 text-xs text-muted-foreground">
						Run · 版本 v{run.task_revision}
					</div>
				</div>
				<Badge className={runStatusClass(run.status)}>{runStatusLabel(run.status)}</Badge>
			</div>

			<div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
				<div className="mb-5 rounded-2xl border border-border bg-card p-4">
					<div className="mb-2 text-xs font-medium text-muted-foreground">本次输入</div>
					<div className="whitespace-pre-wrap text-sm leading-6">
						{run.input || '任务目标作为本次执行输入'}
					</div>
				</div>

				<div className="space-y-3">
					{run.node_runs.map((node) => (
						<div key={node.node_id} className="rounded-2xl border border-border bg-card p-4">
							<div className="flex items-center gap-2">
								<div className="flex size-6 items-center justify-center rounded-full bg-muted">
									{nodeIcon(node)}
								</div>
								<div className="min-w-0 flex-1 truncate text-sm font-medium">{node.name}</div>
								<span className="text-xs text-muted-foreground">{node.status}</span>
							</div>
							{node.output && (
								<div className="mt-3 whitespace-pre-wrap rounded-xl bg-muted/60 p-3 text-sm leading-6">
									{node.output}
								</div>
							)}
							{node.error && (
								<div className="mt-3 rounded-xl bg-red-50 p-3 text-sm leading-6 text-red-700">
									{node.error}
								</div>
							)}
						</div>
					))}
				</div>

				{run.final_output && (
					<div className="mt-5 rounded-2xl border border-primary/15 bg-primary/[0.03] p-4">
						<div className="mb-2 text-xs font-medium text-muted-foreground">最终结果</div>
						<div className="whitespace-pre-wrap text-sm leading-6">{run.final_output}</div>
					</div>
				)}
			</div>

			{!isTerminal(run.status) ? (
				<div className="border-t border-border/70 px-6 py-4">
					<Button variant="outline" size="sm" onClick={onCancel}>
						<Square />
						停止执行
					</Button>
				</div>
			) : (
				<div className="border-t border-border/70 px-6 py-4">
					<Button variant="outline" size="sm" onClick={onRetry}>
						<RotateCcw />
						重新执行
					</Button>
				</div>
			)}
		</section>
	);
}
