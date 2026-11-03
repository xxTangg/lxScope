import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart3, CheckCircle2, Clock3, Eye, Gauge, MousePointerClick } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { adminApi } from '@/api';
import type { SkillAnalyticsDaily, SkillFailureBreakdown, SkillFailureRecord } from '@/api/admin';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { TrendChart } from './trend-chart';

const rangeOptions = [7, 14, 30, 90];

function DailySkillChart({ rows }: { rows: SkillAnalyticsDaily[] }) {
	const { t } = useTranslation();
	const chartRows = rows.map((row) => ({ ...row, requests: row.exposed, errors: row.invoked, calls: row.completed, input_tokens: 0, output_tokens: 0, total_tokens: 0 }));
	return <TrendChart rows={chartRows} emptyText={t('admin.skillAnalyticsNoData')} series={[
		{ key: 'exposed', label: t('admin.skillAnalyticsExposed'), colorClass: 'bg-primary/80', value: (row) => row.requests },
		{ key: 'invoked', label: t('admin.skillAnalyticsInvoked'), colorClass: 'bg-sky-500/80', value: (row) => row.errors },
		{ key: 'completed', label: t('admin.skillAnalyticsCompleted'), colorClass: 'bg-emerald-500/80', value: (row) => row.calls },
	]} />;
}

function LifecycleBar({ label, value, total, tone }: { label: string; value: number; total: number; tone: string }) {
	return <div className="space-y-1.5"><div className="flex justify-between text-sm"><span>{label}</span><span className="font-mono">{formatNumber(value)}</span></div><div className="h-2 rounded-full bg-muted"><div className={`h-2 rounded-full ${tone}`} style={{ width: `${total > 0 ? Math.min((value / total) * 100, 100) : 0}%` }} /></div></div>;
}

function FailureBreakdownList({ rows, title, translateKey }: { rows: SkillFailureBreakdown[]; title: string; translateKey: (key: string) => string }) {
	const max = Math.max(...rows.map((row) => row.count), 1);
	return <div className="space-y-3"><div className="text-sm font-medium">{title}</div>{rows.length ? rows.map((row) => <div key={row.key} className="space-y-1"><div className="flex justify-between gap-3 text-xs"><span className="truncate font-mono">{translateKey(row.key)}</span><span className="font-mono text-muted-foreground">{formatNumber(row.count)}</span></div><div className="h-2 rounded-full bg-muted"><div className="h-2 rounded-full bg-rose-500" style={{ width: `${Math.max((row.count / max) * 100, 4)}%` }} /></div></div>) : <div className="text-sm text-muted-foreground">—</div>}</div>;
}

function RecentFailureList({ rows }: { rows: SkillFailureRecord[] }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.skillAnalyticsNoFailures')}</div>;
	return <div className="space-y-2">{rows.map((row, index) => <div key={`${row.occurred_at}-${row.error_code}-${index}`} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.2fr_1fr_1.4fr_1fr] sm:items-center"><div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div><div><div className="font-medium">{t(`admin.skillAnalyticsStage.${row.stage}`)}</div><div className="font-mono text-muted-foreground">{row.result}</div></div><div><div className="font-mono text-rose-600 dark:text-rose-400">{row.error_code}</div><div className="truncate text-muted-foreground">{row.skill_name ?? '—'}</div></div><div className="truncate text-muted-foreground" title={row.user_id}>{row.user_id}</div></div>)}</div>;
}

export function AdminSkillAnalyticsPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [days, setDays] = useState(14);
	const analytics = useQuery({ queryKey: ['admin', user?.id, 'skill-analytics', days], queryFn: () => adminApi.skillAnalytics(days), enabled: user?.role === 'admin' });
	const data = analytics.data;
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id, 'skill-analytics'] });
	const totalCompleted = data ? data.completed.success + data.completed.failed + data.completed.other : 0;
	const translateStage = (key: string) => t(`admin.skillAnalyticsStage.${key}`);

	return <>
		<div className="flex items-center gap-2"><button type="button" className="text-sm text-muted-foreground hover:text-foreground" onClick={() => navigate('/admin/observability')}>← {t('admin.observabilityBack')}</button></div>
		<AdminHeader title={t('admin.skillAnalyticsTitle')} description={t('admin.skillAnalyticsDescription')} onRefresh={refresh} loading={analytics.isFetching} />
		<div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3"><div className="flex items-center gap-2 text-sm"><BarChart3 className="size-4 text-muted-foreground" />{t('admin.skillAnalyticsPeriod')}</div><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.skillAnalyticsDays', { count: value })}</option>)}</select></div>
		<AdminErrorNotice message={analytics.error?.message} />
		{analytics.isLoading && !data ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : <>
			<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><MetricCard label={t('admin.skillAnalyticsEvents')} value={data ? formatNumber(data.event_count) : '—'} Icon={BarChart3} /><MetricCard label={t('admin.skillAnalyticsInvoked')} value={data ? formatNumber(data.lifecycle.invoked) : '—'} Icon={MousePointerClick} /><MetricCard label={t('admin.skillAnalyticsUsageRate')} value={data ? `${(data.actual_usage_rate * 100).toFixed(1)}%` : '—'} Icon={Gauge} /><MetricCard label={t('admin.skillAnalyticsAvgDuration')} value={data?.average_reconcile_duration_seconds != null ? `${data.average_reconcile_duration_seconds.toFixed(2)}s` : '—'} Icon={Clock3} /></div>
			<div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]"><Card><CardHeader><CardTitle>{t('admin.skillAnalyticsTrendTitle')}</CardTitle><CardDescription>{t('admin.skillAnalyticsTrendDescription')}</CardDescription></CardHeader><CardContent><DailySkillChart rows={data?.daily ?? []} /></CardContent></Card><Card><CardHeader><CardTitle>{t('admin.skillAnalyticsLifecycleTitle')}</CardTitle><CardDescription>{t('admin.skillAnalyticsLifecycleDescription')}</CardDescription></CardHeader><CardContent className="space-y-5"><LifecycleBar label={t('admin.skillAnalyticsExposed')} value={data?.lifecycle.exposed ?? 0} total={Math.max(data?.lifecycle.exposed ?? 0, 1)} tone="bg-primary" /><LifecycleBar label={t('admin.skillAnalyticsInvoked')} value={data?.lifecycle.invoked ?? 0} total={Math.max(data?.lifecycle.exposed ?? 0, 1)} tone="bg-sky-500" /><LifecycleBar label={t('admin.skillAnalyticsCompleted')} value={data?.lifecycle.completed ?? 0} total={Math.max(data?.lifecycle.exposed ?? 0, 1)} tone="bg-emerald-500" /><div className="grid grid-cols-2 gap-3 border-t pt-4 text-sm"><div><div className="text-xs text-muted-foreground">{t('admin.skillAnalyticsSuccess')}</div><div className="mt-1 font-mono">{formatNumber(data?.completed.success ?? 0)}</div></div><div><div className="text-xs text-muted-foreground">{t('admin.skillAnalyticsFailed')}</div><div className="mt-1 font-mono">{formatNumber(data?.completed.failed ?? 0)}</div></div></div></CardContent></Card></div>
			<Card><CardHeader><CardTitle>{t('admin.skillAnalyticsFailureTitle')}</CardTitle><CardDescription>{t('admin.skillAnalyticsFailureDescription')}</CardDescription></CardHeader><CardContent className="space-y-6"><div className="grid gap-3 sm:grid-cols-2"><div className="rounded-lg border border-rose-200 bg-rose-50/60 p-3 dark:border-rose-900 dark:bg-rose-950/20"><div className="text-xs text-muted-foreground">{t('admin.skillAnalyticsFailureCount')}</div><div className="mt-1 font-mono text-xl">{formatNumber(data?.failure_count ?? 0)}</div></div><div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">{t('admin.skillAnalyticsExecutionFailureRate')}</div><div className="mt-1 font-mono text-xl">{data ? `${(data.execution_failure_rate * 100).toFixed(1)}%` : '—'}</div></div></div><div className="grid gap-6 md:grid-cols-2"><FailureBreakdownList rows={data?.failure_by_stage ?? []} title={t('admin.skillAnalyticsFailureStage')} translateKey={translateStage} /><FailureBreakdownList rows={data?.failure_by_error ?? []} title={t('admin.skillAnalyticsFailureReason')} translateKey={(key) => key} /></div><div className="border-t pt-5"><div className="mb-3 text-sm font-medium">{t('admin.skillAnalyticsRecentFailures')}</div><RecentFailureList rows={data?.recent_failures ?? []} /></div></CardContent></Card>
			<div className="grid gap-6 lg:grid-cols-2"><Card><CardHeader><CardTitle>{t('admin.skillAnalyticsTopSkills')}</CardTitle><CardDescription>{t('admin.skillAnalyticsTopSkillsDescription')}</CardDescription></CardHeader><CardContent className="space-y-3">{data?.top_skills.length ? data.top_skills.map((skill) => <div key={skill.skill_name} className="flex items-center justify-between gap-3 rounded-md bg-muted/50 px-3 py-2 text-sm"><span className="truncate font-mono">{skill.skill_name}</span><span className="font-mono text-muted-foreground">{formatNumber(skill.invoked_count)}</span></div>) : <div className="text-sm text-muted-foreground">{t('admin.skillAnalyticsNoData')}</div>}</CardContent></Card><Card><CardHeader><CardTitle>{t('admin.skillAnalyticsSnapshotTitle')}</CardTitle><CardDescription>{t('admin.skillAnalyticsSnapshotDescription')}</CardDescription></CardHeader><CardContent className="grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-muted/50 p-3"><div className="flex items-center gap-2 text-xs text-muted-foreground"><Eye className="size-3.5" />{t('admin.skillAnalyticsVisibleCount')}</div><div className="mt-2 font-mono text-xl">{formatNumber(data?.latest_snapshot?.visible_count ?? 0)}</div></div><div className="rounded-lg bg-muted/50 p-3"><div className="flex items-center gap-2 text-xs text-muted-foreground"><CheckCircle2 className="size-3.5" />{t('admin.skillAnalyticsProvisionedCount')}</div><div className="mt-2 font-mono text-xl">{formatNumber(data?.latest_snapshot?.after_count ?? 0)}</div></div><div className="sm:col-span-2 text-xs text-muted-foreground">{t('admin.skillAnalyticsCompletedTotal')}: {formatNumber(totalCompleted)}</div></CardContent></Card></div>
		</>}
	</>;
}
