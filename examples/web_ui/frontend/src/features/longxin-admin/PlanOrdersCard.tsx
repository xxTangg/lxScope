import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BadgeDollarSign, Check, Loader2, X } from 'lucide-react';
import { useState } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { planBillingApi, type PlanOrder } from './plan-billing-api';

function orderStatusVariant(status: PlanOrder['status']) {
	if (status === 'approved') return 'default' as const;
	if (status === 'rejected') return 'destructive' as const;
	return 'outline' as const;
}

export function PlanOrdersCard() {
	const { t } = useTranslation();
	const queryClient = useQueryClient();
	const [adminPassword, setAdminPassword] = useState('');
	const orders = useQuery({
		queryKey: ['longxin-plan-billing', 'admin-orders'],
		queryFn: () => planBillingApi.adminOrders(),
	});
	const decide = useMutation({
		mutationFn: ({ order, action }: { order: PlanOrder; action: 'approve' | 'reject' }) => {
			const reason = window.prompt(
				action === 'approve' ? t('billing.approveReason') : t('billing.rejectReason'),
			);
			if (!reason || reason.trim().length < 4) {
				throw new Error(t('billing.reasonRequired'));
			}
			return action === 'approve'
				? planBillingApi.approveOrder(order.order_id, adminPassword, reason.trim())
				: planBillingApi.rejectOrder(order.order_id, adminPassword, reason.trim());
		},
		onSuccess: async () => {
			setAdminPassword('');
			await queryClient.invalidateQueries({ queryKey: ['longxin-plan-billing'] });
			await queryClient.invalidateQueries({ queryKey: ['admin'] });
		},
	});
	const items = orders.data?.orders ?? [];

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<BadgeDollarSign className="size-4" />
					{t('billing.adminOrders')}
				</CardTitle>
				<CardDescription>{t('billing.adminOrdersDescription')}</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="max-w-sm space-y-1.5">
					<Label htmlFor="plan-admin-password">{t('billing.adminPassword')}</Label>
					<Input
						id="plan-admin-password"
						type="password"
						value={adminPassword}
						onChange={(event) => setAdminPassword(event.target.value)}
						placeholder={t('billing.adminPasswordPlaceholder')}
					/>
				</div>
				{decide.error && (
					<Alert variant="destructive">
						<AlertDescription>{decide.error.message}</AlertDescription>
					</Alert>
				)}
				{orders.isLoading ? (
					<div className="flex h-20 items-center justify-center">
						<Loader2 className="size-5 animate-spin text-muted-foreground" />
					</div>
				) : items.length === 0 ? (
					<div className="text-sm text-muted-foreground">{t('billing.noOrders')}</div>
				) : (
					<div className="space-y-2">
						{items.map((order) => (
							<div
								key={order.order_id}
								className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3"
							>
								<div className="min-w-0">
									<div className="flex flex-wrap items-center gap-2">
										<span className="font-medium">{order.username}</span>
										<Badge variant={orderStatusVariant(order.status)}>
											{t(`billing.status.${order.status}`)}
										</Badge>
									</div>
									<div className="text-sm text-muted-foreground">
										{order.plan_name} · {order.price} {order.currency} ·{' '}
										{formatNumber(order.monthly_quota)} Token/月
									</div>
									<div className="font-mono text-xs text-muted-foreground">
										{order.order_id}
									</div>
								</div>
								{order.status === 'pending' && (
									<div className="flex gap-1">
										<Button
											size="sm"
											disabled={!adminPassword || decide.isPending}
											onClick={() => decide.mutate({ order, action: 'approve' })}
										>
											<Check />
											{t('billing.approve')}
										</Button>
										<Button
											size="sm"
											variant="outline"
											disabled={!adminPassword || decide.isPending}
											onClick={() => decide.mutate({ order, action: 'reject' })}
										>
											<X />
											{t('billing.reject')}
										</Button>
									</div>
								)}
							</div>
						))}
					</div>
				)}
			</CardContent>
		</Card>
	);
}

