import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Loader2, Save, ShieldCheck } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

import { AdminErrorNotice, AdminHeader, Pagination } from './shared';
import { adminApi, type LedgerEntry } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RechargeMethodsCard } from '@/features/longxin-admin';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

const LEDGER_PAGE_SIZE = 20;

export function AdminQuotaPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const queryClient = useQueryClient();
	const [testDefaultTokens, setTestDefaultTokens] = useState('');
	const [ledgerPage, setLedgerPage] = useState(1);
	const [ledgerType, setLedgerType] = useState('');
	const [ledgerKeyword, setLedgerKeyword] = useState('');
	const quota = useQuery({ queryKey: ['admin', user?.id, 'quota'], queryFn: adminApi.quota, enabled: hasPermission('tenant:manage') });
	const ledger = useQuery({ queryKey: ['admin', user?.id, 'ledger'], queryFn: () => adminApi.ledger(200), enabled: hasPermission('tenant:manage') });
	useEffect(() => { if (quota.data) setTestDefaultTokens(String(quota.data.test_default_tokens)); }, [quota.data]);
	const updateQuota = useMutation({ mutationFn: adminApi.updateQuota, onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id] }) });
	const filteredLedger = useMemo(() => {
		const keyword = ledgerKeyword.trim().toLowerCase();
		return (ledger.data?.entries ?? []).filter((entry) => (!ledgerType || entry.type === ledgerType) && (!keyword || [entry.ledger_id, entry.order_id, entry.related_user_id, entry.source].some((value) => value?.toLowerCase().includes(keyword))));
	}, [ledger.data?.entries, ledgerKeyword, ledgerType]);
	const pageItems = filteredLedger.slice((ledgerPage - 1) * LEDGER_PAGE_SIZE, ledgerPage * LEDGER_PAGE_SIZE);
	const submitQuota = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); updateQuota.mutate(Number(testDefaultTokens) || 0); };
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id] });

	return (
		<>
			<AdminHeader title={t('admin.nav.quota')} description={t('admin.quotaDescription')} onRefresh={refresh} loading={quota.isFetching || ledger.isFetching} />
			<AdminErrorNotice message={quota.error?.message ?? ledger.error?.message ?? updateQuota.error?.message} />
			<Card>
				<CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="size-4" />{t('admin.quota')}</CardTitle><CardDescription>{t('admin.quotaModuleDescription')}</CardDescription></CardHeader>
				<CardContent><div className="grid gap-3 sm:grid-cols-3"><div className="rounded-lg bg-muted/60 p-3"><div className="text-xs text-muted-foreground">{t('admin.poolTokens')}</div><div className="mt-1 font-mono text-lg font-semibold">{quota.data ? formatNumber(quota.data.pool_tokens) : '...'}</div></div><div className="rounded-lg bg-muted/60 p-3"><div className="text-xs text-muted-foreground">{t('admin.totalRecharged')}</div><div className="mt-1 font-mono text-lg font-semibold">{quota.data?.total_recharged ?? '...'}</div></div><div className="rounded-lg bg-muted/60 p-3"><div className="text-xs text-muted-foreground">{t('admin.systemId')}</div><div className="mt-1 truncate font-mono text-xs">{quota.data?.system_id ?? '...'}</div></div></div><form onSubmit={submitQuota} className="mt-5 max-w-md space-y-2"><Label htmlFor="admin-test-default">{t('admin.testDefaultTokens')}</Label><div className="flex gap-2"><Input id="admin-test-default" type="number" min={0} value={testDefaultTokens} onChange={(e) => setTestDefaultTokens(e.target.value)} /><Button type="submit" variant="outline" disabled={updateQuota.isPending}>{updateQuota.isPending ? <Loader2 className="animate-spin" /> : <Save />}{t('common.save')}</Button></div></form></CardContent>
			</Card>
			<Card>
				<CardHeader><CardTitle className="flex items-center gap-2"><Database className="size-4" />{t('admin.ledgerTitle')}</CardTitle><CardDescription>{t('admin.ledgerDescription')}</CardDescription></CardHeader>
				<CardContent className="space-y-4"><div className="flex flex-wrap gap-3"><Input value={ledgerKeyword} onChange={(e) => { setLedgerKeyword(e.target.value); setLedgerPage(1); }} placeholder={t('admin.ledgerSearch')} className="max-w-xs" /><select value={ledgerType} onChange={(e) => { setLedgerType(e.target.value); setLedgerPage(1); }} className="border-input bg-background h-9 rounded-md border px-3 text-sm"><option value="">{t('admin.allLedgerTypes')}</option>{Array.from(new Set((ledger.data?.entries ?? []).map((entry) => entry.type))).map((type) => <option key={type} value={type}>{type}</option>)}</select></div><div className="overflow-x-auto rounded-lg border"><table className="w-full text-sm"><thead className="border-b bg-muted/40"><tr><th className="px-3 py-2 text-left font-medium">{t('admin.createdAt')}</th><th className="px-3 py-2 text-left font-medium">{t('admin.ledgerType')}</th><th className="px-3 py-2 text-right font-medium">{t('admin.deltaTokens')}</th><th className="px-3 py-2 text-right font-medium">{t('admin.balanceAfter')}</th><th className="px-3 py-2 text-left font-medium">{t('admin.source')}</th><th className="px-3 py-2 text-left font-medium">{t('admin.relatedOrder')}</th></tr></thead><tbody>{pageItems.map((entry: LedgerEntry) => <tr key={entry.ledger_id} className="border-b last:border-0"><td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{new Date(entry.created_at).toLocaleString()}</td><td className="px-3 py-2"><Badge variant="outline">{entry.type}</Badge></td><td className={`px-3 py-2 text-right font-mono ${entry.delta_tokens < 0 ? 'text-destructive' : 'text-emerald-600'}`}>{entry.delta_tokens > 0 ? '+' : ''}{formatNumber(entry.delta_tokens)}</td><td className="px-3 py-2 text-right font-mono">{formatNumber(entry.balance_after)}</td><td className="px-3 py-2 text-muted-foreground">{entry.source}</td><td className="px-3 py-2 font-mono text-xs text-muted-foreground">{entry.order_id ?? entry.related_user_id ?? '—'}</td></tr>)}{!ledger.isPending && pageItems.length === 0 && <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">{t('admin.noLedger')}</td></tr>}</tbody></table></div><Pagination page={ledgerPage} pageSize={LEDGER_PAGE_SIZE} total={filteredLedger.length} onPageChange={setLedgerPage} loading={ledger.isFetching} /></CardContent>
			</Card>
			<RechargeMethodsCard />
		</>
	);
}
