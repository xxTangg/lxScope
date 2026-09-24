import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BadgeDollarSign, Check, ChevronLeft, ChevronRight, CircleAlert, Loader2, X } from 'lucide-react';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';
import { useAuth } from '@/hooks/useAuth';

import { planBillingApi, type PlanOrder } from './plan-billing-api';

function orderStatusVariant(status: PlanOrder['status']) {
	if (status === 'approved') return 'default' as const;
	if (status === 'rejected') return 'destructive' as const;
	return 'outline' as const;
}

export function PlanOrdersCard() {
	const { t } = useTranslation();
	const { user } = useAuth();
	const queryClient = useQueryClient();
	const [decision, setDecision] = useState<{
		order: PlanOrder;
		action: 'approve' | 'reject';
	} | null>(null);
	const [reason, setReason] = useState('');
	const [reasonError, setReasonError] = useState('');
	const [status, setStatus] = useState<PlanOrder['status'] | ''>('');
	const [page, setPage] = useState(1);
	const pageSize = 10;
	const orders = useQuery({
		queryKey: ['longxin-plan-billing', user?.id, 'admin-orders', status],
		queryFn: () => planBillingApi.adminOrders(status || undefined),
		enabled: user?.role === 'admin',
	});
	const decide = useMutation({
		mutationFn: ({ order, action, reason }: {
			order: PlanOrder;
			action: 'approve' | 'reject';
			reason: string;
		}) => {
			return action === 'approve'
				? planBillingApi.approveOrder(order.order_id, reason)
				: planBillingApi.rejectOrder(order.order_id, reason);
		},
		onSuccess: async () => {
			setDecision(null);
			setReason('');
			setReasonError('');
			await queryClient.invalidateQueries({ queryKey: ['longxin-plan-billing'] });
			await queryClient.invalidateQueries({ queryKey: ['admin'] });
		},
	});
	const openDecision = (order: PlanOrder, action: 'approve' | 'reject') => {
		decide.reset();
		setReason('');
		setReasonError('');
		setDecision({ order, action });
	};
	const submitDecision = () => {
		const normalizedReason = reason.trim();
		if (normalizedReason.length < 4) {
			setReasonError(t('billing.reasonRequired'));
			return;
		}
		if (!decision) return;
		setReasonError('');
		decide.mutate({ ...decision, reason: normalizedReason });
	};
	const decisionError = (() => {
		if (!decide.error) return null;
		if (!(decide.error instanceof ApiError)) {
			return { message: decide.error.message || t('billing.decisionErrorGeneric'), requestId: '' };
		}
		if (decide.error.status === 0) {
			return { message: t('billing.decisionErrorNetwork'), requestId: '' };
		}
		if (decide.error.status === 408) {
			return { message: t('billing.decisionErrorTimeout'), requestId: '' };
		}

		const code = decide.error.code ?? '';
		const message = decide.error.detail || t('billing.decisionErrorGeneric');
		const requestId = decide.error.requestId ?? '';

		const localizedMessage: Record<string, string> = {
			quota_insufficient: t('billing.decisionErrorQuotaInsufficient'),
			order_not_pending: t('billing.decisionErrorOrderChanged'),
			user_not_available: t('billing.decisionErrorUserUnavailable'),
			plan_downgrade_blocked: t('billing.decisionErrorDowngradeBlocked'),
			admin_required: t('billing.decisionErrorPermission'),
		};
		return {
			message: localizedMessage[code] ?? message,
			requestId,
		};
	})();
	const items = orders.data?.orders ?? [];
	const pageItems = items.slice((page - 1) * pageSize, page * pageSize);
	const pages = Math.max(1, Math.ceil(items.length / pageSize));

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
				<div className="flex flex-wrap items-center gap-3">
					<select value={status} onChange={(event) => { setStatus(event.target.value as PlanOrder['status'] | ''); setPage(1); }} className="border-input bg-background h-9 rounded-md border px-3 text-sm">
						<option value="">{t('billing.allStatuses')}</option>
						<option value="pending">{t('billing.status.pending')}</option>
						<option value="approved">{t('billing.status.approved')}</option>
						<option value="rejected">{t('billing.status.rejected')}</option>
					</select>
					<span className="text-xs text-muted-foreground">{items.length} · {page}/{pages}</span>
				</div>
				{orders.isLoading ? (
					<div className="flex h-20 items-center justify-center">
						<Loader2 className="size-5 animate-spin text-muted-foreground" />
					</div>
				) : items.length === 0 ? (
					<div className="text-sm text-muted-foreground">{t('billing.noOrders')}</div>
				) : (
					<div className="space-y-2">
						{pageItems.map((order) => (
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
											disabled={decide.isPending}
											onClick={() => openDecision(order, 'approve')}
										>
											<Check />
											{t('billing.approve')}
										</Button>
										<Button
											size="sm"
											variant="outline"
											disabled={decide.isPending}
											onClick={() => openDecision(order, 'reject')}
										>
											<X />
											{t('billing.reject')}
										</Button>
									</div>
								)}
						</div>
					))}
						<div className="flex justify-end gap-1">
							<Button
								variant="outline"
								size="sm"
								disabled={page <= 1 || orders.isFetching}
								onClick={() => setPage((value) => value - 1)}
							>
								<ChevronLeft />
							</Button>
							<Button
								variant="outline"
								size="sm"
								disabled={page >= pages || orders.isFetching}
								onClick={() => setPage((value) => value + 1)}
							>
								<ChevronRight />
							</Button>
						</div>
					</div>
				)}
			</CardContent>
			<Dialog
				open={decision !== null}
				onOpenChange={(open) => {
					if (open || decide.isPending) return;
					setDecision(null);
					setReason('');
					setReasonError('');
				}}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>
							{decision?.action === 'approve'
								? t('billing.approveConfirmTitle')
								: t('billing.rejectConfirmTitle')}
						</DialogTitle>
						<DialogDescription>
							{decision?.action === 'approve'
								? t('billing.approveConfirmDescription')
								: t('billing.rejectConfirmDescription')}
						</DialogDescription>
					</DialogHeader>
					{decision && (
						<div className="space-y-3 rounded-lg border bg-muted/30 p-3 text-sm">
							<div className="font-medium">{decision.order.username}</div>
							<div className="text-muted-foreground">
								{decision.order.plan_name} · {decision.order.price} {decision.order.currency} ·{' '}
								{formatNumber(decision.order.monthly_quota)} Token/月
							</div>
						</div>
					)}
					<label
						className="block space-y-1.5 text-sm font-medium"
						htmlFor="plan-decision-reason"
					>
						{t('billing.decisionReason')}
						<Textarea
							id="plan-decision-reason"
							value={reason}
							onChange={(event) => {
								setReason(event.target.value);
								if (reasonError) setReasonError('');
							}}
							placeholder={t('billing.decisionReasonPlaceholder')}
							maxLength={300}
							className="min-h-20 resize-y font-normal"
							disabled={decide.isPending}
						/>
					</label>
					{(reasonError || decisionError) && (
						<Alert variant="destructive">
							<CircleAlert />
							<div className="space-y-1">
								<AlertTitle>{t('billing.decisionErrorTitle')}</AlertTitle>
								<AlertDescription>
									{reasonError || decisionError?.message}
								</AlertDescription>
								{!reasonError && decisionError?.requestId && (
									<div className="break-all font-mono text-xs text-muted-foreground">
										{t('billing.decisionErrorRequestId')}: {decisionError.requestId}
									</div>
								)}
							</div>
						</Alert>
					)}
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setDecision(null)}
							disabled={decide.isPending}
						>
							{t('common.cancel')}
						</Button>
						<Button
							variant={decision?.action === 'reject' ? 'destructive' : 'default'}
							onClick={submitDecision}
							disabled={decide.isPending}
						>
							{decide.isPending ? (
								<Loader2 className="size-4 animate-spin" />
							) : decision?.action === 'approve' ? (
								<Check className="size-4" />
							) : (
								<X className="size-4" />
							)}
							{decision?.action === 'approve' ? t('billing.approve') : t('billing.reject')}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</Card>
	);
}
