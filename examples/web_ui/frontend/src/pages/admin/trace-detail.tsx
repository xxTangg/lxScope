import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, CheckCircle2, CircleAlert, Workflow } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';

import { adminApi } from '@/api';
import type { ObservabilityTraceEvent } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { AdminErrorNotice, AdminHeader } from './shared';

function eventTitle(event: ObservabilityTraceEvent, t: (key: string) => string) {
	if (event.component === 'http') return t('admin.observabilityTraceRequest');
	if (event.component === 'agent') return event.agent_name ?? t('admin.observabilityAgents');
	if (event.component === 'model') return event.model ?? t('admin.observabilityModels');
	if (event.component === 'tool') return event.tool ?? t('admin.observabilityTools');
	if (event.component === 'skill') return event.skill_name ?? t('admin.skillAnalyticsEntryTitle');
	return event.event_name;
}

function TraceEvents({ rows }: { rows: ObservabilityTraceEvent[] }) {
	const { t } = useTranslation();
	if (!rows.length) return <div className="text-sm text-muted-foreground">{t('admin.observabilityTraceEmpty')}</div>;
	return (
		<div className="space-y-1">
			{rows.map((event, index) => {
				const success = event.result === 'success';
				return (
					<div key={`${event.occurred_at}-${event.event_name}-${index}`} className="relative pl-8">
						{index < rows.length - 1 && <div className="absolute left-[11px] top-7 bottom-[-4px] w-px bg-border" />}
						<div className={`absolute left-0 top-2 flex size-6 items-center justify-center rounded-full ${success ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400' : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'}`}>
							{success ? <CheckCircle2 className="size-4" /> : <CircleAlert className="size-4" />}
						</div>
						<div className="rounded-md bg-muted/50 px-3 py-2">
							<div className="flex flex-wrap items-center justify-between gap-2">
								<div className="flex items-center gap-2 text-sm font-medium"><span className="font-mono text-xs text-muted-foreground">{event.component}</span>{eventTitle(event, t)}</div>
								<div className="text-xs text-muted-foreground">{new Date(event.occurred_at).toLocaleString()}</div>
							</div>
							<div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
								{event.username && <span>{t('admin.observabilityUserNames')}: {event.username}</span>}
								{event.component === 'tool' && event.tool_kind && <span>{t('admin.observabilityToolKind')}: {event.tool_kind}</span>}
								{event.component === 'tool' && event.mcp_server && <span>{t('admin.observabilityMcpServer')}: {event.mcp_server}</span>}
								{event.duration_seconds != null && <span>{t('admin.observabilityLatency')}: {event.duration_seconds.toFixed(2)}s</span>}
								{event.total_tokens > 0 && <span>{t('admin.tokenAnalyticsTotal')}: {formatNumber(event.total_tokens)}</span>}
								{event.error_code && <span className="text-rose-600 dark:text-rose-400">{event.error_code}</span>}
							</div>
						</div>
					</div>
				);
			})}
		</div>
	);
}

export function AdminTraceDetailPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const navigate = useNavigate();
	const { traceId } = useParams<{ traceId: string }>();
	const trace = useQuery({
		queryKey: ['admin', user?.id, user?.tenant_id, 'observability-trace', traceId],
		queryFn: () => adminApi.observabilityTrace(traceId ?? ''),
		enabled: (hasPermission('tenant:manage') || hasPermission('platform:observe')) && Boolean(traceId),
	});
	const data = trace.data;

	return (
		<>
			<div className="flex items-center gap-2">
				<Button variant="ghost" size="sm" onClick={() => navigate(-1)}><ArrowLeft />{t('admin.observabilityTraceBack')}</Button>
			</div>
			<AdminHeader title={t('admin.observabilityTraceTitle')} description={t('admin.observabilityTraceDescription')} loading={trace.isFetching} onRefresh={() => void trace.refetch()} />
			<AdminErrorNotice message={trace.error?.message} />
			{trace.isLoading ? <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('common.loading')}</div> : (
				<Card>
					<CardHeader><CardTitle className="flex items-center gap-2"><Workflow className="size-4" />{data?.trace_id ?? traceId}</CardTitle><CardDescription>{data?.request_id ? `${t('admin.observabilityTraceRequestId')}: ${data.request_id}` : t('admin.observabilityTraceDescription')}</CardDescription></CardHeader>
					<CardContent><TraceEvents rows={data?.events ?? []} /></CardContent>
				</Card>
			)}
		</>
	);
}
