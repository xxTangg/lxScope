import { BookOpen, Database, Loader2, RefreshCw } from 'lucide-react';
import * as React from 'react';

import { taskApi } from '@/api';
import type { TaskKnowledgeBaseOption } from '@/api/task';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface Props {
	selectedIds: string[];
	disabled?: boolean;
	onChange: (ids: string[]) => void;
}

export function TaskKnowledgeSelector({ selectedIds, disabled = false, onChange }: Props) {
	const [options, setOptions] = React.useState<TaskKnowledgeBaseOption[]>([]);
	const [loading, setLoading] = React.useState(true);
	const [error, setError] = React.useState<string | null>(null);

	const load = React.useCallback(async () => {
		setLoading(true);
		try {
			const result = await taskApi.listKnowledgeBases();
			setOptions(result);
			setError(null);
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : '知识库列表加载失败。');
		} finally {
			setLoading(false);
		}
	}, []);

	React.useEffect(() => {
		void load();
	}, [load]);

	const toggle = (id: string, checked: boolean) => {
		const next = checked
			? [...new Set([...selectedIds, id])]
			: selectedIds.filter((selectedId) => selectedId !== id);
		onChange(next);
	};

	return (
		<Card className="mb-6 border-blue-200/70 bg-blue-50/30 dark:border-blue-900/60 dark:bg-blue-950/10">
			<CardHeader className="gap-2">
				<div className="flex items-center justify-between gap-3">
					<CardTitle className="flex items-center gap-2 text-sm">
						<div className="flex size-7 items-center justify-center rounded-lg bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300">
							<BookOpen className="size-4" />
						</div>
						任务知识库
						<Badge variant="outline">{selectedIds.length} 个已选</Badge>
					</CardTitle>
					<Button
						type="button"
						size="sm"
						variant="ghost"
						disabled={loading}
						onClick={() => void load()}
					>
						<RefreshCw className={loading ? 'size-3.5 animate-spin' : 'size-3.5'} />
						刷新
					</Button>
				</div>
				<div className="text-xs leading-5 text-muted-foreground">
					任务级选择会被所有 Agent 节点继承；需要时可在节点上改为覆盖或禁用。
					执行时先检索相关片段，再把带来源的证据交给节点。
				</div>
			</CardHeader>
			<CardContent>
				{loading ? (
					<div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
						<Loader2 className="size-4 animate-spin" />
						加载知识库…
					</div>
				) : error ? (
					<div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border bg-background/60 px-3 py-3 text-xs text-muted-foreground">
						<span>{error}</span>
						<Button type="button" size="sm" variant="outline" onClick={() => void load()}>
							重试
						</Button>
					</div>
				) : options.length === 0 ? (
					<div className="flex items-center gap-2 rounded-lg border border-dashed border-border bg-background/60 px-3 py-3 text-xs text-muted-foreground">
						<Database className="size-4" />
						暂无可用知识库，请先在知识库模块上传并完成索引。
					</div>
				) : (
					<div className="grid gap-2 sm:grid-cols-2">
						{options.map((option) => {
							const checked = selectedIds.includes(option.id);
							return (
								<label
									key={option.id}
									className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-3 transition-colors ${
										checked
											? 'border-blue-300 bg-blue-100/60 dark:border-blue-800 dark:bg-blue-950/30'
											: 'border-border bg-background/70 hover:bg-background'
									} ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
								>
									<input
										type="checkbox"
										checked={checked}
										disabled={disabled}
										onChange={(event) => toggle(option.id, event.target.checked)}
										className="mt-0.5 size-4 accent-blue-600"
									/>
									<span className="min-w-0">
										<span className="block truncate text-sm font-medium" title={option.name}>
											{option.name}
										</span>
										<span className="mt-1 block text-[11px] text-muted-foreground">
											{option.ready_document_count}/{option.document_count} 个文档可检索 ·{' '}
											{option.chunk_count} 个片段
										</span>
									</span>
								</label>
							);
						})}
					</div>
				)}
			</CardContent>
		</Card>
	);
}
