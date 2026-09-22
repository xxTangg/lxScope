import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, BarChart3, Bot, Clock3, Coins, Network, TriangleAlert } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';

import { adminApi } from '@/api';
import type { ObservabilityComponent, ObservabilityComponentRow, ObservabilityDaily, ObservabilityFailure } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { TrendChart } from './trend-chart';

const componentKeys: Record<ObservabilityComponent, { title: string; description: string }> = {
	model: { title: 'admin.observabilityModels', description: 'admin.observabilityModelDetailDescription' },
	agent: { title: 'admin.observabilityAgents', description: 'admin.observabilityAgentDetailDescription' },
	tool: { title: 'admin.observabilityTools', description: 'admin.observabilityToolDetailDescription' },
};

function DetailTrend({ rows, component }: { rows: ObservabilityDaily[]; component: ObservabilityComponent }) {
	const { t } = useTranslation();
	const isAgent = component === 'agent';
	return <TrendChart rows={rows} emptyText={t('admin.observabilityNoData')} series={[
		{ key: 'calls', label: t(isAgent ? 'admin.observabilityAgentExecutions' : 'admin.observabilityCalls'), colorClass: 'bg-primary/80', value: (row) => row.calls },
		{ key: 'errors', label: t(isAgent ? 'admin.observabilityAgentFailures' : 'admin.observabilityErrors'), colorClass: 'bg-rose-500/80', value: (row) => row.errors },
	]} />;
}

function ComponentItems({ rows, empty, component, onOpen }: { rows: ObservabilityComponentRow[]; empty: string; component: ObservabilityComponent; onOpen?: (name: string) => void }) {
	const { t } = useTranslation();
	const isAgent = component === 'agent';
	if (!rows.length) return <div className="text-sm text-muted-foreground">{empty}</div>;
	return (
		<div className="space-y-2">
			{rows.map((row) => (
				<div key={row.name} role={isAgent && onOpen ? 'button' : undefined} tabIndex={isAgent && onOpen ? 0 : undefined} onClick={() => isAgent && onOpen?.(row.name)} onKeyDown={(event) => { if (isAgent && onOpen && (event.key === 'Enter' || event.key === ' ')) onOpen(row.name); }} className={`grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm sm:items-center ${isAgent && onOpen ? 'cursor-pointer transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring' : ''} ${isAgent ? 'sm:grid-cols-[1.2fr_1.3fr_repeat(6,1fr)]' : 'sm:grid-cols-[1.3fr_1.3fr_repeat(5,1fr)]'}`}>
					<div className="truncate font-mono" title={row.name}>{row.name}</div>
					<div className="truncate" title={row.user_names.join('、') || undefined}><div className="text-xs text-muted-foreground">{t('admin.observabilityUserNames')}</div><div>{row.user_names.length ? row.user_names.join('、') : '—'}</div></div>
					{isAgent ? <>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityAgentExecutions')}</div><div className="font-mono">{formatNumber(row.call_count)}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityComponentSuccess')}</div><div className="font-mono">{(row.success_rate * 100).toFixed(1)}%</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityLatency')}</div><div className="font-mono">{row.average_duration_seconds == null ? '—' : `${row.average_duration_seconds.toFixed(2)}s`}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.tokenAnalyticsTotal')}</div><div className="font-mono">{formatNumber(row.total_tokens)}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityAgentToolCalls')}</div><div className="font-mono">{formatNumber(row.tool_call_count)}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityFailureCount')}</div><div className="font-mono">{formatNumber(row.failure_count)}</div></div>
					</> : <>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityCalls')}</div><div className="font-mono">{formatNumber(row.call_count)}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityComponentSuccess')}</div><div className="font-mono">{(row.success_rate * 100).toFixed(1)}%</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityFailureCount')}</div><div className="font-mono">{formatNumber(row.failure_count)}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.observabilityLatency')}</div><div className="font-mono">{row.average_duration_seconds == null ? '—' : `${row.average_duration_seconds.toFixed(2)}s`}</div></div>
						<div><div className="text-xs text-muted-foreground">{t('admin.tokenAnalyticsTotal')}</div><div className="font-mono">{formatNumber(row.total_tokens)}</div></div>
					</>}
				</div>
			))}
		</div>
	);
}

function DetailFailures({ rows, empty }: { rows: ObservabilityFailure[]; empty: string }) {
	if (!rows.length) return <div className="text-sm text-muted-foreground">{empty}</div>;
	return (
		<div className="space-y-2">
			{rows.map((row, index) => (
				<div key={`${row.occurred_at}-${row.error_code}-${index}`} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.1fr_1.2fr_1fr_1fr] sm:items-center">
					<div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div>
					<div><div className="font-medium">{row.event_name}</div><div className="text-muted-foreground">{row.route ?? row.tool ?? row.model ?? row.agent_name ?? '—'}</div></div>
					<div className="font-mono text-rose-600 dark:text-rose-400">{row.error_code}</div>
					<div className="truncate text-muted-foreground">{row.user_id ?? row.session_id ?? '—'}</div>
				</div>
			))}
		</div>
	);
}

export function AdminObservabilityDetailPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const navigate = useNavigate();
	const { component: componentParam } = useParams<{ component: string }>();
	const isValidComponent = componentParam === 'model' || componentParam === 'agent' || componentParam === 'tool';
	const component: ObservabilityComponent = isValidComponent ? componentParam : 'model';
	const config = componentKeys[component] ?? componentKeys.model;
	const detail = useQuery({
		queryKey: ['admin', user?.id, 'observability-component', component],
		queryFn: () => adminApi.observabilityComponent(component),
		enabled: hasPermission('tenant:manage') && isValidComponent,
	});
	const data = detail.data;
	const Icon = component === 'agent' ? Bot : component === 'tool' ? Network : Network;

	return (
		<>
			<div className="flex items-center gap-2">
				<Button variant="ghost" size="sm" onClick={() => navigate('/admin/observability')}><ArrowLeft />{t('admin.observabilityBack')}</Button>
			</div>
			<AdminHeader title={t(config.title)} description={t(config.description)} loading={detail.isFetching} onRefresh={() => void detail.refetch()} />
			<AdminErrorNotice message={detail.error?.message} />
			{detail.isLoading ? (
				<div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div>
			) : (
				<>
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
						<MetricCard label={t('admin.observabilityCalls')} value={data ? formatNumber(data.call_count) : '—'} Icon={BarChart3} />
						<MetricCard label={t('admin.observabilityComponentSuccess')} value={data ? `${(data.success_rate * 100).toFixed(1)}%` : '—'} Icon={Icon} />
						<MetricCard label={t('admin.observabilityLatency')} value={data?.average_duration_seconds == null ? '—' : `${data.average_duration_seconds.toFixed(2)}s`} Icon={Clock3} />
						<MetricCard label={t('admin.tokenAnalyticsTotal')} value={data ? formatNumber(data.total_tokens) : '—'} Icon={Coins} />
					</div>
					<Card>
						<CardHeader><CardTitle>{component === 'agent' ? t('admin.observabilityAgentTrendTitle') : t('admin.observabilityTrendTitle')}</CardTitle><CardDescription>{t('admin.observabilityDetailTrendDescription')}</CardDescription></CardHeader>
						<CardContent><DetailTrend rows={data?.daily ?? []} component={component} /></CardContent>
					</Card>
					<Card>
						<CardHeader><CardTitle>{t('admin.observabilityDetailItems')}</CardTitle><CardDescription>{component === 'agent' ? t('admin.observabilityAgentItemsDescription') : t('admin.observabilityDetailItemsDescription')}</CardDescription></CardHeader>
					<CardContent><ComponentItems rows={data?.items ?? []} empty={t('admin.observabilityNoData')} component={component} onOpen={component === 'agent' ? (name) => navigate(`/admin/observability/agent/${encodeURIComponent(name)}`) : undefined} /></CardContent>
					</Card>
					<Card>
						<CardHeader><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityFailures')}</CardTitle></CardHeader>
						<CardContent><DetailFailures rows={data?.failures ?? []} empty={t('admin.observabilityNoFailures')} /></CardContent>
					</Card>
				</>
			)}
		</>
	);
}
