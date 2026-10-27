import type { TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;
type S06Module = 'overview' | 'categories' | 'core' | 'followups';

export interface S06OpinionStat {
	label: string;
	value: string;
}

export interface S06CategorySummary {
	name: string;
	count: number | null;
	example: string | null;
}

export interface S06CoreOpinion {
	title: string;
	text: string;
}

export interface S06FollowUp {
	title: string;
	detail: string;
	status: '待确认' | '待补充' | '存在分歧' | '已核实' | null;
}

export interface S06OpinionResult {
	projectName: string | null;
	stats: S06OpinionStat[];
	contextNote: string | null;
	categories: S06CategorySummary[];
	coreOpinions: S06CoreOpinion[];
	followUps: S06FollowUp[];
	moduleData: Record<S06Module, boolean>;
	markdown: string | null;
	markdownSupplement: string | null;
	artifacts: TaskArtifactRecord[];
	hasStructuredContent: boolean;
	hasFullResult: boolean;
}

interface MarkdownSection {
	title: string;
	body: string;
}

const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;

function asRecord(value: unknown): DataRecord | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? value as DataRecord : null;
}

function parsePayload(value: unknown): unknown {
	if (typeof value !== 'string') return value;
	const candidate = value.trim().replace(/^```(?:json|markdown|md)?\s*/i, '').replace(/\s*```$/, '');
	if (!/^(?:\{|\[)/.test(candidate)) return value;
	try {
		return JSON.parse(candidate) as unknown;
	} catch {
		return value;
	}
}

function normalizeKey(value: string): string {
	return value.toLowerCase().replace(/[\s_\-.:：/\\（）()【】]/g, '');
}

function recordsIn(value: unknown, depth = 0): DataRecord[] {
	if (depth > 8 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (parsed !== value) return recordsIn(parsed, depth + 1);
	if (Array.isArray(parsed)) return parsed.flatMap((item) => recordsIn(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return [record, ...Object.entries(record).flatMap(([key, child]) => SECRET_KEY.test(key) ? [] : recordsIn(child, depth + 1))];
}

function recordsFromPayloads(values: unknown[]): DataRecord[] {
	return values.flatMap((value) => recordsIn(value));
}

function scalarText(value: unknown): string | null {
	if (typeof value === 'string' || typeof value === 'number') return String(value).trim() || null;
	if (Array.isArray(value)) return value.map(scalarText).filter((item): item is string => Boolean(item)).join('、') || null;
	const record = asRecord(value);
	if (!record) return null;
	for (const key of ['text', 'content', 'summary', 'title', 'label', 'name', 'value', 'detail', 'message']) {
		const text = scalarText(record[key]);
		if (text) return text;
	}
	return null;
}

function cleanText(value: unknown): string | null {
	const text = scalarText(value)
		?.replace(/<!--.*?-->/gs, ' ')
		.replace(/\[([^\]]*)\]\((?:https?:\/\/|\/)[^)]+\)/gi, '$1')
		.replace(/[`*_>#]/g, '')
		.replace(/^\s*(?:[-*+•]|\d+[.)、])\s*/, '')
		.replace(/\s+/g, ' ')
		.trim();
	if (!text || /^(?:null|undefined|n\/a|unknown|暂无|未提供|—|-)$/i.test(text)) return null;
	return text;
}

function directValue(records: DataRecord[], aliases: string[]): unknown {
	const wanted = new Set(aliases.map(normalizeKey));
	for (const record of records) {
		for (const [key, value] of Object.entries(record)) {
			if (!SECRET_KEY.test(key) && wanted.has(normalizeKey(key)) && value !== null && value !== undefined && value !== '') return value;
		}
	}
	return undefined;
}

function directText(records: DataRecord[], aliases: string[]): string | null {
	return cleanText(directValue(records, aliases));
}

function directNumber(records: DataRecord[], aliases: string[]): number | null {
	const value = directValue(records, aliases);
	if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
	if (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value.trim())) return Number(value);
	return null;
}

function directBoolean(records: DataRecord[], aliases: string[]): boolean | null {
	const value = directValue(records, aliases);
	if (typeof value === 'boolean') return value;
	if (typeof value === 'string' && /^(true|false)$/i.test(value.trim())) return value.trim().toLowerCase() === 'true';
	return null;
}

function directArray(records: DataRecord[], aliases: string[]): unknown[] | null {
	const value = directValue(records, aliases);
	const parsed = parsePayload(value);
	return Array.isArray(parsed) ? parsed : null;
}

function normalizedRecords(value: unknown): DataRecord[] {
	const parsed = parsePayload(value);
	if (!Array.isArray(parsed)) return [];
	return parsed.map(asRecord).filter((item): item is DataRecord => Boolean(item));
}

function runNodeLabel(run: TaskRunRecord, nodeRun: TaskRunRecord['node_runs'][number]): string {
	const definition = run.nodes.find((node) => node.id === nodeRun.node_id);
	return [nodeRun.name, nodeRun.node_id, definition?.name, definition?.capability_key]
		.filter((item): item is string => Boolean(item))
		.join(' ')
		.toLowerCase();
}

function successfulNodes(run: TaskRunRecord, matcher: RegExp): TaskRunRecord['node_runs'] {
	return run.node_runs.filter((node) => node.status === 'succeeded' && matcher.test(runNodeLabel(run, node)));
}

function nodePayloads(nodes: TaskRunRecord['node_runs']): unknown[] {
	return nodes.flatMap((node) => {
		const latestAttempt = node.attempt_history?.[node.attempt_history.length - 1];
		return [node.result, node.output, latestAttempt?.result, latestAttempt?.output].filter((value) => value !== null && value !== undefined && value !== '');
	});
}

function firstArray(records: DataRecord[], aliases: string[]): unknown[] | null {
	const value = directArray(records, aliases);
	return value?.length ? value : null;
}

function firstCount(records: DataRecord[], aliases: string[]): number | null {
	return directNumber(records, aliases);
}

function markdownText(value: unknown, depth = 0): string | null {
	if (depth > 6 || value === null || value === undefined) return null;
	const parsed = parsePayload(value);
	if (parsed !== value) return markdownText(parsed, depth + 1);
	if (typeof parsed === 'string') return parsed.trim() || null;
	if (Array.isArray(parsed)) {
		for (const item of parsed) {
			const text = markdownText(item, depth + 1);
			if (text) return text;
		}
		return null;
	}
	const record = asRecord(parsed);
	if (!record) return null;
	const preferred = ['markdown', 'report', 'content', 'text', 'final_output', 'final_summary', 'summary', 'body', 'output', 'result'];
	for (const key of preferred) {
		if (key in record) {
			const text = markdownText(record[key], depth + 1);
			if (text) return text;
		}
	}
	return null;
}

function isJsonLike(text: string): boolean {
	const candidate = text.trimStart();
	return (candidate.startsWith('{') || candidate.startsWith('[')) && parsePayload(text) !== text;
}

function selectMarkdown(run: TaskRunRecord, exportNodes: TaskRunRecord['node_runs'], summaryNodes: TaskRunRecord['node_runs']): string | null {
	const candidates = [
		run.final_output,
		run.final_summary,
		...nodePayloads(exportNodes),
		...nodePayloads(summaryNodes),
		...nodePayloads([...run.node_runs].reverse()),
	];
	for (const candidate of candidates) {
		const text = markdownText(candidate);
		if (text && !isJsonLike(text)) return text;
	}
	return null;
}

function markdownSections(markdown: string): MarkdownSection[] {
	const matches = [...markdown.matchAll(/^#{1,6}\s+(.+?)\s*$/gm)];
	if (!matches.length) return [];
	return matches.map((match, index) => {
		const start = (match.index ?? 0) + match[0].length;
		const end = matches[index + 1]?.index ?? markdown.length;
		return { title: match[1].trim(), body: markdown.slice(start, end).trim() };
	});
}

function moduleForHeading(title: string): S06Module | null {
	if (/概览|项目(?:名称|信息)|意见来源|整理范围/.test(title)) return 'overview';
	if (/分类|类别|分布|统计/.test(title)) return 'categories';
	if (/核心意见|主要意见|共性关注|争议|分歧|意见摘要/.test(title)) return 'core';
	if (/待确认|待复核|待补充|处理建议|后续工作|人工复核/.test(title)) return 'followups';
	return null;
}

function markdownBullets(text: string, limit: number): string[] {
	return text.split('\n')
		.filter((line) => /^\s*(?:[-*+]\s+|\d+[.)、]\s+)/.test(line))
		.map((line) => cleanText(line))
		.filter((line): line is string => Boolean(line))
		.slice(0, limit);
}

function arraySummaryItems(records: DataRecord[], aliases: string[], limit: number): S06CoreOpinion[] {
	const items = firstArray(records, aliases);
	if (!items) return [];
	return items.flatMap((item, index) => {
		const record = asRecord(item);
		if (!record) {
			const text = cleanText(item);
			return text ? [{ title: `意见要点 ${index + 1}`, text }] : [];
		}
		const text = directText([record], ['summary', 'text', 'content', 'detail', 'opinion', 'point', 'description', '意见内容', '摘要']);
		if (!text) return [];
		const title = directText([record], ['title', 'name', 'topic', 'category', '类别', '主题']) ?? `意见要点 ${index + 1}`;
		return [{ title, text }];
	}).slice(0, limit);
}

function categorySummaries(records: DataRecord[]): S06CategorySummary[] {
	const itemArray = firstArray(records, ['items', 'classified_items', 'classification_results', 'categorized_opinions', '意见条目', '分类结果']);
	const classifiedItems = normalizedRecords(itemArray);
	if (classifiedItems.length) {
		const counts = new Map<string, { count: number; example: string | null }>();
		for (const item of classifiedItems) {
			const name = directText([item], ['category', 'category_name', 'classification', '类别', '分类', '意见类别']);
			if (!name) continue;
			const current = counts.get(name) ?? { count: 0, example: directText([item], ['text', 'opinion', 'content', '意见内容']) };
			current.count += 1;
			counts.set(name, current);
		}
		if (counts.size) return [...counts.entries()].map(([name, value]) => ({ name, count: value.count, example: value.example }));
	}

	const summaryArray = firstArray(records, ['category_counts', 'category_summary', 'category_statistics', 'classification_summary', '分类统计', '分类汇总']);
	if (summaryArray) {
		const summaries = summaryArray.flatMap((item) => {
			const record = asRecord(item);
			if (!record) return [];
			const name = directText([record], ['category', 'category_name', 'name', 'label', '类别', '分类']);
			if (!name) return [];
			const count = directNumber([record], ['count', 'opinion_count', 'total_count', '数量', '意见数量']);
			return [{ name, count, example: null }];
		});
		if (summaries.length) return summaries;
	}

	const countMap = asRecord(directValue(records, ['category_counts', 'counts_by_category', '分类数量']));
	if (countMap) {
		return Object.entries(countMap).flatMap(([name, value]) => {
			const count = typeof value === 'number' ? value : typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : null;
			return count === null ? [] : [{ name, count, example: null }];
		});
	}

	const names = firstArray(records, ['categories', 'category_names', 'classification_dimensions', '分类名称', '分类类别']);
	return names?.flatMap((item) => {
		const name = cleanText(item);
		return name ? [{ name, count: null, example: null }] : [];
	}) ?? [];
}

function pendingStatus(value: unknown): S06FollowUp['status'] {
	const text = scalarText(value) ?? '';
	if (/待补充|资料缺失|需补充/i.test(text)) return '待补充';
	if (/存在分歧|不同意见|争议/i.test(text)) return '存在分歧';
	if (/已核实|已确认/i.test(text)) return '已核实';
	if (/待确认|待复核|未分类|uncertain|pending/i.test(text)) return '待确认';
	return null;
}

function explicitFollowUps(records: DataRecord[]): S06FollowUp[] {
	const values = firstArray(records, ['unresolved_items', 'items_for_review', 'pending_items', 'issues_to_confirm', 'follow_up_items', '待复核事项', '待确认事项', '人工复核清单']);
	if (!values) return [];
	return values.flatMap((value) => {
		const record = asRecord(value);
		if (!record) {
			const detail = cleanText(value);
			return detail ? [{ title: '待确认事项', detail, status: '待确认' as const }] : [];
		}
		const detail = directText([record], ['suggestion', 'recommendation', 'action', 'review_reason', 'reason', 'detail', 'text', 'content', '说明', '处理建议', '意见内容']);
		if (!detail) return [];
		const title = directText([record], ['title', 'name', 'issue', 'question', 'category', '事项', '问题']) ?? '待确认事项';
		const statusText = directText([record], ['status', 'state', '处理状态', '复核状态']);
		const verifiedEvidence = directText([record], ['verification_record', 'verified_by', 'verified_at', 'verification_basis', '核实记录', '核实人', '核实依据']);
		const status = /已核实|已确认/.test(statusText ?? '') && !verifiedEvidence ? null : pendingStatus(statusText);
		return [{ title, detail, status }];
	});
}

function followUpsFromClassifiedItems(items: DataRecord[]): S06FollowUp[] {
	return items.flatMap((item) => {
		const category = directText([item], ['category', 'category_name', 'classification', '类别', '分类']) ?? '';
		const matches = directArray([item], ['category_matches', 'matched_categories', '匹配类别']) ?? [];
		const needsReview = directBoolean([item], ['needs_review', 'requires_human_review', '待复核']) === true;
		const unresolvedCategory = /待复核|未分类|待确认|无法分类/.test(category);
		if (!needsReview && !unresolvedCategory && matches.length < 2) return [];
		const text = directText([item], ['text', 'opinion', 'content', '意见内容']) ?? '分类结果需要人工确认。';
		const explicitReason = directText([item], ['review_reason', 'reason', 'suggestion', '处理建议']);
		const detail = explicitReason ?? (matches.length > 1
			? `该意见匹配多个分类规则，需人工确认分类。${text}`
			: unresolvedCategory
				? `该意见当前归为“${category}”，请按实际意见内容确认分类。${text}`
				: text);
		return [{ title: category && unresolvedCategory ? `${category}意见` : '意见分类待确认', detail, status: '待确认' as const }];
	});
}

function extractMarkdownCore(sections: MarkdownSection[]): S06CoreOpinion[] {
	const sectionsForCore = sections.filter((section) => moduleForHeading(section.title) === 'core');
	return sectionsForCore.flatMap((section) => markdownBullets(section.body, 3).map((text) => ({ title: section.title, text }))).slice(0, 3);
}

function markdownSupplements(markdown: string | null, moduleData: Record<S06Module, boolean>): string | null {
	if (!markdown) return null;
	const sections = markdownSections(markdown);
	if (!sections.length) return Object.values(moduleData).every(Boolean) ? null : markdown;
	const supplement = sections
		.filter((section) => {
			const module = moduleForHeading(section.title);
			return !module || !moduleData[module];
		})
		.map((section) => `## ${section.title}\n\n${section.body}`)
		.filter((section) => section.trim().length > 0)
		.join('\n\n');
	return supplement || null;
}

function buildResult(run: TaskRunRecord): S06OpinionResult {
	const importNodes = successfulNodes(run, /opinion\.text_import|导入.*意见|意见文本.*导入|collect_recognized_opinions/i);
	const classifyNodes = successfulNodes(run, /opinion\.classify|意见分类|classify_opinion_points/i);
	const summaryNodes = successfulNodes(run, /汇总分类统计|summarize_opinion_dimensions/i);
	const reviewNodes = successfulNodes(run, /人工复核意见汇总|review_opinion_summary/i);
	const exportNodes = successfulNodes(run, /生成意见汇总表|export_opinion_summary/i);
	const importRecords = recordsFromPayloads(nodePayloads(importNodes));
	const classifyRecords = recordsFromPayloads(nodePayloads(classifyNodes));
	const summaryRecords = recordsFromPayloads(nodePayloads(summaryNodes));
	const reviewRecords = recordsFromPayloads(nodePayloads(reviewNodes));
	const reportRecords = recordsFromPayloads([run.final_output, run.final_summary]);
	const allBusinessRecords = [...summaryRecords, ...classifyRecords, ...reviewRecords, ...reportRecords];
	const inputNodes = run.node_runs.filter((node) => node.type === 'input');
	const inputRecords = recordsFromPayloads([run.input, ...inputNodes.map((node) => node.input)]);
	const markdown = selectMarkdown(run, exportNodes, summaryNodes);
	const sections = markdown ? markdownSections(markdown) : [];

	const importedOpinions = firstArray(importRecords, ['opinions', 'opinion_records', '意见记录'])
		?? firstArray(inputRecords, ['opinion_records_json', 'opinion_records', 'opinions', '意见记录']);
	const originalCount = firstCount(importRecords, ['original_count', 'total_count', 'opinion_count', '意见总数'])
		?? firstCount(classifyRecords, ['original_count', 'total_count', 'opinion_count', '意见总数'])
		?? (importedOpinions ? importedOpinions.length : null);
	const classifiedItemsValue = firstArray(classifyRecords, ['items', 'classified_items', 'classification_results', 'categorized_opinions', '意见条目', '分类结果']);
	const classifiedItems = normalizedRecords(classifiedItemsValue);
	const uniqueCount = firstCount(classifyRecords, ['unique_count', 'deduplicated_count', 'unique_opinion_count', '去重后数量']) ?? (classifiedItemsValue ? classifiedItemsValue.length : null);
	const duplicateCount = firstCount(classifyRecords, ['duplicate_count', 'duplicates_count', '重复数量'])
		?? (originalCount !== null && uniqueCount !== null ? Math.max(0, originalCount - uniqueCount) : null);
	const categorySummariesFromRun = categorySummaries(classifyRecords);
	const categories = [...categorySummariesFromRun]
		.sort((left, right) => left.count !== null && right.count !== null ? right.count - left.count : 0)
		.slice(0, 4);
	const unresolvedItems = followUpsFromClassifiedItems(classifiedItems);
	const explicitItems = explicitFollowUps([...summaryRecords, ...reviewRecords, ...reportRecords]);
	const requiresHumanReview = directBoolean(classifyRecords, ['requires_human_review', 'human_review_required', '需要人工复核']);
	const followUps = [...explicitItems, ...unresolvedItems].filter((item, index, list) =>
		list.findIndex((candidate) => candidate.title === item.title && candidate.detail === item.detail) === index,
	).slice(0, 4);
	if (followUps.length === 0 && requiresHumanReview === true) {
		followUps.push({
			title: '分类结果需人工复核',
			detail: '本次运行标记需要人工复核；具体意见请查看完整分类记录。',
			status: '待确认',
		});
	}
	const pendingCount = firstCount([...summaryRecords, ...classifyRecords], ['pending_count', 'review_count', 'unclassified_count', '待确认数量', '待复核数量'])
		?? (unresolvedItems.length ? unresolvedItems.length : null);

	const summaryItems = arraySummaryItems(allBusinessRecords, [
		'core_opinions', 'key_opinions', 'opinion_summary_items', 'core_opinion_summary', 'major_opinions', 'key_points', '主要意见', '核心意见', '意见摘要', '共性关注点',
	], 3);
	const summaryText = directText(summaryRecords, ['core_opinion_summary', 'opinion_summary', '主要意见摘要', '核心意见摘要', '共性意见摘要']);
	const markdownCore = extractMarkdownCore(sections);
	const coreOpinions = summaryItems.length ? summaryItems
		: summaryText ? [{ title: '核心意见摘要', text: summaryText }]
			: markdownCore;

	const projectName = directText(importRecords, ['project_name', 'projectName', '项目名称'])
		?? directText(inputRecords, ['project_name', 'projectName', '项目名称']);
	const sourceKind = directText(importRecords, ['source_kind', 'opinion_source', '意见来源']);
	const ocrPerformed = directBoolean(importRecords, ['ocr_performed', 'ocr_completed', '识别已完成']);
	const contextParts: string[] = [];
	if (sourceKind && /user_supplied_recognized_text|用户提供.*识别文本/i.test(sourceKind)) contextParts.push('用户提供的已识别文本');
	if (ocrPerformed === false) contextParts.push('本次未执行 OCR');
	const contextNote = contextParts.length ? contextParts.join(' · ') : null;

	const stats: S06OpinionStat[] = [];
	if (originalCount !== null) stats.push({ label: '意见总数', value: String(originalCount) });
	if (categories.length) stats.push({ label: '分类数量', value: String(categorySummariesFromRun.length) });
	if (pendingCount !== null) stats.push({ label: '待确认数量', value: String(pendingCount) });
	else if (requiresHumanReview === true) stats.push({ label: '复核状态', value: '待人工复核' });
	if (duplicateCount !== null) stats.push({ label: '重复意见', value: String(duplicateCount) });

	const moduleData: Record<S06Module, boolean> = {
		overview: Boolean(projectName || stats.length || contextNote),
		categories: categories.length > 0,
		core: coreOpinions.length > 0,
		followups: followUps.length > 0,
	};
	const hasStructuredContent = Object.values(moduleData).some(Boolean);
	const artifacts = run.artifacts.filter((artifact) => Boolean(artifact.name));
	const markdownSupplement = hasStructuredContent ? markdownSupplements(markdown, moduleData) : null;
	const hasFullResult = Boolean(markdown || run.final_output.trim() || run.final_summary.trim() || artifacts.length);

	return {
		projectName,
		stats,
		contextNote,
		categories,
		coreOpinions,
		followUps,
		moduleData,
		markdown,
		markdownSupplement,
		artifacts,
		hasStructuredContent,
		hasFullResult,
	};
}

export function adaptS06OpinionRun(run: TaskRunRecord): S06OpinionResult {
	return buildResult(run);
}
