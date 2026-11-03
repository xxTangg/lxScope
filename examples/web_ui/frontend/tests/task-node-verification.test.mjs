import assert from 'node:assert/strict';
import test from 'node:test';

import {
	collectNodeVerificationChecks,
	getNodeAttemptViews,
	nodeExecutionStatusLabel,
} from '../src/pages/task/task-node-verification.ts';
import { sanitizeTaskRunContent } from '../src/pages/task/task-run-sanitization.ts';

function makeNode(overrides = {}) {
	return {
		id: 'agent-1',
		name: '生成报告',
		prompt: '按当前资料生成',
		type: 'agent',
		config: { type: 'agent', prompt: '按当前资料生成' },
		order: 0,
		...overrides,
	};
}

function makeNodeRun(overrides = {}) {
	return {
		node_id: 'agent-1',
		name: '生成报告',
		prompt: '按当前资料生成',
		type: 'agent',
		order: 0,
		status: 'succeeded',
		attempt: 1,
		input: '合成输入',
		output: '合成输出',
		display_summary: '已完成',
		metadata: {},
		...overrides,
	};
}

function checkMap(nodeRun, node = makeNode()) {
	return new Map(
		collectNodeVerificationChecks(nodeRun.metadata, nodeRun, node).map((check) => [
			check.id,
			check,
		]),
	);
}

test('keeps execution state separate and maps each server node status', () => {
	assert.equal(nodeExecutionStatusLabel('pending'), '等待中');
	assert.equal(nodeExecutionStatusLabel('running'), '执行中');
	assert.equal(nodeExecutionStatusLabel('awaiting_input'), '等待补充信息');
	assert.equal(nodeExecutionStatusLabel('succeeded'), '成功');
	assert.equal(nodeExecutionStatusLabel('failed'), '失败');
	assert.equal(nodeExecutionStatusLabel('canceled'), '已取消');

	const failed = makeNodeRun({
		status: 'failed',
		started_at: '2026-10-26T09:00:00Z',
		finished_at: '2026-10-26T09:00:03Z',
		error_detail: {
			category: 'execution_failed',
			code: 'TASK_NODE_EXECUTION_FAILED',
			message: 'controlled failure',
			task_id: 'task-1',
			run_id: 'run-1',
			node_id: 'agent-1',
			details: {},
			occurred_at: '2026-10-26T09:00:03Z',
		},
	});
	const [attempt] = getNodeAttemptViews(failed);
	assert.equal(attempt?.status, 'failed');
	assert.equal(attempt?.started_at, failed.started_at);
	assert.equal(attempt?.finished_at, failed.finished_at);
	assert.equal(attempt?.error_detail?.code, 'TASK_NODE_EXECUTION_FAILED');
});

test('keeps AI, Schema, and Output Contract results separate', () => {
	const node = makeNode({
		output_schema: {
			type: 'object',
			properties: { title: { type: 'string' } },
			required: ['title'],
		},
		output_contract: {
			contract_id: 'task.report.v1',
			contract_version: 1,
			output_type: 'structured_json',
			validation_mode: 'json_schema',
			schema: {
				type: 'object',
				properties: { title: { type: 'string' } },
				required: ['title'],
			},
		},
	});
	const checks = checkMap(
		makeNodeRun({
			metadata: {
				task_ai_verification: { method: 'task-verifier', passed: false, message: '语义校验失败' },
				output_schema_validation: { status: 'failed', issues: ['title: required'] },
				output_contract_validation: {
					contract_id: 'task.report.v1',
					validation_mode: 'json_schema',
					status: 'failed',
					issues: ['title: required'],
				},
			},
		}),
		node,
	);
	assert.equal(checks.get('ai')?.status, 'failed');
	assert.equal(checks.get('schema')?.status, 'failed');
	assert.equal(checks.get('contract')?.status, 'failed');
});

test('does not infer verifier outcome for a failed historical Agent with no structured record', () => {
	const checks = checkMap(
		makeNodeRun({ status: 'failed', error: 'model unavailable', metadata: {} }),
	);
	assert.equal(checks.get('ai')?.status, 'unknown');
	assert.notEqual(checks.get('ai')?.status, 'failed');
});

test('reports verifier as not performed only when the service persisted zero verifier attempts', () => {
	const checks = checkMap(
		makeNodeRun({
			status: 'failed',
			metadata: {
				attempts: 0,
				verifications: [],
				authoritative_output_status: { status: 'failed', error_code: 'TASK_NODE_EXECUTION_FAILED' },
			},
		}),
	);
	assert.equal(checks.get('ai')?.status, 'not_performed');
	assert.match(checks.get('ai')?.message ?? '', /尝试数为 0/);
});

test('missing record is not reported as passed and non-Agent AI checks are not applicable', () => {
	const succeeded = checkMap(makeNodeRun({ metadata: {} }));
	assert.equal(succeeded.get('ai')?.status, 'unknown');
	assert.equal(succeeded.get('schema')?.status, 'not_applicable');
	assert.equal(succeeded.get('contract')?.status, 'not_applicable');

	for (const type of ['input', 'tool', 'python']) {
		const node = makeNode({ type, config: { type } });
		assert.equal(checkMap(makeNodeRun({ type, metadata: {} }), node).get('ai')?.status, 'not_applicable');
	}
});

test('source and Claim-Evidence results display only persisted records', () => {
	const node = makeNode({
		output_contract: {
			contract_id: 'task.report.claim-evidence.v1',
			contract_version: 1,
			output_type: 'evidence_claims',
			validation_mode: 'claim_evidence',
		},
	});
	const checks = checkMap(
		makeNodeRun({
			metadata: {
				final_citation_verification: {
					passed: true,
					verified_citations: [{ citation_id: 'C1', evidence_id: 'EV-1' }],
				},
				evidence_ledger: { passed: false, issues: ['claim CL1 lacks binding'] },
			},
		}),
		node,
	);
	assert.equal(checks.get('citations')?.status, 'passed');
	assert.match(checks.get('citations')?.details.join(' '), /C1.*EV-1/);
	assert.equal(checks.get('claim-evidence')?.status, 'failed');
	assert.match(checks.get('claim-evidence')?.details.join(' '), /CL1/);
});

test('keeps S02 draft delivery gate separate from final draft verification status', () => {
	const checks = checkMap(
		makeNodeRun({
			metadata: {
				s02_lightweight_delivery: {
					passed: true,
					status: 'draft_unverified',
					human_review_required: true,
				},
			},
		}),
	);
	assert.equal(checks.get('business:s02_lightweight_delivery')?.status, 'passed');
	assert.equal(checks.get('business:s02_lightweight_delivery')?.message, 'draft_unverified');
});

test('per-attempt checks and I/O remain associated with their own attempt', () => {
	const nodeRun = makeNodeRun({
		status: 'succeeded',
		attempt: 2,
		input: 'attempt 2 input',
		output: 'attempt 2 output',
		metadata: { task_ai_verification: { passed: true } },
		attempt_history: [
			{
				attempt: 1,
				source: 'manual',
				status: 'failed',
				input: 'attempt 1 input',
				output: 'attempt 1 output',
				metadata: { task_ai_verification: { passed: false, message: '失败一' } },
				error: 'attempt 1 failed',
			},
			{
				attempt: 2,
				source: 'manual',
				status: 'succeeded',
				input: 'attempt 2 input',
				output: 'attempt 2 output',
				metadata: { task_ai_verification: { passed: true } },
			},
		],
	});
	const attempts = getNodeAttemptViews(nodeRun);
	assert.deepEqual(attempts.map((attempt) => attempt.attempt), [1, 2]);
	const attemptOne = attempts[0];
	const attemptTwo = attempts[1];
	assert.ok(attemptOne);
	assert.ok(attemptTwo);
	assert.equal(attemptOne.input, 'attempt 1 input');
	assert.equal(attemptOne.isCurrent, false);
	assert.equal(
		collectNodeVerificationChecks(attemptOne.metadata, { ...nodeRun, ...attemptOne }, makeNode())[0]?.status,
		'failed',
	);
	assert.equal(attemptTwo.input, 'attempt 2 input');
	assert.equal(attemptTwo.isCurrent, true);
	assert.equal(attemptTwo.source, 'manual');
	assert.equal(
		collectNodeVerificationChecks(attemptTwo.metadata, { ...nodeRun, ...attemptTwo }, makeNode())[0]?.status,
		'passed',
	);
});

test('sanitizes sensitive keys and credentials from copied or displayed run content', () => {
	const safe = sanitizeTaskRunContent(JSON.stringify({
		api_key: 'sk-test-secret',
		user_id: 'other-user',
		private_reasoning: 'hidden chain',
		result: 'Bearer abc.def.ghi',
		summary: 'visible result',
	}));
	assert.doesNotMatch(safe, /sk-test-secret|other-user|hidden chain|abc\.def\.ghi/);
	assert.match(safe, /visible result/);
});
