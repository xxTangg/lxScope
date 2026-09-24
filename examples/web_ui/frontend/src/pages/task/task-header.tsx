import { Loader2, Play, RefreshCw, Save } from 'lucide-react';

import type { TaskGenerationStatus } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface TaskHeaderProps {
	title: string;
	goal: string;
	generationStatus: TaskGenerationStatus;
	isDirty: boolean;
	isSaving: boolean;
	isRunning: boolean;
	isGenerating: boolean;
	canRerun: boolean;
	onTitleChange: (title: string) => void;
	onSave: () => void;
	onRerun: () => void;
	onRun: () => void;
}

export function TaskHeader({
	title,
	goal,
	generationStatus,
	isDirty,
	isSaving,
	isRunning,
	isGenerating,
	canRerun,
	onTitleChange,
	onSave,
	onRerun,
	onRun,
}: TaskHeaderProps) {
	return (
		<header className="flex flex-wrap items-center justify-between gap-4 px-5 pb-4 pt-4 xl:px-6">
			<div className="min-w-0 flex-1">
				<div className="flex min-w-0 items-center gap-2">
					<Input
						value={title}
						onChange={(event) => onTitleChange(event.target.value)}
						disabled={isSaving || isRunning || isGenerating}
						className="h-9 max-w-xl border-transparent bg-transparent px-0 text-xl font-semibold text-slate-900 shadow-none focus-visible:border-slate-200 focus-visible:px-2"
						placeholder="任务名称"
					/>
					{isDirty && <Badge variant="outline" className="shrink-0 border-amber-200 bg-amber-50 text-[10px] text-amber-700">未保存</Badge>}
					{generationStatus === 'generating' && <Badge variant="outline" className="shrink-0 border-blue-200 bg-blue-50 text-[10px] text-blue-700">节点生成中</Badge>}
					{generationStatus === 'failed' && <Badge variant="outline" className="shrink-0 border-red-200 bg-red-50 text-[10px] text-red-700">节点生成失败</Badge>}
				</div>
				<div className="mt-1 line-clamp-1 max-w-3xl text-xs text-slate-500" title={goal}>{goal || '补充任务目标，帮助 Agent 规划可执行流程。'}</div>
			</div>
			<div className="flex shrink-0 flex-wrap items-center gap-2">
				<Button variant="outline" size="sm" onClick={onSave} disabled={!isDirty || isSaving || isRunning || isGenerating} className="border-slate-200">
					{isSaving ? <Loader2 className="animate-spin" /> : <Save />}{isSaving ? '保存中…' : '保存'}
				</Button>
				<Button variant="outline" size="sm" onClick={onRerun} disabled={!canRerun || isSaving || isRunning || isGenerating} className="border-slate-200">
					<RefreshCw />重新执行
				</Button>
				<Button size="sm" onClick={onRun} disabled={isSaving || isRunning || isGenerating} className="bg-blue-600 text-white shadow-sm hover:bg-blue-700">
					{isRunning ? <Loader2 className="animate-spin" /> : <Play />}{isRunning ? '执行中…' : '执行任务'}
				</Button>
			</div>
		</header>
	);
}