import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cable, Loader2, RefreshCw, Save } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';

import { AdminErrorNotice, AdminHeader } from './shared';
import { adminApi } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';

export function AdminSalesHubPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const canUseSalesHub =
		hasPermission('tenant:manage') || hasPermission('platform:integration');
	const queryClient = useQueryClient();
	const [systemId, setSystemId] = useState('');
	const [hubUrl, setHubUrl] = useState('');
	const [hubToken, setHubToken] = useState('');
	const [publicKey, setPublicKey] = useState('');
	const hub = useQuery({ queryKey: ['admin', user?.id, 'sales-hub'], queryFn: adminApi.hubConfig, enabled: canUseSalesHub });
	useEffect(() => { if (hub.data) { setSystemId(hub.data.system_id); setHubUrl(hub.data.hub_url); } }, [hub.data]);
	const updateHub = useMutation({ mutationFn: adminApi.updateHubConfig, onSuccess: async () => { setHubToken(''); setPublicKey(''); await queryClient.invalidateQueries({ queryKey: ['admin', user?.id, 'sales-hub'] }); } });
	const verifyHub = useMutation({ mutationFn: adminApi.verifyHub, onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id, 'sales-hub'] }) });
	const submit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); updateHub.mutate({ system_id: systemId.trim(), hub_url: hubUrl.trim(), ...(hubToken ? { token: hubToken } : {}), ...(publicKey ? { public_key: publicKey.trim() } : {}) }); };
	const error = hub.error?.message ?? updateHub.error?.message ?? verifyHub.error?.message;
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id, 'sales-hub'] });

	return (
		<>
			<AdminHeader title={t('admin.nav.salesHub')} description={t('admin.salesHubDescription')} onRefresh={refresh} loading={hub.isFetching} />
			<AdminErrorNotice message={error} />
			<Card>
				<CardHeader><CardTitle className="flex items-center gap-2"><Cable className="size-4" />{t('admin.salesHub')}</CardTitle><CardDescription>{t('admin.salesHubModuleDescription')}</CardDescription></CardHeader>
				<CardContent className="max-w-2xl space-y-4"><form onSubmit={submit} className="space-y-3"><div className="space-y-1.5"><Label htmlFor="admin-system-id">{t('admin.systemId')}</Label><Input id="admin-system-id" value={systemId} onChange={(e) => setSystemId(e.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="admin-hub-url">{t('admin.hubUrl')}</Label><Input id="admin-hub-url" type="url" value={hubUrl} onChange={(e) => setHubUrl(e.target.value)} placeholder="https://sales.example.com" /></div><div className="space-y-1.5"><Label htmlFor="admin-hub-token">{t('admin.hubToken')}</Label><Input id="admin-hub-token" type="password" value={hubToken} onChange={(e) => setHubToken(e.target.value)} placeholder={hub.data?.token_masked ?? t('admin.tokenPlaceholder')} /></div><div className="space-y-1.5"><Label htmlFor="admin-public-key">{t('admin.hubPublicKey')}</Label><Textarea id="admin-public-key" rows={5} className="font-mono text-xs" value={publicKey} onChange={(e) => setPublicKey(e.target.value)} placeholder={hub.data?.public_key_fingerprint ?? t('admin.publicKeyPlaceholder')} /></div><div className="flex flex-wrap gap-2"><Button type="submit" disabled={updateHub.isPending}>{updateHub.isPending ? <Loader2 className="animate-spin" /> : <Save />}{t('common.save')}</Button><Button type="button" variant="outline" disabled={verifyHub.isPending} onClick={() => verifyHub.mutate()}>{verifyHub.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}{t('admin.verifyConnection')}</Button></div></form>{hub.data && <div className="flex flex-wrap gap-2 border-t pt-4 text-xs"><Badge variant={hub.data.outbound_status === 'ok' ? 'default' : 'outline'}>{t('admin.outbound')}: {hub.data.outbound_status}</Badge><Badge variant={hub.data.inbound_status === 'ok' ? 'default' : 'outline'}>{t('admin.inbound')}: {hub.data.inbound_status}</Badge>{hub.data.last_verified_at && <span className="self-center text-muted-foreground">{new Date(hub.data.last_verified_at).toLocaleString()}</span>}</div>}</CardContent>
			</Card>
			<div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{t('admin.salesHubBoundaryNotice')}</div>
		</>
	);
}
