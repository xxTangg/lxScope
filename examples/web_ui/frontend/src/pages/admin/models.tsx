import { KeyRound } from 'lucide-react';

import { AdminHeader } from './shared';
import { ModelConfigCard } from '@/features/longxin-admin';
import { useTranslation } from '@/i18n/useI18n';

export function AdminModelsPage() {
	const { t } = useTranslation();
	return (
		<>
			<AdminHeader title={t('admin.nav.models')} description={t('admin.modelsModuleDescription')} />
			<div className="flex items-center gap-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground"><KeyRound className="size-4 shrink-0" />{t('admin.modelsBoundaryNotice')}</div>
			<ModelConfigCard />
		</>
	);
}
