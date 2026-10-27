import type { NodeRunRecord, TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;

export interface S03DemandField {
	label: string;
	value: string;
}

export interface S03ExpertRecommendation {
	id: string;
	name: string;
	rank: number | null;
	score: number | null;
	professionalFields: string[];
	matchedFields: string[];
	reason: string | null;
}

export interface S03AnalysisItem {
	title: string;
	detail: string;
}

export interface S03RiskItem {
	tone: 'warning' | 'danger';
	title: string;
	detail: string;
}

export interface S03ExpertMatchingResult {
	demandFields: S03DemandField[];
	recommendations: S03ExpertRecommendation[];
	analysis: S03AnalysisItem[];
	risks: S03RiskItem[];
	hasDemandData: boolean;
	hasRecommendationData: boolean;
	hasAnalysisData: boolean;
	hasRiskData: boolean;
	markdown: string | null;
	artifacts: TaskArtifactRecord[];
}

const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;
const TECHNICAL_IDENTIFIER = /^(?:https?:\/\/|DEMO-SOURCE-|EV[-_:# ]?[a-f0-9-]{6,}|(?:evidence|chunk|document)[_-]?(?:id|key)?\b|[a-f0-9]{32,})/i;
const MARKDOWN_KEYS = new Set(['markdown', 'reportmarkdown', 'report', 'content', 'text', 'body', 'finaloutput', 'finalsummary', 'summary', 'output', 'result']);

function asRecord(value: unknown): DataRecord | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? value as DataRecord : null;
}

function parsePayload(value: unknown): unknown {
	if (typeof value !== 'string') return value;
	const candidate = value.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
	if (!/^(?:\{|\[)/.test(candidate)) return value;
	try {
		return JSON.parse(candidate) as unknown;
	} catch {
		return value;
	}
}

function normalizeKey(value: string): string {
	return value.toLowerCase().replace(/[\s_\-.:：/\\（）()【】[\]]/g, '');
}

function recordsIn(value: unknown, depth = 0): DataRecord[] {
	if (depth > 7 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (typeof parsed === 'string') return parsed === value ? [] : recordsIn(parsed, depth + 1);
	if (Array.isArray(parsed)) return parsed.flatMap((item) => recordsIn(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return [record, ...Object.entries(record).flatMap(([key, child]) =>
		SECRET_KEY.test(key) ? [] : recordsIn(child, depth + 1),
	)];
}

function scalarText(value: unknown): string | null {
	if (typeof value === 'string' || typeof value === 'number') return String(value).trim() || null;
	if (Array.isArray(value)) return value.map(scalarText).filter((item): item is string => Boolean(item)).join('、') || null;
	const record = asRecord(value);
	if (!record) return null;
	for (const key of ['value', 'text', 'name', 'label', 'title', 'summary', 'content']) {
		const found = scalarText(record[key]);
		if (found) return found;
	}
	return null;
}

function compactText(value: unknown, maxLength = 180): string | null {
	const text = scalarText(value)?.replace(/[`*_>#]/g, '').replace(/\s+/g, ' ').trim();
	if (!text || TECHNICAL_IDENTIFIER.test(text)) return null;
	if ([...text].length <= maxLength) return text;
	return `${[...text].slice(0, maxLength - 1).join('').trimEnd()}…`;
}

function directValue(record: DataRecord, aliases: string[]): unknown {
	const wanted = new Set(aliases.map(normalizeKey));
	for (const [key, value] of Object.entries(record)) {
		if (!SECRET_KEY.test(key) && wanted.has(normalizeKey(key)) && value !== null && value !== undefined && value !== '') return value;
	}
	return undefined;
}

function valueFrom(records: DataRecord[], aliases: string[]): unknown {
	for (const record of records) {
		const value = directValue(record, aliases);
		if (value !== undefined) return value;
	}
	return undefined;
}

function stringList(value: unknown): string[] {
	const parsed = parsePayload(value);
	if (Array.isArray(parsed)) {
		return parsed.map((item) => compactText(item, 100)).filter((item): item is string => Boolean(item));
	}
	if (typeof parsed !== 'string') return [];
	const parts = parsed.split(/[、,，;；\n]/).map((item) => compactText(item, 100)).filter((item): item is string => Boolean(item));
	return parts.length ? parts : (compactText(parsed, 120) ? [compactText(parsed, 120)!] : []);
}

function nodePayloads(node: NodeRunRecord | undefined): unknown[] {
	if (!node) return [];
	const values = [node.result, node.output, node.display_summary]
		.filter((value) => value !== undefined && value !== null && value !== '');
	if (values.length) return values;
	const lastAttempt = node.attempt_history?.at(-1);
	return lastAttempt ? [lastAttempt.result, lastAttempt.output].filter((value) => value !== undefined && value !== null && value !== '') : [];
}

function matchingNodes(run: TaskRunRecord, capabilityKey: string, fallback: RegExp): NodeRunRecord[] {
	const nodesById = new Map(run.nodes.map((node) => [node.id, node]));
	return run.node_runs.filter((node) => {
		if (node.status !== 'succeeded') return false;
		const definition = nodesById.get(node.node_id);
		return definition?.capability_key === capabilityKey || fallback.test(`${node.name} ${node.prompt} ${definition?.name ?? ''} ${definition?.prompt ?? ''}`);
	}).sort((left, right) => left.order - right.order);
}

function arraysFrom(payloads: unknown[], aliases: string[]): DataRecord[] {
	const wanted = new Set(aliases.map(normalizeKey));
	const found: DataRecord[] = [];
	for (const payload of payloads) {
		for (const record of recordsIn(payload)) {
			for (const [key, value] of Object.entries(record)) {
				if (!wanted.has(normalizeKey(key))) continue;
				const parsed = parsePayload(value);
				if (Array.isArray(parsed)) found.push(...parsed.map(asRecord).filter((item): item is DataRecord => Boolean(item)));
			}
		}
	}
	return found;
}

function arrayRecords(value: unknown): DataRecord[] {
	const parsed = parsePayload(value);
	return Array.isArray(parsed) ? parsed.map(asRecord).filter((item): item is DataRecord => Boolean(item)) : [];
}

function idOf(record: DataRecord): string | null {
	return compactText(directValue(record, ['expert_id', 'candidate_id', 'expert_code', 'id', '专家编号', '候选人编号']), 120);
}

function statusOf(record: DataRecord | undefined): 'clear' | 'conflict' | 'unknown' | null {
	if (!record) return null;
	const raw = scalarText(directValue(record, ['declared_conflict_status', 'conflict_status', 'avoidance_status', '回避状态', '冲突状态']));
	if (!raw) return null;
	const normalized = normalizeKey(raw);
	if (/^(?:clear|cleared|noconflict|无冲突|未发现冲突|通过声明)$/.test(normalized)) return 'clear';
	if (/^(?:conflict|conflicted|冲突|存在冲突|存在回避关系)$/.test(normalized)) return 'conflict';
	return 'unknown';
}

function candidateEligible(status: 'clear' | 'conflict' | 'unknown' | null, records: DataRecord[]): boolean {
	if (status === 'conflict' || status === 'unknown') return false;
	if (records.some((record) => directValue(record, ['eligible_for_preliminary_ranking']) === false)) return false;
	return status === 'clear' || records.some((record) => directValue(record, ['eligible_for_preliminary_ranking']) === true);
}

function candidateName(records: DataRecord[], id: string): string {
	const name = records.map((record) => compactText(directValue(record, ['name', 'expert_name', 'candidate_name', '姓名', '专家姓名']), 100)).find(Boolean);
	if (name && name !== id && !TECHNICAL_IDENTIFIER.test(name)) return name;
	return `候选人 ${id}`;
}

function candidateTextList(records: DataRecord[], aliases: string[]): string[] {
	for (const record of records) {
		const value = directValue(record, aliases);
		if (value === undefined) continue;
		const parsed = parsePayload(value);
		if (Array.isArray(parsed)) return parsed.map((item) => compactText(item, 120)).filter((item): item is string => Boolean(item));
		const text = compactText(value, 120);
		return text ? [text] : [];
	}
	return [];
}

function finiteNumber(value: unknown): number | null {
	if (typeof value !== 'number' && typeof value !== 'string') return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function markdownTexts(value: unknown, depth = 0): string[] {
	if (depth > 6 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (typeof parsed === 'string') {
		if (parsed !== value) return markdownTexts(parsed, depth + 1);
		const text = parsed.trim().replace(/^```(?:markdown|md)\s*/i, '').replace(/\s*```$/, '').trim();
		return text.length > 30 && !/^(?:\{|\[)/.test(text) ? [text] : [];
	}
	if (Array.isArray(parsed)) return parsed.flatMap((item) => markdownTexts(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return Object.entries(record).flatMap(([key, child]) =>
		SECRET_KEY.test(key) || !MARKDOWN_KEYS.has(normalizeKey(key)) ? [] : markdownTexts(child, depth + 1),
	);
}

function markdownReport(run: TaskRunRecord): string | null {
	const reportNodes = run.node_runs.filter((node) =>
		node.status === 'succeeded' && node.type === 'agent' && /专家|候选|匹配|推荐|shortlist|expert|matching/i.test(`${node.name} ${node.prompt}`),
	).sort((left, right) => right.order - left.order);
	const payloads = [
		...run.artifacts.filter((artifact) => artifact.format === 'markdown').map((artifact) => artifact.preview_text),
		run.final_output,
		run.final_summary,
		...reportNodes.flatMap((node) => [node.result, node.output, node.display_summary]),
	];
	const candidates = [...new Set(payloads.flatMap(markdownTexts).map((text) => text.trim()))];
	const score = (text: string) =>
		(Number(/^\s*#{1,6}\s/m.test(text)) * 4) +
		(Number(/^\s*\|.+\|\s*$/m.test(text)) * 2) +
		(Number(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/m.test(text))) +
		Math.min(text.length / 5000, 2);
	return candidates.sort((left, right) => score(right) - score(left) || right.length - left.length)[0] ?? null;
}

function projectDemandFields(run: TaskRunRecord, markdown: string | null): { fields: S03DemandField[]; inputCandidates: DataRecord[]; requiredFields: string[] } {
	const inputNodes = run.node_runs.filter((node) => node.type === 'input' || /读取项目要求|项目画像|collect.*requirement|extract.*profile/i.test(`${node.name} ${node.prompt}`));
	const profileNodes = matchingNodes(run, 'expert.project_profile', /形成项目画像|extract_project_profile/i);
	const inputPayloads = [run.input, ...inputNodes.flatMap((node) => [node.input, node.output, node.result])];
	const profilePayloads = profileNodes.flatMap(nodePayloads);
	const inputRecords = inputPayloads.flatMap(recordsIn);
	const profileRecords = profilePayloads.flatMap(recordsIn);
	const allDemandRecords = [...inputRecords, ...profileRecords];
	const rawCandidateData = valueFrom(inputRecords, ['expert_records_json', 'candidate_records', '专家候选记录']);
	const inputCandidates = arrayRecords(rawCandidateData);
	const requiredFields = stringList(valueFrom(inputRecords, ['professional_fields', 'required_fields', 'expertise_areas', '所需专业领域', '专业方向']));
	const fields: S03DemandField[] = [];
	const add = (label: string, value: unknown, list = false) => {
		const formatted = list ? stringList(value).join('、') : compactText(value, 180);
		if (formatted && !fields.some((field) => field.label === label)) fields.push({ label, value: formatted });
	};
	add('项目名称', valueFrom(allDemandRecords, ['project_name', 'project_title', '项目名称']));
	add('项目类型 / 业务领域', valueFrom(profileRecords, ['project_type', 'industry', 'business_domain', 'project_domain', '项目类型', '所属领域', '业务领域']));
	add('所需专家方向', requiredFields, true);
	add('所需专家人数', valueFrom(inputRecords, ['required_expert_count', 'expert_count', '所需专家人数', '候选人数']));
	add('项目背景与匹配要求', valueFrom(allDemandRecords, ['project_brief', 'project_background', 'matching_requirements', 'professional_requirements', 'qualification_requirements', 'experience_requirements', '项目背景', '项目要求', '专业要求', '任职要求']));
	if (fields.length < 5) add('关键专业要求', valueFrom(profileRecords, ['required_experience', 'required_qualifications', 'key_requirements', '关键要求', '任职资格', '资质要求']));
	if (!fields.length && markdown) {
		const title = markdown.match(/^\s*#{1,3}\s*(?:项目需求画像|项目要求|项目概况)\s*\n([\s\S]*?)(?=\n#{1,3}\s|$)/im)?.[1];
		if (title) add('项目需求摘要', title);
	}
	return { fields: fields.slice(0, 5), inputCandidates, requiredFields };
}

function buildResult(run: TaskRunRecord): S03ExpertMatchingResult {
	const markdown = markdownReport(run);
	const demand = projectDemandFields(run, markdown);
	const lookupNodes = matchingNodes(run, 'expert.user_data_lookup', /读取用户提供的结构化候选|lookup_expert_candidates/i);
	const conflictNodes = matchingNodes(run, 'expert.user_conflict_check', /回避声明|expert.*conflict|check_expert_conflicts/i);
	const rankNodes = matchingNodes(run, 'expert.user_rank', /专业字段重合度|专家候选排序|rank_expert_candidates/i);
	const explanationNodes = matchingNodes(run, '', /生成匹配理由|explain_matching_reasons/i);
	const lookupPayloads = lookupNodes.flatMap(nodePayloads);
	const conflictPayloads = conflictNodes.flatMap(nodePayloads);
	const rankPayloads = rankNodes.flatMap(nodePayloads);
	const explanationPayloads = explanationNodes.flatMap(nodePayloads);
	const lookupCandidates = arraysFrom(lookupPayloads, ['candidates', 'expert_candidates', '候选专家']);
	const conflictCandidates = arraysFrom(conflictPayloads, ['candidates', 'expert_candidates', '候选专家']);
	const rankedCandidates = arraysFrom(rankPayloads, ['candidates', 'ranked_candidates', 'experts', 'expert_candidates', '候选专家', '推荐专家']);
	const inputCandidateMap = new Map(demand.inputCandidates.map((candidate) => [idOf(candidate), candidate]).filter((entry): entry is [string, DataRecord] => Boolean(entry[0])));
	const lookupCandidateMap = new Map(lookupCandidates.map((candidate) => [idOf(candidate), candidate]).filter((entry): entry is [string, DataRecord] => Boolean(entry[0])));
	const conflictCandidateMap = new Map(conflictCandidates.map((candidate) => [idOf(candidate), candidate]).filter((entry): entry is [string, DataRecord] => Boolean(entry[0])));
	const recommendations: S03ExpertRecommendation[] = [];
	const seen = new Set<string>();
	for (let index = 0; index < rankedCandidates.length; index += 1) {
		const ranked = rankedCandidates[index];
		const id = idOf(ranked);
		if (!id || seen.has(id)) continue;
		const sources = [ranked, conflictCandidateMap.get(id), lookupCandidateMap.get(id), inputCandidateMap.get(id)].filter((item): item is DataRecord => Boolean(item));
		const status = sources.map(statusOf).find((item) => item !== null) ?? null;
		if (!candidateEligible(status, sources)) continue;
		seen.add(id);
		const scoreComponents = recordsIn(directValue(ranked, ['score_components', 'score_breakdown', '评分构成']));
		const matchedFields = candidateTextList(sources, ['matched_fields', 'matched_professional_fields', 'professional_field_matches', '匹配专业领域', '重合专业方向']);
		const derivedMatchedFields = matchedFields.length ? matchedFields : candidateTextList(scoreComponents, ['matched', 'matched_fields', '命中领域']);
		const reasonValues = candidateTextList(sources, ['reasons', 'reason', 'match_reason', 'recommendation_reason', '匹配理由', '推荐理由']);
		const reason = reasonValues[0] ?? (derivedMatchedFields.length ? `专业方向重合：${derivedMatchedFields.join('、')}` : null);
		const professionalFields = candidateTextList(sources, ['professional_fields', 'expertise_areas', '专业领域', '研究方向']);
		recommendations.push({
			id,
			name: candidateName(sources, id),
			rank: finiteNumber(directValue(ranked, ['rank', 'ranking', '推荐序号', '排名'])) ?? index + 1,
			score: finiteNumber(directValue(ranked, ['score', 'match_score', '初排分值', '匹配分值'])),
			professionalFields: professionalFields.slice(0, 4),
			matchedFields: derivedMatchedFields.slice(0, 4),
			reason,
		});
	}
	recommendations.sort((left, right) => (left.rank ?? Number.MAX_SAFE_INTEGER) - (right.rank ?? Number.MAX_SAFE_INTEGER));

	const analysisRecords = explanationPayloads.flatMap(recordsIn);
	const analysis: S03AnalysisItem[] = [];
	const addAnalysis = (title: string, value: unknown) => {
		const detail = compactText(value, 150);
		if (detail && !analysis.some((item) => item.detail === detail)) analysis.push({ title, detail });
	};
	const matched = [...new Set(recommendations.flatMap((candidate) => candidate.matchedFields))];
	if (matched.length) addAnalysis('专业能力匹配', `候选人专业方向与需求重合：${matched.join('、')}`);
	for (const record of analysisRecords) {
		addAnalysis('匹配分析', directValue(record, ['matching_analysis', 'professional_match', 'match_analysis', 'analysis', '专业匹配分析', '匹配分析']));
		addAnalysis('项目经验参考', directValue(record, ['project_experience', 'experience_evidence', 'research_experience', '相关项目经验', '研究领域参考', '相关经验']));
		addAnalysis('推荐理由', directValue(record, ['recommendation_reason', 'match_reason', 'reason', '推荐理由', '匹配理由']));
		if (analysis.length >= 3) break;
	}
	for (const candidate of recommendations) {
		if (analysis.length >= 3) break;
		if (candidate.reason) addAnalysis(`候选人匹配依据 · ${candidate.name}`, candidate.reason);
	}
	const analysisResult = analysis.slice(0, 3);

	const riskCandidates = new Map<string, DataRecord>();
	for (const candidate of [...demand.inputCandidates, ...lookupCandidates, ...conflictCandidates]) {
		const id = idOf(candidate);
		if (id) riskCandidates.set(id, { ...(riskCandidates.get(id) ?? {}), ...candidate });
	}
	const risks: S03RiskItem[] = [];
	let clearDeclarations = 0;
	for (const [id, record] of riskCandidates) {
		const status = statusOf(record);
		const name = candidateName([record], id);
		if (status === 'conflict') risks.push({ tone: 'danger', title: `${name}：用户声明存在回避冲突`, detail: '不纳入初步推荐；请业务人员按正式规则复核。' });
		else if (status === 'unknown') risks.push({ tone: 'warning', title: `${name}：回避状态未知`, detail: '该候选不纳入初步推荐，仍需人工核查。' });
		else if (status === null) risks.push({ tone: 'warning', title: `${name}：未提供回避状态`, detail: '本次结果无法确认该候选是否可参与初排，需人工核查。' });
		else if (status === 'clear') clearDeclarations += 1;
	}
	if (clearDeclarations) risks.push({ tone: 'warning', title: '回避声明尚未正式核验', detail: `${clearDeclarations} 位候选人的输入声明为 clear；这不代表正式利益冲突检查通过。` });
	const reviewRecords = run.node_runs.filter((node) => node.type === 'input').flatMap((node) => [node.output, node.result]).flatMap(recordsIn);
	const feedback = compactText(valueFrom(reviewRecords, ['review_feedback', 'review_opinion', '复核意见']), 140);
	if (feedback && !/^(?:暂无补充|无|无补充|无需补充|暂无)$/i.test(feedback)) risks.push({ tone: 'warning', title: '人工复核意见', detail: feedback });
	if (!risks.length) risks.push({ tone: 'warning', title: '回避信息待人工复核', detail: '本次结果未提供可识别的冲突状态；不能据此判断不存在风险。' });
	const riskResult = risks.slice(0, 4);

	const demandData = demand.fields.length > 0;
	const recommendationData = recommendations.length > 0;
	const analysisData = analysisResult.length > 0;
	const riskData = [...riskCandidates.values()].some((candidate) => statusOf(candidate) !== null) || Boolean(feedback);
	return {
		demandFields: demand.fields,
		recommendations: recommendations.slice(0, 3),
		analysis: analysisResult,
		risks: riskResult,
		hasDemandData: demandData,
		hasRecommendationData: recommendationData,
		hasAnalysisData: analysisData,
		hasRiskData: riskData,
		markdown,
		artifacts: run.artifacts,
	};
}

export function adaptS03ExpertMatchingRun(run: TaskRunRecord): S03ExpertMatchingResult {
	return buildResult(run);
}
