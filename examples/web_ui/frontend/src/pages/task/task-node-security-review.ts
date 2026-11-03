import type {
	NodeRunRecord,
	TaskNodeSecurityReviewRecord,
	TaskStepType,
} from '@/api/task';

export type NodeSecurityReviewStatus =
	| 'passed'
	| 'failed'
	| 'pending'
	| 'confirmation'
	| 'not_applicable'
	| 'not_performed'
	| 'missing';

export interface NodeSecurityReviewCheck {
	id: string;
	label: string;
	status: NodeSecurityReviewStatus;
	source: string;
	message: string;
}

export interface NodeSecurityReviewSummary {
	status: NodeSecurityReviewStatus;
	attempt: number;
	checks: NodeSecurityReviewCheck[];
}

export type NodeSecurityReviewCounts = Record<NodeSecurityReviewStatus, number>;

const KNOWLEDGE_RETRIEVAL_CAPABILITIES = new Set([
	'project.similar_search',
	'project.rule_check',
	'report.rag_retrieve',
]);

export function emptyNodeSecurityReviewCounts(): NodeSecurityReviewCounts {
	return {
		passed: 0,
		failed: 0,
		pending: 0,
		confirmation: 0,
		not_applicable: 0,
		not_performed: 0,
		missing: 0,
	};
}

function asRecord(value: unknown): Record<string, unknown> | null {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	return value as Record<string, unknown>;
}

function text(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

function preflightStatus(
	record: TaskNodeSecurityReviewRecord,
): NodeSecurityReviewStatus {
	if (record.status === 'permission_denied' || record.permission_status === 'denied') {
		return 'failed';
	}
	if (
		record.permission_status === 'requires_confirmation' ||
		record.external_side_effect
	) {
		return 'confirmation';
	}
	if (
		record.status === 'missing' ||
		record.status === 'incompatible' ||
		record.status === 'unsupported'
	) {
		return 'failed';
	}
	if (record.permission_status === 'not_applicable') return 'not_applicable';
	if (record.status === 'available' && record.permission_status === 'allowed') {
		return 'passed';
	}
	return 'pending';
}

function runtimeChecks(value: unknown): NodeSecurityReviewCheck[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((item, index) => {
		const record = asRecord(item);
		if (!record) return [];
		const rawStatus = text(record.status);
		const status: NodeSecurityReviewStatus =
			rawStatus === 'passed'
				? 'passed'
				: rawStatus === 'failed'
					? 'failed'
					: rawStatus === 'confirmation' || rawStatus === 'needs_confirmation'
						? 'confirmation'
						: rawStatus === 'not_applicable'
							? 'not_applicable'
							: rawStatus === 'not_performed'
								? 'not_performed'
								: rawStatus === 'missing'
									? 'missing'
									: 'pending';
		return [
			{
				id: text(record.check_id) || `runtime:${index}`,
				label: text(record.check_type) || '运行时权限与执行门禁',
				status,
				source: text(record.source) || 'Task Attempt 记录',
				message: text(record.message) || '服务端未返回检查说明。',
			},
		];
	});
}

function persistedKnowledgeAuthorizationCheck(
	metadata: Record<string, unknown>,
	nodeType: TaskStepType | undefined,
): NodeSecurityReviewCheck | null {
	if (nodeType !== 'tool') return null;
	const retrieval = asRecord(metadata.scenario_retrieval);
	if (!retrieval) return null;
	const capability = text(retrieval.capability_key);
	const retrievalStatus = text(retrieval.status);
	const scopeRecorded =
		retrieval.source_scope === 'authorized_task_knowledge_bases' ||
		(capability === 'project.rule_check' && retrievalStatus === 'not_found');
	if (
		!KNOWLEDGE_RETRIEVAL_CAPABILITIES.has(capability) ||
		!scopeRecorded ||
		!['ready', 'candidates_for_review', 'not_found'].includes(retrievalStatus)
	) {
		return null;
	}
	return {
		id: 'runtime:task-knowledge-scope',
		label: 'Task 知识库运行时权限范围',
		status: 'passed',
		source: '当前 Attempt 的 TaskKnowledgeGateway 检索记录',
		message:
			'服务端记录本次检索限定在当前用户授权的 Task 知识库范围内。此记录只证明访问范围校验，不代表规则适用性、证据质量或 AI 内容验收通过。',
	};
}

function statusForChecks(checks: NodeSecurityReviewCheck[]): NodeSecurityReviewStatus {
	if (checks.some((check) => check.status === 'failed')) return 'failed';
	if (checks.some((check) => check.status === 'confirmation')) return 'confirmation';
	if (checks.length > 0 && checks.every((check) => check.status === 'not_performed')) {
		return 'not_performed';
	}
	if (checks.length > 0 && checks.every((check) => check.status === 'missing')) {
		return 'missing';
	}
	const applicable = checks.filter((check) => check.status !== 'not_applicable');
	if (applicable.length === 0) {
		return 'not_applicable';
	}
	if (applicable.some((check) => check.status === 'pending' || check.status === 'not_performed')) {
		return 'pending';
	}
	if (applicable.some((check) => check.status === 'missing')) {
		return applicable.every((check) => check.status === 'missing') ? 'missing' : 'pending';
	}
	if (applicable.every((check) => check.status === 'passed')) return 'passed';
	return 'pending';
}

export function summarizeNodeSecurityReview(
	nodeRun: NodeRunRecord | null | undefined,
	options: {
		approvalRequired?: boolean;
		attemptMetadata?: Record<string, unknown>;
		attemptErrorDetail?: NodeRunRecord['error_detail'];
		attemptNumber?: number;
		nodeType?: TaskStepType;
	} = {},
): NodeSecurityReviewSummary {
	const currentAttempt = options.attemptNumber ?? nodeRun?.attempt ?? 0;
	const nodeType = options.nodeType ?? nodeRun?.type;
	const metadata = options.attemptMetadata ?? nodeRun?.metadata ?? {};
	const currentRuntimeChecks = runtimeChecks(metadata.task_security_review_checks);
	const checks: NodeSecurityReviewCheck[] = [];

	for (const [index, rawRecord] of (nodeRun?.security_reviews ?? []).entries()) {
		const sourceRecord = rawRecord as TaskNodeSecurityReviewRecord;
		checks.push({
			id: `preflight:${index}:${sourceRecord.capability_key}`,
			label: sourceRecord.tool_name
				? `${sourceRecord.category} · ${sourceRecord.tool_name}`
				: sourceRecord.category || sourceRecord.capability_key,
			status: preflightStatus(sourceRecord),
			source: 'Run 创建时的 Task Preflight',
			message: sourceRecord.message || 'Preflight 未提供检查说明。',
		});
	}
	checks.push(...currentRuntimeChecks);
	if (currentRuntimeChecks.length === 0) {
		const persistedKnowledgeCheck = persistedKnowledgeAuthorizationCheck(metadata, nodeType);
		if (persistedKnowledgeCheck) checks.push(persistedKnowledgeCheck);
	}

	const errorDetail = options.attemptErrorDetail ?? nodeRun?.error_detail;
	if (errorDetail?.category === 'permission_denied') {
		checks.push({
			id: `attempt:${currentAttempt}:permission-denied`,
			label: '当前 Attempt 权限校验',
			status: 'failed',
			source: `Attempt ${currentAttempt || '未知'} 服务端错误记录`,
			message: errorDetail.message || nodeRun?.error || '权限检查未通过。',
		});
	}
	const preflightRecords = nodeRun?.security_reviews ?? [];
	const preflightOnlyNotApplicable =
		preflightRecords.length > 0 &&
		preflightRecords.every(
			(record) =>
				record.permission_status === 'not_applicable' &&
				record.status === 'available' &&
				!record.external_side_effect,
		);
	if (
		(nodeType === 'agent' || nodeType === 'tool' || nodeType === 'python') &&
		currentRuntimeChecks.length === 0 &&
		!checks.some((check) => check.id === 'runtime:task-knowledge-scope') &&
		!preflightOnlyNotApplicable &&
		errorDetail?.category !== 'permission_denied'
	) {
		const attemptInProgress =
			nodeRun?.status === 'running' || nodeRun?.status === 'awaiting_input';
		const runtimeStatus: NodeSecurityReviewStatus =
			currentAttempt === 0
				? 'not_performed'
				: attemptInProgress
					? 'pending'
					: 'missing';
		checks.push({
			id: `attempt:${currentAttempt}:runtime-review-${runtimeStatus}`,
			label: '当前 Attempt 运行时权限与执行门禁',
			status: runtimeStatus,
			source:
				currentAttempt === 0
					? 'Task 执行节点适用性'
					: `Attempt ${currentAttempt} 持久化记录`,
			message:
				runtimeStatus === 'not_performed'
					? '当前节点尚未执行；Run 创建时的 Preflight 不能替代 Attempt 运行时检查。'
					: runtimeStatus === 'pending'
						? '当前 Attempt 尚未完成，运行时检查结果尚未持久化。'
						: '当前 Attempt 没有可追溯的运行时权限与执行门禁记录；Run 创建时的 Preflight 不能替代本次检查。',
		});
	}
	if (options.approvalRequired) {
		checks.push({
			id: 'planning:approval-required',
			label: '执行计划确认',
			status: 'confirmation',
			source: '冻结的 Task 规划快照',
			message: '该节点在服务端规划快照中被标记为需要确认。',
		});
	}
	if (checks.length === 0) {
		const isInput = nodeType === 'input';
		const isAttempted = currentAttempt > 0;
		checks.push({
			id: isInput ? 'node:type-not-applicable' : isAttempted ? 'missing-record' : 'not-performed',
			label: isInput ? '节点类型适用性' : isAttempted ? '节点安全审查记录' : '节点安全审查',
			status: isInput ? 'not_applicable' : isAttempted ? 'missing' : 'not_performed',
			source: isInput ? 'Task InputStep 类型规则' : 'TaskRun 持久化记录',
			message: isInput
				? 'InputStep 不直接调用 Tool、MCP 或 Python 执行器，因此不适用节点级工具权限检查。'
				: isAttempted
					? '当前节点已有 Attempt，但没有可追溯的节点级安全审查记录。'
					: '节点尚未执行，当前没有 Attempt 级安全检查结果。',
		});
	}

	return {
		status: statusForChecks(checks),
		attempt: currentAttempt,
		checks,
	};
}
