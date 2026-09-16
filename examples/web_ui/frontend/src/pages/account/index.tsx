import { useQuery } from '@tanstack/react-query';
import { Loader2, LogOut, RefreshCw, Repeat2, UserRound } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { authApi } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';
import { PlanAccountCard } from '@/features/longxin-admin';

export function AccountPage() {
	const { t } = useTranslation();
	const { user, logout } = useAuth();
	const navigate = useNavigate();
	const usage = useQuery({
		queryKey: ['auth', 'usage', user?.id],
		queryFn: authApi.usage,
		enabled: !!user,
	});

	const leaveAccount = async () => {
		await logout();
		navigate('/login', { replace: true });
	};

	const metrics = usage.data
		? [
				[t('auth.totalTokens'), usage.data.total_tokens],
				[t('auth.inputTokens'), usage.data.input_tokens],
				[t('auth.outputTokens'), usage.data.output_tokens],
				[t('auth.cacheTokens'), usage.data.cache_input_tokens],
			]
		: [];

	return (
		<div className="h-full overflow-auto p-6">
			<div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
				<div>
					<h1 className="font-heading text-xl font-semibold">
						{t('auth.accountCenter')}
					</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						{t('auth.accountCenterDescription')}
					</p>
				</div>

				<Card>
					<CardHeader>
						<CardTitle className="flex items-center gap-2">
							<UserRound className="size-4" />
							{t('auth.accountInfo')}
						</CardTitle>
						<CardDescription>{t('auth.brand')}</CardDescription>
					</CardHeader>
					<CardContent className="flex flex-col gap-4">
						<div className="grid gap-4 sm:grid-cols-2">
							<div>
								<div className="text-xs text-muted-foreground">
									{t('auth.username')}
								</div>
								<div className="mt-1 font-medium">{user?.username}</div>
							</div>
							<div>
								<div className="text-xs text-muted-foreground">
									{t('auth.userId')}
								</div>
								<div className="mt-1 truncate font-mono text-sm">{user?.id}</div>
							</div>
						</div>
						<Separator />
						<div className="flex flex-wrap gap-2">
							<Button variant="outline" onClick={() => void leaveAccount()}>
								<Repeat2 />
								{t('auth.switchAccount')}
							</Button>
							<Button variant="outline" onClick={() => void leaveAccount()}>
								<LogOut />
								{t('auth.logout')}
							</Button>
						</div>
					</CardContent>
				</Card>

				<PlanAccountCard />

				<Card>
					<CardHeader>
						<div className="flex items-start justify-between gap-4">
							<div>
								<CardTitle>{t('auth.tokenUsage')}</CardTitle>
								<CardDescription>{t('auth.tokenUsageDescription')}</CardDescription>
							</div>
							<Button
								variant="ghost"
								size="icon-sm"
								onClick={() => void usage.refetch()}
								disabled={usage.isFetching}
								aria-label={t('auth.refreshUsage')}
							>
								<RefreshCw className={usage.isFetching ? 'animate-spin' : ''} />
							</Button>
						</div>
					</CardHeader>
					<CardContent>
						{usage.isLoading ? (
							<div className="flex h-24 items-center justify-center">
								<Loader2 className="size-5 animate-spin text-muted-foreground" />
							</div>
						) : usage.isError ? (
							<Alert variant="destructive">
								<AlertDescription>{t('auth.usageLoadFailed')}</AlertDescription>
							</Alert>
						) : (
							<>
								<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
									{metrics.map(([label, value]) => (
										<div key={label} className="rounded-lg bg-muted/60 p-3">
											<div className="text-xs text-muted-foreground">
												{label}
											</div>
											<div className="mt-1 font-mono text-lg font-semibold">
												{formatNumber(value as number)}
											</div>
										</div>
									))}
								</div>
								<p className="mt-4 text-xs text-muted-foreground">
									{t('auth.usageScope', {
										sessions: usage.data?.session_count ?? 0,
										messages: usage.data?.message_count ?? 0,
									})}
								</p>
							</>
						)}
					</CardContent>
				</Card>
			</div>
		</div>
	);
}
