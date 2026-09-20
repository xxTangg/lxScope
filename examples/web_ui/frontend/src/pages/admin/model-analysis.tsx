import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, BarChart3, Clock3, Coins, TriangleAlert, Users } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { adminApi } from '@/api';
import type { ObservabilityComponentRow, ObservabilityDaily, ObservabilityFailure, ObservabilityUserBreakdown } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { TrendChart } from './trend-chart';

const rangeOptions = [7, 14, 30, 90];

function ModelTrend({ rows }: { rows: ObservabilityDaily[] }) {
	const { t } = useTranslation();
	return <TrendChart rows={rows} emptyText={t('admin.observabilityNoData')} series={[
		{ key: 'calls', label: t('admin.observabilityModelCalls'), colorClass: 'bg-primary/80', value: (row) => row.calls },
		{ key: 'tokens', label: t('admin.observabilityModelTokens'), colorClass: 'bg-amber-500/80', axis: 'right', value: (row) => row.total_tokens },
		{ key: 'errors', label: t('admin.observabilityModelFailures'), colorClass: 'bg-rose-500/80', value: (row) => row.errors },
	]} />;
}

function ModelItems({ rows, onOpen }: { rows: ObservabilityComponentRow[]; onOpen: (name: string) => void }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoData')}</div>;
	return (
		<div className="space-y-2">
			<div className="hidden grid-cols-[1.35fr_repeat(6,1fr)] gap-3 px-3 text-xs text-muted-foreground md:grid"><div>{t('admin.observabilityModels')}</div><div>{t('admin.observabilityCalls')}</div><div>{t('admin.tokenAnalyticsInput')}</div><div>{t('admin.tokenAnalyticsOutput')}</div><div>{t('admin.observabilityAverageTokens')}</div><div>{t('admin.observabilityLatency')}</div><div>{t('admin.observabilityComponentSuccess')}</div></div>
			{rows.map((row) => (
				<div key={row.name} role="button" tabIndex={0} onClick={() => onOpen(row.name)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onOpen(row.name); }} className="grid cursor-pointer gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[1.35fr_repeat(6,1fr)] md:items-center">
					<div className="truncate font-mono" title={row.name}>{row.name}</div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityCalls')}: </span><span className="font-mono">{formatNumber(row.call_count)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.tokenAnalyticsInput')}: </span><span className="font-mono">{formatNumber(row.input_tokens)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.tokenAnalyticsOutput')}: </span><span className="font-mono">{formatNumber(row.output_tokens)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityAverageTokens')}: </span><span className="font-mono">{formatNumber(row.call_count ? row.total_tokens / row.call_count : 0)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityLatency')}: </span><span className="font-mono">{row.average_duration_seconds == null ? '—' : `${row.average_duration_seconds.toFixed(2)}s`}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityComponentSuccess')}: </span><span className="font-mono">{(row.success_rate * 100).toFixed(1)}%</span></div>
				</div>
			))}
		</div>
	);
}

function ModelUsers({ rows }: { rows: ObservabilityUserBreakdown[] }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoUserBreakdown')}</div>;
	return <div className="space-y-2">{rows.map((row) => <div key={row.user_id} className="grid grid-cols-[1.5fr_1fr_1fr] items-center rounded-md bg-muted/50 px-3 py-2 text-sm"><div><div className="font-medium">{row.username}</div><div className="font-mono text-xs text-muted-foreground">{row.user_id}</div></div><div className="font-mono">{formatNumber(row.call_count)}</div><div className="font-mono text-right">{(row.percentage * 100).toFixed(1)}%</div></div>)}</div>;
}

function ModelFailures({ rows }: { rows: ObservabilityFailure[] }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoFailures')}</div>;
	return <div className="space-y-2">{rows.map((row, index) => <div key={`${row.occurred_at}-${index}`} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.1fr_1.2fr_1fr_1fr] sm:items-center"><div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div><div className="font-mono text-rose-600 dark:text-rose-400">{row.error_code}</div><div>{row.model ?? '—'}</div><div className="truncate text-muted-foreground">{row.user_id ?? '—'}</div></div>)}</div>;
}

export function AdminModelObservabilityPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const { modelName: modelNameParam } = useParams<{ modelName: string }>();
	const lockedModel = Boolean(modelNameParam);
	const [days, setDays] = useState(14);
	const [modelName, setModelName] = useState(modelNameParam ?? '');
	const [userId, setUserId] = useState('');
	const detail = useQuery({
		queryKey: ['admin', user?.id, 'observability-model', days, modelName, userId],
		queryFn: () => adminApi.observabilityComponent('model', days, { name: modelName || undefined, user_id: userId || undefined }),
		enabled: user?.role === 'admin',
	});
	const data = detail.data;
	const modelOptions = data?.items ?? [];

	return (
		<>
			<div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={() => navigate('/admin/observability')}><ArrowLeft />{t('admin.observabilityBack')}</Button></div>
			<AdminHeader title={lockedModel ? `${t('admin.observabilityModelDetailTitle')} · ${modelName}` : t('admin.observabilityModels')} description={t('admin.observabilityModelAnalysisDescription')} loading={detail.isFetching} onRefresh={() => void detail.refetch()} />
			<div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3"><span className="text-sm text-muted-foreground">{t('admin.observabilityPeriod')}</span><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.observabilityDays', { count: value })}</option>)}</select><select value={modelName} disabled={lockedModel} onChange={(event) => setModelName(event.target.value)} className="border-input bg-background h-9 rounded-md border px-3 text-sm"><option value="">{t('admin.observabilityAllModels')}</option>{modelOptions.map((row) => <option key={row.name} value={row.name}>{row.name}</option>)}</select><select value={userId} onChange={(event) => setUserId(event.target.value)} className="border-input bg-background h-9 rounded-md border px-3 text-sm"><option value="">{t('admin.observabilityAllUsers')}</option>{(data?.user_options ?? []).map((option) => <option key={option.user_id} value={option.user_id}>{option.username}</option>)}</select></div>
			<AdminErrorNotice message={detail.error?.message} />
			{detail.isLoading ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : (
				<>
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><MetricCard label={t('admin.observabilityCalls')} value={data ? formatNumber(data.call_count) : '—'} Icon={BarChart3} /><MetricCard label={t('admin.tokenAnalyticsTotal')} value={data ? formatNumber(data.total_tokens) : '—'} Icon={Coins} /><MetricCard label={t('admin.observabilityLatency')} value={data?.average_duration_seconds == null ? '—' : `${data.average_duration_seconds.toFixed(2)}s`} Icon={Clock3} /><MetricCard label={t('admin.observabilityFailureCount')} value={data ? formatNumber(data.failure_count) : '—'} Icon={TriangleAlert} /></div>
					{lockedModel && <div className="grid gap-4 sm:grid-cols-2"><MetricCard label={t('admin.observabilityP95Latency')} value={data?.p95_duration_seconds == null ? '—' : `${data.p95_duration_seconds.toFixed(2)}s`} Icon={Clock3} /><MetricCard label={t('admin.observabilityComponentSuccess')} value={data ? `${(data.success_rate * 100).toFixed(1)}%` : '—'} Icon={Users} /></div>}
					<Card><CardHeader><CardTitle>{t('admin.observabilityModelTrendTitle')}</CardTitle><CardDescription>{t('admin.observabilityModelTrendDescription')}</CardDescription></CardHeader><CardContent><ModelTrend rows={data?.daily ?? []} /></CardContent></Card>
					<Card><CardHeader><CardTitle>{t('admin.observabilityModelUsageDetails')}</CardTitle><CardDescription>{t('admin.observabilityModelUsageDetailsDescription')}</CardDescription></CardHeader><CardContent><ModelItems rows={data?.items ?? []} onOpen={(name) => navigate(`/admin/observability/model/${encodeURIComponent(name)}`)} /></CardContent></Card>
					{lockedModel && <Card><CardHeader><CardTitle>{t('admin.observabilityCallingUsers')}</CardTitle></CardHeader><CardContent><ModelUsers rows={data?.user_breakdown ?? []} /></CardContent></Card>}
					<Card><CardHeader><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityModelFailureRecords')}</CardTitle></CardHeader><CardContent><ModelFailures rows={data?.failures ?? []} /></CardContent></Card>
				</>
			)}
		</>
	);
}
