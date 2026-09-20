import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, BarChart3, Clock3, Network, TriangleAlert, Users } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { adminApi } from '@/api';
import type { McpServerStatus, ObservabilityComponentRow, ObservabilityDaily, ObservabilityFailure, ToolExecutionRecord } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { TrendChart } from './trend-chart';

const rangeOptions = [7, 14, 30, 90];

function ToolTrend({ rows }: { rows: ObservabilityDaily[] }) {
	const { t } = useTranslation();
	return <TrendChart rows={rows} emptyText={t('admin.observabilityNoData')} series={[
		{ key: 'calls', label: t('admin.observabilityToolCalls'), colorClass: 'bg-primary/80', value: (row) => row.calls },
		{ key: 'errors', label: t('admin.observabilityToolFailures'), colorClass: 'bg-rose-500/80', value: (row) => row.errors },
	]} />;
}

function ToolItems({ rows, onOpen }: { rows: ObservabilityComponentRow[]; onOpen: (name: string) => void }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoData')}</div>;
	return (
		<div className="space-y-2">
			<div className="hidden grid-cols-[1.35fr_0.7fr_repeat(5,1fr)] gap-3 px-3 text-xs text-muted-foreground md:grid"><div>{t('admin.observabilityTools')}</div><div>{t('admin.observabilityToolKind')}</div><div>{t('admin.observabilityCalls')}</div><div>{t('admin.observabilityComponentSuccess')}</div><div>{t('admin.observabilityLatency')}</div><div>{t('admin.observabilityToolTimeouts')}</div><div>{t('admin.observabilityToolFailures')}</div></div>
			{rows.map((row) => (
				<div key={row.name} role="button" tabIndex={0} onClick={() => onOpen(row.name)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onOpen(row.name); }} className="grid cursor-pointer gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[1.35fr_0.7fr_repeat(5,1fr)] md:items-center">
					<div className="truncate font-mono" title={row.name}>{row.name}<div className="text-xs font-normal text-muted-foreground">{row.mcp_server ?? '—'}</div></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityToolKind')}: </span>{row.tool_kind ?? 'Tool'}</div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityCalls')}: </span><span className="font-mono">{formatNumber(row.call_count)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityComponentSuccess')}: </span><span className="font-mono">{(row.success_rate * 100).toFixed(1)}%</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityLatency')}: </span><span className="font-mono">{row.average_duration_seconds == null ? '—' : `${row.average_duration_seconds.toFixed(2)}s`}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityToolTimeouts')}: </span><span className="font-mono">{formatNumber(row.timeout_count)}</span></div>
					<div><span className="text-xs text-muted-foreground md:hidden">{t('admin.observabilityToolFailures')}: </span><span className="font-mono">{formatNumber(row.failure_count)}</span></div>
				</div>
			))}
		</div>
	);
}

function CallingAgents({ rows }: { rows: Array<{ agent_name: string; call_count: number; percentage: number }> }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoAgentBreakdown')}</div>;
	return <div className="space-y-2">{rows.map((row) => <div key={row.agent_name} className="grid grid-cols-[1.5fr_1fr_1fr] items-center rounded-md bg-muted/50 px-3 py-2 text-sm"><div className="font-mono">{row.agent_name}</div><div className="font-mono">{formatNumber(row.call_count)}</div><div className="text-right font-mono">{(row.percentage * 100).toFixed(1)}%</div></div>)}</div>;
}

function RecentCalls({ rows, onOpen }: { rows: ToolExecutionRecord[]; onOpen: (traceId: string) => void }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoRecentCalls')}</div>;
	return <div className="space-y-2">{rows.map((row, index) => <div key={`${row.occurred_at}-${index}`} role={row.trace_id ? 'button' : undefined} tabIndex={row.trace_id ? 0 : undefined} onClick={() => row.trace_id && onOpen(row.trace_id)} onKeyDown={(event) => { if (row.trace_id && (event.key === 'Enter' || event.key === ' ')) onOpen(row.trace_id); }} className={`grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.25fr_1fr_1.1fr_0.8fr_0.8fr] sm:items-center ${row.trace_id ? 'cursor-pointer transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring' : ''}`}><div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div><div className="truncate">{row.username ?? row.user_id ?? '—'}</div><div className="truncate font-mono">{row.agent_name ?? '—'}</div><div className={row.result === 'success' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>{row.result === 'success' ? t('admin.observabilitySuccess') : t('admin.observabilityFailed')}</div><div className="font-mono">{row.duration_seconds == null ? '—' : `${row.duration_seconds.toFixed(2)}s`}</div></div>)}</div>;
}

function ToolFailures({ rows }: { rows: ObservabilityFailure[] }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityNoFailures')}</div>;
	return <div className="space-y-2">{rows.map((row, index) => <div key={`${row.occurred_at}-${index}`} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs sm:grid-cols-[1.1fr_1.2fr_1fr_1fr] sm:items-center"><div className="text-muted-foreground">{new Date(row.occurred_at).toLocaleString()}</div><div className="font-mono text-rose-600 dark:text-rose-400">{row.error_code}</div><div className="truncate">{row.agent_name ?? row.tool ?? '—'}</div><div className="truncate text-muted-foreground">{row.user_id ?? '—'}</div></div>)}</div>;
}

function McpServers({ rows }: { rows: McpServerStatus[] }) {
	const { t } = useTranslation();
	return (
		<div className="space-y-2">
			<div className="hidden grid-cols-[1.4fr_0.9fr_repeat(4,1fr)] gap-3 px-3 text-xs text-muted-foreground md:grid"><div>{t('admin.observabilityMcpServer')}</div><div>{t('admin.observabilityMcpStatus')}</div><div>{t('admin.observabilityMcpTools')}</div><div>{t('admin.observabilityCalls')}</div><div>{t('admin.observabilityLatency')}</div><div>{t('admin.observabilityToolFailures')}</div></div>
			{rows.map((row) => <div key={row.server_name} className="grid gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm md:grid-cols-[1.4fr_0.9fr_repeat(4,1fr)] md:items-center"><div className="font-mono">{row.server_name}</div><div>{row.status === 'unknown' ? t('admin.observabilityMcpStatusUnknown') : row.status}</div><div className="font-mono">{formatNumber(row.tool_count)}</div><div className="font-mono">{formatNumber(row.call_count)}</div><div className="font-mono">{row.average_duration_seconds == null ? '—' : `${row.average_duration_seconds.toFixed(2)}s`}</div><div className="font-mono">{formatNumber(row.failure_count)}</div></div>)}
		</div>
	);
}

export function AdminToolObservabilityPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const { toolName: toolNameParam } = useParams<{ toolName: string }>();
	const lockedTool = Boolean(toolNameParam);
	const [days, setDays] = useState(14);
	const [toolName, setToolName] = useState(toolNameParam ?? '');
	const [userId, setUserId] = useState('');
	const detail = useQuery({
		queryKey: ['admin', user?.id, 'observability-tool', days, toolName, userId],
		queryFn: () => adminApi.observabilityComponent('tool', days, { name: toolName || undefined, user_id: userId || undefined }),
		enabled: user?.role === 'admin',
	});
	const data = detail.data;
	const toolOptions = data?.items ?? [];
	const openTrace = (traceId: string) => navigate(`/admin/observability/trace/${encodeURIComponent(traceId)}`);

	return (
		<>
			<div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={() => navigate('/admin/observability')}><ArrowLeft />{t('admin.observabilityBack')}</Button></div>
			<AdminHeader title={lockedTool ? `${t('admin.observabilityToolDetailTitle')} · ${toolName}` : t('admin.observabilityTools')} description={t('admin.observabilityToolAnalysisDescription')} loading={detail.isFetching} onRefresh={() => void detail.refetch()} />
			<div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3"><span className="text-sm text-muted-foreground">{t('admin.observabilityPeriod')}</span><select value={days} onChange={(event) => setDays(Number(event.target.value))} className="border-input bg-background h-9 rounded-md border px-3 text-sm">{rangeOptions.map((value) => <option key={value} value={value}>{t('admin.observabilityDays', { count: value })}</option>)}</select><select value={toolName} disabled={lockedTool} onChange={(event) => setToolName(event.target.value)} className="border-input bg-background h-9 rounded-md border px-3 text-sm"><option value="">{t('admin.observabilityAllTools')}</option>{toolOptions.map((row) => <option key={row.name} value={row.name}>{row.name}</option>)}</select><select value={userId} onChange={(event) => setUserId(event.target.value)} className="border-input bg-background h-9 rounded-md border px-3 text-sm"><option value="">{t('admin.observabilityAllUsers')}</option>{(data?.user_options ?? []).map((option) => <option key={option.user_id} value={option.user_id}>{option.username}</option>)}</select></div>
			<AdminErrorNotice message={detail.error?.message} />
			{detail.isLoading ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : (
				<>
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><MetricCard label={t('admin.observabilityCalls')} value={data ? formatNumber(data.call_count) : '—'} Icon={BarChart3} /><MetricCard label={t('admin.observabilityComponentSuccess')} value={data ? `${(data.success_rate * 100).toFixed(1)}%` : '—'} Icon={Network} /><MetricCard label={t('admin.observabilityLatency')} value={data?.average_duration_seconds == null ? '—' : `${data.average_duration_seconds.toFixed(2)}s`} Icon={Clock3} /><MetricCard label={t('admin.observabilityToolFailureTimeouts')} value={data ? `${formatNumber(data.failure_count)} / ${formatNumber(data.timeout_count)}` : '—'} Icon={TriangleAlert} /></div>
					{lockedTool && <div className="grid gap-4 sm:grid-cols-2"><MetricCard label={t('admin.observabilityP95Latency')} value={data?.p95_duration_seconds == null ? '—' : `${data.p95_duration_seconds.toFixed(2)}s`} Icon={Clock3} /><MetricCard label={t('admin.observabilityToolKind')} value={data?.items[0]?.tool_kind ?? 'Tool'} Icon={Network} /></div>}
					<Card><CardHeader><CardTitle>{t('admin.observabilityToolTrendTitle')}</CardTitle><CardDescription>{t('admin.observabilityToolTrendDescription')}</CardDescription></CardHeader><CardContent><ToolTrend rows={data?.daily ?? []} /></CardContent></Card>
					<Card><CardHeader><CardTitle>{t('admin.observabilityToolUsageDetails')}</CardTitle><CardDescription>{t('admin.observabilityToolUsageDetailsDescription')}</CardDescription></CardHeader><CardContent><ToolItems rows={data?.items ?? []} onOpen={(name) => navigate(`/admin/observability/tool/${encodeURIComponent(name)}`)} /></CardContent></Card>
					{data?.mcp_servers?.length ? <Card><CardHeader><CardTitle>{t('admin.observabilityMcpServerStatus')}</CardTitle><CardDescription>{t('admin.observabilityMcpServerStatusDescription')}</CardDescription></CardHeader><CardContent><McpServers rows={data.mcp_servers} /></CardContent></Card> : null}
					{lockedTool && <div className="grid gap-6 lg:grid-cols-2"><Card><CardHeader><CardTitle className="flex items-center gap-2"><Users className="size-4" />{t('admin.observabilityCallingAgents')}</CardTitle><CardDescription>{t('admin.observabilityCallingAgentsDescription')}</CardDescription></CardHeader><CardContent><CallingAgents rows={data?.agent_breakdown ?? []} /></CardContent></Card><Card><CardHeader><CardTitle>{t('admin.observabilityRecentToolCalls')}</CardTitle><CardDescription>{t('admin.observabilityRecentToolCallsDescription')}</CardDescription></CardHeader><CardContent><RecentCalls rows={data?.executions ?? []} onOpen={openTrace} /></CardContent></Card></div>}
					{lockedTool && <Card><CardHeader><CardTitle className="flex items-center gap-2"><TriangleAlert className="size-4" />{t('admin.observabilityToolFailureRecords')}</CardTitle></CardHeader><CardContent><ToolFailures rows={data?.failures ?? []} /></CardContent></Card>}
				</>
			)}
		</>
	);
}
