import { KeyRound } from 'lucide-react';

import { AdminHeader } from '@/pages/admin/shared';
import { ModelConfigCard } from '@/features/longxin-admin';
import { useTranslation } from '@/i18n/useI18n';

/**
 * Standalone "Models & Credentials" entry page, rendered at /credential
 * (outside the admin-center shell). The credential configuration center
 * itself lives at /credential/config.
 */
export function ModelsCredentialsPage() {
	const { t } = useTranslation();
	return (
		<div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
			<AdminHeader title={t('admin.nav.models')} description={t('admin.modelsModuleDescription')} />
			<div className="flex items-center gap-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground"><KeyRound className="size-4 shrink-0" />{t('admin.modelsBoundaryNotice')}</div>
			<ModelConfigCard />
		</div>
	);
}
