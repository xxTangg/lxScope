import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, BarChart3, Bot, Clock3, Coins, Network, TriangleAlert, Users } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { adminApi } from '@/api';
import type { ObservabilityComponentRow, ObservabilityDaily, ObservabilityFailure } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { TrendChart } from './trend-chart';

const rangeOptions = [7, 14, 30, 90];

function ObservabilityTrend({ rows }: { rows: ObservabilityDaily[] }) {
	const { t } = useTranslation();
	return <TrendChart rows={rows} emptyText={t('admin.observabilityNoData')} series={[
		{ key: 'requests', label: t('admin.observabilityRequests'), colorClass: 'bg-primary/80', value: (row) => row.requests },
		{ key: 'errors', label: t('admin.observabilityErrors'), colorClass: 'bg-rose-500/80', value: (row) => row.errors },
	]} />;
}

function ComponentTable({ rows, empty, component }: { rows: ObservabilityComponentRow[]; empty: string; component?: 'model' | 'agent' | 'tool' }) {
	const { t } = useTranslation();
	const navigate = useNavigate();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{empty}</div>;
	const latestRows = [...rows].sort((left, right) => right.last_occurred_at.localeCompare(left.last_occurred_at)).slice(0, 2);
	return <div className="space-y-2">{latestRows.map((row) => <div key={row.name} role={component === 'agent' ? 'button' : undefined} tabIndex={component === 'agent' ? 0 : undefined} onClick={component === 'agent' ? (event) => { event.stopPropagation(); navigate(`/admin/observability/agent/${encodeURIComponent(row.name)}`); } : undefined} onKeyDown={component === 'agent' ? (event) => { event.stopPropagation(); if (event.key === 'Enter' || event.key === ' ') navigate(`/admin/observability/agent/${encodeURIComponent(row.name)}`); } : undefined} className={`grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm sm:grid-cols-[1.4fr_repeat(3,1fr)] sm:items-center ${component === 'agent' ? 'cursor-pointer transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring' : ''}`}><div className="truncate font-mono" title={row.name}>{row.name}</div><div><div className="text-xs text-muted-foreground">{t('admin.observabilityCalls')}</div><div className="font-mono">{formatNumber(row.call_count)}</div></div><div><div className="text-xs text-muted-foreground">{t('admin.observabilityComponentSuccess')}</div><div className="font-mono">{(row.success_rate * 100).toFixed(1)}%</div></div><div><div className="text-xs text-muted-foreground">{t('admin.observabilityLatency')}</div><div className="font-mono">{row.average_duration_seconds == null ? '—' : `${row.average_duration_seconds.toFixed(2)}s`}</div></div></div>)}</div>;
}

function failureSubject(row: ObservabilityFailure) {
	return row.skill_name ?? row.model ?? row.agent_name ?? row.tool ?? row.route ?? '—';
}

function ProjectFailureList({ rows, empty, onOpen }: { rows: ObservabilityFailure[]; empty: string; onOpen: (row: ObservabilityFailure) => void }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{empty}</div>;
	return <div className="space-y-2">{rows.slice(0, 5).map((row, index) => <div key={`${row.occurred_at}-${row.event_name}-${index}`} role={row.trace_id ? 'button' : undefined} tabIndex={row.trace_id ? 0 : undefined} onClick={() => onOpen(row)} onKeyDown={(event) => { if (row.trace_id && (event.key === 'Enter' || event.key === ' ')) onOpen(row); }} className={`grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.1fr_0.8fr_1.3fr_1fr_1fr] sm:items-center ${row.trace_id ? 'cursor-pointer transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring' : ''}`}><div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div><div className="font-medium">{t(`admin.observabilityFailureComponent.${row.component}`)}</div><div className="truncate">{failureSubject(row)}</div><div className="truncate text-muted-foreground">{row.username ?? row.user_id ?? '—'}</div><div><div className="font-mono text-rose-600 dark:text-rose-400">{row.error_code}</div><div className="text-muted-foreground">{row.error_type ? t(`admin.observabilityFailureType.${row.error_type}`) : row.event_name}</div></div></div>)}</div>;
}

export function AdminObservabilityPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const queryClient = useQueryClient();
	const navigate = useNavigate();
	const [days, setDays] = useState(14);
	const runtimeAnalytics = useQuery({ queryKey: ['admin', user?.id, 'observability', days], queryFn: () => adminApi.observability(days), enabled: hasPermission('tenant:manage') });
	const runtime = runtimeAnalytics.data;
	const tokenUsage = runtime?.token_usage;
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id, 'observability'] });
	const openComponentDetail = (component: 'model' | 'agent' | 'tool') => navigate(`/admin/observability/${component}`);
	const clickable = (component: 'model' | 'agent' | 'tool') => ({ role: 'button' as const, tabIndex: 0, onClick: () => openComponentDetail(component), onKeyDown: (event: React.KeyboardEvent) => { if (event.key === 'Enter' || event.key === ' ') openComponentDetail(component); } });

	return <>
		<AdminHeader title={t('admin.analyticsTitle')} description={t('admin.analyticsDescription')} onRefresh={refresh} loading={runtimeAnalytics.isFetching} />
		<div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3"><div className="flex items-center gap-2 text-sm"><BarChart3 className="size-4 text-muted-foreground" />{t('admin.observabilityPeriod')}</div><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.observabilityDays', { count: value })}</option>)}</select></div>
		<AdminErrorNotice message={runtimeAnalytics.error?.message} />
		{runtimeAnalytics.isLoading && !runtime ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : <>
		<Card><CardHeader><CardTitle>{t('admin.observabilityRuntimeTitle')}</CardTitle><CardDescription>{t('admin.observabilityRuntimeDescription')}</CardDescription></CardHeader><CardContent className="space-y-5"><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><MetricCard label={t('admin.observabilityBusinessRequestCount')} value={runtime ? formatNumber(runtime.request_count) : '—'} Icon={BarChart3} onClick={() => navigate('/admin/observability/requests')} /><MetricCard label={t('admin.tokenAnalyticsTotal')} value={tokenUsage ? formatNumber(tokenUsage.total_tokens) : '—'} Icon={Coins} onClick={() => navigate('/admin/observability/tokens')} /><MetricCard label={t('admin.observabilityActiveUsers')} value={runtime ? formatNumber(Math.max(runtime.active_user_count, tokenUsage?.user_count ?? 0)) : '—'} Icon={Users} onClick={() => navigate('/admin/observability/users')} /><MetricCard label={t('admin.observabilityAvgLatency')} value={runtime?.average_response_time_seconds == null ? '—' : `${runtime.average_response_time_seconds.toFixed(2)}s`} Icon={Clock3} onClick={() => navigate('/admin/observability/requests')} /></div><div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]"><div><div className="mb-3 flex items-center gap-2 text-sm font-medium"><BarChart3 className="size-4 text-muted-foreground" />{t('admin.observabilityTrendTitle')}</div><ObservabilityTrend rows={runtime?.daily ?? []} /></div><div className="space-y-3"><div className="flex items-center justify-between text-sm"><span>{t('admin.observabilitySuccessRate')}</span><span className="font-mono font-semibold">{runtime ? `${(runtime.success_rate * 100).toFixed(1)}%` : '—'}</span></div><div className="h-2 rounded-full bg-muted"><div className="h-2 rounded-full bg-emerald-500" style={{ width: `${Math.min((runtime?.success_rate ?? 0) * 100, 100)}%` }} /></div><div className="grid grid-cols-2 gap-3 border-t pt-4 text-sm"><div><div className="text-xs text-muted-foreground">{t('admin.observabilitySuccessfulRequests')}</div><div className="mt-1 font-mono">{formatNumber(runtime?.successful_requests ?? 0)}</div></div><div><div className="text-xs text-muted-foreground">{t('admin.observabilityFailedRequests')}</div><div className="mt-1 font-mono">{formatNumber(runtime?.failed_requests ?? 0)}</div></div></div></div></div></CardContent></Card>
			<div className="grid gap-6 lg:grid-cols-3"><Card className="cursor-pointer transition-colors hover:border-primary/50" {...clickable('model')}><CardHeader><CardTitle className="flex items-center gap-2"><Network className="size-4" />{t('admin.observabilityModels')}</CardTitle><CardDescription>{t('admin.observabilityClickForDetails')}</CardDescription></CardHeader><CardContent><ComponentTable rows={runtime?.models ?? []} empty={t('admin.observabilityNoData')} component="model" /></CardContent></Card><Card className="cursor-pointer transition-colors hover:border-primary/50" {...clickable('agent')}><CardHeader><CardTitle className="flex items-center gap-2"><Bot className="size-4" />{t('admin.observabilityAgents')}</CardTitle><CardDescription>{t('admin.observabilityClickForDetails')}</CardDescription></CardHeader><CardContent><ComponentTable rows={runtime?.agents ?? []} empty={t('admin.observabilityNoData')} component="agent" /></CardContent></Card><Card className="cursor-pointer transition-colors hover:border-primary/50" {...clickable('tool')}><CardHeader><CardTitle className="flex items-center gap-2"><Network className="size-4" />{t('admin.observabilityTools')}</CardTitle><CardDescription>{t('admin.observabilityClickForDetails')}</CardDescription></CardHeader><CardContent><ComponentTable rows={runtime?.tools ?? []} empty={t('admin.observabilityNoData')} component="tool" /></CardContent></Card></div>
			<Card><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityFailures')}</CardTitle><CardDescription>{t('admin.observabilityFailuresDescription')}</CardDescription></div><Button variant="outline" size="sm" onClick={() => navigate('/admin/observability/failures')}>{t('admin.observabilityViewAllFailures')}<ArrowRight /></Button></div></CardHeader><CardContent className="space-y-4"><ProjectFailureList rows={runtime?.failures ?? []} empty={t('admin.observabilityNoFailures')} onOpen={(row) => row.trace_id && navigate(`/admin/observability/trace/${encodeURIComponent(row.trace_id)}`)} />{runtime?.failure_by_type?.length ? <div className="flex flex-wrap gap-2 border-t pt-3">{runtime.failure_by_type.slice(0, 5).map((row) => <span key={row.key} className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">{t(`admin.observabilityFailureType.${row.key}`)} · {formatNumber(row.count)}</span>)}</div> : null}</CardContent></Card>
			<Card className="cursor-pointer transition-colors hover:border-primary/50" {...clickable('tool')} onClick={() => navigate('/admin/observability/skills')} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') navigate('/admin/observability/skills'); }}><CardHeader><CardTitle>{t('admin.skillAnalyticsEntryTitle')}</CardTitle><CardDescription>{t('admin.skillAnalyticsEntryDescription')}</CardDescription></CardHeader><CardContent><span className="text-sm text-primary">{t('admin.observabilityClickForDetails')} →</span></CardContent></Card>
		</>}
	</>;
}
