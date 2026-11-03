import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, BarChart3, Clock3, Coins, TriangleAlert, Users } from 'lucide-react';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { adminApi } from '@/api';
import type { ObservabilityDaily, TokenUserUsage } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { TrendChart } from './trend-chart';

type Focus = 'requests' | 'tokens' | 'users';

const rangeOptions = [7, 14, 30, 90];

const focusKeys: Record<Focus, { title: string; description: string }> = {
	requests: {
		title: 'admin.observabilityRequestsDetailTitle',
		description: 'admin.observabilityRequestsDetailDescription',
	},
	tokens: {
		title: 'admin.observabilityTokensDetailTitle',
		description: 'admin.observabilityTokensDetailDescription',
	},
	users: {
		title: 'admin.observabilityUsersDetailTitle',
		description: 'admin.observabilityUsersDetailDescription',
	},
};

function FocusTrend({ rows, mode }: { rows: ObservabilityDaily[]; mode: 'requests' | 'tokens' }) {
	const { t } = useTranslation();
	return <TrendChart rows={rows} emptyText={t('admin.observabilityNoFocusData')} series={mode === 'tokens' ? [
		{ key: 'tokens', label: t('admin.observabilityTokenTrend'), colorClass: 'bg-primary/80', value: (row) => row.total_tokens },
	] : [
		{ key: 'requests', label: t('admin.observabilityRequests'), colorClass: 'bg-primary/80', value: (row) => row.requests },
		{ key: 'errors', label: t('admin.observabilityErrors'), colorClass: 'bg-rose-500/80', value: (row) => row.errors },
	]} />;
}

function TokenUserTable({ rows }: { rows: TokenUserUsage[] }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.tokenAnalyticsNoUsers')}</div>;
	return (
		<div className="space-y-2">
			<div className="hidden grid-cols-[1.5fr_repeat(4,1fr)] gap-3 px-3 text-xs text-muted-foreground md:grid"><div>{t('admin.tokenAnalyticsUser')}</div><div>{t('admin.tokenAnalyticsTotal')}</div><div>{t('admin.tokenAnalyticsInput')}</div><div>{t('admin.tokenAnalyticsOutput')}</div><div>{t('admin.tokenAnalyticsMessages')}</div></div>
			{rows.map((row) => (
				<div key={row.user_id} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm md:grid-cols-[1.5fr_repeat(4,1fr)] md:items-center">
					<div><div className="font-medium">{row.username}</div><div className="truncate font-mono text-xs text-muted-foreground">{row.user_id}</div></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.tokenAnalyticsTotal')}: </span><span className="font-mono font-semibold">{formatNumber(row.total_tokens)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.tokenAnalyticsInput')}: </span><span className="font-mono">{formatNumber(row.input_tokens)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.tokenAnalyticsOutput')}: </span><span className="font-mono">{formatNumber(row.output_tokens)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.tokenAnalyticsMessages')}: </span><span className="font-mono">{formatNumber(row.message_count)}</span></div>
				</div>
			))}
		</div>
	);
}

export function AdminObservabilityFocusPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const { pathname } = useLocation();
	const focusParam = pathname.split('/').pop();
	const isValidFocus = focusParam === 'requests' || focusParam === 'tokens' || focusParam === 'users';
	const focus: Focus = isValidFocus ? focusParam : 'requests';
	const [days, setDays] = useState(14);
	const config = focusKeys[focus];
	const analytics = useQuery({
		// Request analysis is another view of the overview projection. Reuse
		// the overview cache so navigating between the two pages cannot show
		// different snapshots for the same period.
		queryKey: ['admin', user?.id, 'observability', days],
		queryFn: () => adminApi.observability(days),
		enabled: user?.role === 'admin' && isValidFocus,
	});
	const data = analytics.data;
	const tokenUsage = data?.token_usage;
	const activeUsers = data ? Math.max(data.active_user_count, tokenUsage?.user_count ?? 0) : null;

	return (
		<>
			<div className="flex items-center gap-2">
				<Button variant="ghost" size="sm" onClick={() => navigate('/admin/observability')}><ArrowLeft />{t('admin.observabilityBack')}</Button>
			</div>
			<AdminHeader title={t(config.title)} description={t(config.description)} loading={analytics.isFetching} onRefresh={() => void analytics.refetch()} />
			<div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3"><div className="text-sm text-muted-foreground">{t('admin.observabilityPeriod')}</div><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.observabilityDays', { count: value })}</option>)}</select></div>
			<AdminErrorNotice message={analytics.error?.message} />
			{analytics.isLoading ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : (
				<>
					{focus === 'requests' && (
						<>
							<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
								<MetricCard label={t('admin.observabilityRequestCount')} value={data ? formatNumber(data.request_count) : '—'} Icon={BarChart3} />
								<MetricCard label={t('admin.observabilitySuccessRate')} value={data ? `${(data.success_rate * 100).toFixed(1)}%` : '—'} Icon={BarChart3} />
								<MetricCard label={t('admin.observabilityAvgLatency')} value={data?.average_response_time_seconds == null ? '—' : `${data.average_response_time_seconds.toFixed(2)}s`} Icon={Clock3} />
								<MetricCard label={t('admin.observabilityFailedRequests')} value={data ? formatNumber(data.failed_requests) : '—'} Icon={TriangleAlert} />
							</div>
							<Card><CardHeader><CardTitle>{t('admin.observabilityTrendTitle')}</CardTitle><CardDescription>{t('admin.observabilityRequestsDetailDescription')}</CardDescription></CardHeader><CardContent><FocusTrend rows={data?.daily ?? []} mode="requests" /></CardContent></Card>
							<Card><CardHeader><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityRequestFailures')}</CardTitle></CardHeader><CardContent>{data?.failures?.length ? <div className="space-y-2">{data.failures.map((failure, index) => <div key={`${failure.occurred_at}-${index}`} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.1fr_1.2fr_1fr_1fr] sm:items-center"><div className="text-muted-foreground">{new Date(failure.occurred_at).toLocaleString()}</div><div className="font-medium">{failure.event_name}</div><div className="font-mono text-rose-600 dark:text-rose-400">{failure.error_code}</div><div className="truncate text-muted-foreground">{failure.user_id ?? failure.route ?? '—'}</div></div>)}</div> : <div className="text-sm text-muted-foreground">{t('admin.observabilityNoFailures')}</div>}</CardContent></Card>
						</>
					)}
					{focus === 'tokens' && (
						<>
							<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
								<MetricCard label={t('admin.tokenAnalyticsTotal')} value={tokenUsage ? formatNumber(tokenUsage.total_tokens) : '—'} Icon={Coins} />
								<MetricCard label={t('admin.tokenAnalyticsInput')} value={tokenUsage ? formatNumber(tokenUsage.input_tokens) : '—'} Icon={Coins} />
								<MetricCard label={t('admin.tokenAnalyticsOutput')} value={tokenUsage ? formatNumber(tokenUsage.output_tokens) : '—'} Icon={Coins} />
								<MetricCard label={t('admin.tokenAnalyticsUsers')} value={tokenUsage ? formatNumber(tokenUsage.user_count) : '—'} Icon={Users} />
							</div>
							<Card><CardHeader><CardTitle>{t('admin.observabilityTokenTrend')}</CardTitle><CardDescription>{t('admin.observabilityTokensDetailDescription')}</CardDescription></CardHeader><CardContent><FocusTrend rows={data?.daily ?? []} mode="tokens" /></CardContent></Card>
							<Card><CardHeader><CardTitle>{t('admin.observabilityUserUsageList')}</CardTitle><CardDescription>{t('admin.tokenAnalyticsDescription')}</CardDescription></CardHeader><CardContent><TokenUserTable rows={tokenUsage?.users ?? []} /></CardContent></Card>
						</>
					)}
					{focus === 'users' && (
						<>
							<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
								<MetricCard label={t('admin.observabilityActiveUsers')} value={activeUsers == null ? '—' : formatNumber(activeUsers)} Icon={Users} />
								<MetricCard label={t('admin.tokenAnalyticsUsers')} value={tokenUsage ? formatNumber(tokenUsage.user_count) : '—'} Icon={Users} />
								<MetricCard label={t('admin.tokenAnalyticsMessages')} value={tokenUsage ? formatNumber(tokenUsage.message_count) : '—'} Icon={BarChart3} />
								<MetricCard label={t('admin.observabilityRequestCount')} value={data ? formatNumber(data.request_count) : '—'} Icon={BarChart3} />
							</div>
							<Card><CardHeader><CardTitle>{t('admin.observabilityTrendTitle')}</CardTitle><CardDescription>{t('admin.observabilityUsersDetailDescription')}</CardDescription></CardHeader><CardContent><FocusTrend rows={data?.daily ?? []} mode="requests" /></CardContent></Card>
							<Card><CardHeader><CardTitle>{t('admin.observabilityUserUsageList')}</CardTitle><CardDescription>{t('admin.tokenAnalyticsDescription')}</CardDescription></CardHeader><CardContent><TokenUserTable rows={tokenUsage?.users ?? []} /></CardContent></Card>
						</>
					)}
				</>
			)}
		</>
	);
}
