import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, BarChart3, Coins, Database, RefreshCw, Users } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { adminApi } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

export function AdminOverviewPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const overview = useQuery({
		queryKey: ['admin', user?.id, 'overview'],
		queryFn: adminApi.overview,
		enabled: user?.role === 'admin',
	});
	const quota = useQuery({
		queryKey: ['admin', user?.id, 'quota'],
		queryFn: adminApi.quota,
		enabled: user?.role === 'admin',
	});
	// 运行指标取与可观测性页同一接口，避免两处口径打架。
	const runtime = useQuery({
		queryKey: ['admin', user?.id, 'observability', 7],
		queryFn: () => adminApi.observability(7),
		enabled: user?.role === 'admin',
	});
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id] });

	const runtimeData = runtime.data;
	const today = runtimeData?.daily?.at(-1);
	const successRate = runtimeData ? runtimeData.success_rate * 100 : null;
	const errorMessage =
		overview.error?.message ?? quota.error?.message ?? runtime.error?.message;

	return (
		<>
			<AdminHeader title={t('admin.nav.overview')} description={t('admin.overviewDescription')} onRefresh={refresh} loading={overview.isFetching || runtime.isFetching} />
			<AdminErrorNotice message={errorMessage} />

			<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
				<MetricCard
					label={t('admin.overviewTodayRequests')}
					value={today ? formatNumber(today.requests) : '...'}
					hint={t('admin.overviewTodayRequestsHint', { count: formatNumber(today?.platform_requests ?? 0) })}
					Icon={BarChart3}
					emphasis
					onClick={() => navigate('/admin/observability')}
				/>
				<MetricCard
					label={t('admin.overviewTodayTokens')}
					value={today ? formatNumber(today.total_tokens) : '...'}
					hint={t('admin.overviewTodayTokensHint', {
						input: formatNumber(today?.input_tokens ?? 0),
						output: formatNumber(today?.output_tokens ?? 0),
					})}
					Icon={Coins}
					emphasis
					onClick={() => navigate('/admin/observability/tokens')}
				/>
				<MetricCard
					label={t('admin.overviewActiveUsers')}
					value={runtimeData ? formatNumber(runtimeData.active_user_count) : '...'}
					hint={t('admin.overviewActiveUsersHint')}
					Icon={Users}
					onClick={() => navigate('/admin/observability/users')}
				/>
				<MetricCard
					label={t('admin.overviewSuccessRate7d')}
					value={successRate == null ? '...' : `${successRate.toFixed(1)}%`}
					hint={t('admin.overviewSuccessRateHint', {
						ok: formatNumber(runtimeData?.successful_requests ?? 0),
						fail: formatNumber(runtimeData?.failed_requests ?? 0),
					})}
					Icon={Activity}
					onClick={() => navigate('/admin/observability')}
				/>
			</div>

			<Card className="mt-4 border-dashed bg-muted/40">
				<CardContent className="flex gap-3 p-4">
					<Badge variant="secondary" className="mt-0.5 h-fit flex-none">{t('admin.overviewCaliberTitle')}</Badge>
					<p className="text-xs leading-relaxed text-muted-foreground">{t('admin.overviewCaliberBody')}</p>
				</CardContent>
			</Card>

			<div className="mt-8 flex items-center gap-2">
				<h2 className="font-heading text-sm font-semibold text-muted-foreground">{t('admin.overviewSnapshotSecondary')}</h2>
				<span className="h-px flex-1 bg-border" />
			</div>

			<div className="mt-4 grid gap-6 lg:grid-cols-2">
				<Card>
					<CardHeader>
						<CardTitle>{t('admin.systemSnapshot')}</CardTitle>
						<CardDescription>{t('admin.systemSnapshotDescription')}</CardDescription>
					</CardHeader>
					<CardContent className="grid gap-3 sm:grid-cols-2">
						<div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{t('admin.systemId')}</div><div className="mt-1 truncate font-mono text-sm">{overview.data?.system_id ?? '...'}</div></div>
						<div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{t('admin.appVersion')}</div><div className="mt-1 font-mono text-sm">{overview.data?.app_version ?? '...'}</div></div>
						<div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{t('admin.coreVersion')}</div><div className="mt-1 font-mono text-sm">{overview.data?.core_version ?? '...'}</div></div>
						<div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{t('admin.activeAccounts')}</div><div className="mt-1 font-mono text-sm">{overview.data ? formatNumber(overview.data.active_account_count) : '...'}</div></div>
					</CardContent>
				</Card>
				<Card>
					<CardHeader>
						<CardTitle>{t('admin.readiness')}</CardTitle>
						<CardDescription>{t('admin.readinessDescription')}</CardDescription>
					</CardHeader>
					<CardContent className="space-y-3 text-sm">
						<div className="flex items-center justify-between gap-3"><span>{t('admin.systemHealth')}</span><Badge variant={overview.data?.health === 'ok' ? 'default' : 'outline'}>{overview.data?.health === 'ok' ? t('admin.healthy') : t('admin.unknown')}</Badge></div>
						<div className="flex items-center justify-between gap-3"><span>{t('admin.poolTokens')}</span><span className="font-mono">{quota.data ? formatNumber(quota.data.pool_tokens) : '...'}</span></div>
						<div className="flex items-center justify-between gap-3"><span>{t('admin.platformRequestCount')}</span><span className="font-mono">{runtimeData ? formatNumber(runtimeData.platform_request_count) : '...'}</span></div>
						<div className="flex items-center gap-2 text-xs text-muted-foreground"><RefreshCw className="size-3.5" />{overview.data?.updated_at ? new Date(overview.data.updated_at).toLocaleString() : '...'}</div>
					</CardContent>
				</Card>
			</div>
		</>
	);
}
