import { Building2, CircleAlert, Loader2, LogOut } from 'lucide-react';
import { useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';

export function OrganizationGate({ mode }: { mode: 'none' | 'select' }) {
	const { t } = useTranslation();
	const { organizations, setActiveOrganizationId, logout } = useAuth();
	const [selectedOrganizationId, setSelectedOrganizationId] = useState('');
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState('');

	const selectOrganization = async (organizationId: string) => {
		setSelectedOrganizationId(organizationId);
		setSubmitting(true);
		setError('');
		try {
			await setActiveOrganizationId(organizationId);
		} catch {
			setError(t('auth.organizationSwitchFailed'));
		} finally {
			setSubmitting(false);
		}
	};

	const leaveAccount = async () => {
		await logout();
	};

	return (
		<div className="flex h-screen items-center justify-center bg-canvas px-4">
			<Card className="w-full max-w-md">
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Building2 className="size-5" />
						{mode === 'none'
							? t('auth.noOrganizationTitle')
							: t('auth.chooseOrganizationTitle')}
					</CardTitle>
					<CardDescription>
						{mode === 'none'
							? t('auth.noOrganizationDescription')
							: t('auth.chooseOrganizationDescription')}
					</CardDescription>
				</CardHeader>
				<CardContent className="flex flex-col gap-4">
					{mode === 'none' ? (
						<Alert>
							<CircleAlert />
							<AlertTitle>{t('auth.noOrganizationTitle')}</AlertTitle>
							<AlertDescription>
								{t('auth.noOrganizationDescription')}
							</AlertDescription>
						</Alert>
					) : (
						<>
							<Select
								value={selectedOrganizationId}
								onValueChange={(value) => void selectOrganization(value)}
								disabled={submitting}
							>
								<SelectTrigger className="w-full">
									<SelectValue placeholder={t('auth.chooseOrganization')} />
								</SelectTrigger>
								<SelectContent>
									{organizations.map((organization) => (
										<SelectItem key={organization.id} value={organization.id}>
											{organization.name}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							{error && (
								<Alert variant="destructive">
									<CircleAlert />
									<AlertDescription>{error}</AlertDescription>
								</Alert>
							)}
							{submitting && (
								<div className="flex items-center gap-2 text-sm text-muted-foreground">
									<Loader2 className="size-4 animate-spin" />
									{t('auth.switchingOrganization')}
								</div>
							)}
						</>
					)}
					<Button variant="outline" onClick={() => void leaveAccount()} disabled={submitting}>
						<LogOut />
						{t('auth.logout')}
					</Button>
				</CardContent>
			</Card>
		</div>
	);
}
