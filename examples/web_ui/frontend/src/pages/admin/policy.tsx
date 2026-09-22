import { useQuery } from '@tanstack/react-query';
import { Settings2, ShieldCheck } from 'lucide-react';

import { AdminErrorNotice, AdminHeader } from './shared';
import { adminApi } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';

export function AdminPolicyPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const policy = useQuery({ queryKey: ['admin', user?.id, 'policy'], queryFn: adminApi.policy, enabled: hasPermission('tenant:manage') });
	return (
		<>
			<AdminHeader title={t('admin.nav.policy')} description={t('admin.policyDescription')} />
			<AdminErrorNotice message={policy.error?.message} />
			<Card><CardHeader><CardTitle className="flex items-center gap-2"><Settings2 className="size-4" />{t('admin.policyTitle')}</CardTitle><CardDescription>{t('admin.policyModuleDescription')}</CardDescription></CardHeader><CardContent className="space-y-3 text-sm"><div className="flex justify-between gap-3"><span>{t('admin.policyCredentials')}</span><Badge>{policy.data?.credential_management ?? '...'}</Badge></div><div className="flex justify-between gap-3"><span>{t('admin.policyChatMutation')}</span><Badge variant={policy.data?.policy_mutation_from_chat ? 'destructive' : 'default'}>{policy.data?.policy_mutation_from_chat ? t('common.enabled') : t('common.disabled')}</Badge></div><div className="flex justify-between gap-3"><span>{t('admin.policyPlugins')}</span><Badge variant="outline">{policy.data?.high_risk_plugin_installation ?? '...'}</Badge></div><div className="flex justify-between gap-3"><span>{t('admin.requestIdEnforced')}</span><Badge variant={policy.data?.request_id_enforced ? 'default' : 'destructive'}><ShieldCheck className="mr-1 size-3" />{policy.data?.request_id_enforced ? t('common.enabled') : t('common.disabled')}</Badge></div></CardContent></Card>
			<div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{t('admin.policyBoundaryNotice')}</div>
		</>
	);
}
