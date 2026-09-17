import type { Node as NvlNode, Relationship as NvlRelationship } from '@neo4j-nvl/base';
import { InteractiveNvlWrapper } from '@neo4j-nvl/react';
import { AlertCircle, Loader2, Network, RefreshCw, Search } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { knowledgeBaseApi } from '@/api';
import type { KnowledgeGraphNode, KnowledgeGraphResponse } from '@/api';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card.tsx';
import { Input } from '@/components/ui/input.tsx';

interface Props {
	knowledgeBaseId: string;
	editable?: boolean;
}

function statusLabel(status: KnowledgeGraphResponse['status'], t: (key: string) => string) {
	if (status === 'building') return t('knowledge.graph.building');
	if (status === 'error') return t('knowledge.graph.error');
	if (status === 'disabled') return t('knowledge.graph.disabled');
	if (status === 'empty') return t('knowledge.graph.emptyStatus');
	return t('knowledge.graph.ready');
}

export function KnowledgeGraphPanel({ knowledgeBaseId, editable = false }: Props) {
	const { t } = useTranslation();
	const [graph, setGraph] = useState<KnowledgeGraphResponse | null>(null);
	const [query, setQuery] = useState('');
	const [submittedQuery, setSubmittedQuery] = useState('');
	const [selected, setSelected] = useState<KnowledgeGraphNode | null>(null);
	const [loading, setLoading] = useState(true);
	const [rebuilding, setRebuilding] = useState(false);
	const [refreshToken, setRefreshToken] = useState(0);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		let timer: number | undefined;
		const load = async () => {
			try {
				const result = await knowledgeBaseApi.getGraph(knowledgeBaseId, {
					query: submittedQuery || undefined,
					nodeLimit: 80,
					edgeLimit: 160,
				});
				if (cancelled) return;
				setGraph(result);
				setError(null);
				setLoading(false);
				if (result.status === 'building') {
					timer = window.setTimeout(load, 3000);
				}
			} catch (err) {
				if (cancelled) return;
				setError((err as Error).message || String(err));
				setLoading(false);
			}
		};
		setLoading(true);
		void load();
		return () => {
			cancelled = true;
			if (timer !== undefined) window.clearTimeout(timer);
		};
	}, [knowledgeBaseId, submittedQuery, refreshToken]);

	const nvlNodes = useMemo<NvlNode[]>(
		() =>
			(graph?.nodes ?? []).map((node) => {
				const active = selected?.id === node.id;
				return {
					id: node.id,
					caption: node.label,
					captionSize: 12,
					color: active ? '#2563eb' : '#60a5fa',
					size: active ? 32 : 24,
					selected: active,
				};
			}),
		[graph?.nodes, selected?.id],
	);

	const nvlRelationships = useMemo<NvlRelationship[]>(
		() =>
			(graph?.edges ?? []).map((edge) => ({
				id: edge.id,
				from: edge.source,
				to: edge.target,
				caption: edge.label,
				captionSize: 10,
				color: '#94a3b8',
				width: 1.5,
			})),
		[graph?.edges],
	);

	const mouseEventCallbacks = useMemo(
		() => ({
			onNodeClick: (node: NvlNode) => {
				const selectedNode = graph?.nodes.find((candidate) => candidate.id === node.id);
				if (selectedNode) setSelected(selectedNode);
			},
			onCanvasClick: () => setSelected(null),
		}),
		[graph?.nodes],
	);

	const submit = (event: FormEvent) => {
		event.preventDefault();
		setSubmittedQuery(query.trim());
		setSelected(null);
	};

	const rebuild = async () => {
		setRebuilding(true);
		try {
			await knowledgeBaseApi.rebuildGraph(knowledgeBaseId);
			setRefreshToken((value) => value + 1);
		} catch (err) {
			setError((err as Error).message || String(err));
		} finally {
			setRebuilding(false);
		}
	};

	return (
		<Card className="overflow-visible">
			<CardHeader className="gap-y-3">
				<div className="flex items-center justify-between gap-3">
					<CardTitle className="flex items-center gap-2">
						<Network className="size-4" />
						{t('knowledge.graph.title')}
					</CardTitle>
					{graph && (
						<div className="flex items-center gap-2">
							<Badge variant={graph.status === 'error' ? 'destructive' : 'secondary'}>
								{statusLabel(graph.status, t)}
							</Badge>
							<Badge variant="outline">
								{t('knowledge.graph.stats', {
									nodes: graph.node_count,
									edges: graph.edge_count,
								})}
							</Badge>
						</div>
					)}
				</div>
				<div className="flex flex-wrap gap-2">
				<form className="flex max-w-md gap-2" onSubmit={submit}>
					<Input
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						placeholder={t('knowledge.graph.searchPlaceholder')}
						aria-label={t('knowledge.graph.searchPlaceholder')}
					/>
					<Button type="submit" size="sm" variant="outline">
						<Search className="size-3.5" />
						{t('knowledge.graph.search')}
					</Button>
				</form>
					{editable && <Button
						type="button"
						size="sm"
						variant="outline"
						disabled={rebuilding || graph?.status === 'building'}
						onClick={() => void rebuild()}
					>
						<RefreshCw className={rebuilding ? 'size-3.5 animate-spin' : 'size-3.5'} />
						{t('knowledge.graph.rebuild')}
					</Button>
					}
				</div>
			</CardHeader>
			<CardContent>
				{error ? (
					<div className="flex items-center gap-2 py-8 text-sm text-destructive">
						<AlertCircle className="size-4" />
						{error}
					</div>
				) : loading ? (
					<div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
						<Loader2 className="size-4 animate-spin" />
						{t('knowledge.graph.loading')}
					</div>
				) : !graph || graph.status === 'empty' || graph.nodes.length === 0 ? (
					<div className="py-16 text-center text-sm text-muted-foreground">
						{graph?.status === 'building'
							? t('knowledge.graph.building')
							: t('knowledge.graph.empty')}
					</div>
				) : (
					<div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_230px]">
						<div className="overflow-auto rounded-lg border bg-background">
							<div className="h-[520px] min-h-[420px] min-w-[720px] w-full">
								<InteractiveNvlWrapper
									nodes={nvlNodes}
									rels={nvlRelationships}
									nvlOptions={{
										renderer: 'canvas',
										layout: 'forceDirected',
										initialZoom: 0.8,
										styling: {
											defaultNodeColor: '#60a5fa',
											defaultRelationshipColor: '#94a3b8',
										},
									}}
									mouseEventCallbacks={mouseEventCallbacks}
									className="h-full w-full"
								/>
							</div>
						</div>
						<div className="rounded-lg border bg-muted/20 p-3 text-sm">
							{selected ? (
								<>
									<div className="font-medium">{selected.label}</div>
									<div className="mt-1 text-xs text-muted-foreground">{selected.type}</div>
									{selected.aliases.length > 0 && (
										<div className="mt-3 text-xs">{selected.aliases.join('、')}</div>
									)}
									<div className="mt-4 text-xs font-medium">{t('knowledge.graph.sources')}</div>
									<ul className="mt-1 space-y-1 text-xs text-muted-foreground">
										{selected.source_refs.slice(0, 12).map((source) => (
											<li key={`${source.document_id}-${source.chunk_index}`}>
												{source.filename || source.document_id}
												{source.chunk_index !== null && source.chunk_index !== undefined ? ` · ${t('knowledge.graph.chunk', { index: source.chunk_index + 1 })}` : ''}
												{source.metadata?.page || source.metadata?.slide
													? ` · ${String(source.metadata.page ?? source.metadata.slide)}`
													: ''}
											</li>
										))}
									</ul>
								</>
							) : (
								<p className="text-xs text-muted-foreground">{t('knowledge.graph.selectNode')}</p>
							)}
						</div>
					</div>
				)}
			</CardContent>
		</Card>
	);
}
