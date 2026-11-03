import type { TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;
type S07Module = 'overview' | 'chapters' | 'quality' | 'issues';
type QualityStatus = '通过' | '发现问题' | '未通过' | '待人工复核' | '建议复核' | '已检查';

export interface S07ReportChapter {
	title: string;
	summary: string | null;
}

export interface S07QualityItem {
	title: string;
	detail: string;
	source: string;
	status: QualityStatus;
}

export interface S07ReportIssue {
	title: string;
	detail: string;
	kind: '问题' | 'AI建议';
	priority: string | null;
	status: string | null;
}

export interface S07ReportResult {
	reportName: string | null;
	reportType: string | null;
	projectName: string | null;
	chapterCount: number | null;
	formats: string[];
	statuses: string[];
	chapters: S07ReportChapter[];
	remainingChapters: number;
	qualityItems: S07QualityItem[];
	issues: S07ReportIssue[];
	moduleData: Record<S07Module, boolean>;
	markdown: string | null;
	markdownSupplement: string | null;
	artifacts: TaskArtifactRecord[];
	hasStructuredContent: boolean;
	hasFullResult: boolean;
}

interface MarkdownHeading {
	level: number;
	title: string;
	body: string;
}

const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;
const TECHNICAL_TEXT = /(?:\b(?:EV[-_:# ]?[A-Z0-9-]{6,}|[a-f0-9]{64})\b|evidence[_ -]?id|chunk[_ -]?id|document[_ -]?id|content_sha256|task-citations)/i;

function asRecord(value: unknown): DataRecord | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? value as DataRecord : null;
}

function parsePayload(value: unknown): unknown {
	if (typeof value !== 'string') return value;
	const candidate = value.trim().replace(/^```(?:json|markdown|md)?\s*/i, '').replace(/\s*```$/, '');
	if (!candidate.startsWith('{') && !candidate.startsWith('[')) return value;
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

function recordsFrom(values: unknown[]): DataRecord[] {
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
		.replace(/\[C\d+\]/gi, '')
		.replace(/[`*_>#]/g, '')
		.replace(/^\s*(?:[-*+•]|\d+[.)、])\s*/, '')
		.replace(/\s+/g, ' ')
		.trim();
	if (!text || TECHNICAL_TEXT.test(text) || /^(?:null|undefined|n\/a|unknown|暂无|未提供|—|-)$/i.test(text)) return null;
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

function directBoolean(records: DataRecord[], aliases: string[]): boolean | null {
	const value = directValue(records, aliases);
	if (typeof value === 'boolean') return value;
	if (typeof value === 'string' && /^(true|false)$/i.test(value.trim())) return value.trim().toLowerCase() === 'true';
	return null;
}

function directArray(records: DataRecord[], aliases: string[]): unknown[] | null {
	const parsed = parsePayload(directValue(records, aliases));
	return Array.isArray(parsed) ? parsed : null;
}

function nodeLabel(run: TaskRunRecord, nodeRun: TaskRunRecord['node_runs'][number]): string {
	const definition = run.nodes.find((node) => node.id === nodeRun.node_id);
	return [nodeRun.name, nodeRun.node_id, definition?.name, definition?.capability_key]
		.filter((item): item is string => Boolean(item))
		.join(' ')
		.toLowerCase();
}

function successfulNodes(run: TaskRunRecord, matcher: RegExp): TaskRunRecord['node_runs'] {
	return run.node_runs.filter((node) => node.status === 'succeeded' && matcher.test(nodeLabel(run, node)));
}

function matchingNodes(run: TaskRunRecord, matcher: RegExp): TaskRunRecord['node_runs'] {
	return run.node_runs.filter((node) => matcher.test(nodeLabel(run, node)));
}

function nodePayloads(nodes: TaskRunRecord['node_runs']): unknown[] {
	return nodes.flatMap((node) => {
		const attempt = node.attempt_history?.[node.attempt_history.length - 1];
		return [node.result, node.output, node.metadata, attempt?.result, attempt?.output, attempt?.metadata]
			.filter((value) => value !== null && value !== undefined && value !== '');
	});
}

function markdownText(value: unknown, depth = 0): string | null {
	if (depth > 7 || value === null || value === undefined) return null;
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
	for (const key of ['markdown', 'report_markdown', 'report', 'content', 'text', 'final_output', 'body', 'output', 'result']) {
		if (!(key in record)) continue;
		const text = markdownText(record[key], depth + 1);
		if (text) return text;
	}
	return null;
}

function isJsonText(text: string): boolean {
	const candidate = text.trimStart();
	return (candidate.startsWith('{') || candidate.startsWith('[')) && parsePayload(text) !== text;
}

function selectMarkdown(run: TaskRunRecord, reportNodes: TaskRunRecord['node_runs']): string | null {
	const candidates = [run.final_output, ...nodePayloads(reportNodes), run.final_summary];
	for (const candidate of candidates) {
		const text = markdownText(candidate);
		if (text && !isJsonText(text)) return text;
	}
	return null;
}

function parseMarkdownHeadings(markdown: string): MarkdownHeading[] {
	const headings: MarkdownHeading[] = [];
	const lines = markdown.split('\n');
	let inCodeFence = false;
	let current: { level: number; title: string; body: string[] } | null = null;
	for (const line of lines) {
		if (/^\s*```/.test(line)) {
			inCodeFence = !inCodeFence;
			if (current) current.body.push(line);
			continue;
		}
		const match = !inCodeFence ? line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/) : null;
		if (match) {
			if (current) headings.push({ level: current.level, title: current.title, body: current.body.join('\n').trim() });
			current = { level: match[1].length, title: match[2].trim(), body: [] };
		} else if (current) {
			current.body.push(line);
		}
	}
	if (current) headings.push({ level: current.level, title: current.title, body: current.body.join('\n').trim() });
	return headings;
}

function chapterLevel(headings: MarkdownHeading[]): number | null {
	if (!headings.length) return null;
	const firstLevel = Math.min(...headings.map((heading) => heading.level));
	const h1 = headings.filter((heading) => heading.level === 1);
	const h2 = headings.filter((heading) => heading.level === 2);
	if (h1.length === 1 && h2.length > 0) return 2;
	if (h2.length > 0 && h1.length === 0) return 2;
	if (headings.length === 1 && firstLevel === 1) return null;
	return firstLevel;
}

function sentenceSummary(value: unknown, maxLength = 120): string | null {
	const text = cleanText(value);
	if (!text || /^(?:```|\||>)/.test(text)) return null;
	const sentence = text.match(/^.*?[。！？!?；](?:\s|$)/)?.[0]?.trim();
	if (sentence && [...sentence].length <= maxLength) return sentence;
	return [...text].length <= maxLength ? text : null;
}

function structuredChapters(records: DataRecord[]): S07ReportChapter[] {
	const list = directArray(records, ['generated_chapters', 'report_chapters', 'chapter_results', 'chapters', 'sections', '章节结果', '实际章节']);
	if (!list?.length) return [];
	return list.flatMap((item) => {
		const record = asRecord(item);
		const title = record
			? directText([record], ['title', 'name', 'heading', 'chapter_title', '章节标题', '章节名称'])
			: cleanText(item);
		if (!title) return [];
		const summary = record ? sentenceSummary(directValue([record], ['summary', 'abstract', 'description', 'summary_text', '摘要', '章节摘要'])) : null;
		return [{ title, summary }];
	});
}

function markdownChapters(headings: MarkdownHeading[]): S07ReportChapter[] {
	const level = chapterLevel(headings);
	if (level === null) return [];
	return headings.filter((heading) => heading.level === level).map((heading) => ({
		title: cleanText(heading.title) ?? heading.title,
		summary: sentenceSummary(heading.body.split(/\n\s*\n/).find((part) => part.trim()) ?? ''),
	}));
}

function statusFromValue(value: unknown, passed: boolean | null): QualityStatus {
	const text = scalarText(value) ?? '';
	if (/ready_for_ai_review|待人工复核|待人工审核|待复核/i.test(text)) return '待人工复核';
	if (/failed|blocked|rejected|不通过|未通过|失败/i.test(text)) return '发现问题';
	if (/passed|success|通过|合格/i.test(text)) return passed === false ? '发现问题' : '通过';
	if (passed === true) return '通过';
	if (passed === false) return '发现问题';
	return '已检查';
}

function textItems(value: unknown): DataRecord[] {
	const parsed = parsePayload(value);
	if (!Array.isArray(parsed)) return [];
	return parsed.map((item) => {
		if (typeof item === 'string') return { detail: item };
		return asRecord(item);
	}).filter((item): item is DataRecord => Boolean(item));
}

function qualityItemsFromNodes(run: TaskRunRecord, checkNodes: TaskRunRecord['node_runs']): S07QualityItem[] {
	const items: S07QualityItem[] = checkNodes.flatMap((node): S07QualityItem[] => {
		const records = recordsFrom(nodePayloads([node]));
		const metadataRecords = recordsFrom([node.metadata, node.attempt_history?.[node.attempt_history.length - 1]?.metadata]);
		const capability = nodeLabel(run, node);
		const programValidation = directValue(metadataRecords, ['citation_verification', 'deterministic_check', 'source_validation', 'validation_report']);
		const programRecords = recordsFrom([programValidation]);
		const programPassed = directBoolean(programRecords, ['passed', 'valid', 'success']);
		const checkArray = directArray(programValidation ? programRecords : records, ['quality_checks', 'checks', 'check_items', 'check_results', 'citation_checks', 'validation_results', 'findings']);
		const checkRows = textItems(checkArray);
		const isCitationReviewInput = /report\.citation_check/.test(capability) && /ready_for_ai_review/i.test(directText(records, ['status']) ?? '');
		const isAIReview = node.type === 'agent' || isCitationReviewInput;
		const source = programValidation ? '程序校验结果' : isAIReview ? 'AI检查意见' : '本次检查输出';
		if (programValidation && !checkRows.length) {
			const issues = directArray(programRecords, ['issues', 'errors', 'failed_checks', '问题清单']);
			const issueRows = textItems(issues);
			if (issueRows.length) {
				return issueRows.map((row, index) => ({
					title: directText([row], ['title', 'name', 'check_name', '检查项']) ?? `${node.name || '检查项'} ${index + 1}`,
					detail: directText([row], ['message', 'detail', 'reason', 'description', '说明']) ?? '本次检查结果包含待处理问题。',
					source,
					status: '发现问题' as const,
				}));
			}
		}
		if (checkRows.length) {
			return checkRows.map((row, index) => {
				const explicitPassed = directBoolean([row], ['passed', 'valid', 'success']);
				const rowStatus = directValue([row], ['status', 'result', 'state', '结论', '状态']);
				const detail = directText([row], ['message', 'detail', 'description', 'reason', 'finding', '说明', '发现'])
					?? directText([row], ['title', 'name', 'check_name', '检查项'])
					?? '本次检查已返回结果。';
				let status = statusFromValue(rowStatus, explicitPassed);
				if (isAIReview && status === '通过') status = '建议复核';
				return {
					title: directText([row], ['title', 'name', 'check_name', 'category', '检查项']) ?? (node.name || `检查项 ${index + 1}`),
					detail,
					source,
					status,
				};
			});
		}

		if (isCitationReviewInput) {
			return [{
				title: '引用与资料复核',
				detail: '本次节点准备报告与授权来源供复核，不代表引用语义或事实已验证。',
				source: '待 AI / 人工复核',
				status: '待人工复核',
			}];
		}
		if (programValidation) {
			return [{
				title: directText(programRecords, ['name', 'title', 'check_name']) ?? (node.name || '程序检查'),
				detail: directText(programRecords, ['message', 'summary', 'detail', 'description']) ?? '程序检查已返回结果，请查看执行详情。',
				source,
				status: statusFromValue(directValue(programRecords, ['status', 'result']), programPassed),
			}];
		}
		if (node.status === 'succeeded') {
			const detail = directText(records, ['check_summary', 'review_instruction', 'message', 'summary', 'detail'])
				?? cleanText(node.display_summary);
			if (!detail || TECHNICAL_TEXT.test(detail)) return [];
			return [{
				title: node.name || '报告检查',
				detail,
				source,
				status: isAIReview ? '建议复核' : '已检查',
			}];
		}
		return [];
	});
	return uniqueBy(items, (item) => `${item.title}|${item.detail}`)
		.sort((left, right) => {
			const rank = (status: QualityStatus) => status === '发现问题' || status === '未通过' ? 0 : status === '待人工复核' || status === '建议复核' ? 1 : status === '已检查' ? 2 : 3;
			return rank(left.status) - rank(right.status);
		})
		.slice(0, 4);
}

function uniqueBy<T>(items: T[], keyOf: (item: T) => string): T[] {
	const seen = new Set<string>();
	return items.filter((item) => {
		const key = keyOf(item);
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

function markdownListItems(body: string, limit: number): string[] {
	const bullets = body.split('\n')
		.filter((line) => /^\s*(?:[-*+]\s+|\d+[.)、]\s+)/.test(line))
		.map(cleanText)
		.filter((item): item is string => Boolean(item));
	if (bullets.length) return bullets.slice(0, limit);
	const firstParagraph = body.split(/\n\s*\n/).map((item) => item.trim()).find(Boolean);
	const sentence = sentenceSummary(firstParagraph, 180);
	return sentence ? [sentence] : [];
}

function qualityItemsFromMarkdown(headings: MarkdownHeading[]): S07QualityItem[] {
	const qualitySections = headings.filter((heading) => /质量检查|检查结果|检查意见|核查意见|一致性检查|引用核验|完整性检查|合规检查|审核意见/.test(heading.title));
	return qualitySections.flatMap((section) => markdownListItems(section.body, 4).map((detail) => ({
		title: section.title,
		detail,
		source: 'AI检查意见',
		status: '建议复核' as const,
	}))).slice(0, 4);
}

function issueRows(records: DataRecord[], aliases: string[], kind: S07ReportIssue['kind']): S07ReportIssue[] {
	const rows = textItems(directArray(records, aliases));
	return rows.flatMap((row) => {
		const detail = directText([row], ['detail', 'description', 'message', 'text', 'content', 'reason', 'suggestion', 'recommendation', '说明', '问题描述', '修改建议']);
		if (!detail) return [];
		return [{
			title: directText([row], ['title', 'name', 'issue', 'problem', 'subject', '事项', '问题']) ?? (kind === 'AI建议' ? '修改建议' : '待处理问题'),
			detail,
			kind,
			priority: directText([row], ['priority', 'severity', 'level', '严重程度', '优先级']),
			status: directText([row], ['status', 'state', '处理状态', '检查状态']),
		}];
	});
}

function issuesFromMarkdown(headings: MarkdownHeading[]): S07ReportIssue[] {
	return headings.flatMap((section) => {
		const isSuggestion = /修改建议|改进建议|优化建议|建议/.test(section.title);
		const isIssue = /问题|待核实|待补充|风险|缺失|未解决/.test(section.title);
		if (!isSuggestion && !isIssue) return [];
		return markdownListItems(section.body, 4).map((detail) => ({
			title: section.title,
			detail,
			kind: isSuggestion ? 'AI建议' as const : '问题' as const,
			priority: null,
			status: /待核实|待复核|待补充/.test(section.title) ? '待核实' : null,
		}));
	}).slice(0, 4);
}

function moduleForHeading(title: string): S07Module | null {
	if (/概览|报告信息|报告生成/.test(title)) return 'overview';
	if (/质量检查|检查结果|检查意见|核查意见|一致性检查|引用核验|完整性检查|合规检查|审核意见/.test(title)) return 'quality';
	if (/问题|待核实|待补充|修改建议|改进建议|优化建议|风险|未解决/.test(title)) return 'issues';
	return null;
}

function markdownSupplement(markdown: string | null, modules: Record<S07Module, boolean>, chapterTitles: string[]): string | null {
	if (!markdown || Object.values(modules).every(Boolean)) return null;
	const headings = parseMarkdownHeadings(markdown);
	if (!headings.length) return markdown;
	const chapterKeys = new Set(chapterTitles.map(normalizeKey));
	const sections: MarkdownHeading[] = [];
	let consumedParentLevel: number | null = null;
	for (const heading of headings) {
		if (consumedParentLevel !== null && heading.level > consumedParentLevel) continue;
		consumedParentLevel = null;
		const module = moduleForHeading(heading.title);
		if ((module && modules[module]) || chapterKeys.has(normalizeKey(heading.title))) {
			consumedParentLevel = heading.level;
			continue;
		}
		sections.push(heading);
	}
	const text = sections.map((section) => `## ${section.title}\n\n${section.body}`).filter((item) => item.trim().length > 0).join('\n\n');
	return text || null;
}

function buildResult(run: TaskRunRecord): S07ReportResult {
	const reportNodes = successfulNodes(run, /draft_report_chapters|produce_report_draft|生成章节草稿|整理报告草稿|报告正文|final_report/i);
	const checkNodes = matchingNodes(run, /report\.(?:citation_check|consistency_check|compliance_check)|document\.citation_verify|核验引用|检查报告|质量检查/i);
	const inputNodes = run.node_runs.filter((node) => node.type === 'input');
	const inputRecords = recordsFrom([run.input, ...inputNodes.map((node) => node.input)]);
	const reportRecords = recordsFrom([...nodePayloads(reportNodes), run.final_output, run.final_summary]);
	const markdown = selectMarkdown(run, reportNodes);
	const headings = markdown ? parseMarkdownHeadings(markdown) : [];
	const chapters = structuredChapters(reportRecords);
	const generatedChapters = chapters.length ? chapters : markdownChapters(headings);
	const reportTitleFromHeading = headings.find((heading) => heading.level === 1)?.title ?? null;
	const reportName = directText(reportRecords, ['report_name', 'report_title', 'document_title', 'name', '报告名称', '报告标题'])
		?? cleanText(reportTitleFromHeading)
		?? directText(inputRecords, ['report_name', 'report_title', 'document_title', '报告名称', '报告标题']);
	const reportType = directText(reportRecords, ['report_type', 'document_type', 'report_kind', '报告类型', '文档类型'])
		?? directText(inputRecords, ['report_type', 'document_type', 'report_kind', '报告类型']);
	const projectName = directText(reportRecords, ['project_name', 'projectName', '项目名称'])
		?? directText(inputRecords, ['project_name', 'projectName', '项目名称']);
	const artifacts = run.artifacts.filter((artifact) => Boolean(artifact.name));
	const formats = [...new Set(artifacts.map((artifact) => artifact.format.toUpperCase()))];
	const statusText = `${markdown ?? ''}\n${run.final_summary}\n${directText(reportRecords, ['status', 'report_status', 'delivery_status', '报告状态']) ?? ''}`;
	const statuses: string[] = [];
	if (markdown || generatedChapters.length > 0) statuses.push('报告草稿已生成');
	if (artifacts.length) statuses.push('报告文件已生成');
	if (run.delivery_status === 'draft_unverified' || /待人工复核|待人工审核|待复核/.test(statusText)) statuses.push('报告待人工复核');

	const qualityStructured = qualityItemsFromNodes(run, checkNodes);
	const qualityItems = qualityStructured.length ? qualityStructured : qualityItemsFromMarkdown(headings);
	const problems = issueRows(reportRecords, ['issues', 'problems', 'findings', 'unresolved_issues', 'missing_items', '待核实事项', '问题清单', '待处理问题'], '问题');
	const suggestions = issueRows(reportRecords, ['modification_suggestions', 'revision_suggestions', 'recommendations', 'suggestions', 'improvement_actions', '修改建议', '改进建议'], 'AI建议');
	const markdownIssues = problems.length || suggestions.length ? [] : issuesFromMarkdown(headings);
	const issues = uniqueBy([...problems, ...suggestions, ...markdownIssues], (item) => `${item.title}|${item.detail}`).slice(0, 4);
	const moduleData: Record<S07Module, boolean> = {
		overview: Boolean(reportName || reportType || projectName || generatedChapters.length || formats.length || statuses.length),
		chapters: generatedChapters.length > 0,
		quality: qualityItems.length > 0,
		issues: issues.length > 0,
	};
	const hasStructuredContent = Object.values(moduleData).some(Boolean);
	const chapterTitles = generatedChapters.map((chapter) => chapter.title);
	const supplement = hasStructuredContent ? markdownSupplement(markdown, moduleData, chapterTitles) : null;
	const hasFullResult = Boolean(markdown || run.final_output.trim() || run.final_summary.trim() || artifacts.length);

	return {
		reportName,
		reportType,
		projectName,
		chapterCount: generatedChapters.length ? generatedChapters.length : null,
		formats,
		statuses: uniqueBy(statuses, (status) => status),
		chapters: generatedChapters.slice(0, 5),
		remainingChapters: Math.max(0, generatedChapters.length - 5),
		qualityItems: qualityItems.slice(0, 4),
		issues,
		moduleData,
		markdown,
		markdownSupplement: supplement,
		artifacts,
		hasStructuredContent,
		hasFullResult,
	};
}

export function adaptS07ReportRun(run: TaskRunRecord): S07ReportResult {
	return buildResult(run);
}
