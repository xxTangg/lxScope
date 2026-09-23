import { Check, ChevronDown, ChevronUp, CircleAlert, Download, Eye, Loader2, Play, RotateCcw, Square } from 'lucide-react';
import * as React from 'react';

import { TaskKnowledgeGraph } from './task-knowledge-graph';
import { taskApi } from '@/api';
import type { NodeRunRecord, RunStatus, TaskArtifactRecord, TaskNode, TaskRunRecord, TaskStepType } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';

interface TaskRunPanelProps {
	run: TaskRunRecord | null;
	width?: number;
	onCancel: () => void;
	onRetry: () => void;
}

function runStatusLabel(status: RunStatus): string {
	const labels: Record<RunStatus, string> = {
		queued: '等待启动',
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

function stepTypeLabel(type: TaskStepType): string {
	if (type === 'tool') return 'ToolStep';
	if (type === 'python') return 'CodeStep';
	return 'AgentStep';
}

function nodeStatusLabel(status: NodeRunRecord['status']): string {
	if (status === 'running') return '运行中';
	if (status === 'succeeded') return '成功';
	if (status === 'failed') return '失败';
	if (status === 'canceled') return '已取消';
	return '等待中';
}

function nodeDetails(node: TaskNode | undefined, run: TaskRunRecord) {
	if (!node) return null;
	if (node.config.type === 'tool') {
		return (
			<div className="space-y-1 text-xs leading-5 text-muted-foreground">
				<div>工具：{node.config.tool_name || '未配置'}</div>
				<div className="break-all">参数：{JSON.stringify(node.config.arguments)}</div>
			</div>
		);
	}
	if (node.config.type === 'python') {
		return (
			<div className="space-y-1 text-xs leading-5 text-muted-foreground">
				<div>超时：{node.config.timeout_seconds} 秒</div>
				<div>代码：{node.config.code || '未配置'}</div>
			</div>
		);
	}
	return (
		<div className="space-y-1 text-xs leading-5 text-muted-foreground">
			<div>Agent：{node.config.agent_id || run.context.agent_id || '当前会话 Agent'}</div>
			<div>Session：{node.config.session_id || run.context.session_id || '当前会话'}</div>
			<div className="whitespace-pre-wrap">Prompt：{node.config.prompt}</div>
		</div>
	);
}

function isTerminal(status: RunStatus): boolean {
	return ['succeeded', 'failed', 'canceled', 'timed_out'].includes(status);
}

function artifactFormatLabel(artifact: TaskArtifactRecord): string {
	if (artifact.format === 'markdown') return 'Markdown';
	if (artifact.format === 'docx') return 'Word';
	return 'Excel';
}

function artifactSizeLabel(size: number): string {
	if (size < 1024) return `${size} B`;
	if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
	return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function TaskRunPanel({ run, width = 420, onCancel, onRetry }: TaskRunPanelProps) {
	const [previewArtifact, setPreviewArtifact] = React.useState<TaskArtifactRecord | null>(null);
	const [artifactBusyId, setArtifactBusyId] = React.useState<string | null>(null);
	const [artifactsOpen, setArtifactsOpen] = React.useState(true);
	const [previewGraphNodeId, setPreviewGraphNodeId] = React.useState<string | null>(null);

	React.useEffect(() => {
		setPreviewArtifact(null);
		setPreviewGraphNodeId(null);
		setArtifactsOpen(true);
	}, [run?.id]);

	const previewGraphNode = run?.node_runs.find((node) => node.node_id === previewGraphNodeId);

	const openArtifactPreview = (artifact: TaskArtifactRecord) => {
		// ``preview_text`` is the task-owned, text-safe representation. Reading
		// a .docx/.xlsx response as text produces binary gibberish, so previews
		// deliberately use the metadata already returned for this Run.
		setPreviewArtifact(artifact);
	};

	const downloadArtifact = async (artifact: TaskArtifactRecord) => {
		setArtifactBusyId(artifact.id);
		try {
			const blob = await taskApi.getArtifactContent(run!.id, artifact.id, true);
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = artifact.name;
			anchor.click();
			URL.revokeObjectURL(url);
		} finally {
			setArtifactBusyId(null);
		}
	};
	if (!run) {
		return (
			<section
				className="flex min-h-0 min-w-0 shrink-0 flex-col border-l border-border/70 bg-muted/20 p-6"
				style={{ width: `${width}px` }}
			>
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
		<section
			className="flex min-h-0 min-w-0 shrink-0 flex-col border-l border-border/70 bg-muted/20"
			style={{ width: `${width}px` }}
		>
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
							{(() => {
								const definition = run.nodes.find((item) => item.id === node.node_id);
								return (
									<>
							<div className="flex items-center gap-2">
								<div className="flex size-6 items-center justify-center rounded-full bg-muted">
									{nodeIcon(node)}
								</div>
								<div className="min-w-0 flex-1 truncate text-sm font-medium">{node.name}</div>
								<span className="text-xs text-muted-foreground">{nodeStatusLabel(node.status)}</span>
							</div>
							<div className="mt-3 rounded-xl bg-muted/40 p-3">
								<div className="mb-2 text-xs font-medium text-foreground">
									步骤类型：{stepTypeLabel(definition?.type ?? node.type)}
								</div>
								{nodeDetails(definition, run)}
							</div>
							{(node.display_summary || node.output) && (
								<div className="mt-3 whitespace-pre-wrap rounded-xl bg-muted/60 p-3 text-sm leading-6">
									{node.display_summary || node.output}
								</div>
							)}
							{node.output && node.output !== (node.display_summary || node.output) && (
								<details className="mt-2 rounded-xl border border-border/70 bg-background/70 p-3 text-sm">
									<summary className="cursor-pointer text-xs font-medium text-muted-foreground">
										查看完整输出
									</summary>
									<div className="mt-2 whitespace-pre-wrap leading-6">{node.output}</div>
								</details>
							)}
							{definition?.knowledge_graph_enabled && (
								<>
									<div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2">
										<div className="min-w-0">
											<div className="text-xs font-medium text-blue-900">节点知识图谱</div>
											<div className="mt-0.5 text-[11px] text-blue-700/70">
												在执行结果中按需查看该节点的实体关系
											</div>
										</div>
										<Button
											type="button"
											size="sm"
											variant="outline"
											disabled={node.status === 'pending'}
											onClick={() =>
												setPreviewGraphNodeId((current) =>
													current === node.node_id ? null : node.node_id,
												)
											}
										>
											<Eye />
											预览图谱
										</Button>
									</div>
								</>
							)}
							{node.error && (
								<div className="mt-3 rounded-xl bg-red-50 p-3 text-sm leading-6 text-red-700">
									{node.error}
								</div>
							)}
									</>
								);
							})()}
						</div>
					))}
				</div>

				{(run.final_summary || run.final_output) && (
					<div className="mt-5 rounded-2xl border border-primary/15 bg-primary/[0.03] p-4">
						<div className="mb-2 text-xs font-medium text-muted-foreground">最终结果</div>
						<div className="whitespace-pre-wrap text-sm leading-6">
							{run.final_summary || run.final_output}
						</div>
						{run.final_output && run.final_output !== (run.final_summary || run.final_output) && (
							<details className="mt-3 rounded-xl border border-primary/10 bg-background/70 p-3 text-sm">
								<summary className="cursor-pointer text-xs font-medium text-muted-foreground">
									查看完整最终结果
								</summary>
								<div className="mt-2 whitespace-pre-wrap leading-6">{run.final_output}</div>
							</details>
						)}
					</div>
				)}
			</div>
			{run.artifacts?.length > 0 && (
				<Collapsible
					open={artifactsOpen}
					onOpenChange={setArtifactsOpen}
					className="mt-5 rounded-2xl border border-border bg-card p-4"
				>
					<div className="flex items-center justify-between gap-2">
						<div>
							<div className="text-sm font-semibold">文件产物</div>
							<div className="mt-1 text-xs text-muted-foreground">
								已保存到当前 Workspace，并关联到本次 Run
							</div>
						</div>
						<CollapsibleTrigger asChild>
							<Button variant="ghost" size="sm" aria-label={artifactsOpen ? '收起文件产物' : '展开文件产物'}>
								<span>{run.artifacts.length} 个文件</span>
								{artifactsOpen ? <ChevronUp /> : <ChevronDown />}
							</Button>
						</CollapsibleTrigger>
					</div>
					<CollapsibleContent className="mt-3 space-y-2">
						{run.artifacts.map((artifact) => (
							<div key={artifact.id} className="rounded-xl border border-border/70 bg-background/70 p-3">
								<div className="flex items-center gap-2">
									<div className="min-w-0 flex-1">
										<div className="truncate text-sm font-medium">{artifact.name}</div>
										<div className="mt-1 text-xs text-muted-foreground">
											{artifactFormatLabel(artifact)} · {artifactSizeLabel(artifact.size_bytes)}
										</div>
									</div>
									{artifact.preview_text && (
										<Button
											variant="ghost"
											size="sm"
											onClick={() => openArtifactPreview(artifact)}
										>
											<Eye />
											预览
										</Button>
									)}
									<Button
										variant="outline"
										size="sm"
										disabled={artifactBusyId === artifact.id}
										onClick={() => void downloadArtifact(artifact)}
									>
										<Download />
										下载
									</Button>
								</div>
							</div>
						))}
					</CollapsibleContent>
				</Collapsible>
			)}
			<Dialog
				open={previewArtifact !== null}
				onOpenChange={(open) => {
					if (!open) setPreviewArtifact(null);
				}}
			>
				{previewArtifact && (
					<DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
						<DialogHeader>
							<DialogTitle>文件预览 · {previewArtifact.name}</DialogTitle>
							<DialogDescription>
								{artifactFormatLabel(previewArtifact)} · {artifactSizeLabel(previewArtifact.size_bytes)}。预览内容为本次任务生成的文本摘要。
							</DialogDescription>
						</DialogHeader>
						<pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded-xl bg-muted/50 p-4 text-sm leading-6">
							{previewArtifact.preview_text}
						</pre>
					</DialogContent>
				)}
			</Dialog>
			<Dialog
				open={previewGraphNodeId !== null}
				onOpenChange={(open) => {
					if (!open) setPreviewGraphNodeId(null);
				}}
			>
				{previewGraphNode && (
					<DialogContent className="!w-[min(1500px,calc(100%-2rem))] !max-w-none max-h-[94vh] overflow-y-auto">
						<DialogHeader>
							<DialogTitle>节点知识图谱预览 · {previewGraphNode.name}</DialogTitle>
							<DialogDescription>
								在执行结果中查看该节点的实体关系、关系证据和来源片段；图谱支持拖动、缩放和适配视图。
							</DialogDescription>
						</DialogHeader>
						<TaskKnowledgeGraph
							taskId={run.task_id}
							selectedIds={run.knowledge_base_ids ?? []}
							runId={run.id}
							nodeId={previewGraphNode.node_id}
							preview
							disabled={previewGraphNode.status === 'pending'}
						/>
					</DialogContent>
				)}
			</Dialog>
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
