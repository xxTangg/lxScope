import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Loader2, UserPlus, UserRoundX } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { useLocation } from 'react-router-dom';

import { AdminErrorNotice, AdminHeader, Pagination } from './shared';
import { adminApi, type AdminUser } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PlanOrdersCard, planBillingApi } from '@/features/longxin-admin';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

function statusVariant(status: AdminUser['status']) {
	if (status === 'active') return 'default' as const;
	if (status === 'banned') return 'destructive' as const;
	return 'outline' as const;
}

export function AdminMembersPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const usesExternalDirectory = user?.identity_provider === 'logto';
	const location = useLocation();
	const queryClient = useQueryClient();
	const [keyword, setKeyword] = useState('');
	const [searchEditable, setSearchEditable] = useState(false);
	const [statusFilter, setStatusFilter] = useState('');
	const [page, setPage] = useState(1);
	const [newUsername, setNewUsername] = useState('');
	const [newPassword, setNewPassword] = useState('');
	const [newExternalUserId, setNewExternalUserId] = useState('');
	const [newPlanId, setNewPlanId] = useState('plan_basic');
	const [bonusTokens, setBonusTokens] = useState('0');
	useEffect(() => {
		setKeyword('');
		setSearchEditable(false);
		setStatusFilter('');
		setPage(1);
	}, [location.pathname]);
	useEffect(() => {
		const syncRestoredSearchValue = () => {
			const input = document.querySelector('[data-admin-member-search]') as HTMLInputElement | null;
			if (input && input.value !== keyword) {
				input.value = keyword;
			}
		};
		const timers = [0, 100, 500, 1000].map((delay) => window.setTimeout(syncRestoredSearchValue, delay));
		const handlePageShow = () => {
			syncRestoredSearchValue();
			window.setTimeout(syncRestoredSearchValue, 0);
		};
		window.addEventListener('pageshow', handlePageShow);
		window.addEventListener('load', handlePageShow);
		return () => {
			timers.forEach((timer) => window.clearTimeout(timer));
			window.removeEventListener('pageshow', handlePageShow);
			window.removeEventListener('load', handlePageShow);
		};
	}, [keyword]);
	const plans = useQuery({
		queryKey: ['admin', user?.id, 'plans'],
		queryFn: planBillingApi.plans,
		enabled: hasPermission('tenant:manage') && !usesExternalDirectory,
		refetchOnMount: 'always',
	});
	const users = useQuery({
		queryKey: ['admin', user?.id, 'users', keyword, statusFilter, page],
		queryFn: () => adminApi.users({ keyword: keyword || undefined, status: statusFilter || undefined, page, page_size: 20 }),
		enabled: hasPermission('tenant:manage') && !usesExternalDirectory,
		refetchOnMount: 'always',
	});
	const tenantMembers = useQuery({
		queryKey: ['admin', user?.id, 'tenant-members', keyword, statusFilter, page],
		queryFn: () => adminApi.tenantMembers({ keyword: keyword || undefined, status: statusFilter || undefined, page, page_size: 20 }),
		enabled: hasPermission('tenant:manage') && usesExternalDirectory,
		refetchOnMount: 'always',
	});
	const refresh = () => {
		void Promise.all([
			queryClient.invalidateQueries({ queryKey: ['admin', user?.id] }),
			queryClient.invalidateQueries({ queryKey: ['longxin-plan-billing', user?.id] }),
		]);
	};
	const createUser = useMutation({
		mutationFn: adminApi.createUser,
		onSuccess: () => { setNewUsername(''); setNewPassword(''); setNewPlanId('plan_basic'); setBonusTokens('0'); refresh(); },
	});
	const createTenantMember = useMutation({
		mutationFn: adminApi.addTenantMember,
		onSuccess: () => { setNewExternalUserId(''); refresh(); },
	});
	const updateUser = useMutation({ mutationFn: ({ userId, status }: { userId: string; status: 'active' | 'banned' }) => adminApi.updateUser(userId, { status }), onSuccess: refresh });
	const deleteUser = useMutation({ mutationFn: ({ userId, reason }: { userId: string; reason: string }) => adminApi.deleteUser(userId, reason), onSuccess: refresh });
	const resetPassword = useMutation({
		mutationFn: ({ userId, reason, admin_password }: { userId: string; reason: string; admin_password: string }) => adminApi.resetPassword(userId, { reason, admin_password }),
		onSuccess: (data) => { window.alert(`${t('admin.temporaryPassword')}: ${data.temporary_password}\n${t('admin.expiresAt')}: ${data.expires_at}`); refresh(); },
	});
	const revokeSessions = useMutation({ mutationFn: adminApi.revokeSessions, onSuccess: refresh });
	const updateTenantMember = useMutation({
		mutationFn: ({ membershipId, body }: { membershipId: string; body: { role?: string; status?: 'active' | 'disabled' | 'removed' } }) => adminApi.updateTenantMember(membershipId, body),
		onSuccess: refresh,
	});
	const error = [users.error?.message, tenantMembers.error?.message, plans.error?.message, createUser.error?.message, createTenantMember.error?.message, updateUser.error?.message, updateTenantMember.error?.message, deleteUser.error?.message, resetPassword.error?.message, revokeSessions.error?.message].find((item): item is string => Boolean(item));
	const items: AdminUser[] = usesExternalDirectory
		? (tenantMembers.data?.members ?? []).map((member) => ({
				id: member.id,
				username: member.username,
				display_name: member.display_name,
				external_user_id: member.external_user_id,
				organization_roles: member.organization_roles,
				role: 'user',
				membership_role: member.role,
				membership_status: member.status,
				permissions: member.permissions,
				status: member.status === 'active' ? 'active' : member.status === 'removed' ? 'deleted' : 'banned',
				plan_id: 'plan_none', plan_name: '', monthly_quota: 0, monthly_used: 0, bonus_tokens: 0, account_type: 'standard',
				created_at: member.created_at, updated_at: member.updated_at,
			}))
		: (users.data?.users ?? []);
	const listing = usesExternalDirectory ? tenantMembers.data : users.data;

	const submitCreate = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		createUser.mutate({ username: newUsername.trim(), initial_password: newPassword, plan_id: newPlanId, bonus_tokens: Number(bonusTokens) || 0 });
	};
	const submitTenantMember = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		createTenantMember.mutate({ external_user_id: newExternalUserId.trim() });
	};
	const reset = () => setPage(1);
	const handleResetPassword = (item: AdminUser) => {
		const password = window.prompt(t('admin.adminPasswordPrompt'));
		const reason = password ? window.prompt(t('admin.resetPasswordReasonPrompt')) : null;
		if (!password || !reason || reason.trim().length < 4) { if (reason !== null) window.alert(t('admin.reasonRequired')); return; }
		resetPassword.mutate({ userId: item.id, reason: reason.trim(), admin_password: password });
	};

	return (
		<>
			<AdminHeader title={t('admin.nav.members')} description={t('admin.membersDescription')} onRefresh={refresh} loading={usesExternalDirectory ? tenantMembers.isFetching : users.isFetching} />
			<AdminErrorNotice message={error} />
			<Card>
				<CardHeader><CardTitle className="flex items-center gap-2"><UserPlus className="size-4" />{t('admin.members')}</CardTitle><CardDescription>{usesExternalDirectory ? t('admin.logtoMemberDirectoryNotice') : t('admin.memberModuleDescription')}</CardDescription></CardHeader>
				<CardContent className="space-y-5">
					{usesExternalDirectory && <form onSubmit={submitTenantMember} autoComplete="off" className="grid gap-3 rounded-lg bg-muted/40 p-3 md:grid-cols-[2fr_auto]">
						<div className="space-y-1.5"><Label htmlFor="admin-logto-user-id">{t('auth.userId')}</Label><Input id="admin-logto-user-id" name="logto-user-id" autoComplete="off" value={newExternalUserId} onChange={(e) => setNewExternalUserId(e.target.value)} placeholder={t('admin.logtoUserIdPlaceholder')} required /></div>
						<div className="flex items-end"><Button type="submit" className="w-full" disabled={createTenantMember.isPending}>{createTenantMember.isPending ? <Loader2 className="animate-spin" /> : <UserPlus />}{t('admin.bindMember')}</Button></div>
					</form>}
					{!usesExternalDirectory && <form onSubmit={submitCreate} autoComplete="off" className="grid gap-3 rounded-lg bg-muted/40 p-3 md:grid-cols-5">
						<div className="space-y-1.5"><Label htmlFor="admin-new-username">{t('admin.username')}</Label><Input id="admin-new-username" name="new-member-account" autoComplete="off" data-form-type="other" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} required /></div>
						<div className="space-y-1.5"><Label htmlFor="admin-new-password">{t('admin.initialPassword')}</Label><Input id="admin-new-password" name="new-member-initial-password" type="password" autoComplete="new-password" minLength={8} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required /></div>
						<div className="space-y-1.5"><Label htmlFor="admin-bonus-tokens">{t('admin.bonusTokens')}</Label><Input id="admin-bonus-tokens" type="number" min={0} value={bonusTokens} onChange={(e) => setBonusTokens(e.target.value)} /></div>
						<div className="space-y-1.5"><Label htmlFor="admin-plan">{t('admin.plan')}</Label><select id="admin-plan" value={newPlanId} onChange={(e) => setNewPlanId(e.target.value)} className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm">{(plans.data?.plans ?? []).map((plan) => <option key={plan.id} value={plan.id}>{plan.name} · {plan.monthly_quota.toLocaleString()} Token/月</option>)}</select></div>
						<div className="flex items-end"><Button type="submit" className="w-full" disabled={createUser.isPending}>{createUser.isPending ? <Loader2 className="animate-spin" /> : <UserPlus />}{t('admin.createMember')}</Button></div>
					</form>}
					<div className="flex flex-wrap items-center gap-3"><Input key={location.key} id={`admin-member-search-${location.key}`} name={`member-filter-${location.key}`} data-admin-member-search data-form-type="other" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" type="search" role="searchbox" autoComplete="off" autoCapitalize="none" spellCheck={false} readOnly={!searchEditable} value={keyword} onFocus={(e) => { e.currentTarget.value = keyword; setSearchEditable(true); }} onBlur={() => setSearchEditable(false)} onChange={(e) => { if (!searchEditable) { e.currentTarget.value = keyword; return; } setKeyword(e.target.value); reset(); }} placeholder={t('admin.searchMembers')} className="max-w-xs" /><select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); reset(); }} className="border-input bg-background h-9 rounded-md border px-3 text-sm"><option value="">{t('admin.allStatuses')}</option><option value="active">{t('admin.statusValues.active')}</option>{usesExternalDirectory ? <><option value="disabled">disabled</option><option value="removed">removed</option></> : <><option value="locked">{t('admin.statusValues.locked')}</option><option value="banned">{t('admin.statusValues.banned')}</option></>}{!usesExternalDirectory && <option value="deleted">{t('admin.statusValues.deleted')}</option>}</select>{(usesExternalDirectory ? tenantMembers.isFetching : users.isFetching) && <Loader2 className="size-4 animate-spin text-muted-foreground" />}</div>
						<div className="overflow-x-auto rounded-lg border"><table className="w-full text-sm"><thead className="border-b bg-muted/40"><tr><th className="px-3 py-2 text-left font-medium">{t('admin.memberIdentity')}</th><th className="px-3 py-2 text-left font-medium">{t('admin.status')}</th><th className="px-3 py-2 text-left font-medium">{t('admin.plan')}</th><th className="px-3 py-2 text-right font-medium">{t('admin.usage')}</th><th className="px-3 py-2 text-right font-medium">{t('admin.actions')}</th></tr></thead><tbody>
						{(usesExternalDirectory ? tenantMembers.isPending : users.isPending) ? <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground"><Loader2 className="mx-auto size-4 animate-spin" /></td></tr> : items.map((item) => <tr key={item.id} className="border-b last:border-0"><td className="px-3 py-2 font-medium"><div>{item.display_name ?? item.username}</div><div className="font-normal text-xs text-muted-foreground">{usesExternalDirectory ? (item.organization_roles ?? []).join(', ') || '—' : item.role}</div>{item.external_user_id && <div className="font-normal text-xs text-muted-foreground">{t('auth.userId')}: {item.external_user_id}</div>}</td><td className="px-3 py-2"><Badge variant={statusVariant(item.status)}>{usesExternalDirectory ? (item.membership_status ?? item.status) : t(`admin.statusValues.${item.status}`)}</Badge></td><td className="px-3 py-2 text-muted-foreground">{usesExternalDirectory ? '—' : item.plan_id === 'plan_none' ? t('billing.noPlan') : item.plan_name}</td><td className="px-3 py-2 text-right font-mono text-xs">{usesExternalDirectory ? (item.permissions ?? []).join(', ') || '—' : `${formatNumber(item.monthly_used)} / ${formatNumber(item.monthly_quota)}`}</td><td className="px-3 py-2"><div className="flex flex-wrap justify-end gap-1">{usesExternalDirectory ? <><Button variant="ghost" size="sm" disabled={updateTenantMember.isPending} onClick={() => updateTenantMember.mutate({ membershipId: item.id, body: { status: item.membership_status === 'active' ? 'disabled' : 'active' } })}>{item.membership_status === 'active' ? t('admin.pauseAccess') : t('admin.restoreAccess')}</Button><Button variant="destructive" size="sm" disabled={updateTenantMember.isPending || item.membership_status === 'removed'} onClick={() => updateTenantMember.mutate({ membershipId: item.id, body: { status: 'removed' } })}>{t('admin.unbindMember')}</Button></> : item.role !== 'admin' && <><Button variant="ghost" size="sm" disabled={updateUser.isPending} onClick={() => updateUser.mutate({ userId: item.id, status: item.status === 'active' ? 'banned' : 'active' })}>{item.status === 'active' ? t('admin.ban') : t('admin.activate')}</Button><Button variant="destructive" size="sm" disabled={deleteUser.isPending} onClick={() => { if (!window.confirm(t('admin.deleteConfirm', { username: item.username }))) return; const reason = window.prompt(t('admin.deleteReasonPrompt')); if (reason && reason.trim().length >= 4) deleteUser.mutate({ userId: item.id, reason: reason.trim() }); else if (reason !== null) window.alert(t('admin.reasonRequired')); }}>{t('common.delete')}</Button><Button variant="ghost" size="sm" disabled={resetPassword.isPending} onClick={() => handleResetPassword(item)}><KeyRound />{t('admin.resetPassword')}</Button><Button variant="ghost" size="sm" disabled={revokeSessions.isPending} onClick={() => { if (window.confirm(t('admin.revokeSessionsConfirm', { username: item.username }))) revokeSessions.mutate(item.id); }}><UserRoundX className="size-4" />{t('admin.revokeSessions')}</Button></>}</div></td></tr>)}
						{!(usesExternalDirectory ? tenantMembers.isPending : users.isPending) && items.length === 0 && <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">{t('admin.noMembers')}</td></tr>}
					</tbody></table></div>
					{listing && <Pagination page={page} pageSize={listing.page_size} total={listing.total} onPageChange={setPage} loading={usesExternalDirectory ? tenantMembers.isFetching : users.isFetching} />}
				</CardContent>
			</Card>
			<div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{t('admin.pricingBoundaryNotice')}</div>
			<PlanOrdersCard />
		</>
	);
}
