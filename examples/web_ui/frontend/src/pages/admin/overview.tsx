import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, BadgeDollarSign, Database, RefreshCw, Users } from 'lucide-react';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { adminApi } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

export function AdminOverviewPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const queryClient = useQueryClient();
	const overview = useQuery({
		queryKey: ['admin', user?.id, 'overview'],
		queryFn: adminApi.overview,
		enabled: hasPermission('tenant:manage'),
	});
	const quota = useQuery({
		queryKey: ['admin', user?.id, 'quota'],
		queryFn: adminApi.quota,
		enabled: hasPermission('tenant:manage'),
	});
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id] });

	return (
		<>
			<AdminHeader title={t('admin.nav.overview')} description={t('admin.overviewDescription')} onRefresh={refresh} loading={overview.isFetching} />
			<AdminErrorNotice message={overview.error?.message ?? quota.error?.message} />
			<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
				<MetricCard label={t('admin.poolTokens')} value={overview.data ? formatNumber(overview.data.pool_tokens) : '...'} Icon={Database} />
				<MetricCard label={t('admin.totalRecharged')} value={overview.data?.total_recharged ?? '...'} Icon={BadgeDollarSign} />
				<MetricCard label={t('admin.accountCount')} value={overview.data ? formatNumber(overview.data.account_count) : '...'} Icon={Users} />
				<MetricCard label={t('admin.systemHealth')} value={overview.data?.health === 'ok' ? t('admin.healthy') : '...'} Icon={Activity} />
			</div>
			<div className="grid gap-6 lg:grid-cols-2">
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
						<div className="flex items-center justify-between gap-3"><span>{t('admin.testDefaultTokens')}</span><span className="font-mono">{quota.data ? formatNumber(quota.data.test_default_tokens) : '...'}</span></div>
						<div className="flex items-center gap-2 text-xs text-muted-foreground"><RefreshCw className="size-3.5" />{overview.data?.updated_at ? new Date(overview.data.updated_at).toLocaleString() : '...'}</div>
					</CardContent>
				</Card>
			</div>
		</>
	);
}
