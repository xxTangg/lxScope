import assert from 'node:assert/strict';
import test from 'node:test';

import { summarizeNodeSecurityReview } from '../src/pages/task/task-node-security-review.ts';

function makeNodeRun(overrides = {}) {
	return {
		node_id: 'node-tool',
		name: '查询数据',
		prompt: '',
		type: 'tool',
		order: 0,
		status: 'succeeded',
		attempt: 1,
		input: '',
		output: 'result',
		display_summary: '',
		metadata: {},
		...overrides,
	};
}

function preflightRecord(overrides = {}) {
	return {
		source: 'task_preflight',
		checked_at: '2026-10-26T00:00:00Z',
		step_id: 'node-tool',
		step_name: '查询数据',
		capability_key: 'data.read',
		category: 'data_query',
		tool_name: 'read_data',
		tool_available: true,
		interface_compatible: true,
		permission_status: 'allowed',
		business_data_verified: true,
		external_side_effect: false,
		status: 'available',
		message: '当前会话具有读取权限。',
		...overrides,
	};
}

test('shows Run Preflight pass without treating it as an Attempt runtime pass', () => {
	const pendingNode = makeNodeRun({
		status: 'pending',
		attempt: 0,
		security_reviews: [preflightRecord()],
	});
	const review = summarizeNodeSecurityReview(pendingNode);
	assert.equal(review.status, 'pending');
	assert.match(review.checks[0]?.source ?? '', /Task Preflight/);
	assert.equal(review.checks[0]?.status, 'passed');
	assert.equal(review.checks[1]?.status, 'not_performed');
});

test('does not infer passed from a succeeded node without persisted checks', () => {
	assert.equal(summarizeNodeSecurityReview(makeNodeRun()).status, 'missing');
});

test('shows preflight and current Attempt as separate check results', () => {
	const retriedTool = makeNodeRun({
		attempt: 2,
		security_reviews: [preflightRecord()],
	});
	const review = summarizeNodeSecurityReview(retriedTool);
	assert.equal(review.status, 'pending');
	assert.ok(review.checks.some((check) => check.status === 'passed'));
	assert.ok(review.checks.some((check) => check.status === 'missing'));
});

test('projects S01 rule-check Gateway authorization evidence for the same Attempt only', () => {
	const s01Node = makeNodeRun({
		node_id: '2cc184a3807e43bda2a8de7563b443ae',
		name: '核对立项规则',
		type: 'tool',
		attempt: 1,
		security_reviews: [preflightRecord({
			step_id: '2cc184a3807e43bda2a8de7563b443ae',
			step_name: '核对立项规则',
			capability_key: 'project.rule_check',
			category: 'knowledge_retrieval',
		})],
		metadata: {
			capability_key: 'project.rule_check',
			scenario_retrieval: {
				capability_key: 'project.rule_check',
				status: 'candidates_for_review',
				evidence_count: 2,
				source_scope: 'authorized_task_knowledge_bases',
			},
			project_rule_check: { status: 'passed' },
			task_ai_verification: { passed: true },
		},
	});
	const review = summarizeNodeSecurityReview(s01Node);
	assert.equal(review.status, 'passed');
	assert.ok(review.checks.some((check) => check.label === 'Task 知识库运行时权限范围' && check.status === 'passed'));
	assert.equal(review.attempt, 1);

	const staleCurrentAttempt = summarizeNodeSecurityReview({
		...s01Node,
		attempt: 2,
		metadata: {
			task_ai_verification: { passed: true },
			project_rule_check: { status: 'passed' },
		},
	});
	assert.equal(staleCurrentAttempt.status, 'pending');
	assert.equal(staleCurrentAttempt.attempt, 2);
	assert.ok(staleCurrentAttempt.checks.some((check) => check.status === 'missing'));
});

test('does not treat business validation or AI verification as security checks', () => {
	const review = summarizeNodeSecurityReview(makeNodeRun({
		metadata: {
			project_rule_check: { status: 'passed' },
			task_ai_verification: { passed: true },
		},
	}));
	assert.equal(review.status, 'missing');
	assert.equal(review.checks.some((check) => check.status === 'passed'), false);
});

test('reports explicit authorization denial and confirmation requirement', () => {
	const denied = summarizeNodeSecurityReview(
		makeNodeRun({ security_reviews: [preflightRecord({
			status: 'permission_denied',
			permission_status: 'denied',
			message: '访问被拒绝',
		})] }),
	);
	assert.equal(denied.status, 'failed');
	assert.match(denied.checks[0]?.message ?? '', /访问被拒绝/);

	const confirmation = summarizeNodeSecurityReview(
		makeNodeRun({ security_reviews: [preflightRecord({
			permission_status: 'requires_confirmation',
			message: '需要确认',
		})] }),
	);
	assert.equal(confirmation.status, 'confirmation');
});

test('uses current Attempt runtime record and permission error details', () => {
	const runtimePassed = summarizeNodeSecurityReview(
		makeNodeRun({
			attempt: 2,
			metadata: {
				task_security_review_checks: [{
					check_type: '运行时权限门禁',
					status: 'passed',
					source: 'PermissionEngine',
					message: '当前参数已授权。',
				}],
			},
		}),
	);
	assert.equal(runtimePassed.status, 'passed');
	assert.match(runtimePassed.checks[0]?.source ?? '', /PermissionEngine/);

	const runtimeDenied = summarizeNodeSecurityReview(
		makeNodeRun({
			status: 'failed',
			attempt: 2,
			error: 'permission denied',
			error_detail: {
				category: 'permission_denied',
				code: 'TASK_PERMISSION_DENIED',
				message: '当前 Attempt 权限被拒绝。',
				task_id: 'task-1',
				run_id: 'run-1',
				node_id: 'node-tool',
				details: {},
				occurred_at: '2026-10-26T00:00:00Z',
			},
		}),
	);
	assert.equal(runtimeDenied.status, 'failed');
	assert.ok(runtimeDenied.checks.some((check) => check.status === 'failed'));
});

test('marks InputStep permission checks not applicable and unstarted Tool checks not performed', () => {
	const inputReview = summarizeNodeSecurityReview(null, { nodeType: 'input' });
	assert.equal(inputReview.status, 'not_applicable');
	assert.equal(inputReview.checks[0]?.status, 'not_applicable');

	const toolReview = summarizeNodeSecurityReview(makeNodeRun({
		status: 'pending',
		attempt: 0,
		security_reviews: [],
	}));
	assert.equal(toolReview.status, 'not_performed');
	assert.equal(toolReview.checks[0]?.status, 'not_performed');
});

test('preserves not-applicable preflight instead of turning it into pending', () => {
	const review = summarizeNodeSecurityReview(makeNodeRun({
		attempt: 1,
		security_reviews: [preflightRecord({
			permission_status: 'not_applicable',
		})],
	}));
	assert.equal(review.status, 'not_applicable');
	assert.equal(review.checks[0]?.status, 'not_applicable');
});
