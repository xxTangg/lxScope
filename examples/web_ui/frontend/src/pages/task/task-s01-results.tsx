import { ArrowUpRight, BookOpen, FileSearch, Lightbulb, ListChecks } from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import type { NodeRunRecord, TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

type DataRecord = Record<string, unknown>;
type ResultField = { label: string; aliases: string[] };
type ProjectCase = { name: string; reason: string; experience?: string; pending: boolean };
type RuleItem = { name: string; requirement: string; pending: boolean };

const BASIC_FIELDS: ResultField[] = [
	{ label: '项目名称', aliases: ['project_name', 'projectName', 'project_title', '项目名称', '项目名', '项目全称', '拟建项目名称'] },
	{ label: '项目编号', aliases: ['project_code', 'project_number', 'project_no', 'project_id', '项目编号', '项目代码', '项目号'] },
	{ label: '项目类型', aliases: ['project_type', 'project_category', '项目类型', '项目类别', '建设类型'] },
	{ label: '所属领域', aliases: ['industry', 'project_field', 'business_domain', 'field', '所属领域', '所属业务领域', '业务领域', '所属行业', '行业领域', '行业', '领域'] },
	{ label: '项目主要目标', aliases: ['project_goal', 'main_goal', 'objective', 'project_objective', '项目主要目标', '主要目标', '项目目标', '建设目标', '目标'] },
];

const CASE_NAME_KEYS = ['historical_project_name', 'project_name', 'projectName', 'project_title', 'case_name', 'historical_name', '项目名称', '历史项目名称', '项目名', '项目标题', '案例名称'];
const CASE_REASON_KEYS = ['similarity_reason', 'match_reason', 'reason', '相似原因', '匹配原因', '相似度说明', '相关性说明', '案例摘要', '相似项目摘要'];
const CASE_EXPERIENCE_KEYS = ['transferable_experience', 'experience', 'lessons', '可借鉴经验', '借鉴经验', '经验', '经验启示'];
const RULE_NAME_KEYS = ['rule_name', 'basis_name', 'policy_name', 'requirement_name', '名称', '规则名称', '依据名称', '立项依据', '制度名称', '条款名称', '评审要求', '适用规则', '检查项'];
const RULE_TEXT_KEYS = ['key_requirement', 'requirement', 'requirement_text', 'rule_text', '关键要求', '要求', '规则要求', '依据要求', '注意事项', '关键约束', '适用说明'];
const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;
const TECHNICAL_TEXT = /(DEMO-SOURCE-|\bEV[-_ ]?\d|evidence[_ -]?id|chunk[_ -]?id|document[_ -]?id|content[_ -]?sha|sha-?256|knowledge_base[_ -]?id|authorization_subject)/i;
const PROFILE_HEADINGS = /^(?:(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*)?(?:项目画像|项目概况|项目基本信息|基本信息|项目背景|项目概述)$/i;
const CASE_HEADINGS = /^(?:(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*)?(?:相似项目(?:推荐|检索结果)?(?:\s*[/／]\s*参考(?:材料)?(?:线索)?)?|相似案例|历史案例|案例参考)$/i;
const RULE_HEADINGS = /^(?:(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*)?(?:立项依据(?:参考)?|立项规则|规则核对(?:结果)?|规则适用(?:说明)?|评审要求|关键约束|待核实事项)$/i;
const ADVICE_HEADINGS = /^(?:(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*)?(?:AI\s*)?(?:立项建议(?:摘要|书|结论)?|建议摘要|建议结论|立项条件与建议|主要风险|下一步工作建议)$/i;

function normalizeKey(value: string): string {
	return [...value.toLowerCase().replace(/[`*_#]/g, '')].filter((character) => !/\s/.test(character) && !'_-.:：/\\（）()【】[]'.includes(character)).join('');
}

function asRecord(value: unknown): DataRecord | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? value as DataRecord : null;
}

function parseJson(value: unknown): unknown {
	if (typeof value !== 'string') return value;
	const candidate = value.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
	if (!candidate || !/^(?:\{|\[)/.test(candidate)) return value;
	try {
		return JSON.parse(candidate) as unknown;
	} catch {
		return value;
	}
}

function isDisclosure(value: string): boolean {
	return /DEMO\s*[\x2f_-]\s*SYNTHETIC|演示数据|合成演示|虚构演示|不代表实际|synthetic/i.test(value);
}

function scalarText(value: unknown): string | null {
	if (typeof value === 'string' || typeof value === 'number') return String(value).trim() || null;
	if (Array.isArray(value)) return value.map(scalarText).filter(Boolean).join('、') || null;
	const record = asRecord(value);
	if (record) {
		for (const key of ['value', 'text', 'name', 'label', 'title']) {
			const found = scalarText(record[key]);
			if (found) return found;
		}
	}
	return null;
}

function cleanDisplayText(value: unknown): string | null {
	const raw = scalarText(value);
	if (!raw) return null;
	if (/^\s*(?:\{\{[^{}]+\}\}|\$\{[^{}]+\}|<[^<>]+>)\s*$/.test(raw)) return null;
	const cleaned = raw
		.replace(/<!--.*?-->/gs, ' ')
		.replace(/\[[CE]\d+\]/gi, ' ')
		.replace(/\[([^\]]*)\]\((?:https?:\/\/|\/)[^)]+\)/gi, '$1')
		.replace(/https?:\/\/\S+/gi, ' ')
		.replace(/\bDEMO-SOURCE-[A-Z0-9_-]+\b/gi, ' ')
		.replace(/\b(?:EV|EVIDENCE|CHUNK|DOCUMENT|DOC)[-_:# ]?[A-Z0-9-]{3,}\b/gi, ' ')
		.replace(/(?:evidence|chunk|document|knowledge_base|authorization)[_-]?(?:id|key)?\s*[:=：]\s*[^\s,，;；|]+/gi, ' ')
		.replace(/\b[a-f0-9]{64}\b/gi, ' ')
		.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
		.replace(/[`*_>#]/g, '')
		.replace(/\s+/g, ' ')
		.trim();
	if (!cleaned || TECHNICAL_TEXT.test(cleaned)) return null;
	return cleaned;
}

function nodeLabel(node: NodeRunRecord): string {
	return `${node.name} ${node.prompt} ${node.node_id}`.toLowerCase();
}

function findNode(run: TaskRunRecord, pattern: RegExp): NodeRunRecord | undefined {
	return run.node_runs.find((node) => pattern.test(nodeLabel(node)));
}

function nodePayloads(node: NodeRunRecord | undefined, includeInput = false): unknown[] {
	if (!node) return [];
	const current = [parseJson(node.result), parseJson(node.output), parseJson(node.display_summary), ...(includeInput ? [parseJson(node.input)] : [])]
		.filter((value) => value !== undefined && value !== null && value !== '');
	if (current.length) return current;
	const lastAttempt = node.attempt_history?.[node.attempt_history.length - 1];
	return lastAttempt ? [parseJson(lastAttempt.result), parseJson(lastAttempt.output)].filter((value) => value !== undefined && value !== null && value !== '') : [];
}

function collectRecords(value: unknown, depth = 0): DataRecord[] {
	if (depth > 8) return [];
	const parsed = parseJson(value);
	if (typeof parsed === 'string' && parsed !== value) return collectRecords(parsed, depth + 1);
	if (Array.isArray(parsed)) return parsed.flatMap((item) => collectRecords(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	const records = [record];
	for (const [key, child] of Object.entries(record)) {
		if (!SECRET_KEY.test(key) && child && (typeof child === 'object' || typeof child === 'string')) records.push(...collectRecords(child, depth + 1));
	}
	return records;
}

function collectTexts(value: unknown, depth = 0): string[] {
	if (depth > 8 || value === null || value === undefined) return [];
	const parsed = parseJson(value);
	if (typeof parsed === 'string') {
		if (parsed !== value) return collectTexts(parsed, depth + 1);
		return parsed.trim() ? [parsed.trim()] : [];
	}
	if (Array.isArray(parsed)) return parsed.flatMap((item) => collectTexts(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return typeof parsed === 'number' ? [String(parsed)] : [];
	return Object.entries(record).flatMap(([key, child]) => SECRET_KEY.test(key) ? [] : collectTexts(child, depth + 1));
}

function uniqueTexts(texts: string[]): string[] {
	return [...new Set(texts.map((text) => text.trim()).filter(Boolean))];
}

function directField(record: DataRecord, aliases: string[]): string | null {
	const wanted = new Set(aliases.map(normalizeKey));
	for (const [key, value] of Object.entries(record)) {
		if (SECRET_KEY.test(key) || !wanted.has(normalizeKey(key))) continue;
		const cleaned = cleanDisplayText(value);
		if (cleaned) return cleaned;
	}
	return null;
}

function parseTableRecords(text: string): DataRecord[] {
	const lines = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line.includes('|'));
	const records: DataRecord[] = [];
	for (let index = 0; index < lines.length - 1; index += 1) {
		const headers = lines[index].replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/^:?-{2,}:?$/, ''));
		const separator = lines[index + 1].replace(/^\|/, '').replace(/\|$/, '').split('|').every((cell) => /^\s*:?-{2,}:?\s*$/.test(cell));
		if (!separator || headers.length < 2) continue;
		for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
			const cells = lines[rowIndex].replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map((cell) => cell.trim());
			if (cells.length !== headers.length) break;
			if (cells.every((cell) => /^:?-{2,}:?$/.test(cell))) break;
			const record: DataRecord = {};
			headers.forEach((header, cellIndex) => { record[header.replace(/\\\|/g, '|')] = cells[cellIndex].replace(/\\\|/g, '|'); });
			records.push(record);
		}
	}
	return records;
}

function recordsFromPayloads(payloads: unknown[]): DataRecord[] {
	const records = payloads.flatMap((payload) => collectRecords(payload));
	for (const text of uniqueTexts(payloads.flatMap((payload) => collectTexts(payload)))) {
		records.push(...parseTableRecords(text), ...labelledValues(text));
	}
	return records;
}

function recordsWithTextBlocks(payloads: unknown[]): DataRecord[] {
	const texts = uniqueTexts(payloads.flatMap((payload) => collectTexts(payload)));
	return [
		...payloads.flatMap((payload) => collectRecords(payload)),
		...texts.flatMap((text) => parseTableRecords(text)),
		...texts.flatMap((text) => text.split(/\n\s*\n/).flatMap((block) => labelledValues(block))),
	];
}

function labelledValues(text: string): DataRecord[] {
	const record: DataRecord = {};
	for (const line of text.split(/\r?\n/)) {
		const match = line.match(/^\s*(?:[-*•]\s*)?(?:\*\*)?([^|:：*]{2,40}?)(?:[:：]\*\*|\*\*[:：]|[:：])\s*(.+)$/);
		if (match) record[match[1].replace(/[`*_#]/g, '').trim()] = match[2].replace(/\*\*$/, '').trim();
	}
	return Object.keys(record).length ? [record] : [];
}

function extractSection(text: string, headings: RegExp): string | null {
	const lines = text.split(/\r?\n/);
	const headingText = (line: string) => line.replace(/^\s*#{1,6}\s*/, '').replace(/^\s*>\s*/, '').replace(/^\s*\*\*/, '').replace(/\*\*\s*$/, '').replace(/[*`]/g, '').replace(/^\s*(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*/, '').replace(/[（(].*$/, '').replace(/(?:与|及|和).*/, '').trim();
	const start = lines.findIndex((line) => headings.test(headingText(line)));
	if (start < 0) return null;
	const markdownHeadingLevel = (line: string) => line.match(/^\s*(#{1,6})\s+/)?.[1].length;
	const isBoldHeading = (line: string) => /^\s*\*\*[^*].*\*\*\s*$/.test(line);
	const startLevel = markdownHeadingLevel(lines[start]) ?? (isBoldHeading(lines[start]) ? 2 : 0);
	const content: string[] = [];
	for (let index = start + 1; index < lines.length; index += 1) {
		const level = markdownHeadingLevel(lines[index]) ?? (isBoldHeading(lines[index]) ? 2 : undefined);
		if (level !== undefined && (!startLevel || level <= startLevel)) break;
		content.push(lines[index]);
	}
	return content.join('\n').trim() || null;
}

function sectionsFromTexts(texts: string[], headings: RegExp): string[] {
	return uniqueTexts(texts.map((text) => extractSection(text, headings)).filter((section): section is string => Boolean(section)));
}

function extractBasicFields(run: TaskRunRecord, profileNode?: NodeRunRecord, cardNode?: NodeRunRecord): { fields: Array<{ label: string; value: string }>; excerpt: string | null } {
	const templateArguments = run.nodes.flatMap((node) => {
		const config = asRecord(node.config);
		return config?.type === 'tool' && config.arguments ? [config.arguments] : [];
	});
	const sourceGroups: unknown[][] = [
		[parseJson(run.input)],
		nodePayloads(profileNode, true),
		run.node_runs.map((node) => parseJson(node.input)),
		templateArguments,
		nodePayloads(cardNode),
		[parseJson(run.final_output), parseJson(run.final_summary)],
		...run.artifacts.map((artifact) => [parseJson(artifact.preview_text)]),
	];
	const recordsBySource = sourceGroups.map(recordsFromPayloads);
	const fields: Array<{ label: string; value: string }> = [];
	for (const field of BASIC_FIELDS) {
		const value = recordsBySource.flatMap((records) => records.map((record) => directField(record, field.aliases))).find(Boolean);
		if (value) fields.push({ label: field.label, value });
	}
	if (fields.length) return { fields, excerpt: null };
	const profileTexts = sourceGroups.flatMap((group) => group.flatMap((payload) => collectTexts(payload)));
	for (const section of sectionsFromTexts(profileTexts, PROFILE_HEADINGS)) {
		const excerpt = safeProseSummary(section, 180);
		if (excerpt) return { fields, excerpt };
	}
	const profileNodeTexts = nodePayloads(profileNode, true).flatMap((payload) => collectTexts(payload));
	for (const text of profileNodeTexts) {
		const excerpt = safeProseSummary(text, 180);
		if (excerpt) return { fields, excerpt };
	}
	return { fields, excerpt: null };
}

function redactRawText(value: string): string {
 return value
  .replace(/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi, 'Bearer [已脱敏]')
  .replace(/(authorization|access.?token|refresh.?token|secret|password|api.?key)\s*[:=：]\s*(?:"[^"]*"|'[^']*'|[^\s,，;；]+)/gi, '$1: [已脱敏]')
  .replace(/(https?:\/\/[^\s?#)]+)\?[^\s#)]*/gi, '$1?[查询参数已脱敏]');
}

function redactRawValue(value: unknown, depth = 0): unknown {
 if (depth > 8) return '[内容层级过深]';
 if (typeof value === 'string') return redactRawText(value);
 if (Array.isArray(value)) return value.map((item) => redactRawValue(item, depth + 1));
 const record = asRecord(value);
 if (!record) return value;
 return Object.fromEntries(Object.entries(record).map(([key, child]) => [
  key,
  SECRET_KEY.test(key) ? '[已脱敏]' : redactRawValue(child, depth + 1),
 ]));
}

function rawTextFromPayload(payload: unknown, depth = 0): string | null {
 if (depth > 8 || payload === null || payload === undefined || payload === '') return null;
 const parsed = parseJson(payload);
 if (typeof parsed === 'string') {
  if (parsed !== payload) return rawTextFromPayload(parsed, depth + 1);
  return redactRawText(parsed).trim() || null;
 }
 const record = asRecord(parsed);
 if (record) {
  for (const key of ['markdown', 'content', 'text', 'output', 'report', 'final_output', 'display_summary']) {
   const candidate = rawTextFromPayload(record[key], depth + 1);
   if (candidate) return candidate;
  }
  const chunks = Array.isArray(record.chunks) ? record.chunks
   .map((chunk) => rawTextFromPayload(asRecord(parseJson(chunk))?.text ?? chunk, depth + 1))
   .filter((text): text is string => Boolean(text)) : [];
  if (chunks.length) return chunks.join('\n\n');
 }
 const safeValue = redactRawValue(parsed);
 if (typeof safeValue === 'number' || typeof safeValue === 'boolean') return String(safeValue);
 if (Array.isArray(safeValue) || asRecord(safeValue)) {
  try {
   return JSON.stringify(safeValue, null, 2);
  } catch {
   return null;
  }
 }
 return null;
}

function fallbackTextFromPayloads(payloads: unknown[]): string | null {
 for (const payload of payloads) {
  const text = rawTextFromPayload(payload);
  if (text) return text;
 }
 return null;
}

function nodeFallbackPayloads(nodes: NodeRunRecord[]): unknown[] {
	return nodes.flatMap((node) => {
		const result = asRecord(parseJson(node.result));
		const chunks = Array.isArray(result?.chunks) ? result.chunks.flatMap((chunk) => {
			if (typeof chunk === 'string') return [chunk];
			const item = asRecord(parseJson(chunk));
			return typeof item?.text === 'string' ? [item.text] : [];
		}) : [];
		return [node.output, node.display_summary, ...chunks, parseJson(node.result)].filter((payload) => payload !== undefined && payload !== null && payload !== '');
	});
}

function markdownReportPayloads(run: TaskRunRecord, cardNode?: NodeRunRecord): string[] {
	const candidates = [
		run.final_output,
		run.final_summary,
		cardNode?.output,
		cardNode?.display_summary,
		...run.artifacts.map((artifact) => artifact.preview_text),
	];
	return candidates.filter((candidate): candidate is string =>
		typeof candidate === 'string' &&
		Boolean(candidate.trim()) &&
		parseJson(candidate) === candidate &&
		/(?:^|\n)\s*(?:#{1,6}\s|>\s|\|[^|\n]+\||[-*]\s+)/m.test(candidate),
	);
}

function sentenceSummary(value: unknown, maxLength: number): string | null {
	const text = cleanDisplayText(value);
	if (!text) return null;
	if (text.length <= maxLength) return text;
	const sentences = text.match(/[^。！？!?]+[。！？!?]?/g) ?? [];
	const chosen: string[] = [];
	for (const sentence of sentences) {
		const next = sentence.trim();
		if (!next) continue;
		if (next.length > maxLength) {
			if (!chosen.length && next.length <= maxLength + 40) return next;
			break;
		}
		if (chosen.join('').length + next.length > maxLength) break;
		chosen.push(next);
		if (/[。！？!?]$/.test(next)) break;
	}
	const result = chosen.join('').trim();
	return result && (result.length < text.length || result.length <= maxLength) ? result : null;
}

function statusIsPending(record: DataRecord, text: string): boolean {
	const status = directField(record, ['status', 'verification_status', 'pending_status', '状态', '核实状态']) ?? '';
	return /待核实|待确认|待补充|候选|pending|unverified|candidate/i.test(`${status} ${text}`);
}

function validCaseName(value: string | null): value is string {
	return Boolean(value && value.length <= 100 && !/\.(md|docx?|xlsx?|pdf)$/i.test(value) && !/(?:source|文件名|file_name|document|evidence|chunk|hash|来源)/i.test(value) && !TECHNICAL_TEXT.test(value));
}

function addCase(cases: ProjectCase[], item: ProjectCase): void {
	if (!cases.some((existing) => existing.name === item.name && existing.reason === item.reason)) cases.push(item);
}

function caseFromRecord(record: DataRecord): ProjectCase | null {
	let name = directField(record, CASE_NAME_KEYS);
	const reasonText = directField(record, CASE_REASON_KEYS);
	if (!name && reasonText) name = directField(record, ['name', 'title', '案例', '项目']);
	if (!validCaseName(name)) return null;
	const reason = sentenceSummary(reasonText, 110);
	if (!reason) return null;
	const experience = sentenceSummary(directField(record, CASE_EXPERIENCE_KEYS), 90) ?? undefined;
	return { name, reason, experience, pending: statusIsPending(record, `${name} ${reason}`) };
}

function caseSummaryFromRecord(record: DataRecord): ProjectCase | null {
	const name = directField(record, CASE_NAME_KEYS);
	if (validCaseName(name)) return null;
	const summaryText = directField(record, ['historical_case_summary', 'similar_project_summary', 'case_summary', 'historical_summary', '历史案例摘要', '历史案例参考摘要', '案例摘要', '相似项目摘要']);
	const reason = sentenceSummary(summaryText, 120);
	if (!reason) return null;
	return { name: '历史案例参考摘要', reason, experience: sentenceSummary(directField(record, CASE_EXPERIENCE_KEYS), 90) ?? undefined, pending: statusIsPending(record, reason) };
}

function headingCaseName(line: string): string | null {
	const clean = line.replace(/^\s*#{1,6}\s*/, '').replace(/[*`]/g, '').trim();
	const match = clean.match(/^(?:案例|历史案例|项目案例)\s*(?:[一二三四五六七八九十\d]+)?\s*[:：]\s*(.+)$/);
	return match?.[1]?.trim() ?? null;
}

function caseBlocks(section: string): string[] {
	const lines = section.split(/\r?\n/);
	const blocks: string[] = [];
	let current: string[] = [];
	for (const line of lines) {
		if (headingCaseName(line) && current.length) {
			blocks.push(current.join('\n').trim());
			current = [line];
		} else {
			current.push(line);
		}
	}
	if (current.length) blocks.push(current.join('\n').trim());
	const nonEmpty = blocks.filter(Boolean);
	return nonEmpty.some((block) => block.split(/\r?\n/).some((line) => headingCaseName(line)))
		? nonEmpty
		: section.split(/\n\s*\n/).map((block) => block.trim()).filter(Boolean);
}

function extractSimilarCases(nodes: NodeRunRecord[], reportTexts: string[]): ProjectCase[] {
	const cases: ProjectCase[] = [];
	const retrievalPayloads = nodes.flatMap((node) => nodePayloads(node));
	for (const record of recordsWithTextBlocks(retrievalPayloads)) {
		const item = caseFromRecord(record) ?? caseSummaryFromRecord(record);
		if (item) addCase(cases, item);
		if (cases.length >= 3) return cases;
	}
	const caseSections = sectionsFromTexts([...nodes.flatMap((node) => nodePayloads(node).flatMap((payload) => collectTexts(payload))), ...reportTexts], CASE_HEADINGS);
	for (const section of caseSections) {
		const sectionRecords = recordsWithTextBlocks([section]);
		for (const record of sectionRecords) {
			const item = caseFromRecord(record) ?? caseSummaryFromRecord(record);
			if (item) addCase(cases, item);
			if (cases.length >= 3) return cases;
		}
		const blocks = caseBlocks(section);
		for (const block of blocks) {
			const record = labelledValues(block)[0] ?? {};
			const name = directField(record, CASE_NAME_KEYS) ?? block.split(/\r?\n/).map(headingCaseName).find(Boolean) ?? null;
			const reason = sentenceSummary(directField(record, CASE_REASON_KEYS) ?? proseForSummary(block), 110);
			if (validCaseName(name) && reason) {
				addCase(cases, { name, reason, experience: sentenceSummary(directField(record, CASE_EXPERIENCE_KEYS), 90) ?? undefined, pending: statusIsPending(record, `${name} ${reason}`) });
			} else if (!name) {
				const summary = safeProseSummary(block, 120);
				if (summary && /(?:历史案例|相似|项目|案例|实施|建设|改造|平台|系统)/i.test(block)) {
					addCase(cases, { name: '历史案例参考摘要', reason: summary, pending: statusIsPending(record, block) });
				}
			}
			if (cases.length >= 3) return cases;
		}
	}
	return cases;
}

function ruleItemsFromRecords(records: DataRecord[], items: RuleItem[], max = 5): void {
	for (const record of records) {
		let name = directField(record, RULE_NAME_KEYS);
		const requirementText = directField(record, RULE_TEXT_KEYS);
		if (!name && requirementText) name = '立项要求';
		if (!name || !requirementText) continue;
		const requirement = sentenceSummary(requirementText, 120);
		if (!requirement || name.length > 90 || /\.(md|docx?|xlsx?|pdf)$/i.test(name) || TECHNICAL_TEXT.test(name)) continue;
		const item = { name, requirement, pending: statusIsPending(record, `${name} ${requirement}`) };
		if (!items.some((existing) => existing.name === item.name && existing.requirement === item.requirement)) items.push(item);
		if (items.length >= max) return;
	}
}

function proseForSummary(value: string): string | null {
	const lines = value.split(/\r?\n/)
		.map((line) => line.trim())
		.filter((line) => line && !/^#{1,6}\s/.test(line) && !/^\s*\|/.test(line) && !/^```/.test(line) && !isDisclosure(line) && !TECHNICAL_TEXT.test(line));
	return lines.map((line) => line.replace(/^[-*•]\s*/, '').replace(/^\d+[.、)]\s*/, '')).join(' ').trim() || null;
}

function extractRuleItems(nodes: NodeRunRecord[], reportTexts: string[]): RuleItem[] {
	const items: RuleItem[] = [];
	const retrievalPayloads = nodes.flatMap((node) => nodePayloads(node));
	ruleItemsFromRecords(recordsWithTextBlocks(retrievalPayloads), items);
	if (items.length >= 5) return items;
	const ruleSections = sectionsFromTexts([...nodes.flatMap((node) => nodePayloads(node).flatMap((payload) => collectTexts(payload))), ...reportTexts], RULE_HEADINGS);
	for (const section of ruleSections) {
		ruleItemsFromRecords(recordsWithTextBlocks([section]), items);
		if (items.length >= 5) return items;
		for (const line of section.split(/\r?\n/)) {
			const candidate = proseForSummary(line);
			if (!candidate || !/(依据|立项|评审|规则|约束|要求|必须|应当|不得|需满足|待核实)/i.test(candidate)) continue;
			const labelled = line.match(/^\s*(?:[-*•]\s*)?(?:\*\*)?([^:：*]{2,24})(?:[:：]\*\*|\*\*[:：]|[:：])\s*(.+)$/);
			const name = labelled?.[1]?.replace(/[`*_#]/g, '').trim() ?? '关键要求';
			const requirement = sentenceSummary(labelled?.[2] ?? candidate, 120);
			if (!requirement || TECHNICAL_TEXT.test(name)) continue;
			const item = { name, requirement, pending: /待核实|待确认|待补充|pending|unverified/i.test(line) };
			if (!items.some((existing) => existing.name === item.name && existing.requirement === item.requirement)) items.push(item);
			if (items.length >= 5) return items;
		}
	}
	return items;
}

function stringsForKeys(records: DataRecord[], keys: string[], maxLength = 150): string[] {
	const wanted = new Set(keys.map(normalizeKey));
	const values: string[] = [];
	for (const record of records) {
		for (const [key, value] of Object.entries(record)) {
			if (!wanted.has(normalizeKey(key)) || SECRET_KEY.test(key)) continue;
			const parsed = parseJson(value);
			const candidates = Array.isArray(parsed) ? parsed : [parsed];
			for (const candidate of candidates) {
				const text = safeProseSummary(candidate, maxLength);
				if (text && !values.includes(text)) values.push(text);
			}
		}
	}
	return values;
}

function safeProseSummary(value: unknown, maxLength: number): string | null {
	const raw = scalarText(value) ?? '';
	if (
		/^(?:\{|\[|```)/.test(raw.trim()) ||
		/^\s*\|/m.test(raw) ||
		/\b(?:claims|bindings|unverified_items|task-claim-evidence|evidence_id|document_id)\b/i.test(raw)
	) return null;
	const cleanedSource = proseForSummary(raw);
	if (!cleanedSource) return null;
	const cleaned = cleanDisplayText(cleanedSource);
	return cleaned && !isDisclosure(cleaned) ? sentenceSummary(cleaned, maxLength) : null;
}

function extractAdviceBullets(text: string | undefined): string[] {
	if (!text) return [];
	return text.split(/\r?\n/)
		.map((line) => line.match(/^\s*(?:[-*•]|\d+[.、])\s+(.+)$/)?.[1])
		.filter((value): value is string => Boolean(value))
		.map((value) => safeProseSummary(value, 120))
		.filter((value): value is string => Boolean(value));
}

function advicePayload(run: TaskRunRecord, adviceNode?: NodeRunRecord): { summary: string | null; fallback: string | null; suggestions: string[]; pendingCount: number | null } {
	const cardNode = findNode(run, /立项信息卡|输出立项卡/);
	const advicePayloads = nodePayloads(adviceNode);
	const cardPayloads = nodePayloads(cardNode);
	const reportPayloads = [parseJson(run.final_output), parseJson(run.final_summary), ...run.artifacts.map((artifact) => parseJson(artifact.preview_text))];
	const sourcePayloads = [advicePayloads, cardPayloads, reportPayloads];
	const recordsBySource = sourcePayloads.map(recordsFromPayloads);
	const explicitSummaryKeys = ['advice_summary', 'recommendation_summary', 'core_recommendation', '立项建议摘要', '建议摘要', '建议结论', '核心建议'];
	const summary = recordsBySource.flatMap((records) => stringsForKeys(records, explicitSummaryKeys, 160)).find((item) => item.length >= 20)
		?? stringsForKeys(recordsBySource[0], ['summary', '摘要', '结论'], 160).find((item) => item.length >= 20)
		?? stringsForKeys(recordsBySource[1], ['summary', '摘要', '结论'], 160).find((item) => item.length >= 20)
		?? stringsForKeys(recordsBySource[2], ['summary', '摘要', '结论'], 160).find((item) => item.length >= 20);
	const textSources = sourcePayloads.flatMap((group) => group.flatMap((payload) => collectTexts(payload)));
	const adviceSections = sectionsFromTexts(textSources, ADVICE_HEADINGS);
	const paragraphSummary = adviceSections.map((section) => safeProseSummary(section, 160)).find(Boolean) ?? null;
	const fallback = fallbackTextFromPayloads([...adviceSections, adviceNode?.output, adviceNode?.display_summary, cardNode?.output, run.final_output]);
	const nodeAdviceFallback = safeProseSummary(adviceNode?.output, 160);
	const finalSummaryFallback = /建议|推荐|优先|风险|推进|立项条件/.test(run.final_summary) ? safeProseSummary(run.final_summary, 160) : null;
	const resolvedSummary = summary ?? paragraphSummary ?? (nodeAdviceFallback && nodeAdviceFallback.length >= 20 ? nodeAdviceFallback : null) ?? (finalSummaryFallback && finalSummaryFallback.length >= 20 ? finalSummaryFallback : null);
	const suggestionCandidates = [
		...recordsBySource.flatMap((records) => stringsForKeys(records, ['key_recommendations', 'recommendations', '关键建议', '建议事项', '改进建议'], 120)),
		...adviceSections.flatMap((section) => extractAdviceBullets(section)),
		...extractAdviceBullets(adviceNode?.output),
	];
	const suggestions = suggestionCandidates.filter((item, index) => suggestionCandidates.indexOf(item) === index).slice(0, 3);
	const pendingItems = recordsBySource.flat().map((record) => {
		for (const [key, value] of Object.entries(record)) {
			if (/^(unverifieditems|待核实事项|待核实清单)$/i.test(normalizeKey(key)) && Array.isArray(value)) return value.length;
		}
		return null;
	}).find((value) => value !== null) ?? null;
	const countText = textSources.join('\n');
	const countMatch = countText.match(/(?:待核实(?:事项)?\s*(?:共|数量|：|:)\s*(\d+)\s*(?:项|条)?|有\s*(\d+)\s*项待核实)/);
	const pendingCount = pendingItems ?? (countMatch ? Number(countMatch[1] ?? countMatch[2]) : null);
	return { summary: resolvedSummary, fallback, suggestions, pendingCount };
}

function ResultCard({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
	return (
  <section className={`flex h-[320px] min-h-[320px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 ${TASK_RESULT_CARD_CLASS}`}>
   <header className="mb-3 flex shrink-0 items-center gap-2 text-sm font-semibold text-slate-800">
				<span className="flex size-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600">{icon}</span>
				{title}
			</header>
   <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden pr-1">{children}</div>
		</section>
	);
}

function EmptyHint({ children }: { children: React.ReactNode }) {
	return <p className="text-xs leading-5 text-slate-500">{children}</p>;
}

function RawFallback({ text }: { text: string }) {
	return (
  <div className="min-w-0">
   <Markdown
    animated={false}
    className="prose prose-slate prose-sm max-w-none break-words leading-5 prose-headings:my-2 prose-headings:text-xs prose-p:my-2 prose-p:text-xs prose-li:my-1 prose-li:text-xs prose-th:text-xs prose-td:text-xs prose-pre:overflow-x-auto prose-pre:rounded-lg prose-table:block prose-table:max-w-full prose-table:overflow-x-auto"
   >
			{text}
   </Markdown>
		</div>
	);
}

export function TaskS01Results({
	run,
	onViewExecution,
	onViewFullResult,
}: {
	run: TaskRunRecord;
	onViewExecution: () => void;
	onViewFullResult: () => void;
}) {
	const profileNode = findNode(run, /识别立项字段|项目画像/);
	const cardNode = findNode(run, /输出立项信息卡|输出立项卡/);
	const similarNodes = run.node_runs.filter((node) => /project\.similar_search/.test(nodeLabel(node)) || /检索相似项目|相似项目检索/.test(nodeLabel(node)));
	const ruleNodes = run.node_runs.filter((node) => /project\.rule_check/.test(nodeLabel(node)) || /核对立项规则|立项规则/.test(nodeLabel(node)));
	const adviceNode = findNode(run, /形成立项建议|立项建议/);
	const profile = extractBasicFields(run, profileNode, cardNode);
	const fields = profile.fields;
	const reportTexts = [run.final_output, run.final_summary, ...nodePayloads(cardNode), ...run.artifacts.map((artifact) => artifact.preview_text)]
		.flatMap((payload) => collectTexts(payload));
	const similarCases = extractSimilarCases(similarNodes, reportTexts);
	const ruleItems = extractRuleItems(ruleNodes, reportTexts);
	const reportMarkdownFallbacks = markdownReportPayloads(run, cardNode);
	const profileFallback = fallbackTextFromPayloads([
		profileNode?.output,
		profileNode?.display_summary,
		parseJson(profileNode?.result),
		run.input,
		...run.node_runs.map((node) => node.input),
		cardNode?.output,
		run.final_output,
		...run.artifacts.map((artifact) => artifact.preview_text),
	]) ?? profile.excerpt;
	const similarSectionFallbacks = sectionsFromTexts([...nodeFallbackPayloads(similarNodes).flatMap((payload) => collectTexts(payload)), ...reportTexts], CASE_HEADINGS);
	const similarFallback = fallbackTextFromPayloads([...similarSectionFallbacks, ...reportMarkdownFallbacks, ...nodeFallbackPayloads(similarNodes)]);
	const ruleSectionFallbacks = sectionsFromTexts([...nodeFallbackPayloads(ruleNodes).flatMap((payload) => collectTexts(payload)), ...reportTexts], RULE_HEADINGS);
	const ruleFallback = fallbackTextFromPayloads([...ruleSectionFallbacks, ...reportMarkdownFallbacks, ...nodeFallbackPayloads(ruleNodes)]);
	const advice = advicePayload(run, adviceNode);
	const hasArtifacts = run.artifacts.length > 0;

	return (
		<div className="mx-auto flex w-full max-w-7xl flex-col gap-4 pb-2">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<h2 className="text-lg font-semibold text-slate-900">项目立项成果</h2>
					<p className="mt-1 text-xs text-slate-500">从本次 TaskRun 结果中提取的关键摘要</p>
				</div>
				<Button type="button" size="sm" variant="outline" onClick={onViewExecution}>
					查看执行过程 <ArrowUpRight className="ml-1 size-3.5" />
				</Button>
			</div>

			<div className="grid items-start gap-4 xl:grid-cols-2">
				<ResultCard title="立项知识卡片" icon={<BookOpen className="size-4" />}>
					{fields.length ? (
						<dl className="grid grid-cols-2 gap-x-4 gap-y-3">
							{fields.map((field) => (
								<div key={field.label} className={field.label === '项目主要目标' ? 'col-span-2' : ''}>
									<dt className="text-[11px] text-slate-500">{field.label}</dt>
									<dd className="mt-0.5 break-words text-xs font-medium leading-5 text-slate-800">{field.value}</dd>
								</div>
							))}
						</dl>
					) : profileFallback ? <RawFallback text={profileFallback} /> : <EmptyHint>本次结果未包含可识别的项目基本信息。</EmptyHint>}
				</ResultCard>

				<ResultCard title="相似项目推荐" icon={<FileSearch className="size-4" />}>
					{similarCases.length ? (
						<div className="space-y-2.5">
							{similarCases.map((item, index) => (
								<div key={`${item.name}-${index}`} className="border-b border-slate-100 pb-2.5 last:border-0 last:pb-0">
									<div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-800">
										<span>{item.name}</span>
										{item.pending && <span className="rounded-full border border-amber-200 px-1.5 py-0.5 text-[10px] font-normal text-amber-700">待核实</span>}
									</div>
									<p className="mt-1 text-[11px] leading-4 text-slate-600">{item.reason}</p>
									{item.experience && <p className="mt-1 text-[11px] leading-4 text-slate-500">可借鉴：{item.experience}</p>}
								</div>
							))}
						</div>
					) : similarFallback ? <RawFallback text={similarFallback} /> : <EmptyHint>本次检索结果未识别到具备项目名称和相似原因的历史案例。详细案例请查看完整立项报告。</EmptyHint>}
				</ResultCard>

				<ResultCard title="立项依据参考" icon={<ListChecks className="size-4" />}>
					{ruleItems.length ? (
						<div className="space-y-2.5">
							{ruleItems.map((item, index) => (
								<div key={`${item.name}-${index}`} className="flex items-start gap-2">
									<span className="mt-1 size-1.5 shrink-0 rounded-full bg-blue-500" />
									<div className="min-w-0">
										<div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-800">
											<span>{item.name}</span>
											{item.pending && <span className="rounded-full border border-amber-200 px-1.5 py-0.5 text-[10px] font-normal text-amber-700">待核实</span>}
										</div>
										<p className="mt-0.5 text-[11px] leading-4 text-slate-600">{item.requirement}</p>
									</div>
								</div>
							))}
						</div>
					) : ruleFallback ? <RawFallback text={ruleFallback} /> : <EmptyHint>本次结果未提取到可简明展示的规则要求，请查看完整立项报告。</EmptyHint>}
				</ResultCard>

				<ResultCard title="AI 立项建议" icon={<Lightbulb className="size-4" />}>
					{advice.summary ? <p className="text-xs leading-5 text-slate-700">{advice.summary}</p> : advice.fallback ? <RawFallback text={advice.fallback} /> : <EmptyHint>本次结果未提取到可靠的建议摘要，请查看完整结果。</EmptyHint>}
					{advice.suggestions.length > 0 && (
						<ul className="mt-3 space-y-1.5 border-t border-slate-100 pt-2.5">
							{advice.suggestions.map((suggestion, index) => <li key={`${index}-${suggestion}`} className="flex gap-2 text-[11px] leading-4 text-slate-600"><span className="font-semibold text-blue-600">{index + 1}.</span><span>{suggestion}</span></li>)}
						</ul>
					)}
					{advice.pendingCount !== null && advice.pendingCount > 0 && <div className="mt-3 text-[11px] text-amber-700">待核实事项 {advice.pendingCount} 项</div>}
				</ResultCard>
			</div>

			<footer className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3">
			<p className="text-xs leading-5 text-slate-600">以上为本次任务的关键成果摘要，完整内容请查看成果文件。</p>
				<Button type="button" size="sm" variant="outline" onClick={onViewFullResult}>
					{hasArtifacts ? '查看成果文件' : '查看完整结果'} <ArrowUpRight className="ml-1 size-3.5" />
				</Button>
			</footer>
		</div>
	);
}
