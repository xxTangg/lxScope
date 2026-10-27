import type { NodeRunRecord, TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;

export interface S02OverviewField {
	label: string;
	value: string;
}

export interface S02PlanningReference {
	name: string;
	usage: string | null;
}

export interface S02WorkItem {
	title: string;
	detail: string | null;
	timing: string | null;
}

export interface S02OutlineSection {
	number: string;
	title: string;
	summary: string | null;
}

export interface S02ProjectPlanningResult {
	projectName: string | null;
	overviewFields: S02OverviewField[];
	overviewExcerpt: string | null;
	outline: S02OutlineSection[];
	recommendations: string[];
	workPlan: S02WorkItem[];
	references: S02PlanningReference[];
	pendingItems: string[];
	docxArtifacts: TaskArtifactRecord[];
}

interface MarkdownSection {
	level: number;
	title: string;
	number: string;
	body: string;
}

const FIELD_ALIASES: Array<{ label: string; aliases: string[] }> = [
	{ label: '项目编号', aliases: ['project_code', 'project_number', 'project_no', 'project_id', '项目编号', '项目代码', '项目号'] },
	{ label: '项目类型 / 行业', aliases: ['project_type', 'project_category', 'industry', 'industry_type', 'sector', '项目类型', '项目类别', '所属行业', '所属领域', '行业领域'] },
	{ label: '项目背景', aliases: ['project_background', 'background', 'project_context', '项目背景', '背景说明', '项目概况'] },
	{ label: '策划目标', aliases: ['planning_goal', 'project_goal', 'objective', 'project_objective', 'planning_objective', '策划目标', '项目目标', '建设目标', '主要目标'] },
	{ label: '工作范围', aliases: ['work_scope', 'project_scope', 'scope', 'scope_of_work', '工作范围', '策划范围', '工作内容', '服务范围'] },
];

const PROJECT_NAME_ALIASES = ['project_name', 'project_title', 'projectName', '项目名称', '项目名', '项目全称', '拟建项目名称'];
const TITLE_KEYS = ['title', 'name', 'filename', 'file_name', 'document_name', 'source_name', '文件名', '资料名称', '文档名称', '来源文件'];
const USAGE_KEYS = ['usage', 'purpose', 'role', 'category', 'document_type', 'content_type', '资料用途', '用途', '资料类型', '文件类型', '内容类型'];
const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;
const TECHNICAL_VALUE = /^(?:ev[-_:# ]?[a-f0-9-]+|evidence[-_:# ]?id|chunk[-_:# ]?id|document[-_:# ]?id|[a-f0-9]{32,}|https?:\/\/)/i;
const OVERVIEW_HEADINGS = /^(?:项目概况|项目画像|项目基本信息|项目背景|项目概述|项目资料概况)$/i;
const RECOMMENDATION_HEADINGS = /(?:方案建议|策划建议|核心建议|关键建议|实施建议|重点工作建议|建议措施|项目建议|策划思路)/i;
const WORK_PLAN_HEADINGS = /(?:工作计划|实施计划|工作安排|推进计划|阶段安排|实施步骤|工作任务|项目计划)/i;
const PENDING_HEADINGS = /(?:待核实事项|待确认事项|待补充事项|待补充资料|资料缺口|数据缺口|风险与待核实|风险及待确认|需核实事项)/i;
const PENDING_MARKERS = /待核实|待确认|待补充|需补充|需确认|需核对|尚未提供|未提供|缺失|不完整|待完善|待落实|需核实|待明确/;

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
	if (depth > 7) return [];
	const parsed = parsePayload(value);
	if (Array.isArray(parsed)) return parsed.flatMap((item) => recordsIn(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return [record, ...Object.entries(record).flatMap(([key, child]) =>
		SECRET_KEY.test(key) || child === null || typeof child !== 'object' && typeof child !== 'string'
			? []
			: recordsIn(child, depth + 1),
	)];
}

function textualLeaves(value: unknown, depth = 0): string[] {
	if (depth > 7 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (typeof parsed === 'string') {
		if (parsed !== value) return textualLeaves(parsed, depth + 1);
		return parsed.trim() ? [parsed.trim()] : [];
	}
	if (Array.isArray(parsed)) return parsed.flatMap((item) => textualLeaves(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return Object.entries(record).flatMap(([key, child]) => SECRET_KEY.test(key) ? [] : textualLeaves(child, depth + 1));
}

function directText(record: DataRecord, aliases: string[]): string | null {
	const wanted = new Set(aliases.map(normalizeKey));
	for (const [key, value] of Object.entries(record)) {
		if (SECRET_KEY.test(key) || !wanted.has(normalizeKey(key))) continue;
		const text = scalarText(value);
		if (text) return text;
	}
	return null;
}

function scalarText(value: unknown): string | null {
	if (typeof value === 'string' || typeof value === 'number') return String(value).trim() || null;
	if (Array.isArray(value)) return value.map(scalarText).filter((item): item is string => Boolean(item)).join('、') || null;
	const record = asRecord(value);
	if (!record) return null;
	for (const key of ['value', 'text', 'name', 'label', 'title', 'summary', 'content']) {
		const text = scalarText(record[key]);
		if (text) return text;
	}
	return null;
}

function cleanText(value: string): string {
	return value
		.replace(/<!--.*?-->/gs, ' ')
		.replace(/```[\s\S]*?```/g, ' ')
		.replace(/\[([^\]]*)\]\((?:https?:\/\/|\/)[^)]+\)/gi, '$1')
		.replace(/https?:\/\/\S+/gi, ' ')
		.replace(/\b(?:DEMO-SOURCE-[A-Z0-9_-]+|EV[-_:# ]?[A-Z0-9-]{6,}|[a-f0-9]{64})\b/gi, ' ')
		.replace(/(?:evidence|chunk|document|knowledge_base)[_-]?(?:id|key)?\s*[:=：]\s*[^\s,，;；|]+/gi, ' ')
		.replace(/[`*_>#]/g, '')
		.replace(/\s+/g, ' ')
		.trim();
}

function summaryText(value: string, maxLength = 110): string | null {
	const cleaned = cleanText(value)
		.split(/\n+/)
		.filter((line) => line.trim() && !/DEMO\s*[/_-]\s*SYNTHETIC|演示数据|合成演示|仅供演示|不代表实际/i.test(line))
		.join(' ')
		.replace(/\|/g, ' ')
		.replace(/\s+/g, ' ')
		.trim();
	if (!cleaned || TECHNICAL_VALUE.test(cleaned)) return null;
	if ([...cleaned].length <= maxLength) return cleaned;
	const prefix = [...cleaned].slice(0, maxLength).join('');
	const sentenceEnd = Math.max(prefix.lastIndexOf('。'), prefix.lastIndexOf('；'), prefix.lastIndexOf('！'), prefix.lastIndexOf('？'));
	const wordEnd = prefix.lastIndexOf(' ');
	const cutAt = sentenceEnd >= maxLength * 0.55 ? sentenceEnd + 1 : wordEnd >= maxLength * 0.55 ? wordEnd : maxLength;
	return `${prefix.slice(0, cutAt).trimEnd()}…`;
}

function sourceNode(run: TaskRunRecord, pattern: RegExp): NodeRunRecord | undefined {
	return [...run.node_runs].sort((left, right) => left.order - right.order).find((node) => pattern.test(`${node.name} ${node.prompt} ${node.node_id}`));
}

function nodePayloads(node: NodeRunRecord | undefined, includeInput = false): unknown[] {
	if (!node) return [];
	const payloads = [node.result, node.output, node.display_summary, ...(includeInput ? [node.input] : [])]
		.filter((value) => value !== undefined && value !== null && value !== '');
	if (payloads.length) return payloads.map(parsePayload);
	const lastAttempt = node.attempt_history?.at(-1);
	return lastAttempt ? [parsePayload(lastAttempt.result), parsePayload(lastAttempt.output)].filter((value) => value !== undefined) : [];
}

function textFromReportPayload(value: unknown, depth = 0): string[] {
	if (depth > 5 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (typeof parsed === 'string') {
		if (parsed !== value) return textFromReportPayload(parsed, depth + 1);
		return parsed.trim() ? [parsed.trim()] : [];
	}
	if (Array.isArray(parsed)) return parsed.flatMap((item) => textFromReportPayload(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	const reportKeys = ['markdown', 'report', 'report_markdown', 'content', 'text', 'body', 'document_content', 'final_output', 'summary', 'output'];
	return Object.entries(record).flatMap(([key, child]) =>
		reportKeys.includes(normalizeKey(key)) && !SECRET_KEY.test(key) ? textFromReportPayload(child, depth + 1) : [],
	);
}

function reportPayloads(run: TaskRunRecord): unknown[] {
	const finalNodes = [...run.node_runs]
		.filter((node) => node.type === 'agent' && node.status === 'succeeded')
		.sort((left, right) => right.order - left.order)
		.filter((node) => /报告|策划|方案|draft|final|review|编制|生成/i.test(`${node.name} ${node.prompt}`));
	return [
		...run.artifacts.filter((artifact) => artifact.format === 'docx' || artifact.format === 'markdown').map((artifact) => artifact.preview_text),
		run.final_output,
		run.final_summary,
		...finalNodes.flatMap((node) => [node.result, node.output, node.display_summary]),
	].filter((value) => value !== null && value !== undefined && value !== '');
}

function reportTexts(run: TaskRunRecord): string[] {
	return [...new Set(reportPayloads(run).flatMap(textFromReportPayload).map((text) => text.trim()).filter((text) => text.length > 30))];
}

function markdownReport(run: TaskRunRecord): string | null {
	const candidates = reportPayloads(run)
		.flatMap(textFromReportPayload)
		.map((text) => text.trim())
		.filter((text) => text.length > 80 && !/^(?:\{|\[)/.test(text));
	if (!candidates.length) return null;
	const markdownScore = (text: string) =>
		(Number(/^\s*#{1,6}\s/m.test(text)) * 4) +
		(Number(/^\s*\|.+\|\s*$/m.test(text)) * 2) +
		(Number(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/m.test(text))) +
		Math.min(text.length / 5000, 2);
	return candidates.sort((left, right) => markdownScore(right) - markdownScore(left) || right.length - left.length)[0] ?? null;
}

function parseLabelledText(text: string): DataRecord[] {
	const record: DataRecord = {};
	for (const line of text.split(/\r?\n/)) {
		const match = line.match(/^\s*(?:[-*•]\s*)?(?:\*\*)?([^|:：*]{2,48}?)(?:\*\*)?\s*[:：]\s*(.+)$/);
		if (match) record[match[1].replace(/[`*_#]/g, '').trim()] = match[2].replace(/\*\*$/, '').trim();
	}
	return Object.keys(record).length ? [record] : [];
}

function parseTable(text: string): DataRecord[] {
	const lines = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line.startsWith('|') && line.endsWith('|'));
	const rows: DataRecord[] = [];
	for (let index = 0; index < lines.length - 1; index += 1) {
		const headers = lines[index].slice(1, -1).split('|').map((cell) => cell.trim());
		const divider = lines[index + 1].slice(1, -1).split('|').every((cell) => /^\s*:?-{2,}:?\s*$/.test(cell));
		if (!divider || headers.length < 2) continue;
		for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
			const cells = lines[rowIndex].slice(1, -1).split('|').map((cell) => cell.trim());
			if (cells.length !== headers.length) break;
			const row: DataRecord = {};
			headers.forEach((header, cellIndex) => { row[header] = cells[cellIndex]; });
			rows.push(row);
		}
	}
	return rows;
}

function recordsForPayloads(payloads: unknown[]): DataRecord[] {
	const nested = payloads.flatMap((payload) => recordsIn(payload));
	const fromText = payloads.flatMap((payload) => textualLeaves(payload)).flatMap((text) => [...parseLabelledText(text), ...parseTable(text)]);
	return [...nested, ...fromText];
}

function extractReportSections(text: string): MarkdownSection[] {
	const lines = text.split(/\r?\n/);
	const headings: Array<{ index: number; level: number; title: string; number: string }> = [];
	for (let index = 0; index < lines.length; index += 1) {
		const line = lines[index].trim();
		if (!line || line.startsWith('|') || line.startsWith('```')) continue;
		const markdown = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
		const plainChapter = line.match(/^((?:第\s*[一二三四五六七八九十百\d]+\s*[章节篇]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．)]))\s*(.{2,72})$/);
		const heading = markdown?.[2] ?? (plainChapter && [...line].length <= 82 ? line : null);
		if (!heading) continue;
		const cleaned = heading.replace(/^\*\*|\*\*$/g, '').replace(/[`*_]/g, '').trim();
		const numberMatch = cleaned.match(/^(第\s*[一二三四五六七八九十百\d]+\s*[章节篇]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．)]?)\s*/);
		headings.push({
			index,
			level: markdown ? markdown[1].length : 2,
			title: cleaned.replace(/^(?:第\s*[一二三四五六七八九十百\d]+\s*[章节篇]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．)]?)\s*/, '').trim(),
			number: numberMatch?.[1]?.trim() ?? '',
		});
	}
	return headings.map((heading, headingIndex) => {
		const nextHeading = headings.slice(headingIndex + 1).find((next) => next.level <= heading.level);
		return {
			level: heading.level,
			title: heading.title,
			number: heading.number,
			body: lines.slice(heading.index + 1, nextHeading?.index ?? lines.length).join('\n').trim(),
		};
	});
}

function outlineSections(markdown: string | null): S02OutlineSection[] {
	if (!markdown) return [];
	const sections = extractReportSections(markdown).filter((section) => section.title.trim());
	if (!sections.length) return [];

	// Report title is commonly H1 and its chapters H2. Prefer the first chapter
	// level found below H1; if the report has no such headings, use its shallowest level.
	const chapterLevel = sections.some((section) => section.level > 1)
		? Math.min(...sections.filter((section) => section.level > 1).map((section) => section.level))
		: Math.min(...sections.map((section) => section.level));
	const chapters = sections.filter((section) => section.level === chapterLevel).slice(0, 12);

	return chapters.map((section, index) => ({
		number: section.number || String(index + 1),
		title: section.title,
		summary: section.body ? summaryText(section.body, 100) : null,
	}));
}

function extractSectionList(text: string, matcher: RegExp): MarkdownSection[] {
	return extractReportSections(text).filter((section) => matcher.test(section.title));
}

function listLines(text: string): string[] {
	return text.split(/\r?\n/).map((line) => line.trim())
		.filter((line) => /^(?:[-*•]\s+|\d+[.、)]\s+|[一二三四五六七八九十]+[、.．]\s+)/.test(line))
		.map((line) => line.replace(/^(?:[-*•]\s+|\d+[.、)]\s+|[一二三四五六七八九十]+[、.．]\s+)/, '').trim());
}

function sentences(text: string): string[] {
	return text.split(/\n+|(?<=[。！？；])\s*/).map((item) => item.replace(/^\s*[-*•\d.、)]+\s*/, '').trim()).filter(Boolean);
}

function recommendationResults(run: TaskRunRecord, texts: string[]): string[] {
	const structured = reportPayloads(run).flatMap((payload) => recordsIn(payload)).flatMap((record) => {
		const array = Object.entries(record).find(([key, value]) => /^(?:recommendations|key_recommendations|suggestions|planning_advice|策划建议|建议事项)$/.test(normalizeKey(key)) && Array.isArray(value))?.[1];
		return Array.isArray(array) ? array.map((item) => scalarText(item) ?? directText(asRecord(item) ?? {}, ['recommendation', 'suggestion', 'title', 'content', 'text', '建议', '措施', '内容'])).filter((item): item is string => Boolean(item)) : [];
	});
	const fromSections = texts.flatMap((text) => extractSectionList(text, RECOMMENDATION_HEADINGS).flatMap((section) => {
		const bullets = listLines(section.body);
		return bullets.length ? bullets : sentences(section.body);
	}));
	return [...new Set([...structured, ...fromSections].map((item) => summaryText(item, 96)).filter((item): item is string => Boolean(item)))].slice(0, 3);
}

function markdownTables(text: string): DataRecord[] {
	return parseTable(text);
}

function workPlanResults(run: TaskRunRecord, texts: string[]): S02WorkItem[] {
	const structured = reportPayloads(run).flatMap((payload) => recordsIn(payload)).flatMap((record) => {
		const value = Object.entries(record).find(([key, child]) =>
			/^(?:workplan|workitems|implementationplan|workschedule|工作计划|实施计划|阶段安排)$/.test(normalizeKey(key)) && Array.isArray(child),
		)?.[1];
		if (!Array.isArray(value)) return [];
		return value.flatMap((item) => {
			const row = asRecord(item);
			if (!row) return [];
			const title = directText(row, ['stage', 'phase', 'title', 'name', '阶段', '阶段名称', '工作阶段']);
			const detail = directText(row, ['task', 'work', 'activity', 'description', '主要工作', '工作任务', '工作内容', '任务']);
			const timing = directText(row, ['time', 'duration', 'schedule', 'timeframe', '时间安排', '预计时间', '周期', '工期']);
			return title || detail ? [{ title: title ?? detail ?? '工作要点', detail: title ? detail : null, timing: timing ? summaryText(timing, 48) : null }] : [];
		});
	});
	if (structured.length) return structured.slice(0, 6);
	for (const text of texts) {
		for (const section of extractSectionList(text, WORK_PLAN_HEADINGS)) {
			const rows = markdownTables(section.body);
			const mapped = rows.flatMap((row) => {
				const title = directText(row, ['stage', 'phase', '阶段', '阶段名称', '工作阶段']);
				const detail = directText(row, ['task', 'work', 'activity', '主要工作', '工作任务', '工作内容', '任务']);
				const timing = directText(row, ['time', 'duration', 'schedule', 'timeframe', '时间安排', '预计时间', '周期', '工期']);
				return title || detail ? [{ title: title ?? detail ?? '工作要点', detail: title ? detail : null, timing }] : [];
			});
			if (mapped.length) return mapped.slice(0, 6);
			const bullets = listLines(section.body).map((item) => summaryText(item, 100)).filter((item): item is string => Boolean(item));
			if (bullets.length) return bullets.slice(0, 6).map((detail, index) => ({ title: `工作要点 ${index + 1}`, detail, timing: null }));
		}
	}
	return [];
}

function isLikelyFilename(value: string): boolean {
	return /\.(?:docx?|xlsx?|pdf|md|markdown|pptx?|csv|txt)$/i.test(value) || /[\\/]/.test(value);
}

function safeReferenceName(value: string): string | null {
	const candidate = value.trim().replace(/[?#].*$/, '').replace(/^['"`]|['"`]$/g, '');
	if (!candidate || TECHNICAL_VALUE.test(candidate) || /^https?:\/\//i.test(candidate)) return null;
	const basename = candidate.split(/[\\/]/).filter(Boolean).at(-1) ?? candidate;
	if (!isLikelyFilename(basename) || /^(?:source|document|file|资料|文档)[_-]?(?:id|key)?\d*\.(?:md|txt)$/i.test(basename)) return null;
	return basename.replace(/^DEMO-SOURCE-[A-Z0-9_-]+[-_]/i, '').replace(/\.(?:markdown|md|docx?|pdf|xlsx?|pptx?|csv|txt)$/i, '').replace(/[-_]+/g, ' ').trim() || basename;
}

function referencesFromRun(run: TaskRunRecord): S02PlanningReference[] {
	const nodesById = new Map(run.nodes.map((node) => [node.id, node]));
	const retrievalPayloads = run.node_runs.filter((node) => {
		const config = nodesById.get(node.node_id);
		return node.status === 'succeeded' && (
			node.type === 'tool' ||
			/检索|资料|知识库|参考|retriev|search|document/i.test(`${node.name} ${config?.capability_key ?? ''}`)
		) && /检索|资料|知识库|参考|retriev|search|document|template/i.test(`${node.name} ${node.prompt} ${config?.capability_key ?? ''}`);
	}).flatMap((node) => nodePayloads(node));
	const verificationSources = run.verification_report?.retrieval?.sources ?? [];
	const records = [...recordsForPayloads(retrievalPayloads), ...recordsIn(verificationSources)];
	const references: S02PlanningReference[] = [];
	for (const record of records) {
		const rawName = directText(record, TITLE_KEYS) ?? directText(record, ['source', '来源', '文件路径', 'path']);
		if (!rawName) continue;
		const name = safeReferenceName(rawName);
		if (!name) continue;
		const usage = directText(record, USAGE_KEYS);
		if (!references.some((reference) => reference.name === name)) references.push({ name, usage: usage ? summaryText(usage, 72) : null });
		if (references.length >= 5) break;
	}
	return references;
}

function pendingResults(run: TaskRunRecord, texts: string[]): string[] {
	const items: string[] = [];
	const structuredRecords = reportPayloads(run).flatMap((payload) => recordsIn(payload));
	for (const record of structuredRecords) {
		for (const [key, value] of Object.entries(record)) {
			if (!Array.isArray(value) || !/^(?:pendingitems|unverifieditems|itemstoconfirm|datagaps|待核实事项|待确认事项|待补充事项|资料缺口)$/.test(normalizeKey(key))) continue;
			for (const item of value) {
				const itemRecord = asRecord(item);
				const text = scalarText(item) ?? directText(itemRecord ?? {}, ['title', 'name', 'item', 'description', 'content', 'text', '事项', '待核实内容']);
				if (!text) continue;
				const status = directText(itemRecord ?? {}, ['status', 'verification_status', '核实状态', '状态']);
				const isExplicitlyPending = /pending|unverified|needs?_?confirmation|待核实|待确认/i.test(status ?? '') || /^(?:pendingitems|unverifieditems|itemstoconfirm|datagaps|待核实事项|待确认事项|待补充事项|资料缺口)$/.test(normalizeKey(key));
				const summary = isExplicitlyPending || PENDING_MARKERS.test(text) ? summaryText(text, 110) : null;
				if (summary && !items.includes(summary)) items.push(summary);
				if (items.length >= 5) return items;
			}
		}
	}
	for (const text of texts) {
		for (const section of extractSectionList(text, PENDING_HEADINGS)) {
			const lines = listLines(section.body);
			const candidates = lines.length ? lines : sentences(section.body);
			for (const candidate of candidates) {
				if (!PENDING_MARKERS.test(candidate)) continue;
				const summary = summaryText(candidate, 110);
				if (summary && !items.includes(summary)) items.push(summary);
				if (items.length >= 5) return items;
			}
		}
	}
	return items;
}

function overviewResult(run: TaskRunRecord, texts: string[]): { projectName: string | null; fields: S02OverviewField[]; excerpt: string | null } {
	const inputs: unknown[] = [run.input];
	const inputNodes = run.node_runs.filter((node) => node.type === 'input' || /项目资料|收集.*资料|读取.*资料|planning inputs|collect.*input/i.test(`${node.name} ${node.prompt}`));
	for (const node of inputNodes) inputs.push(...nodePayloads(node, true));
	const inputRecords = recordsForPayloads(inputs);
	const reportRecords = recordsForPayloads(reportPayloads(run));
	const rawProjectName = [...inputRecords, ...reportRecords].map((record) => directText(record, PROJECT_NAME_ALIASES)).find(Boolean) ?? null;
	const projectName = rawProjectName ? summaryText(rawProjectName, 160) : null;
	const allRecords = [...inputRecords, ...reportRecords, ...recordsForPayloads(texts)];
	const fields = FIELD_ALIASES.flatMap(({ label, aliases }) => {
		const value = allRecords.map((record) => directText(record, aliases)).find(Boolean);
		const summary = value ? summaryText(value, label === '项目背景' ? 150 : 115) : null;
		return summary ? [{ label, value: summary }] : [];
	});
	const profileNode = sourceNode(run, /项目概况|项目资料|project profile|project overview|planning inputs|collect.*input/i);
	const profileTexts = [...inputs.flatMap(textualLeaves), ...texts];
	for (const text of profileTexts) {
		const matching = extractReportSections(text).filter((section) => OVERVIEW_HEADINGS.test(section.title));
		for (const section of matching) {
			const excerpt = summaryText(section.body, 180);
			if (excerpt) return { projectName, fields, excerpt };
		}
	}
	for (const value of nodePayloads(profileNode, true).flatMap(textualLeaves)) {
		const excerpt = summaryText(value, 180);
		if (excerpt && !/^[{[]/.test(value.trim())) return { projectName, fields, excerpt };
	}
	return { projectName, fields, excerpt: null };
}

export function adaptS02ProjectPlanningRun(run: TaskRunRecord): S02ProjectPlanningResult {
	const texts = reportTexts(run);
	const overview = overviewResult(run, texts);
	const report = markdownReport(run);
	return {
		projectName: overview.projectName,
		overviewFields: overview.fields,
		overviewExcerpt: overview.excerpt,
		outline: outlineSections(report),
		recommendations: recommendationResults(run, texts),
		workPlan: workPlanResults(run, texts),
		references: referencesFromRun(run),
		pendingItems: pendingResults(run, texts),
		docxArtifacts: run.artifacts.filter((artifact) => artifact.format === 'docx'),
	};
}
