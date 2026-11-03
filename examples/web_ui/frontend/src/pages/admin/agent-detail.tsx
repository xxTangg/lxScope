import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Bot, Clock3, Coins, Network, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { adminApi } from '@/api';
import type { AgentExecutionRecord } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';

const rangeOptions = [7, 14, 30, 90];

function ExecutionList({ rows, onOpen }: { rows: AgentExecutionRecord[]; onOpen: (traceId: string) => void }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoData')}</div>;
	return (
		<div className="space-y-2">
			<div className="hidden grid-cols-[1.2fr_1.1fr_0.8fr_0.8fr_0.8fr_0.8fr] gap-3 px-3 text-xs text-muted-foreground md:grid">
				<div>{t('admin.observabilityOccurredAt')}</div><div>{t('admin.observabilityUserNames')}</div><div>{t('admin.observabilityResult')}</div><div>{t('admin.observabilityLatency')}</div><div>{t('admin.tokenAnalyticsTotal')}</div><div>{t('admin.observabilityAgentToolCalls')}</div>
			</div>
			{rows.map((row, index) => {
				const clickable = Boolean(row.trace_id);
				return (
					<div
						key={`${row.occurred_at}-${row.request_id ?? index}`}
						role={clickable ? 'button' : undefined}
						tabIndex={clickable ? 0 : undefined}
						onClick={() => row.trace_id && onOpen(row.trace_id)}
						onKeyDown={(event) => { if (clickable && (event.key === 'Enter' || event.key === ' ')) row.trace_id && onOpen(row.trace_id); }}
						className={`grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm md:grid-cols-[1.2fr_1.1fr_0.8fr_0.8fr_0.8fr_0.8fr] md:items-center ${clickable ? 'cursor-pointer transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring' : ''}`}
					>
						<div className="text-xs text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div>
						<div className="truncate"><div className="font-medium">{row.username ?? '—'}</div><div className="font-mono text-xs text-muted-foreground">{row.user_id ?? '—'}</div></div>
						<div className={row.result === 'success' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>{row.result === 'success' ? t('admin.observabilitySuccess') : t('admin.observabilityFailed')}</div>
						<div className="font-mono">{row.duration_seconds == null ? '—' : `${row.duration_seconds.toFixed(2)}s`}</div>
						<div className="font-mono">{formatNumber(row.total_tokens)}</div>
						<div className="font-mono">{formatNumber(row.tool_call_count)}</div>
					</div>
				);
			})}
		</div>
	);
}

export function AdminAgentDetailPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const { agentName: agentNameParam } = useParams<{ agentName: string }>();
	const agentName = agentNameParam ?? '';
	const [days, setDays] = useState(14);
	const detail = useQuery({
		queryKey: ['admin', user?.id, 'observability-agent', agentName, days],
		queryFn: () => adminApi.observabilityAgent(agentName, days),
		enabled: user?.role === 'admin' && Boolean(agentName),
	});
	const data = detail.data;

	return (
		<>
			<div className="flex items-center gap-2">
				<Button variant="ghost" size="sm" onClick={() => navigate('/admin/observability')}><ArrowLeft />{t('admin.observabilityBack')}</Button>
			</div>
			<AdminHeader title={`${t('admin.observabilityAgentDetailTitle')} · ${agentName}`} description={t('admin.observabilityAgentDetailDescription')} loading={detail.isFetching} onRefresh={() => void detail.refetch()} />
			<div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3"><div className="text-sm text-muted-foreground">{t('admin.observabilityPeriod')}</div><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.observabilityDays', { count: value })}</option>)}</select></div>
			<AdminErrorNotice message={detail.error?.message} />
			{detail.isLoading ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : (
				<>
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
						<MetricCard label={t('admin.observabilityAgentExecutions')} value={data ? formatNumber(data.call_count) : '—'} Icon={Bot} />
						<MetricCard label={t('admin.observabilityComponentSuccess')} value={data ? `${(data.success_rate * 100).toFixed(1)}%` : '—'} Icon={Bot} />
						<MetricCard label={t('admin.observabilityLatency')} value={data?.average_duration_seconds == null ? '—' : `${data.average_duration_seconds.toFixed(2)}s`} Icon={Clock3} />
						<MetricCard label={t('admin.tokenAnalyticsTotal')} value={data ? formatNumber(data.total_tokens) : '—'} Icon={Coins} />
						<MetricCard label={t('admin.observabilityAgentToolCalls')} value={data ? formatNumber(data.tool_call_count) : '—'} Icon={Network} />
					</div>
					<Card>
						<CardHeader><CardTitle>{t('admin.observabilityAgentExecutionRecords')}</CardTitle><CardDescription>{t('admin.observabilityAgentExecutionRecordsDescription')}</CardDescription></CardHeader>
						<CardContent><ExecutionList rows={data?.executions ?? []} onOpen={(traceId) => navigate(`/admin/observability/trace/${encodeURIComponent(traceId)}`)} /></CardContent>
					</Card>
					{data?.failure_count ? <Card><CardHeader><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityFailures')}</CardTitle></CardHeader><CardContent><div className="text-sm text-muted-foreground">{t('admin.observabilityFailedRequests')}: {formatNumber(data.failure_count)}</div></CardContent></Card> : null}
				</>
			)}
		</>
	);
}
