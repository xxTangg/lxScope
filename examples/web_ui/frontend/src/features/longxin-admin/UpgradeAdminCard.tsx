import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArchiveRestore, Download, Loader2, RotateCcw, Upload } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { upgradeApi, type ArtifactType, type ReleaseMeta, type UpgradeOperation } from './upgrade-api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';

function bytes(value: number) {
	if (value < 1024) return `${value} B`;
	if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
	return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function operationVariant(state: UpgradeOperation['state']) {
	if (state === 'completed') return 'default' as const;
	if (state === 'failed') return 'destructive' as const;
	if (state === 'rolled_back') return 'outline' as const;
	return 'secondary' as const;
}

export function UpgradeAdminCard() {
	const { t } = useTranslation();
	const { user, hasPermission } = useAuth();
	const canUpgrade =
		hasPermission('tenant:manage') || hasPermission('platform:upgrade');
	const queryClient = useQueryClient();
	const [artifactType, setArtifactType] = useState<ArtifactType>('app');
	const [version, setVersion] = useState('');
	const [file, setFile] = useState<File | null>(null);
	const [adminPassword, setAdminPassword] = useState('');
	const catalog = useQuery({
		queryKey: ['longxin-upgrades', user?.id, 'catalog'],
		queryFn: upgradeApi.catalog,
		enabled: canUpgrade,
	});
	const status = useQuery({
		queryKey: ['longxin-upgrades', user?.id, 'status'],
		queryFn: upgradeApi.status,
		enabled: canUpgrade,
	});
	const backups = useQuery({
		queryKey: ['longxin-upgrades', user?.id, 'backups'],
		queryFn: () => upgradeApi.backups(20),
		enabled: canUpgrade,
	});
	const operations = useQuery({
		queryKey: ['longxin-upgrades', user?.id, 'operations'],
		queryFn: () => upgradeApi.operations(20),
		enabled: canUpgrade,
		refetchInterval: (query) =>
			query.state.data?.operations.some((item) =>
				['pending', 'downloading', 'backing_up', 'applying', 'health_check'].includes(item.state),
			)
				? 3000
				: false,
	});
	const refresh = async () => {
		await queryClient.invalidateQueries({ queryKey: ['longxin-upgrades'] });
	};
	const upload = useMutation({
		mutationFn: () => {
			if (!file || !version.trim()) throw new Error(t('upgrade.fileAndVersionRequired'));
			return upgradeApi.upload(artifactType, version.trim(), file);
		},
		onSuccess: async () => {
			setVersion('');
			setFile(null);
			await refresh();
		},
	});
	const apply = useMutation({
		mutationFn: ({ type, release }: { type: ArtifactType; release: ReleaseMeta }) => {
			if (!adminPassword) throw new Error(t('upgrade.passwordRequired'));
			return upgradeApi.apply(type, release.version, adminPassword);
		},
		onSuccess: refresh,
	});
	const rollback = useMutation({
		mutationFn: (backupId: string) => {
			if (!adminPassword) throw new Error(t('upgrade.passwordRequired'));
			return upgradeApi.rollback(backupId, adminPassword);
		},
		onSuccess: refresh,
	});
	const deleteBackup = useMutation({
		mutationFn: ({ backupId, reason }: { backupId: string; reason: string }) =>
			upgradeApi.deleteBackup(backupId, reason),
		onSuccess: refresh,
	});

	const latest: Array<{ type: ArtifactType; release: ReleaseMeta | null }> = [
		{ type: 'app', release: catalog.data?.app ?? null },
		{ type: 'core', release: catalog.data?.core ?? null },
	];
	const error =
		upload.error?.message ??
		apply.error?.message ??
		rollback.error?.message ??
		deleteBackup.error?.message ??
		catalog.error?.message ??
		status.error?.message;

	const submitUpload = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		upload.mutate();
	};

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<ArchiveRestore className="size-4" />
					{t('upgrade.title')}
				</CardTitle>
				<CardDescription>{t('upgrade.description')}</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{error && (
					<Alert variant="destructive">
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				<div className="text-sm text-muted-foreground">
					{t('upgrade.health')}: <Badge variant={status.data?.health === 'ok' ? 'default' : 'outline'}>{status.data?.health ?? t('upgrade.unknown')}</Badge>
					 · {t('upgrade.installed')}: {status.data?.app_version ?? t('upgrade.unknown')} / {status.data?.core_version ?? t('upgrade.unknown')}
				</div>
				<div className="max-w-sm space-y-1.5">
					<Label htmlFor="upgrade-admin-password">{t('upgrade.adminPassword')}</Label>
					<Input
						id="upgrade-admin-password"
						type="password"
						value={adminPassword}
						onChange={(event) => setAdminPassword(event.target.value)}
						placeholder={t('upgrade.adminPasswordPlaceholder')}
					/>
				</div>

				<div className="grid gap-3 md:grid-cols-2">
					{latest.map(({ type, release }) => {
						const installed = catalog.data?.installed_versions[type] ?? null;
						const configured = catalog.data?.target_configured[type] ?? false;
						return (
							<div key={type} className="space-y-3 rounded-lg border p-4">
								<div className="flex items-center justify-between gap-2">
									<div className="font-medium">{t(`upgrade.types.${type}`)}</div>
									<Badge variant={configured ? 'default' : 'outline'}>
										{configured ? t('upgrade.targetReady') : t('upgrade.targetMissing')}
									</Badge>
								</div>
								<div className="text-sm text-muted-foreground">
									{t('upgrade.installed')}: {installed ?? t('upgrade.unknown')}
								</div>
								{release ? (
									<>
										<div className="text-sm">
											{t('upgrade.latest')}: <span className="font-medium">{release.version}</span> · {bytes(release.size)}
										</div>
										<div className="truncate font-mono text-xs text-muted-foreground" title={release.sha256}>
											SHA-256: {release.sha256}
										</div>
										<Button
											size="sm"
											disabled={!configured || !adminPassword || apply.isPending}
											onClick={() => apply.mutate({ type, release })}
										>
											{apply.isPending ? <Loader2 className="animate-spin" /> : <Download />}
											{t('upgrade.apply')}
										</Button>
									</>
								) : (
									<div className="text-sm text-muted-foreground">{t('upgrade.noRelease')}</div>
								)}
							</div>
						);
					})}
				</div>

				<form onSubmit={submitUpload} className="grid gap-3 rounded-lg bg-muted/40 p-3 md:grid-cols-[auto_1fr_1.5fr_auto] md:items-end">
					<div className="space-y-1.5">
						<Label htmlFor="upgrade-artifact-type">{t('upgrade.type')}</Label>
						<select
							id="upgrade-artifact-type"
							value={artifactType}
							onChange={(event) => setArtifactType(event.target.value as ArtifactType)}
							className="border-input bg-background h-9 rounded-md border px-3 text-sm"
						>
							<option value="app">{t('upgrade.types.app')}</option>
							<option value="core">{t('upgrade.types.core')}</option>
						</select>
					</div>
					<div className="space-y-1.5">
						<Label htmlFor="upgrade-version">{t('upgrade.version')}</Label>
						<Input id="upgrade-version" value={version} onChange={(event) => setVersion(event.target.value)} placeholder="3.0.4" required />
					</div>
					<div className="space-y-1.5">
						<Label htmlFor="upgrade-file">{t('upgrade.package')}</Label>
						<Input
							id="upgrade-file"
							type="file"
							accept=".tar.gz,.tgz,application/gzip"
							onChange={(event) => setFile(event.target.files?.[0] ?? null)}
							required
						/>
					</div>
					<Button type="submit" disabled={upload.isPending}>
						{upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
						{t('upgrade.upload')}
					</Button>
				</form>

				<div className="space-y-2">
					<div className="flex items-center gap-2 text-sm font-medium">
						<RotateCcw className="size-4" />
						{t('upgrade.backups')}
					</div>
					{backups.data?.backups.length ? (
						backups.data.backups.map((backup) => (
							<div key={backup.backup_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
								<div className="min-w-0">
									<div>{t(`upgrade.types.${backup.artifact_type}`)} · {backup.version}</div>
									<div className="truncate font-mono text-xs text-muted-foreground">{backup.backup_id}</div>
								</div>
																											<Button size="sm" variant="outline" disabled={!adminPassword || rollback.isPending} onClick={() => rollback.mutate(backup.backup_id)}>
																												<RotateCcw />
																												{t('upgrade.rollback')}
																											</Button>
																											<Button
																												variant="destructive"
																												size="sm"
																												disabled={deleteBackup.isPending}
																												onClick={() => {
																													if (!window.confirm(t('upgrade.deleteBackupConfirm'))) return;
																													const reason = window.prompt(t('upgrade.deleteBackupReason'));
																													if (reason && reason.trim().length >= 4) {
																														deleteBackup.mutate({ backupId: backup.backup_id, reason: reason.trim() });
																													} else if (reason !== null) {
																														window.alert(t('upgrade.reasonRequired'));
																													}
																													}}
																												>
																												{t('upgrade.deleteBackup')}
																											</Button>
							</div>
						))
					) : (
						<div className="text-sm text-muted-foreground">{t('upgrade.noBackups')}</div>
					)}
				</div>

				<div className="space-y-2">
					<div className="text-sm font-medium">{t('upgrade.operations')}</div>
					{operations.data?.operations.length ? (
						operations.data.operations.slice(0, 8).map((operation) => (
							<div key={operation.operation_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-xs">
								<div className="min-w-0">
									<div className="font-mono">{operation.operation_id}</div>
									<div className="text-muted-foreground">{t(`upgrade.types.${operation.artifact_type}`)} · {operation.version}</div>
								</div>
								<Badge variant={operationVariant(operation.state)}>{t(`upgrade.states.${operation.state}`)}</Badge>
							</div>
						))
					) : (
						<div className="text-sm text-muted-foreground">{t('upgrade.noOperations')}</div>
					)}
				</div>
			</CardContent>
		</Card>
	);
}
