import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Activity, BarChart3, Coins, Users } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { AdminErrorNotice, AdminHeader, MetricCard } from './shared';
import { adminApi } from '@/api';
import type { LedgerEntry } from '@/api/admin';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

type TrendMode = 'hour' | 'day';

type TrendPoint = {
	label: string;
	value: number;
	error: number;
	tokens: number;
	highlight: boolean;
	showLabel: boolean;
};

function TrendBars({ points }: { points: TrendPoint[] }) {
	const max = Math.max(...points.map((point) => point.value), 0);
	return (
		<div className="flex h-40 items-end gap-1 pt-2">
			{points.map((point, index) => {
				const barHeight = point.value === 0 || max === 0 ? 0 : Math.max((point.value / max) * 100, 4);
				const errorHeight = point.error === 0 || max === 0 ? 0 : Math.max((point.error / max) * 100, 3);
				return (
					<div
						key={`${point.label}-${index}`}
						className="flex h-full min-w-0 flex-1 flex-col justify-end items-center gap-0.5"
						title={`${point.label} · 业务调用 ${formatNumber(point.value)} · 失败 ${formatNumber(point.error)} · Token ${formatNumber(point.tokens)}`}
					>
						{point.value === 0
							? <div className="w-full max-w-8 rounded-t-sm bg-muted" style={{ height: 3 }} />
							: <div className={`w-full max-w-8 rounded-t-sm bg-primary ${point.highlight ? 'ring-1 ring-primary/60' : ''}`} style={{ height: `${barHeight}%` }} />}
						{errorHeight > 0 && <div className="w-full max-w-8 bg-destructive/70" style={{ height: `${errorHeight}%` }} />}
						<div className={`font-mono text-[9px] leading-3 ${point.highlight ? 'text-primary' : 'text-muted-foreground'}`}>
							{point.showLabel ? point.label : ''}
						</div>
					</div>
				);
			})}
		</div>
	);
}

export function AdminOverviewPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [trendMode, setTrendMode] = useState<TrendMode>('hour');
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
	const ledger = useQuery({
		queryKey: ['admin', user?.id, 'ledger', 5],
		queryFn: () => adminApi.ledger(5),
		enabled: user?.role === 'admin',
	});
	// 运行指标取与可观测性页同一接口，避免两处口径打架。14 天窗口：活跃用户看全程，成功率取最近 7 天切片。
	const runtime = useQuery({
		queryKey: ['admin', user?.id, 'observability', 14],
		queryFn: () => adminApi.observability(14),
		enabled: user?.role === 'admin',
	});
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id] });

	const runtimeData = runtime.data;
	const today = runtimeData?.daily?.at(-1);
	// 7 天成功率 = 最近 7 个日切片的业务调用合计，避免整窗口径混入「7 天」标签。
	const last7 = runtimeData?.daily?.slice(-7) ?? [];
	const ok7 = last7.reduce((sum, row) => sum + Math.max(row.requests - row.errors, 0), 0);
	const total7 = last7.reduce((sum, row) => sum + row.requests, 0);
	const successRate = total7 > 0 ? (ok7 / total7) * 100 : runtimeData ? runtimeData.success_rate * 100 : null;
	const trendPoints: TrendPoint[] = trendMode === 'hour'
		? (runtimeData?.hourly ?? []).map((row) => ({
			label: String(row.hour).padStart(2, '0'),
			value: row.requests,
			error: row.errors,
			tokens: row.total_tokens,
			highlight: row.hour === new Date().getHours(),
			showLabel: row.hour % 3 === 0,
		}))
		: (runtimeData?.daily ?? []).map((row, index, rows) => ({
			label: row.date.slice(5),
			value: row.requests,
			error: row.errors,
			tokens: row.total_tokens,
			highlight: index === rows.length - 1,
			showLabel: true,
		}));
	const errorMessage =
		overview.error?.message ?? quota.error?.message ?? runtime.error?.message ?? ledger.error?.message;

	return (
		<>
			<AdminHeader title={t('admin.centerTitle')} description={t('admin.centerDescription')} onRefresh={refresh} loading={overview.isFetching || runtime.isFetching} />
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
					hint={t('admin.overviewActiveUsersHint', { count: formatNumber(overview.data?.account_count ?? 0) })}
					Icon={Users}
					onClick={() => navigate('/admin/observability/users')}
				/>
				<MetricCard
					label={t('admin.overviewSuccessRate7d')}
					value={successRate == null ? '...' : `${successRate.toFixed(1)}%`}
					hint={t('admin.overviewSuccessRateHint', { ok: formatNumber(ok7), fail: formatNumber(total7 - ok7) })}
					Icon={Activity}
					onClick={() => navigate('/admin/observability')}
				/>
			</div>

			<Card className="mt-4 border-dashed bg-muted/40">
				<CardContent className="flex gap-3 p-4">
					<Badge variant="secondary" className="mt-0.5 h-fit flex-none">{t('admin.overviewCaliberTitle')}</Badge>
					<p className="whitespace-pre-line text-xs leading-relaxed text-muted-foreground">{t('admin.overviewCaliberBody')}</p>
				</CardContent>
			</Card>

			<div className="mt-6 grid gap-6 lg:grid-cols-2">
				<Card>
					<CardHeader>
						<CardTitle>{t('admin.overviewLedgerTitle')}</CardTitle>
						<CardDescription>{t('admin.overviewLedgerDescription')}</CardDescription>
					</CardHeader>
					<CardContent>
						<div className="overflow-x-auto rounded-lg border">
							<table className="w-full text-sm">
								<thead className="border-b bg-muted/40">
									<tr>
										<th className="px-3 py-2 text-left font-medium">{t('admin.createdAt')}</th>
										<th className="px-3 py-2 text-left font-medium">{t('admin.ledgerType')}</th>
										<th className="px-3 py-2 text-right font-medium">{t('admin.deltaTokens')}</th>
										<th className="px-3 py-2 text-right font-medium">{t('admin.balanceAfter')}</th>
									</tr>
								</thead>
								<tbody>
									{(ledger.data?.entries ?? []).map((entry: LedgerEntry) => (
										<tr key={entry.ledger_id} className="border-b last:border-0">
											<td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{new Date(entry.created_at).toLocaleString()}</td>
											<td className="px-3 py-2"><Badge variant="outline">{entry.type}</Badge></td>
											<td className={`px-3 py-2 text-right font-mono ${entry.delta_tokens < 0 ? 'text-destructive' : 'text-emerald-600'}`}>
												{entry.delta_tokens > 0 ? '+' : ''}{formatNumber(entry.delta_tokens)}
											</td>
											<td className="px-3 py-2 text-right font-mono">{formatNumber(entry.balance_after)}</td>
										</tr>
									))}
									{!ledger.isPending && (ledger.data?.entries ?? []).length === 0 && (
										<tr><td colSpan={4} className="px-3 py-8 text-center text-muted-foreground">{t('admin.noLedger')}</td></tr>
									)}
								</tbody>
							</table>
						</div>
					</CardContent>
				</Card>
				<Card>
					<CardHeader>
						<CardTitle>{t('admin.systemSnapshot')}</CardTitle>
						<CardDescription>{t('admin.systemSnapshotDescription')}</CardDescription>
					</CardHeader>
					<CardContent className="space-y-1 text-sm">
						<div className="flex items-center justify-between gap-3 border-b py-2 last:border-0"><span className="text-muted-foreground">{t('admin.systemId')}</span><span className="truncate font-mono">{overview.data?.system_id ?? '...'}</span></div>
						<div className="flex items-center justify-between gap-3 border-b py-2 last:border-0"><span className="text-muted-foreground">{t('admin.appVersion')}</span><span className="font-mono">{overview.data?.app_version ?? '...'}</span></div>
						<div className="flex items-center justify-between gap-3 border-b py-2 last:border-0"><span className="text-muted-foreground">{t('admin.coreVersion')}</span><span className="font-mono">{overview.data?.core_version ?? '...'}</span></div>
						<div className="flex items-center justify-between gap-3 border-b py-2 last:border-0"><span className="text-muted-foreground">{t('admin.poolTokens')}</span><span className="font-mono">{quota.data ? formatNumber(quota.data.pool_tokens) : '...'}</span></div>
						<div className="flex items-center justify-between gap-3 py-2"><span className="text-muted-foreground">{t('admin.lastUpdated')}</span><span className="font-mono text-xs">{overview.data?.updated_at ? new Date(overview.data.updated_at).toLocaleString() : '...'}</span></div>
					</CardContent>
				</Card>
			</div>

			<Card className="mt-6">
				<CardHeader className="flex-row items-start justify-between space-y-0">
					<div>
						<CardTitle>{t('admin.overviewTrendTitle')}</CardTitle>
						<CardDescription>{t('admin.overviewTrendDescription')}</CardDescription>
					</div>
					<div className="flex gap-1">
						<Button variant={trendMode === 'hour' ? 'secondary' : 'ghost'} size="sm" onClick={() => setTrendMode('hour')}>{t('admin.overviewTrendByHour')}</Button>
						<Button variant={trendMode === 'day' ? 'secondary' : 'ghost'} size="sm" onClick={() => setTrendMode('day')}>{t('admin.overviewTrendByDay')}</Button>
					</div>
				</CardHeader>
				<CardContent>
					<TrendBars points={trendPoints} />
					<div className="mt-3 flex flex-wrap gap-4 border-t pt-3 text-xs text-muted-foreground">
						<span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm bg-primary" />{t('admin.overviewLegendBusiness')}</span>
						<span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm bg-destructive/70" />{t('admin.overviewLegendFailure')}</span>
						<span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm bg-muted" />{t('admin.overviewLegendNoData')}</span>
					</div>
				</CardContent>
			</Card>
		</>
	);
}
