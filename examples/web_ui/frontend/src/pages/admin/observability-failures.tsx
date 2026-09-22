import { ArrowLeft, ArrowRight, CircleAlert, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { adminApi } from '@/api';
import type { ObservabilityFailure } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader } from './shared';

const rangeOptions = [7, 14, 30, 90];
const componentOptions = ['all', 'http', 'model', 'agent', 'tool', 'skill'] as const;
const errorTypeOptions = ['all', 'timeout', 'permission', 'rate_limit', 'upstream', 'system'] as const;

function subject(row: ObservabilityFailure) {
	return row.skill_name ?? row.model ?? row.agent_name ?? row.tool ?? row.route ?? '—';
}

function FailureRows({ rows }: { rows: ObservabilityFailure[] }) {
	const { t } = useTranslation();
	const navigate = useNavigate();
	if (!rows.length) return <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">{t('admin.observabilityFailureNoData')}</div>;
	return <div className="space-y-2">{rows.map((row, index) => <div key={`${row.occurred_at}-${row.event_name}-${index}`} className="grid gap-3 rounded-md bg-muted/50 px-3 py-3 text-xs md:grid-cols-[1.1fr_0.8fr_1.3fr_1fr_1.1fr_0.9fr] md:items-center"><div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div><div><div className="font-medium">{t(`admin.observabilityFailureComponent.${row.component}`)}</div><div className="text-muted-foreground">{t(`admin.observabilityFailureType.${row.error_type ?? 'system'}`)}</div></div><div className="truncate font-medium" title={subject(row)}>{subject(row)}</div><div className="truncate text-muted-foreground">{row.username ?? row.user_id ?? '—'}</div><div><div className="font-mono text-rose-600 dark:text-rose-400">{row.error_code}</div><div className="text-muted-foreground">{row.event_name}</div></div>{row.trace_id ? <Button variant="ghost" size="sm" onClick={() => navigate(`/admin/observability/trace/${encodeURIComponent(row.trace_id ?? '')}`)}>{t('admin.observabilityFailureTrace')}<ArrowRight /></Button> : <span className="text-muted-foreground">{t('admin.observabilityFailureTraceUnavailable')}</span>}</div>)}</div>;
}

export function AdminObservabilityFailuresPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const navigate = useNavigate();
	const [days, setDays] = useState(14);
	const [component, setComponent] = useState<(typeof componentOptions)[number]>('all');
	const [errorType, setErrorType] = useState<(typeof errorTypeOptions)[number]>('all');
	const [userId, setUserId] = useState('');
	const failures = useQuery({
		queryKey: ['admin', user?.id, user?.tenant_id, 'observability-failures', days, component, errorType, userId],
		queryFn: () => adminApi.observabilityFailures(days, { component: component === 'all' ? undefined : component, error_type: errorType === 'all' ? undefined : errorType, user_id: userId.trim() || undefined, limit: 500 }),
		enabled: hasPermission('tenant:manage') || hasPermission('platform:observe'),
	});
	const data = failures.data;

	return <>
		<div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={() => navigate('/admin/observability')}><ArrowLeft />{t('admin.observabilityBack')}</Button></div>
		<AdminHeader title={t('admin.observabilityFailureCenterTitle')} description={t('admin.observabilityFailureCenterDescription')} loading={failures.isFetching} onRefresh={() => void failures.refetch()} />
		<div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3"><div className="text-sm text-muted-foreground">{t('admin.observabilityPeriod')}</div><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.observabilityDays', { count: value })}</option>)}</select><select value={component} onChange={(event) => setComponent(event.target.value as (typeof componentOptions)[number])} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{componentOptions.map((value) => <option key={value} value={value}>{value === 'all' ? t('admin.observabilityFailureAllComponents') : t(`admin.observabilityFailureComponent.${value}`)}</option>)}</select><select value={errorType} onChange={(event) => setErrorType(event.target.value as (typeof errorTypeOptions)[number])} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{errorTypeOptions.map((value) => <option key={value} value={value}>{value === 'all' ? t('admin.observabilityFailureAllTypes') : t(`admin.observabilityFailureType.${value}`)}</option>)}</select><input value={userId} onChange={(event) => setUserId(event.target.value)} placeholder={t('admin.observabilityFailureUserPlaceholder')} className="border-input bg-background h-9 min-w-44 rounded-md border px-3 text-sm" /></div>
		<AdminErrorNotice message={failures.error?.message} />
		{failures.isLoading ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : <>
			<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><Card><CardContent><div className="text-xs text-muted-foreground">{t('admin.observabilityFailureTotal')}</div><div className="mt-1 font-mono text-xl font-semibold">{formatNumber(data?.failure_count ?? 0)}</div></CardContent></Card>{(data?.failure_by_type ?? []).slice(0, 3).map((row) => <Card key={row.key}><CardContent><div className="flex items-center gap-2 text-xs text-muted-foreground"><CircleAlert className="size-3.5" />{t(`admin.observabilityFailureType.${row.key}`)}</div><div className="mt-1 font-mono text-xl font-semibold">{formatNumber(row.count)}</div></CardContent></Card>)}</div>
			<Card><CardHeader><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityRecentFailures')}</CardTitle><CardDescription>{t('admin.observabilityFailureCenterTableDescription')}</CardDescription></CardHeader><CardContent><div className="mb-3 hidden grid-cols-[1.1fr_0.8fr_1.3fr_1fr_1.1fr_0.9fr] gap-3 px-3 text-xs text-muted-foreground md:grid"><div>{t('admin.observabilityFailureTime')}</div><div>{t('admin.observabilityFailureCategory')}</div><div>{t('admin.observabilityFailureSubject')}</div><div>{t('admin.observabilityFailureUser')}</div><div>{t('admin.observabilityFailureReason')}</div><div /></div><FailureRows rows={data?.failures ?? []} /></CardContent></Card>
		</>}
	</>;
}
