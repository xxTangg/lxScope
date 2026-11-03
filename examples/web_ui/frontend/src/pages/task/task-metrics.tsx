import {
	Bot,
	CheckCircle2,
	Clock3,
	Database,
	Files,
	GitBranch,
	Search,
} from 'lucide-react';

import { getTaskMetrics } from './task-view-model';
import type { TaskRunRecord, TaskNode } from '@/api';

interface TaskMetricsProps {
	task: { id: string; nodes: TaskNode[]; knowledge_base_ids: string[] };
	run: TaskRunRecord | null;
	isDirty: boolean;
}

const METRIC_ICONS = {
	nodes: GitBranch,
	progress: CheckCircle2,
	duration: Clock3,
	agent: Bot,
	knowledge: Database,
	retrieval: Search,
	artifact: Files,
} as const;

export function TaskMetrics({ task, run, isDirty }: TaskMetricsProps) {
	const metrics = getTaskMetrics(task, run, isDirty);
	return (
		<section
			className="grid grid-cols-[repeat(auto-fit,minmax(112px,1fr))] gap-2 pb-4"
			aria-label="任务指标"
		>
			{metrics.map((metric) => {
				const Icon = METRIC_ICONS[metric.icon];
				return (
					<div
						key={metric.label}
						className="flex min-w-0 items-center gap-2 rounded-xl border border-slate-200/80 bg-white px-2 py-2.5 transition-colors hover:border-blue-200"
					>
						<div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
							<Icon className="size-4" />
						</div>
						<div className="min-w-0">
							<div className="whitespace-nowrap text-[10px] leading-4 text-slate-500">{metric.label}</div>
							<div className="whitespace-nowrap text-sm font-semibold leading-5 text-slate-800">{metric.value}</div>
						</div>
					</div>
				);
			})}
		</section>
	);
}
