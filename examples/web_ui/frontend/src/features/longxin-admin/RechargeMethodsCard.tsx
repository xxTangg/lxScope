import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BadgeDollarSign, ChevronLeft, ChevronRight, Database, Loader2, RefreshCw, TicketCheck } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { adminApi } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n/useI18n';
import { useAuth } from '@/hooks/useAuth';

/**
 * Keeps the two system-quota recharge paths visible and independent:
 * online recharge goes through Sales Hub, while a one-time code is redeemed
 * locally after its signature and nonce have been verified by the backend.
 */
export function RechargeMethodsCard() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const queryClient = useQueryClient();
	const [rechargeAmount, setRechargeAmount] = useState('');
	const [rechargeCode, setRechargeCode] = useState('');
	const [rechargePage, setRechargePage] = useState(1);
	const recharges = useQuery({
		queryKey: ['admin', user?.id, 'recharges'],
		queryFn: () => adminApi.rechargeRequests(20),
		enabled: user?.role === 'admin',
		refetchInterval: 10_000,
	});
	const createRecharge = useMutation({
		mutationFn: adminApi.createRechargeRequest,
		onSuccess: async () => {
			setRechargeAmount('');
			await queryClient.invalidateQueries({ queryKey: ['admin'] });
		},
	});
	const syncRecharge = useMutation({
		mutationFn: adminApi.syncRecharge,
		onSuccess: async () => {
			await queryClient.invalidateQueries({ queryKey: ['admin'] });
		},
	});
	const reportUsage = useMutation({
		mutationFn: adminApi.reportUsage,
		onSuccess: async () => {
			await queryClient.invalidateQueries({ queryKey: ['admin'] });
		},
	});
	const redeemCode = useMutation({
		mutationFn: adminApi.redeemRechargeCode,
		onSuccess: async () => {
			setRechargeCode('');
			await queryClient.invalidateQueries({ queryKey: ['admin'] });
		},
	});

	const submitRecharge = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!rechargeAmount) return;
		createRecharge.mutate({ amount: rechargeAmount });
	};

	const submitCode = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		const code = rechargeCode.trim();
		if (!code || !window.confirm(t('admin.redeemCodeConfirm'))) return;
		redeemCode.mutate(code);
	};

	const error = [
		recharges.error?.message,
		createRecharge.error?.message,
		syncRecharge.error?.message,
		reportUsage.error?.message,
		redeemCode.error?.message,
	].find((message): message is string => Boolean(message));
	const operation = reportUsage.data ?? syncRecharge.data;
	const operationError = operation?.error;
	const rechargeItems = recharges.data?.orders ?? [];
	const rechargePageSize = 5;
	const rechargePages = Math.max(1, Math.ceil(rechargeItems.length / rechargePageSize));
	const visibleRechargeItems = rechargeItems.slice((rechargePage - 1) * rechargePageSize, rechargePage * rechargePageSize);

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<BadgeDollarSign className="size-4" />
					{t('admin.recharge')}
				</CardTitle>
				<CardDescription>{t('admin.rechargeDescription')}</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{error && (
					<Alert variant="destructive">
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				{operationError && (
					<Alert variant="destructive">
						<AlertDescription>
							{String(operationError.message ?? 'Sales Hub operation failed.')}
						</AlertDescription>
					</Alert>
				)}
				{redeemCode.data && (
					<Alert>
						<AlertDescription>
							{t('admin.redeemSuccess', {
								amount: redeemCode.data.amount,
								tokens: redeemCode.data.tokens.toLocaleString(),
							})}
						</AlertDescription>
					</Alert>
				)}

				<div className="grid gap-4 lg:grid-cols-2">
					<section className="space-y-3 rounded-lg border p-3">
						<div className="flex items-center gap-2 font-medium">
							<BadgeDollarSign className="size-4" />
							{t('admin.onlineRecharge')}
						</div>
						<p className="text-xs text-muted-foreground">
							{t('admin.onlineRechargeDescription')}
						</p>
						<form onSubmit={submitRecharge} className="flex gap-2">
							<Input
								type="number"
								min="0.01"
								step="0.01"
								value={rechargeAmount}
								onChange={(event) => setRechargeAmount(event.target.value)}
								placeholder={t('admin.rechargeAmount')}
								required
							/>
							<Button type="submit" disabled={createRecharge.isPending}>
								{createRecharge.isPending ? <Loader2 className="animate-spin" /> : <BadgeDollarSign />}
								{t('admin.submitRecharge')}
							</Button>
						</form>
						<Button
							type="button"
							variant="outline"
							disabled={syncRecharge.isPending}
							onClick={() => syncRecharge.mutate()}
						>
							{syncRecharge.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
							{t('admin.pollRecharge')}
						</Button>
						<Button
							type="button"
							variant="outline"
							disabled={reportUsage.isPending}
							onClick={() => reportUsage.mutate()}
						>
							{reportUsage.isPending ? <Loader2 className="animate-spin" /> : <Database />}
							{t('admin.reportUsage')}
						</Button>
					</section>

					<section className="space-y-3 rounded-lg border p-3">
						<div className="flex items-center gap-2 font-medium">
							<TicketCheck className="size-4" />
							{t('admin.redeemRechargeCode')}
						</div>
						<p className="text-xs text-muted-foreground">
							{t('admin.redeemRechargeCodeDescription')}
						</p>
						<form onSubmit={submitCode} className="space-y-2">
							<Label htmlFor="admin-recharge-code">{t('admin.rechargeCode')}</Label>
							<div className="flex gap-2">
								<Input
									id="admin-recharge-code"
									value={rechargeCode}
									onChange={(event) => setRechargeCode(event.target.value)}
									placeholder="LXRC2...."
									className="font-mono"
									required
								/>
								<Button type="submit" disabled={!rechargeCode.trim() || redeemCode.isPending}>
									{redeemCode.isPending ? <Loader2 className="animate-spin" /> : <TicketCheck />}
									{t('admin.redeem')}
								</Button>
							</div>
						</form>
					</section>
				</div>

				<div className="flex items-center justify-between gap-2">
					<div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
						<Database className="size-3.5" />
						{t('admin.recentRecharge')}
					</div>
					{rechargeItems.length > 0 && <span className="text-xs text-muted-foreground">{rechargeItems.length} · {rechargePage}/{rechargePages}</span>}
					</div>
				{recharges.isLoading ? (
					<div className="flex h-12 items-center justify-center">
						<Loader2 className="size-4 animate-spin text-muted-foreground" />
					</div>
				) : rechargeItems.length === 0 ? (
					<div className="text-xs text-muted-foreground">{t('admin.noRecharge')}</div>
				) : (
					<div className="space-y-2">
						{visibleRechargeItems.map((item) => {
			const isDelivered = item.delivery_status === 'delivered';
			const approvalLabel =
				item.status === 'pending'
					? t('admin.waitingApproval')
					: item.status === 'approved'
						? t('admin.approved')
						: item.status === 'rejected'
							? t('admin.rejected')
							: t('admin.unknownStatus');
			const statusLabel = isDelivered
								? t('admin.delivered')
								: item.status === 'pending'
									? t('admin.sentAwaitingApproval')
									: item.status === 'approved'
										? t('admin.approvedAwaitingSync')
										: t('admin.waitingDelivery');
							return (
								<div
									key={item.order_id}
									className="flex items-center justify-between gap-2 rounded-md border px-2 py-2 text-xs"
								>
									<div className="min-w-0">
										<div className="truncate font-mono">{item.order_id}</div>
									<div className="text-muted-foreground">
										{item.amount} - {approvalLabel}
									</div>
										{item.request_id && (
											<div className="truncate font-mono text-[10px] text-muted-foreground">
												request_id: {item.request_id}
											</div>
										)}
									</div>
									<Badge variant={isDelivered ? 'default' : 'outline'}>
										{statusLabel}
									</Badge>
								</div>
							);
						})}
						<div className="flex justify-end gap-1">
							<Button variant="outline" size="sm" disabled={rechargePage <= 1 || recharges.isFetching} onClick={() => setRechargePage((value) => value - 1)}><ChevronLeft /></Button>
							<Button variant="outline" size="sm" disabled={rechargePage >= rechargePages || recharges.isFetching} onClick={() => setRechargePage((value) => value + 1)}><ChevronRight /></Button>
						</div>
					</div>
				)}
			</CardContent>
		</Card>
	);
}
