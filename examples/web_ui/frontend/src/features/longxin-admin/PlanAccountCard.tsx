import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BadgeDollarSign, CheckCircle2, Clock3, Loader2 } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

import { planBillingApi } from './plan-billing-api';

export function PlanAccountCard() {
	const { t } = useTranslation();
	const queryClient = useQueryClient();
	const current = useQuery({
		queryKey: ['longxin-plan-billing', 'current-plan'],
		queryFn: planBillingApi.currentPlan,
	});
	const plans = useQuery({
		queryKey: ['longxin-plan-billing', 'plans'],
		queryFn: planBillingApi.plans,
	});
	const orders = useQuery({
		queryKey: ['longxin-plan-billing', 'my-orders'],
		queryFn: () => planBillingApi.myOrders(5),
	});
	const createOrder = useMutation({
		mutationFn: (planId: string) => planBillingApi.createOrder(planId),
		onSuccess: async () => {
			await queryClient.invalidateQueries({ queryKey: ['longxin-plan-billing'] });
		},
	});
	const activePlanId = current.data?.status === 'active' ? current.data.plan_id : null;

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<BadgeDollarSign className="size-4" />
					{t('billing.accountTitle')}
				</CardTitle>
				<CardDescription>{t('billing.accountDescription')}</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{current.isLoading ? (
					<Loader2 className="size-5 animate-spin text-muted-foreground" />
				) : current.data ? (
					<div className="grid gap-3 sm:grid-cols-3">
						<div className="rounded-lg bg-muted/60 p-3">
							<div className="text-xs text-muted-foreground">{t('billing.currentPlan')}</div>
							<div className="mt-1 flex items-center gap-2 font-medium">
								{current.data.plan_name}
								<Badge variant={current.data.status === 'active' ? 'default' : 'outline'}>
									{t(`billing.planStatus.${current.data.status}`)}
								</Badge>
							</div>
						</div>
						<div className="rounded-lg bg-muted/60 p-3">
							<div className="text-xs text-muted-foreground">{t('billing.remainingTokens')}</div>
							<div className="mt-1 font-mono font-semibold">
								{formatNumber(current.data.remaining_tokens)}
							</div>
						</div>
						<div className="rounded-lg bg-muted/60 p-3">
							<div className="text-xs text-muted-foreground">{t('billing.expiresAt')}</div>
							<div className="mt-1 text-sm">
								{current.data.expires_at
									? new Date(current.data.expires_at).toLocaleDateString()
									: t('billing.notActivated')}
							</div>
						</div>
					</div>
				) : null}

				{createOrder.error && (
					<Alert variant="destructive">
						<AlertDescription>{createOrder.error.message}</AlertDescription>
					</Alert>
				)}

				<div className="grid gap-3 md:grid-cols-3">
					{(plans.data?.plans ?? []).map((plan) => (
						<div key={plan.id} className="flex flex-col gap-3 rounded-lg border p-4">
							<div>
								<div className="flex items-center justify-between gap-2">
									<div className="font-medium">{plan.name}</div>
									<Badge variant="outline">
										{plan.price} {plan.currency}/{t('billing.month')}
									</Badge>
								</div>
								<div className="mt-1 text-sm text-muted-foreground">{plan.description}</div>
								<div className="mt-2 text-xs text-muted-foreground">
									{formatNumber(plan.monthly_quota)} Token/月
								</div>
							</div>
							<Button
								variant={plan.id === activePlanId ? 'default' : 'outline'}
								disabled={createOrder.isPending}
								onClick={() => createOrder.mutate(plan.id)}
							>
								{plan.id === activePlanId
									? t('billing.renew')
									: t('billing.choose')}
							</Button>
						</div>
					))}
				</div>

				<div className="space-y-2">
					<div className="text-sm font-medium">{t('billing.myOrders')}</div>
					{(orders.data?.orders ?? []).length === 0 ? (
						<div className="text-xs text-muted-foreground">{t('billing.noOrders')}</div>
					) : (
						orders.data?.orders.map((order) => (
							<div key={order.order_id} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
								<div className="flex min-w-0 items-center gap-2">
									{order.status === 'approved' ? (
										<CheckCircle2 className="size-4 text-emerald-600" />
									) : (
										<Clock3 className="size-4 text-muted-foreground" />
									)}
									<span>{order.plan_name}</span>
								</div>
								<Badge variant={order.status === 'rejected' ? 'destructive' : 'outline'}>
									{t(`billing.status.${order.status}`)}
								</Badge>
							</div>
						))
					)}
				</div>
			</CardContent>
		</Card>
	);
}
