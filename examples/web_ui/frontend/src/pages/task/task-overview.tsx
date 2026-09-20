import {
	ArrowRight,
	Bot,
	Check,
	CircleAlert,
	CircleDashed,
	Code2,
	GitBranch,
	Loader2,
	Play,
	Target,
	Wrench,
	XCircle,
} from 'lucide-react';
import * as React from 'react';

import type {
	NodeRunStatus,
	TaskGenerationStatus,
	TaskNode,
	TaskRunRecord,
	TaskStepType,
} from '@/api';
import { Badge } from '@/components/ui/badge';

interface TaskOverviewProps {
	nodes: TaskNode[];
	run: TaskRunRecord | null;
	generationStatus: TaskGenerationStatus;
	isDirty: boolean;
}

type OverviewNodeStatus = NodeRunStatus | 'upcoming';

function stepTypeLabel(type: TaskStepType): string {
	if (type === 'tool') return 'ToolStep';
	if (type === 'python') return 'PythonStep';
	return 'AgentStep';
}

function stepTypeIcon(type: TaskStepType) {
	if (type === 'tool') return <Wrench className="size-3.5" />;
	if (type === 'python') return <Code2 className="size-3.5" />;
	return <Bot className="size-3.5" />;
}

function nodeStatusLabel(status: OverviewNodeStatus): string {
	if (status === 'running') return '进行中';
	if (status === 'succeeded') return '已完成';
	if (status === 'failed') return '失败';
	if (status === 'canceled') return '已取消';
	if (status === 'pending') return '待执行';
	return '未开始';
}

function nodeStatusIcon(status: OverviewNodeStatus) {
	if (status === 'running') return <Loader2 className="size-4 animate-spin" />;
	if (status === 'succeeded') return <Check className="size-4" />;
	if (status === 'failed') return <CircleAlert className="size-4" />;
	if (status === 'canceled') return <XCircle className="size-4" />;
	if (status === 'pending') return <Play className="size-3.5" />;
	return <CircleDashed className="size-4" />;
}

function nodeStatusClass(status: OverviewNodeStatus): string {
	if (status === 'running') {
		return 'border-primary/50 bg-primary/[0.08] text-primary ring-2 ring-primary/10';
	}
	if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'canceled') return 'border-border bg-muted text-muted-foreground';
	if (status === 'pending') return 'border-amber-200 bg-amber-50 text-amber-700';
	return 'border-border bg-background text-muted-foreground';
}

function matchesCurrentNodes(nodes: TaskNode[], run: TaskRunRecord | null, isDirty: boolean): boolean {
	if (isDirty || !run || nodes.length !== run.nodes.length) return false;
	return nodes.every((node, index) => run.nodes[index]?.id === node.id);
}

export function TaskOverview({ nodes, run, generationStatus, isDirty }: TaskOverviewProps) {
	const hasMatchingRun = matchesCurrentNodes(nodes, run, isDirty);
	const nodeRuns = new Map(run?.node_runs.map((nodeRun) => [nodeRun.node_id, nodeRun.status]));
	const statuses: OverviewNodeStatus[] = nodes.map((node) =>
		hasMatchingRun ? nodeRuns.get(node.id) ?? 'pending' : 'upcoming',
	);
	const completedCount = statuses.filter((status) => status === 'succeeded').length;
	const progress = nodes.length > 0 ? Math.round((completedCount / nodes.length) * 100) : 0;
	const activeIndex = statuses.findIndex(
		(status) => status === 'running' || status === 'failed' || status === 'pending',
	);
	const activeNode = activeIndex >= 0 ? nodes[activeIndex] : null;
	const currentLabel = !nodes.length
		? '等待生成节点'
		: generationStatus === 'generating'
			? '正在生成节点'
			: !run || !hasMatchingRun
				? '尚未执行'
				: activeNode?.name ?? '任务已完成';

	const summary = !nodes.length
		? '先填写任务目标，再生成或新增任务节点。'
		: generationStatus === 'generating'
			? '正在根据任务目标生成可执行的线性流程。'
			: !run
				? '流程已准备好，执行后会在这里显示每个节点的进度。'
				: !hasMatchingRun
					? '流程内容已发生变化，保存并重新执行后可更新节点状态。'
					: run.status === 'failed'
						? '本次执行在一个节点处中断，请查看右侧执行结果。'
						: run.status === 'canceled'
							? '本次执行已取消，可以从头重新执行。'
							: `${completedCount} / ${nodes.length} 个节点已完成。`;

	return (
		<section
			className="mb-5 rounded-2xl border border-primary/15 bg-gradient-to-br from-primary/[0.08] via-card to-card p-4 shadow-sm"
			aria-label="任务节点概览"
		>
			<div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
				<div className="min-w-0">
					<div className="flex flex-wrap items-center gap-2">
						<div className="flex items-center gap-2 text-sm font-semibold">
							<div className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
								<GitBranch className="size-4" />
							</div>
							<span>任务节点概览</span>
						</div>
						<Badge variant="outline" className="border-primary/20 bg-card/70">
							{nodes.length ? `${completedCount}/${nodes.length} 已完成` : '待规划'}
						</Badge>
					</div>
					<div className="mt-1 text-xs leading-5 text-muted-foreground">
						从任务目标到最终结果的执行路径
					</div>
				</div>

				<div className="w-full max-w-xs shrink-0">
					<div className="mb-1.5 flex items-center justify-between text-xs">
						<span className="text-muted-foreground">整体进度</span>
						<span className="font-semibold text-foreground">{progress}%</span>
					</div>
					<div className="h-2 overflow-hidden rounded-full bg-primary/10">
						<div
							className="h-full rounded-full bg-primary transition-[width] duration-500"
							style={{ width: `${progress}%` }}
						/>
					</div>
				</div>
			</div>

			{nodes.length ? (
				<div className="mt-5 overflow-x-auto pb-1">
					<div className="flex min-w-max items-start">
						<div className="flex w-12 shrink-0 flex-col items-center gap-2">
							<div className="flex size-9 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground shadow-sm">
								始
							</div>
							<span className="text-[11px] font-medium text-muted-foreground">开始</span>
						</div>

						{nodes.map((node, index) => {
							const status = statuses[index];
							return (
								<React.Fragment key={node.id}>
									<div className="flex items-center px-2 pt-4 text-border">
										<div className="h-px w-7 bg-border" />
										<ArrowRight className="size-3.5" />
									</div>
									<div className="w-40 shrink-0">
										<div className="flex items-center gap-2">
											<div
												className={`flex size-9 items-center justify-center rounded-full border ${nodeStatusClass(status)}`}
												aria-label={nodeStatusLabel(status)}
											>
												{nodeStatusIcon(status)}
											</div>
											<div className="min-w-0">
												<div className="truncate text-sm font-semibold" title={node.name}>
													{node.name}
												</div>
												<div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
													{stepTypeIcon(node.type)}
													<span>{stepTypeLabel(node.type)}</span>
												</div>
											</div>
										</div>
										<div className="mt-2 text-[11px] font-medium text-muted-foreground">
											第 {index + 1} 步 · {nodeStatusLabel(status)}
										</div>
									</div>
								</React.Fragment>
							);
						})}

						<div className="flex items-center px-2 pt-4 text-border">
							<div className="h-px w-7 bg-border" />
							<ArrowRight className="size-3.5" />
						</div>
						<div className="flex w-12 shrink-0 flex-col items-center gap-2">
							<div className="flex size-9 items-center justify-center rounded-full border border-border bg-background text-xs font-semibold text-muted-foreground">
								终
							</div>
							<span className="text-[11px] font-medium text-muted-foreground">结束</span>
						</div>
					</div>
				</div>
			) : (
				<div className="mt-5 flex items-center gap-3 rounded-xl border border-dashed border-primary/20 bg-card/50 px-4 py-3 text-xs text-muted-foreground">
					<CircleDashed className="size-4 shrink-0 text-primary/60" />
					<span>{summary}</span>
				</div>
			)}

			<div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border/60 pt-3 text-xs">
				<div className="flex items-center gap-1.5 text-muted-foreground">
					<Target className="size-3.5 text-primary" />
					<span>当前节点</span>
					<span className="font-medium text-foreground">{currentLabel}</span>
				</div>
				<div className="flex items-center gap-1.5 text-muted-foreground">
					<span className="font-medium text-foreground">{summary}</span>
				</div>
			</div>
		</section>
	);
}
