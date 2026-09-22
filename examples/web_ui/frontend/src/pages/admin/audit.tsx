import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Loader2, LockKeyhole, MessageSquare, ScrollText } from 'lucide-react';
import { useMemo, useState } from 'react';

import { AdminErrorNotice, AdminHeader, Pagination } from './shared';
import { adminApi } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';

type AuditOverviewData = {
	user?: { username?: string; id?: string; status?: string };
	sessions?: Array<{ id: string; agent_id: string; name?: string; created_at: string }>;
	documents?: Array<{ id: string; knowledge_base_id: string; filename?: string; status?: string }>;
	[key: string]: unknown;
};

export function AdminAuditPage() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const queryClient = useQueryClient();
	const [targetUserId, setTargetUserId] = useState('');
	const [reason, setReason] = useState('');
	const [auditPage, setAuditPage] = useState(1);
	const [selectedResource, setSelectedResource] = useState<Record<string, unknown> | null>(null);
	const users = useQuery({ queryKey: ['admin', user?.id, user?.tenant_id, 'audit-users'], queryFn: () => adminApi.users({ page: 1, page_size: 100 }), enabled: hasPermission('tenant:manage') || hasPermission('platform:observe') });
	const events = useQuery({ queryKey: ['admin', user?.id, user?.tenant_id, 'audit-events'], queryFn: () => adminApi.auditEvents(200), enabled: hasPermission('tenant:manage') || hasPermission('platform:observe') });
	const overview = useMutation({
		mutationFn: adminApi.auditOverview,
		onSuccess: async () => { setReason(''); await queryClient.invalidateQueries({ queryKey: ['admin', user?.id, user?.tenant_id, 'audit-events'] }); },
	});
	const accessSession = useMutation({ mutationFn: ({ sessionId, body }: { sessionId: string; body: { overview_event_id: string; agent_id: string; reason: string } }) => adminApi.auditSession(sessionId, body), onSuccess: (data) => setSelectedResource(data.data) });
	const accessDocument = useMutation({ mutationFn: ({ documentId, body }: { documentId: string; body: { overview_event_id: string; knowledge_base_id: string; reason: string } }) => adminApi.auditDocument(documentId, body), onSuccess: (data) => setSelectedResource(data.data) });
	const error = overview.error?.message ?? accessSession.error?.message ?? accessDocument.error?.message ?? users.error?.message ?? events.error?.message;
	const data = (overview.data?.data ?? null) as AuditOverviewData | null;
	const overviewEvent = useMemo(() => {
		if (!overview.data) return null;
		return (events.data?.events ?? []).find((event) => event.action === 'audit.overview' && event.request_id === overview.data?.request_id) ?? null;
	}, [events.data?.events, overview.data]);
	const auditItems = events.data?.events ?? [];
	const auditPageItems = auditItems.slice((auditPage - 1) * 20, auditPage * 20);
	const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin', user?.id] });
	const submitOverview = () => {
		if (!targetUserId || reason.trim().length < 4) return;
		overview.mutate({ target_user_id: targetUserId, reason: reason.trim() });
	};
	const accessReason = () => window.prompt(t('admin.auditResourceReasonPrompt'))?.trim() ?? '';

	return (
		<>
			<AdminHeader title={t('admin.nav.audit')} description={t('admin.auditModuleDescription')} onRefresh={refresh} loading={events.isFetching} />
			<AdminErrorNotice message={error} />
			<Card>
				<CardHeader><CardTitle className="flex items-center gap-2"><LockKeyhole className="size-4" />{t('admin.auditTwoStepTitle')}</CardTitle><CardDescription>{t('admin.auditTwoStepDescription')}</CardDescription></CardHeader>
				<CardContent className="space-y-4"><div className="flex flex-wrap gap-3"><select value={targetUserId} onChange={(e) => setTargetUserId(e.target.value)} className="border-input bg-background h-9 min-w-56 rounded-md border px-3 text-sm"><option value="">{t('admin.selectAuditMember')}</option>{(users.data?.users ?? []).filter((item) => item.role !== 'admin').map((item) => <option key={item.id} value={item.id}>{item.username} · {item.id}</option>)}</select><Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t('admin.auditReasonPlaceholder')} className="min-w-64 max-w-md" /><Button onClick={submitOverview} disabled={!targetUserId || reason.trim().length < 4 || overview.isPending}>{overview.isPending ? <Loader2 className="animate-spin" /> : <LockKeyhole />}{t('admin.auditMember')}</Button></div>{overview.data && <Alert><AlertDescription>{t('admin.auditOverviewReady')} {data?.user?.username ?? overview.data.target_user_id}{overviewEvent ? ` · ${overviewEvent.event_id}` : ` · ${overview.data.request_id}`}</AlertDescription></Alert>}
					{data && <div className="grid gap-4 md:grid-cols-2"><div className="rounded-lg border p-3"><div className="mb-2 flex items-center gap-2 font-medium"><MessageSquare className="size-4" />{t('admin.auditSessions')}</div>{data.sessions?.length ? data.sessions.map((session) => <div key={session.id} className="flex items-center justify-between gap-2 border-t py-2 text-sm"><div className="min-w-0"><div className="truncate">{session.name ?? session.id}</div><div className="font-mono text-[10px] text-muted-foreground">{session.id}</div></div><Button size="sm" variant="outline" disabled={!overviewEvent || accessSession.isPending} onClick={() => { const access = accessReason(); if (access.length >= 4 && overviewEvent) accessSession.mutate({ sessionId: session.id, body: { overview_event_id: overviewEvent.event_id, agent_id: session.agent_id, reason: access } }); }}>{t('admin.viewResource')}</Button></div>) : <div className="text-sm text-muted-foreground">{t('admin.noAuditResources')}</div>}</div><div className="rounded-lg border p-3"><div className="mb-2 flex items-center gap-2 font-medium"><FileText className="size-4" />{t('admin.auditDocuments')}</div>{data.documents?.length ? data.documents.map((document) => <div key={document.id} className="flex items-center justify-between gap-2 border-t py-2 text-sm"><div className="min-w-0"><div className="truncate">{document.filename ?? document.id}</div><div className="font-mono text-[10px] text-muted-foreground">{document.id}</div></div><Button size="sm" variant="outline" disabled={!overviewEvent || accessDocument.isPending} onClick={() => { const access = accessReason(); if (access.length >= 4 && overviewEvent) accessDocument.mutate({ documentId: document.id, body: { overview_event_id: overviewEvent.event_id, knowledge_base_id: document.knowledge_base_id, reason: access } }); }}>{t('admin.viewResource')}</Button></div>) : <div className="text-sm text-muted-foreground">{t('admin.noAuditResources')}</div>}</div></div>}
					{selectedResource && <pre className="max-h-72 overflow-auto rounded-lg bg-muted/60 p-3 text-xs">{JSON.stringify(selectedResource, null, 2)}</pre>}
				</CardContent>
			</Card>
			<Card>
				<CardHeader><CardTitle className="flex items-center gap-2"><ScrollText className="size-4" />{t('admin.auditTitle')}</CardTitle><CardDescription>{t('admin.auditDescription')}</CardDescription></CardHeader>
				<CardContent className="space-y-3">{auditPageItems.map((event) => <div key={event.event_id} className="rounded-md border p-3 text-xs"><div className="flex flex-wrap justify-between gap-2 font-medium"><span>{event.action}</span><Badge variant={event.status === 'completed' ? 'default' : 'destructive'}>{event.status}</Badge></div><div className="mt-1 text-muted-foreground">{event.actor_name} · {event.target_user_name ?? event.target_user_id ?? '—'} · {new Date(event.created_at).toLocaleString()}</div><div className="mt-1">{event.reason}</div></div>)}{!events.isPending && auditPageItems.length === 0 && <div className="text-sm text-muted-foreground">{t('admin.noAudit')}</div>}<Pagination page={auditPage} pageSize={20} total={auditItems.length} onPageChange={setAuditPage} loading={events.isFetching} /></CardContent>
			</Card>
		</>
	);
}
