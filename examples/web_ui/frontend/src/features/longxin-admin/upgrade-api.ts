import { client } from '@/api/client';

export type ArtifactType = 'app' | 'core';
export type UpgradeState =
	| 'pending'
	| 'downloading'
	| 'backing_up'
	| 'applying'
	| 'health_check'
	| 'completed'
	| 'failed'
	| 'rolled_back';

export interface ReleaseMeta {
	type: ArtifactType;
	version: string;
	file: string;
	size: number;
	sha256: string;
	uploaded_at: string;
	uploaded_by: string;
	manifest: Record<string, unknown>;
}

export interface UpgradeCatalog {
	app: ReleaseMeta | null;
	core: ReleaseMeta | null;
	releases: ReleaseMeta[];
	installed_versions: Record<string, string | null>;
	target_configured: Record<string, boolean>;
}

export interface BackupMeta {
	backup_id: string;
	artifact_type: ArtifactType;
	version: string;
	path: string;
	operation_id: string;
	created_at: string;
	size_bytes: number;
}

export interface UpgradeOperation {
	operation_id: string;
	artifact_type: ArtifactType;
	version: string;
	state: UpgradeState;
	source: 'admin' | 'sales_hub';
	request_id: string;
	backup_id: string | null;
	started_at: string | null;
	finished_at: string | null;
	result: Record<string, unknown> | null;
	error: Record<string, unknown> | null;
}

interface BackupListResponse {
	backups: BackupMeta[];
	total: number;
}

interface UpgradeOperationListResponse {
	operations: UpgradeOperation[];
	total: number;
}

const idempotencyKey = () => {
	if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
	return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export const upgradeApi = {
	catalog: () => client.get<UpgradeCatalog>('/admin/upgrades'),
	backups: (limit = 20) =>
		client.get<BackupListResponse>('/admin/upgrades/backups', { limit: String(limit) }),
	operations: (limit = 20) =>
		client.get<UpgradeOperationListResponse>('/admin/upgrades/operations', { limit: String(limit) }),
	upload: (artifactType: ArtifactType, version: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return client.form<ReleaseMeta>(
			`/admin/upgrades/releases/${artifactType}?version=${encodeURIComponent(version)}`,
			form,
			{ headers: { 'Idempotency-Key': idempotencyKey() } },
		);
	},
	apply: (artifactType: ArtifactType, version: string, admin_password: string) =>
		client.post<UpgradeOperation>(
			`/admin/upgrades/${artifactType}/apply`,
			{ version, admin_password },
			undefined,
			{ headers: { 'Idempotency-Key': idempotencyKey() } },
		),
	rollback: (backup_id: string, admin_password: string) =>
		client.post<UpgradeOperation>(
			'/admin/upgrades/rollback',
			{ backup_id, admin_password },
			undefined,
			{ headers: { 'Idempotency-Key': idempotencyKey() } },
		),
};
