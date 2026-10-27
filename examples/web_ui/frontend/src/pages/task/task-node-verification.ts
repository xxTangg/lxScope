import type {
	NodeRunRecord,
	TaskNode,
	TaskNodeAttemptRecord,
} from '@/api/task';

export type NodeCheckStatus =
	| 'passed'
	| 'failed'
	| 'not_performed'
	| 'not_applicable'
	| 'unknown';

export interface NodeVerificationCheck {
	id: string;
	checkType: string;
	status: NodeCheckStatus;
	source: string;
	message: string;
	details: string[];
	attempt: number;
}

export interface NodeAttemptView {
	attempt: number;
	source: 'automatic' | 'manual';
	status: NodeRunRecord['status'];
	input: string;
	output: string;
	result?: unknown;
	metadata: Record<string, unknown>;
	error?: string | null;
	error_detail?: NodeRunRecord['error_detail'];
	started_at?: string | null;
	finished_at?: string | null;
	isCurrent: boolean;
}

export function nodeExecutionStatusLabel(status: NodeRunRecord['status']): string {
	if (status === 'awaiting_input') return '等待补充信息';
	if (status === 'running') return '执行中';
	if (status === 'succeeded') return '成功';
	if (status === 'failed') return '失败';
	if (status === 'canceled') return '已取消';
	return '等待中';
}

const CHECK_STATUS_VALUES = new Set<NodeCheckStatus>([
	'passed',
	'failed',
	'not_performed',
	'not_applicable',
	'unknown',
]);

function asRecord(value: unknown): Record<string, unknown> | null {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	return value as Record<string, unknown>;
}

function stringValue(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

function validationStatus(record: Record<string, unknown>): NodeCheckStatus {
	if (typeof record.passed === 'boolean') return record.passed ? 'passed' : 'failed';
	if (typeof record.status === 'string' && CHECK_STATUS_VALUES.has(record.status as NodeCheckStatus)) {
		return record.status as NodeCheckStatus;
	}
	return 'unknown';
}

function sourceLabel(record: Record<string, unknown>, fallback: string): string {
	const parts = [
		stringValue(record.method),
		stringValue(record.verifier),
		stringValue(record.contract_id),
		typeof record.contract_version === 'number'
			? `v${record.contract_version}`
			: '',
		stringValue(record.record_id),
		stringValue(record.verification_id),
	];
	const source = [...new Set(parts.filter(Boolean))].join(' · ');
	return source || fallback;
}

function safeDetails(record: Record<string, unknown>): string[] {
	const details: string[] = [];
	for (const key of ['issues', 'warnings']) {
		const items = record[key];
		if (!Array.isArray(items)) continue;
		for (const item of items) {
			if (typeof item === 'string' && item.trim()) details.push(item.trim());
			else {
				const issue = asRecord(item);
				const message = issue ? stringValue(issue.message) : '';
				if (message) details.push(message);
			}
		}
	}
	for (const key of ['diagnostics', 'all_diagnostics']) {
		const items = record[key];
		if (!Array.isArray(items)) continue;
		for (const item of items) {
			const diagnostic = asRecord(item);
			if (!diagnostic) continue;
			const summary = [
				stringValue(diagnostic.error_code),
				stringValue(diagnostic.phase),
				stringValue(diagnostic.location),
				stringValue(diagnostic.message),
			]
				.filter(Boolean)
				.join(' · ');
			if (summary) details.push(summary);
		}
	}
	for (const key of ['verified_citations', 'citations', 'source_ledger', 'ledger']) {
		const items = record[key];
		if (!Array.isArray(items)) continue;
		for (const item of items) {
			const evidence = asRecord(item);
			if (!evidence) continue;
			const identifiers = [
				stringValue(evidence.claim_id),
				stringValue(evidence.occurrence_id),
				stringValue(evidence.citation_id),
				stringValue(evidence.evidence_id),
				stringValue(evidence.source_id),
			].filter(Boolean);
			if (identifiers.length) details.push(`依据标识：${identifiers.join(' / ')}`);
		}
	}
	return [...new Set(details)].slice(0, 16);
}

function recordCheck(
	id: string,
	checkType: string,
	recordValue: unknown,
	attempt: number,
	fallbackSource: string,
): NodeVerificationCheck | null {
	const record = asRecord(recordValue);
	if (!record) return null;
	return {
		id,
		checkType,
		status: validationStatus(record),
		source: sourceLabel(record, fallbackSource),
		message:
			stringValue(record.message) ||
			stringValue(record.error) ||
			stringValue(record.status_detail) ||
			(CHECK_STATUS_VALUES.has(stringValue(record.status) as NodeCheckStatus)
				? ''
				: stringValue(record.status)),
		details: safeDetails(record),
		attempt,
	};
}

function missingRecordStatus(nodeRun: Pick<NodeRunRecord, 'status'>): NodeCheckStatus {
	return ['pending', 'running', 'awaiting_input'].includes(nodeRun.status)
		? 'not_performed'
		: 'unknown';
}

function missingCheck(
	id: string,
	checkType: string,
	status: NodeCheckStatus,
	attempt: number,
	message: string,
): NodeVerificationCheck {
	return {
		id,
		checkType,
		status,
		source:
			status === 'unknown'
				? '服务端未返回该项记录'
				: status === 'not_applicable'
					? '冻结节点类型 / 输出契约'
					: 'Task 执行 / 验收记录',
		message,
		details: [],
		attempt,
	};
}

export function collectNodeVerificationChecks(
	metadata: Record<string, unknown> | undefined,
	nodeRun: Pick<NodeRunRecord, 'type' | 'status'> & Partial<Pick<NodeRunRecord, 'attempt'>>,
	node: TaskNode | undefined,
): NodeVerificationCheck[] {
	const records = metadata ?? {};
	const attempt = nodeRun.attempt ?? 0;
	const checks: NodeVerificationCheck[] = [];
	const addStored = (
		id: string,
		checkType: string,
		key: string,
		fallbackSource = key,
	) => {
		const check = recordCheck(id, checkType, records[key], attempt, fallbackSource);
		if (check) checks.push(check);
		return Boolean(check);
	};

	const verificationRecords = Array.isArray(records.verifications)
		? records.verifications
				.map(asRecord)
				.filter((record): record is Record<string, unknown> => Boolean(record))
		: [];
	const taskAiRecord =
		asRecord(records.task_ai_verification) ??
		verificationRecords.find((record) => stringValue(record.verifier) === 'task-verifier') ??
		null;
	if (taskAiRecord) {
		const aiCheck = recordCheck(
			'ai',
			'Agent AI 验收',
			taskAiRecord,
			attempt,
			'Task task-verifier',
		);
		if (aiCheck) checks.push(aiCheck);
	} else {
		const verifierWasExplicitlyNotRun =
			nodeRun.type === 'agent' &&
			Array.isArray(records.verifications) &&
			records.verifications.length === 0 &&
			records.attempts === 0;
		checks.push(
			missingCheck(
				'ai',
				'Agent AI 验收',
				['input', 'tool', 'python'].includes(nodeRun.type)
					? 'not_applicable'
					: verifierWasExplicitlyNotRun
						? 'not_performed'
						: missingRecordStatus(nodeRun),
				attempt,
				['input', 'tool', 'python'].includes(nodeRun.type)
					? `${nodeRun.type === 'input' ? 'InputStep' : nodeRun.type === 'tool' ? 'ToolStep' : 'CodeStep'} 不执行 Agent AI 验收。`
					: verifierWasExplicitlyNotRun
						? 'TaskService 持久化记录显示 verifier 尝试数为 0。'
						: '当前 Run 没有可确认的 AI 验收记录；不会根据执行状态推断验收结论。',
			),
		);
	}

	const outputContract = node?.output_contract;
	const schemaApplicable = Boolean(
		node?.output_schema || outputContract?.validation_mode === 'json_schema',
	);
	const hasSchemaRecord = addStored(
		'schema',
		'Output Schema 校验',
		'output_schema_validation',
		'Task 确定性 JSON Schema Validator',
	);
	if (!hasSchemaRecord) {
		checks.push(
			missingCheck(
				'schema',
				'Output Schema 校验',
				schemaApplicable ? missingRecordStatus(nodeRun) : 'not_applicable',
				attempt,
				schemaApplicable
					? '适用 Schema，但此 Attempt 没有结构化校验记录。'
					: '冻结节点没有声明 Output Schema。',
			),
		);
	}

	const contractApplicable = Boolean(
		outputContract && outputContract.validation_mode !== 'none',
	);
	const hasContractRecord = addStored(
		'contract',
		'Output Contract 校验',
		'output_contract_validation',
		'Task 冻结输出契约 Validator',
	);
	if (!hasContractRecord) {
		checks.push(
			missingCheck(
				'contract',
				'Output Contract 校验',
				contractApplicable ? missingRecordStatus(nodeRun) : 'not_applicable',
				attempt,
				contractApplicable
					? '冻结节点声明了输出契约，但此 Attempt 没有校验记录。'
					: '冻结节点未声明需执行的 Output Contract。',
			),
		);
	}

	const citationApplicable = Boolean(
		outputContract &&
		['citations', 'claim_evidence'].includes(outputContract.validation_mode),
	);
	const citationRecordKey = [
		'citation_output_validation',
		'final_citation_verification',
		'citation_verification',
	].find((key) => asRecord(records[key]));
	if (citationRecordKey) {
		addStored('citations', '来源 / Citation 核验', citationRecordKey, 'Task 授权来源 Validator');
	} else {
		checks.push(
			missingCheck(
				'citations',
				'来源 / Citation 核验',
				citationApplicable ? missingRecordStatus(nodeRun) : 'not_applicable',
				attempt,
				citationApplicable
					? '冻结契约要求来源核验，但此 Attempt 没有核验记录。'
					: '冻结节点没有声明 Citation 校验。',
			),
		);
	}

	const ledger = asRecord(records.evidence_ledger);
	const ledgerApplicable = outputContract?.validation_mode === 'claim_evidence';
	const ledgerCheck = recordCheck(
		'claim-evidence',
		'Claim–Evidence 台账',
		ledger,
		attempt,
		'Task Claim–Evidence Validator',
	);
	if (ledgerCheck) checks.push(ledgerCheck);
	else {
		checks.push(
			missingCheck(
				'claim-evidence',
				'Claim–Evidence 台账',
				ledgerApplicable ? missingRecordStatus(nodeRun) : 'not_applicable',
				attempt,
				ledgerApplicable
					? '冻结契约要求 Claim–Evidence 校验，但此 Attempt 没有台账记录。'
					: '冻结节点没有声明 Claim–Evidence 校验。',
			),
		);
	}

	const additionalRecords: Array<[string, string]> = [
		['task_input_validation', 'InputStep 输入校验'],
		['s02_lightweight_source_validation', 'S02 来源完整性校验'],
		['s02_lightweight_output_validation', 'S02 草稿输出校验'],
		['s02_lightweight_delivery', 'S02 草稿交付门禁'],
		['s02_delivery_report', 'S02 业务交付校验'],
		['s02_v4_delivery', 'S02 v4 业务交付校验'],
	];
	for (const [key, label] of additionalRecords) {
		addStored(`business:${key}`, label, key, `Task ${key}`);
	}

	if (Array.isArray(records.verifications)) {
		verificationRecords.forEach((record, index) => {
			const verifier = stringValue(record.verifier);
			if (
				(verifier === 'task-verifier' && taskAiRecord) ||
				(verifier === 'task-output-contract' && asRecord(records.output_contract_validation))
			) return;
			const check = recordCheck(
				`other:${index}`,
				verifier ? `其他验收 · ${verifier}` : '其他 Task 验收',
				record,
				attempt,
				'Task persisted verification record',
			);
			if (check) checks.push(check);
		});
	}

	return checks;
}

export function getNodeAttemptViews(nodeRun: NodeRunRecord): NodeAttemptView[] {
	const attempts = new Map<number, NodeAttemptView>();
	for (const attempt of nodeRun.attempt_history ?? []) {
		attempts.set(attempt.attempt, fromAttemptRecord(attempt));
	}
	if ((nodeRun.attempt ?? 0) > 0) {
		const currentAttempt = nodeRun.attempt ?? 0;
		const persistedCurrentAttempt = attempts.get(currentAttempt);
		if (persistedCurrentAttempt) {
			attempts.set(currentAttempt, { ...persistedCurrentAttempt, isCurrent: true });
		} else {
			attempts.set(currentAttempt, {
			attempt: nodeRun.attempt ?? 0,
			source: nodeRun.metadata?.next_attempt_source === 'manual' ? 'manual' : 'automatic',
			status: nodeRun.status,
			input: nodeRun.input,
			output: nodeRun.output,
			result: nodeRun.result,
			metadata: nodeRun.metadata ?? {},
			error: nodeRun.error,
			error_detail: nodeRun.error_detail,
			started_at: nodeRun.started_at,
			finished_at: nodeRun.finished_at,
			isCurrent: true,
			});
		}
	}
	return [...attempts.values()].sort((left, right) => left.attempt - right.attempt);
}

function fromAttemptRecord(attempt: TaskNodeAttemptRecord): NodeAttemptView {
	return {
		...attempt,
		metadata: attempt.metadata ?? {},
		isCurrent: false,
	};
}

