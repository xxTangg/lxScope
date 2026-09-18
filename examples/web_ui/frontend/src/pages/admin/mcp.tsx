import { CheckCircle2, EyeOff, Plug, Settings2, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { AdminHeader } from './shared';
import { UserScopePicker } from './skills';
import { adminApi, mcpApi } from '@/api';
import type { AdminUser, MCPView, PublicationScope, ResourcePublication } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
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
	onRemove,
}: {
	mcp: MCPView;
	publication?: ResourcePublication;
	users: AdminUser[];
	onSaved: () => void;
	onRemove: () => void;
}) {
	const { t } = useTranslation();
	const [scope, setScope] = useState<PublicationScope>(publication?.scope ?? 'none');
	const [userIds, setUserIds] = useState<string[]>(publication?.user_ids ?? []);
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		setScope(publication?.scope ?? 'none');
		setUserIds(publication?.user_ids ?? []);
	}, [publication]);

	const save = async (nextScope = scope, nextUserIds = userIds) => {
		if (nextScope === 'selected' && nextUserIds.length === 0) return;
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
				user_ids: nextScope === 'selected' ? nextUserIds : [],
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
							if (next !== 'selected') {
								setUserIds([]);
								void save(next, []);
							}
						}}
					/>
					{scope === 'selected' && (
						<>
							<UserScopePicker users={users} userIds={userIds} onChange={setUserIds} />
							<Button size="sm" disabled={saving || userIds.length === 0} onClick={() => void save()}>
								{saving ? <Spinner /> : <CheckCircle2 className="size-3.5" />}
								{t('admin.savePublication')}
							</Button>
						</>
					)}
				</div>
				<div className="flex items-center justify-between gap-2">
					<div
						className={`flex items-center gap-1 text-xs ${scope === 'none' ? 'text-muted-foreground' : 'text-emerald-600 dark:text-emerald-400'}`}
					>
						{scope === 'none' ? <EyeOff className="size-3.5" /> : <CheckCircle2 className="size-3.5" />}
						{scope === 'all'
							? t('admin.mcpVisibleToAllBadge')
							: scope === 'selected'
								? t('admin.selectedUsersBadge', { count: userIds.length })
								: t('admin.hiddenMcpBadge')}
					</div>
					<Button
						variant="ghost"
						size="icon-sm"
						title={t('admin.removeInstalledMcp')}
						onClick={(event) => {
							event.stopPropagation();
							onRemove();
						}}
					>
						<Trash2 />
					</Button>
				</div>
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
	const [removeTarget, setRemoveTarget] = useState<MCPView | null>(null);

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

	const removeInstalledMcp = async () => {
		if (!removeTarget) return;
		await adminApi.removeMcp(removeTarget.id);
		setRemoveTarget(null);
		await refetch();
	};

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
							onRemove={() => setRemoveTarget(mcp)}
						/>
					))}
				</div>
			)}
			<DeleteDialog
				open={removeTarget !== null}
				onOpenChange={(open) => {
					if (!open) setRemoveTarget(null);
				}}
				title={t('admin.removeInstalledMcp')}
				description={t('admin.removeInstalledMcpDescription', {
					name: removeTarget?.display_name || removeTarget?.name || '',
				})}
				confirmLabel={t('admin.removeMcp')}
				onConfirm={removeInstalledMcp}
			/>
		</>
	);
}
