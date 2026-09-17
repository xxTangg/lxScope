import { AdminHeader } from './shared';
import { UpgradeAdminCard } from '@/features/longxin-admin';
import { useTranslation } from '@/i18n/useI18n';

export function AdminUpgradesPage() {
	const { t } = useTranslation();
	return <><AdminHeader title={t('admin.nav.upgrades')} description={t('admin.upgradesModuleDescription')} /><UpgradeAdminCard /></>;
}
