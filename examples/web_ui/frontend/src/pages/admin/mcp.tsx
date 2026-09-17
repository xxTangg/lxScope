import { Plug, Save, Settings2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { adminApi, mcpApi } from '@/api';
import type { AdminUser, MCPView, PublicationScope, ResourcePublication } from '@/api';
import { AdminHeader } from './shared';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Spinner } from '@/components/ui/spinner';
import { useTranslation } from '@/i18n/useI18n';

function PublishScopeSelect({
	value,
	onChange,
}: {
	value: PublicationScope;
	onChange: (value: PublicationScope) => void;
}) {
	const { t } = useTranslation();
	return (
		<select
			value={value}
			onChange={(event) => onChange(event.target.value as PublicationScope)}
			className="h-9 rounded-md border border-input bg-background px-2 text-sm"
		>
			<option value="none">{t('admin.publishNone')}</option>
			<option value="all">{t('admin.publishAll')}</option>
			<option value="selected">{t('admin.publishSelected')}</option>
		</select>
	);
}

function McpPublishRow({
	mcp,
	publication,
	users,
	onSaved,
}: {
	mcp: MCPView;
	publication?: ResourcePublication;
	users: AdminUser[];
	onSaved: () => void;
}) {
	const { t } = useTranslation();
	const [scope, setScope] = useState<PublicationScope>(publication?.scope ?? 'none');
	const [userIds, setUserIds] = useState<string[]>(publication?.user_ids ?? []);
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		setScope(publication?.scope ?? 'none');
		setUserIds(publication?.user_ids ?? []);
	}, [publication]);

	const save = async (nextScope = scope) => {
		if (nextScope === 'selected' && userIds.length === 0) return;
		setSaving(true);
		try {
			await adminApi.publishResource({
				kind: 'mcp',
				source_id: mcp.id,
				source_record_id: mcp.id,
				name: mcp.name,
				display_name: mcp.display_name,
				description: mcp.description,
				tags: mcp.tags,
				author: mcp.author,
				icon_url: mcp.icon_url,
				version: mcp.version,
				scope: nextScope,
				user_ids: userIds,
			});
			onSaved();
		} finally {
			setSaving(false);
		}
	};

	return (
		<Card>
			<CardHeader className="pb-3">
				<CardTitle className="flex items-center gap-2 text-base">
					<Plug className="size-4" />
					{mcp.display_name || mcp.name}
					{mcp.enabled ? <Badge variant="secondary">{t('admin.configured')}</Badge> : null}
				</CardTitle>
				<CardDescription>{mcp.description || mcp.name}</CardDescription>
			</CardHeader>
			<CardContent className="space-y-3">
				<div className="flex flex-wrap items-center gap-2">
					<span className="text-sm text-muted-foreground">{t('admin.publishTo')}</span>
					<PublishScopeSelect
						value={scope}
						onChange={(next) => {
							setScope(next);
							if (next !== 'selected') void save(next);
						}}
					/>
					{scope === 'selected' && (
						<Button size="sm" disabled={saving || userIds.length === 0} onClick={() => void save()}>
							{saving ? <Spinner /> : <Save className="size-3.5" />}
							{t('admin.savePublication')}
						</Button>
					)}
				</div>
				{scope === 'selected' && (
					<select
						multiple
						value={userIds}
						onChange={(event) =>
							setUserIds(Array.from(event.target.selectedOptions, (option) => option.value))
						}
						className="min-h-24 w-full rounded-md border border-input bg-background p-2 text-sm"
					>
						{users.map((user) => (
							<option key={user.id} value={user.id}>
								{user.username}
							</option>
						))}
					</select>
				)}
			</CardContent>
		</Card>
	);
}

export function AdminMcpPage() {
	const { t } = useTranslation();
	const navigate = useNavigate();
	const [mcps, setMcps] = useState<MCPView[]>([]);
	const [publications, setPublications] = useState<ResourcePublication[]>([]);
	const [users, setUsers] = useState<AdminUser[]>([]);
	const [loading, setLoading] = useState(true);

	const refetch = async () => {
		setLoading(true);
		try {
			const [nextMcps, nextPublications, nextUsers] = await Promise.all([
				mcpApi.list(),
				adminApi.resourcePublications('mcp'),
				adminApi.users({ page: 1, page_size: 100 }),
			]);
			setMcps(nextMcps);
			setPublications(nextPublications.resources);
			setUsers(nextUsers.users);
		} finally {
			setLoading(false);
		}
	};

	useEffect(() => {
		void refetch();
	}, []);

	const publicationBySource = useMemo(
		() => new Map(publications.map((publication) => [publication.source_id, publication])),
		[publications],
	);

	return (
		<>
			<AdminHeader title={t('admin.nav.mcp')} description={t('admin.mcpModuleDescription')} />
			<div className="flex flex-wrap items-center justify-between gap-3 border-b pb-5">
				<p className="text-sm text-muted-foreground">{t('admin.mcpConfigNotice')}</p>
				<Button onClick={() => navigate('/mcp')}>
					<Settings2 />
					{t('admin.openMcpLibrary')}
				</Button>
			</div>
			{loading ? (
				<div className="flex justify-center py-16"><Spinner /></div>
			) : mcps.length === 0 ? (
				<Card className="mt-6">
					<CardContent className="py-12 text-center text-sm text-muted-foreground">
						{t('admin.noConfiguredMcps')}
					</CardContent>
				</Card>
			) : (
				<div className="mt-6 grid gap-4 lg:grid-cols-2">
					{mcps.map((mcp) => (
						<McpPublishRow
							key={mcp.id}
							mcp={mcp}
							publication={publicationBySource.get(mcp.id)}
							users={users}
							onSaved={() => void refetch()}
						/>
					))}
				</div>
			)}
		</>
	);
}
