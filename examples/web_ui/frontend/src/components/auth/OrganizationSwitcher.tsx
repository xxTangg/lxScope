import { useState } from 'react';

import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';

export function OrganizationSwitcher() {
	const { t } = useTranslation();
	const { organizations, activeOrganizationId, setActiveOrganizationId } = useAuth();
	const [switching, setSwitching] = useState(false);

	if (organizations.length < 2) return null;

	const switchOrganization = async (organizationId: string) => {
		setSwitching(true);
		try {
			await setActiveOrganizationId(organizationId);
		} finally {
			setSwitching(false);
		}
	};

	return (
		<div className="px-2 py-1.5">
			<div className="mb-1 text-xs text-muted-foreground">
				{t('auth.organization')}
			</div>
			<Select
				value={activeOrganizationId ?? ''}
				onValueChange={(value) => void switchOrganization(value)}
				disabled={switching}
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
		</div>
	);
}
