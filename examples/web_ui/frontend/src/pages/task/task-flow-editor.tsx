import { ArrowDown, ArrowUp, Bot, GripVertical, Plus, Trash2 } from 'lucide-react';

import type { TaskNode } from '@/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

interface TaskFlowEditorProps {
	nodes: TaskNode[];
	onChange: (nodes: TaskNode[]) => void;
	disabled?: boolean;
}

function normalizeNodes(nodes: TaskNode[]): TaskNode[] {
	return nodes.map((node, index) => ({ ...node, order: index, type: 'ai' }));
}

export function TaskFlowEditor({ nodes, onChange, disabled = false }: TaskFlowEditorProps) {
	const updateNode = (index: number, update: Partial<TaskNode>) => {
		const next = nodes.map((node, nodeIndex) =>
			nodeIndex === index ? { ...node, ...update } : node,
		);
		onChange(normalizeNodes(next));
	};

	const moveNode = (index: number, offset: number) => {
		const target = index + offset;
		if (target < 0 || target >= nodes.length) return;
		const next = [...nodes];
		[next[index], next[target]] = [next[target], next[index]];
		onChange(normalizeNodes(next));
	};

	const removeNode = (index: number) => {
		if (nodes.length <= 1) return;
		onChange(normalizeNodes(nodes.filter((_, nodeIndex) => nodeIndex !== index)));
	};

	const addNode = () => {
		onChange(
			normalizeNodes([
				...nodes,
				{
					id: `node-${Date.now()}`,
					name: `AI 节点 ${nodes.length + 1}`,
					prompt: '请基于上一节点的输出继续完成任务。',
					type: 'ai',
					order: nodes.length,
				},
			]),
		);
	};

	return (
		<section>
			<div className="mb-3 flex items-center justify-between">
				<div>
					<div className="text-base font-semibold">任务流程</div>
					<div className="mt-1 text-xs text-muted-foreground">
						按顺序执行，每一步的输出会自动传给下一步
					</div>
				</div>
				<Button variant="outline" size="sm" onClick={addNode} disabled={disabled}>
					<Plus />
					新增节点
				</Button>
			</div>

			<div className="relative pl-8">
				<div className="absolute top-6 bottom-6 left-[13px] w-px bg-border" />
				<div className="relative mb-3 flex items-center gap-3">
					<div className="z-10 flex size-7 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
						始
					</div>
					<span className="text-sm font-medium">开始</span>
				</div>

				{nodes.map((node, index) => (
					<div key={node.id} className="relative mb-3">
						<div className="absolute top-6 -left-8 z-10 flex size-7 items-center justify-center rounded-full border border-border bg-card text-xs font-semibold text-muted-foreground">
							{index + 1}
						</div>
						<div className="rounded-2xl border border-border bg-background/70 p-4 transition-colors focus-within:border-primary/30">
							<div className="flex items-start gap-3">
								<GripVertical className="mt-1 size-4 shrink-0 text-muted-foreground/60" />
								<div className="min-w-0 flex-1 space-y-3">
									<div className="flex items-center gap-2">
										<Bot className="size-4 text-primary" />
										<Input
											value={node.name}
											onChange={(event) => updateNode(index, { name: event.target.value })}
											disabled={disabled}
											placeholder="节点名称"
											className="h-8 border-transparent bg-transparent px-1 text-sm font-semibold shadow-none focus-visible:bg-muted/60"
										/>
									</div>
									<Textarea
										value={node.prompt}
										onChange={(event) => updateNode(index, { prompt: event.target.value })}
										disabled={disabled}
										placeholder="写入这个 AI 节点要完成的 Prompt"
										className="min-h-24 resize-y bg-card text-sm leading-6"
									/>
								</div>
								<div className="flex shrink-0 items-center gap-1">
									<Button
										variant="ghost"
										size="icon-xs"
										onClick={() => moveNode(index, -1)}
										disabled={disabled || index === 0}
										aria-label="上移节点"
									>
										<ArrowUp />
									</Button>
									<Button
										variant="ghost"
										size="icon-xs"
										onClick={() => moveNode(index, 1)}
										disabled={disabled || index === nodes.length - 1}
										aria-label="下移节点"
									>
										<ArrowDown />
									</Button>
									<Button
										variant="ghost"
										size="icon-xs"
										className="text-destructive hover:text-destructive"
										onClick={() => removeNode(index)}
										disabled={disabled || nodes.length <= 1}
										aria-label="删除节点"
									>
										<Trash2 />
									</Button>
								</div>
							</div>
						</div>
					</div>
				))}

				<div className="relative flex items-center gap-3">
					<div className="z-10 flex size-7 items-center justify-center rounded-full border border-border bg-card text-xs font-semibold text-muted-foreground">
						终
					</div>
					<span className="text-sm font-medium">结束</span>
				</div>
			</div>
		</section>
	);
}
