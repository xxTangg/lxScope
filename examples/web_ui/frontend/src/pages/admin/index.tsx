import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
	Activity,
	BadgeDollarSign,
	ChevronLeft,
	ChevronRight,
	Database,
	KeyRound,
	Loader2,
	RefreshCw,
	Save,
	ShieldCheck,
	UserRoundX,
	UserPlus,
	Users,
	type LucideIcon,
} from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';

import { adminApi, type AdminUser } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import {
	ModelConfigCard,
	PlanOrdersCard,
	planBillingApi,
	RechargeMethodsCard,
	UpgradeAdminCard,
} from '@/features/longxin-admin';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

function statusVariant(status: AdminUser['status']) {
	if (status === 'active') return 'default' as const;
	if (status === 'banned') return 'destructive' as const;
	return 'outline' as const;
}

function MetricCard({
	label,
	value,
	Icon,
}: {
	label: string;
	value: string | number;
	Icon: LucideIcon;
}) {
	return (
		<Card>
			<CardContent className="flex items-center justify-between">
				<div>
					<div className="text-xs text-muted-foreground">{label}</div>
					<div className="mt-1 font-mono text-lg font-semibold">{value}</div>
				</div>
				<Icon className="size-5 text-muted-foreground" />
			</CardContent>
		</Card>
	);
}

function ErrorNotice({ message }: { message: string }) {
	return (
		<Alert variant="destructive">
			<AlertDescription>{message}</AlertDescription>
		</Alert>
	);
}

export function AdminPage() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const queryClient = useQueryClient();
	const [keyword, setKeyword] = useState('');
	const [newUsername, setNewUsername] = useState('');
	const [newPassword, setNewPassword] = useState('');
	const [newPlanId, setNewPlanId] = useState('plan_basic');
	const [bonusTokens, setBonusTokens] = useState('0');
	const [testDefaultTokens, setTestDefaultTokens] = useState('');
	const [systemId, setSystemId] = useState('');
	const [hubUrl, setHubUrl] = useState('');
	const [hubToken, setHubToken] = useState('');
	const [statusFilter, setStatusFilter] = useState('');
	const [userPage, setUserPage] = useState(1);

	const enabled = user?.role === 'admin';
	const overview = useQuery({
		queryKey: ['admin', user?.id, 'overview'],
		queryFn: adminApi.overview,
		enabled,
	});
	const quota = useQuery({
		queryKey: ['admin', user?.id, 'quota'],
		queryFn: adminApi.quota,
		enabled,
	});
	const users = useQuery({
		queryKey: ['admin', user?.id, 'users', keyword, statusFilter, userPage],
		queryFn: () =>
			adminApi.users({
				keyword: keyword || undefined,
				status: statusFilter || undefined,
				page: userPage,
				page_size: 20,
			}),
		enabled,
	});
	const policy = useQuery({
		queryKey: ['admin', user?.id, 'policy'],
		queryFn: adminApi.policy,
		enabled,
	});
	const audit = useQuery({
		queryKey: ['admin', user?.id, 'audit'],
		queryFn: () => adminApi.auditEvents(20),
		enabled,
	});
	const hub = useQuery({
		queryKey: ['admin', user?.id, 'sales-hub'],
		queryFn: adminApi.hubConfig,
		enabled,
	});
	const plans = useQuery({
		queryKey: ['longxin-plan-billing', 'plans'],
		queryFn: planBillingApi.plans,
		enabled,
	});

	useEffect(() => {
		if (quota.data) setTestDefaultTokens(String(quota.data.test_default_tokens));
	}, [quota.data]);

	useEffect(() => {
		if (!hub.data) return;
		setSystemId(hub.data.system_id);
		setHubUrl(hub.data.hub_url);
	}, [hub.data]);

	const refresh = async () => {
		await queryClient.invalidateQueries({ queryKey: ['admin'] });
	};
	const createUser = useMutation({
		mutationFn: adminApi.createUser,
		onSuccess: async () => {
			setNewUsername('');
			setNewPassword('');
			setNewPlanId('plan_basic');
			setBonusTokens('0');
			await refresh();
		},
	});
	const updateUser = useMutation({
		mutationFn: ({ userId, status }: { userId: string; status: 'active' | 'banned' }) =>
			adminApi.updateUser(userId, { status }),
		onSuccess: refresh,
	});
	const deleteUser = useMutation({
		mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
			adminApi.deleteUser(userId, reason),
		onSuccess: refresh,
	});
	const resetPassword = useMutation({
		mutationFn: ({ userId, reason, admin_password }: { userId: string; reason: string; admin_password: string }) =>
			adminApi.resetPassword(userId, { reason, admin_password }),
		onSuccess: async (data) => {
			window.alert(`${t('admin.temporaryPassword')}: ${data.temporary_password}\n${t('admin.expiresAt')}: ${data.expires_at}`);
			await refresh();
		},
	});
	const revokeSessions = useMutation({
		mutationFn: adminApi.revokeSessions,
		onSuccess: refresh,
	});
	const auditOverview = useMutation({
		mutationFn: adminApi.auditOverview,
		onSuccess: (data) => {
			window.alert(`${t('admin.auditOverviewResult')}\n${JSON.stringify(data.data, null, 2)}`);
			void refresh();
		},
	});
	const updateQuota = useMutation({
		mutationFn: adminApi.updateQuota,
		onSuccess: refresh,
	});
	const updateHub = useMutation({
		mutationFn: adminApi.updateHubConfig,
		onSuccess: async () => {
			setHubToken('');
			await refresh();
		},
	});
	const verifyHub = useMutation({
		mutationFn: adminApi.verifyHub,
		onSuccess: refresh,
	});

	if (!user || user.role !== 'admin') {
		return <Navigate to="/chat" replace />;
	}

	const error = [
		overview.error?.message,
		quota.error?.message,
		policy.error?.message,
		users.error?.message,
		hub.error?.message,
		audit.error?.message,
		createUser.error?.message,
		plans.error?.message,
		updateUser.error?.message,
		deleteUser.error?.message,
		updateQuota.error?.message,
		updateHub.error?.message,
		verifyHub.error?.message,
		resetPassword.error?.message,
		revokeSessions.error?.message,
		auditOverview.error?.message,
	].find((message): message is string => Boolean(message));
	const userItems = users.data?.users ?? [];
	const hasPreviousPage = userPage > 1;
	const hasNextPage = users.data ? userPage * users.data.page_size < users.data.total : false;

	const handleResetPassword = (item: AdminUser) => {
		const adminPassword = window.prompt(t('admin.adminPasswordPrompt'));
		if (!adminPassword) return;
		const reason = window.prompt(t('admin.resetPasswordReasonPrompt'));
		if (!reason || reason.trim().length < 4) {
			window.alert(t('admin.reasonRequired'));
			return;
		}
		resetPassword.mutate({ userId: item.id, reason: reason.trim(), admin_password: adminPassword });
	};

	const handleAuditOverview = (item: AdminUser) => {
		const reason = window.prompt(t('admin.auditOverviewPrompt', { username: item.username }));
		if (!reason || reason.trim().length < 4) {
			if (reason !== null) window.alert(t('admin.reasonRequired'));
			return;
		}
		auditOverview.mutate({ target_user_id: item.id, reason: reason.trim() });
	};

	const submitCreateUser = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		createUser.mutate({
			username: newUsername.trim(),
			initial_password: newPassword,
			plan_id: newPlanId,
			bonus_tokens: Number(bonusTokens) || 0,
		});
	};

	const submitQuota = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		updateQuota.mutate(Number(testDefaultTokens) || 0);
	};

	const submitHub = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		updateHub.mutate({
			system_id: systemId.trim(),
			hub_url: hubUrl.trim(),
			...(hubToken ? { token: hubToken } : {}),
		});
	};

	return (
		<div className="h-full overflow-auto p-6">
			<div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div>
						<h1 className="font-heading text-xl font-semibold">{t('admin.title')}</h1>
						<p className="mt-1 text-sm text-muted-foreground">{t('admin.description')}</p>
					</div>
					<Button variant="outline" onClick={refresh} disabled={overview.isFetching}>
						<RefreshCw className={overview.isFetching ? 'animate-spin' : ''} />
						{t('admin.refresh')}
					</Button>
				</div>

				{error && <ErrorNotice message={error} />}

				<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
					<MetricCard
						label={t('admin.poolTokens')}
						value={overview.data ? formatNumber(overview.data.pool_tokens) : '...'}
						Icon={Database}
					/>
					<MetricCard
						label={t('admin.totalRecharged')}
						value={overview.data?.total_recharged ?? '...'}
						Icon={BadgeDollarSign}
					/>
					<MetricCard
						label={t('admin.accountCount')}
						value={overview.data ? formatNumber(overview.data.account_count) : '...'}
						Icon={Users}
					/>
					<MetricCard
						label={t('admin.systemHealth')}
						value={overview.data?.health === 'ok' ? t('admin.healthy') : '...'}
						Icon={Activity}
					/>
				</div>

				<div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
					<div className="flex flex-col gap-6">
					<Card>
						<CardHeader>
							<CardTitle className="flex items-center gap-2">
								<UserPlus className="size-4" />
								{t('admin.members')}
							</CardTitle>
							<CardDescription>{t('admin.membersDescription')}</CardDescription>
						</CardHeader>
						<CardContent className="space-y-5">
							<form
								onSubmit={submitCreateUser}
								className="grid gap-3 rounded-lg bg-muted/40 p-3 sm:grid-cols-5"
							>
								<div className="space-y-1.5">
									<Label htmlFor="admin-new-username">{t('admin.username')}</Label>
									<Input
										id="admin-new-username"
										value={newUsername}
										onChange={(event) => setNewUsername(event.target.value)}
										placeholder="alice"
										required
									/>
								</div>
								<div className="space-y-1.5">
									<Label htmlFor="admin-new-password">{t('admin.initialPassword')}</Label>
									<Input
										id="admin-new-password"
										type="password"
										value={newPassword}
										onChange={(event) => setNewPassword(event.target.value)}
										minLength={8}
										required
									/>
								</div>
								<div className="space-y-1.5">
									<Label htmlFor="admin-bonus-tokens">{t('admin.bonusTokens')}</Label>
									<Input
										id="admin-bonus-tokens"
										type="number"
										min={0}
										value={bonusTokens}
										onChange={(event) => setBonusTokens(event.target.value)}
									/>
								</div>
								<div className="space-y-1.5">
									<Label htmlFor="admin-plan">{t('admin.plan')}</Label>
									<select
										id="admin-plan"
										value={newPlanId}
										onChange={(event) => setNewPlanId(event.target.value)}
										className="border-input bg-background ring-offset-background focus-visible:ring-ring h-9 w-full rounded-md border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
									>
										{(plans.data?.plans ?? []).map((plan) => (
											<option key={plan.id} value={plan.id}>
												{plan.name} · {plan.monthly_quota.toLocaleString()} Token/月
											</option>
										))}
									</select>
								</div>
								<div className="flex items-end">
									<Button type="submit" className="w-full" disabled={createUser.isPending}>
										{createUser.isPending ? (
											<Loader2 className="animate-spin" />
										) : (
											<UserPlus />
										)}
										{t('admin.createMember')}
									</Button>
								</div>
							</form>

							<div className="flex items-center gap-3">
								<Input
									value={keyword}
									onChange={(event) => {
										setKeyword(event.target.value);
										setUserPage(1);
									}}
									placeholder={t('admin.searchMembers')}
									className="max-w-xs"
								/>
								<select
									value={statusFilter}
									onChange={(event) => {
										setStatusFilter(event.target.value);
										setUserPage(1);
									}}
									className="border-input bg-background h-9 rounded-md border px-3 text-sm"
								>
									<option value="">{t('admin.allStatuses')}</option>
									<option value="active">{t('admin.statusValues.active')}</option>
									<option value="locked">{t('admin.statusValues.locked')}</option>
									<option value="banned">{t('admin.statusValues.banned')}</option>
								</select>
								{users.isFetching && (
									<Loader2 className="size-4 animate-spin text-muted-foreground" />
								)}
							</div>

							<div className="overflow-x-auto rounded-lg border">
								<table className="w-full text-sm">
									<thead className="border-b bg-muted/40">
										<tr>
											<th className="px-3 py-2 text-left font-medium">{t('admin.username')}</th>
											<th className="px-3 py-2 text-left font-medium">{t('admin.status')}</th>
											<th className="px-3 py-2 text-left font-medium">{t('admin.plan')}</th>
											<th className="px-3 py-2 text-right font-medium">{t('admin.usage')}</th>
											<th className="px-3 py-2 text-right font-medium">{t('admin.actions')}</th>
										</tr>
									</thead>
									<tbody>
										{userItems.map((item) => (
											<tr key={item.id} className="border-b last:border-0">
												<td className="px-3 py-2 font-medium">{item.username}</td>
												<td className="px-3 py-2">
													<Badge variant={statusVariant(item.status)}>
														{t(`admin.statusValues.${item.status}`)}
													</Badge>
												</td>
												<td className="px-3 py-2 text-muted-foreground">{item.plan_name}</td>
												<td className="px-3 py-2 text-right font-mono text-xs">
													{formatNumber(item.monthly_used)} / {formatNumber(item.monthly_quota)}
												</td>
														<td className="px-3 py-2 text-right">
															<div className="flex justify-end gap-1">
																<Button
																	variant="ghost"
																	size="sm"
																	disabled={auditOverview.isPending}
																	onClick={() => handleAuditOverview(item)}
																>
																	{t('admin.auditMember')}
																</Button>
																{item.role !== 'admin' && (
																	<>
																		<Button
																			variant="ghost"
																			size="sm"
																			disabled={updateUser.isPending}
																			onClick={() =>
																				updateUser.mutate({
																						userId: item.id,
																						status: item.status === 'active' ? 'banned' : 'active',
																					})
																			}
																		>
																			{item.status === 'active' ? t('admin.ban') : t('admin.activate')}
																		</Button>
																		<Button
																			variant="destructive"
																			size="sm"
																			disabled={deleteUser.isPending}
																			onClick={() => {
																				if (!window.confirm(t('admin.deleteConfirm', { username: item.username }))) return;
																				const reason = window.prompt(t('admin.deleteReasonPrompt'));
																				if (reason && reason.trim().length >= 4) {
																					deleteUser.mutate({ userId: item.id, reason: reason.trim() });
																				} else if (reason !== null) {
																					window.alert(t('admin.reasonRequired'));
																				}
																			}}
																			>
																				{t('common.delete')}
																			</Button>
																			<Button
																				variant="ghost"
																				size="sm"
																				disabled={resetPassword.isPending}
																				onClick={() => handleResetPassword(item)}
																			>
																				<KeyRound />
																				{t('admin.resetPassword')}
																			</Button>
																			<Button
																				variant="ghost"
																				size="sm"
																				disabled={revokeSessions.isPending}
																				onClick={() => {
																				if (window.confirm(t('admin.revokeSessionsConfirm', { username: item.username }))) {
																					revokeSessions.mutate(item.id);
																				}
																			}}
																			>
																				<UserRoundX />
																				{t('admin.revokeSessions')}
																			</Button>
																	</>
																)}
															</div>
														</td>
											</tr>
										))}
										{!users.isPending && userItems.length === 0 && (
											<tr>
												<td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
													{t('admin.noMembers')}
												</td>
											</tr>
										)}
									</tbody>
								</table>
							</div>
							<div className="flex items-center justify-between text-xs text-muted-foreground">
								<span>
									{users.data ? `${users.data.total} · ${userPage}/${Math.max(1, Math.ceil(users.data.total / users.data.page_size))}` : '...'}
								</span>
								<div className="flex gap-1">
									<Button variant="outline" size="sm" disabled={!hasPreviousPage || users.isFetching} onClick={() => setUserPage((page) => page - 1)}>
										<ChevronLeft />
									</Button>
									<Button variant="outline" size="sm" disabled={!hasNextPage || users.isFetching} onClick={() => setUserPage((page) => page + 1)}>
										<ChevronRight />
									</Button>
								</div>
							</div>
						</CardContent>
					</Card>

					<Card>
						<CardHeader>
							<CardTitle>{t('admin.auditTitle')}</CardTitle>
							<CardDescription>{t('admin.auditDescription')}</CardDescription>
						</CardHeader>
						<CardContent>
							<div className="space-y-2 text-xs">
								{(audit.data?.events ?? []).map((event) => (
									<div key={event.event_id} className="rounded-md border p-2">
										<div className="flex justify-between gap-2 font-medium">
											<span>{event.action}</span>
											<span className="text-muted-foreground">{new Date(event.created_at).toLocaleString()}</span>
										</div>
										<div className="mt-1 text-muted-foreground">{event.reason}</div>
									</div>
								))}
								{!audit.isPending && (audit.data?.events.length ?? 0) === 0 && (
									<div className="text-muted-foreground">{t('admin.noAudit')}</div>
								)}
							</div>
						</CardContent>
					</Card>

					<PlanOrdersCard />
					<UpgradeAdminCard />
					</div>

					<div className="flex flex-col gap-6">
						<Card>
							<CardHeader>
								<CardTitle className="flex items-center gap-2">
									<ShieldCheck className="size-4" />
									{t('admin.quota')}
								</CardTitle>
								<CardDescription>{t('admin.quotaDescription')}</CardDescription>
							</CardHeader>
							<CardContent>
								<div className="grid grid-cols-2 gap-3">
									<div className="rounded-lg bg-muted/60 p-3">
										<div className="text-xs text-muted-foreground">{t('admin.poolTokens')}</div>
										<div className="mt-1 font-mono text-lg font-semibold">
											{quota.data ? formatNumber(quota.data.pool_tokens) : '...'}
										</div>
									</div>
									<div className="rounded-lg bg-muted/60 p-3">
										<div className="text-xs text-muted-foreground">{t('admin.systemId')}</div>
										<div className="mt-1 truncate font-mono text-xs">
											{quota.data?.system_id ?? '...'}
										</div>
									</div>
								</div>
								<Separator className="my-4" />
								<form onSubmit={submitQuota} className="space-y-2">
									<Label htmlFor="admin-test-default">{t('admin.testDefaultTokens')}</Label>
									<div className="flex gap-2">
										<Input
											id="admin-test-default"
											type="number"
											min={0}
											value={testDefaultTokens}
											onChange={(event) => setTestDefaultTokens(event.target.value)}
										/>
										<Button type="submit" variant="outline" disabled={updateQuota.isPending}>
											<Save />
											{t('common.save')}
										</Button>
									</div>
								</form>
							</CardContent>
						</Card>

						<ModelConfigCard />

						<Card>
							<CardHeader>
								<CardTitle className="flex items-center gap-2">
									<ShieldCheck className="size-4" />
									{t('admin.policyTitle')}
								</CardTitle>
								<CardDescription>{t('admin.policyDescription')}</CardDescription>
							</CardHeader>
							<CardContent className="space-y-2 text-xs">
								<div className="flex justify-between gap-3">
									<span>{t('admin.policyCredentials')}</span>
									<Badge>{policy.data?.credential_management ?? '...'}</Badge>
								</div>
								<div className="flex justify-between gap-3">
									<span>{t('admin.policyChatMutation')}</span>
									<Badge variant={policy.data?.policy_mutation_from_chat ? 'destructive' : 'default'}>
										{policy.data?.policy_mutation_from_chat ? t('common.enabled') : t('common.disabled')}
									</Badge>
								</div>
								<div className="flex justify-between gap-3">
									<span>{t('admin.policyPlugins')}</span>
									<Badge variant="outline">{policy.data?.high_risk_plugin_installation ?? '...'}</Badge>
								</div>
							</CardContent>
						</Card>

						<Card>
							<CardHeader>
								<CardTitle className="flex items-center gap-2">
									<Database className="size-4" />
									{t('admin.salesHub')}
								</CardTitle>
								<CardDescription>{t('admin.salesHubDescription')}</CardDescription>
							</CardHeader>
							<CardContent>
								<form onSubmit={submitHub} className="space-y-3">
									<div className="space-y-1.5">
										<Label htmlFor="admin-system-id">{t('admin.systemId')}</Label>
										<Input
											id="admin-system-id"
											value={systemId}
											onChange={(event) => setSystemId(event.target.value)}
										/>
									</div>
									<div className="space-y-1.5">
										<Label htmlFor="admin-hub-url">{t('admin.hubUrl')}</Label>
										<Input
											id="admin-hub-url"
											type="url"
											value={hubUrl}
											onChange={(event) => setHubUrl(event.target.value)}
											placeholder="https://sales.example.com"
										/>
									</div>
									<div className="space-y-1.5">
										<Label htmlFor="admin-hub-token">{t('admin.hubToken')}</Label>
										<Input
											id="admin-hub-token"
											type="password"
											value={hubToken}
											onChange={(event) => setHubToken(event.target.value)}
											placeholder={hub.data?.token_masked ?? t('admin.tokenPlaceholder')}
										/>
									</div>
									<div className="flex flex-wrap gap-2">
										<Button type="submit" disabled={updateHub.isPending}>
											<Save />
											{t('common.save')}
										</Button>
										<Button
											type="button"
											variant="outline"
											disabled={verifyHub.isPending}
											onClick={() => verifyHub.mutate()}
										>
											{verifyHub.isPending ? (
												<Loader2 className="animate-spin" />
											) : (
												<RefreshCw />
											)}
											{t('admin.verifyConnection')}
										</Button>
									</div>
								</form>
								{hub.data && (
									<div className="mt-4 flex flex-wrap gap-2 text-xs">
										<Badge variant={hub.data.outbound_status === 'ok' ? 'default' : 'outline'}>
											{t('admin.outbound')}: {hub.data.outbound_status}
										</Badge>
										<Badge variant={hub.data.inbound_status === 'ok' ? 'default' : 'outline'}>
											{t('admin.inbound')}: {hub.data.inbound_status}
										</Badge>
									</div>
								)}
							</CardContent>
						</Card>

						<RechargeMethodsCard />
					</div>
				</div>
			</div>
		</div>
	);
}
