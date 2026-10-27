import type { NodeRunRecord, TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;

export interface S05MeetingField {
	label: string;
	value: string;
}

export interface S05MeetingOpinion {
	speaker: string | null;
	text: string;
	type: string | null;
	dissent: boolean;
}

export interface S05MeetingConclusion {
	text: string;
	kind: 'decision' | 'discussion';
}

export type S05ActionStatus = 'complete' | 'pending' | 'incomplete' | null;

export interface S05MeetingAction {
	text: string;
	owner: string | null;
	deadline: string | null;
	status: S05ActionStatus;
}

export interface S05MeetingResult {
	overviewFields: S05MeetingField[];
	transcriptSource: string | null;
	minutesSummary: string | null;
	discussionPoints: string[];
	minutesDraftLabel: string | null;
	opinions: S05MeetingOpinion[];
	conclusions: S05MeetingConclusion[];
	actions: S05MeetingAction[];
	markdown: string | null;
	markdownSections: {
		overview: string | null;
		minutes: string | null;
		opinions: string | null;
		outcomes: string | null;
	};
	artifacts: TaskArtifactRecord[];
	hasStructuredContent: boolean;
	hasFullResult: boolean;
}

interface MarkdownSection {
	title: string;
	body: string;
}

interface ArrayField {
	key: string;
	value: unknown;
}

const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;
const MARKDOWN_KEYS = new Set([
	'markdown', 'reportmarkdown', 'report', 'content', 'text', 'body', 'documentcontent',
	'finaloutput', 'finalsummary', 'summary', 'output', 'result', 'meetingminutes',
	'minutes', 'minutescontent', 'minutesmarkdown', 'meetingminutesmarkdown',
]);
const SUMMARY_KEYS = ['meeting_summary', 'minutes_summary', 'summary', 'abstract', 'overview', 'summary_text', '会议摘要', '纪要摘要', '核心摘要', '摘要'];
const DISCUSSION_POINT_KEYS = [
	'key_points', 'key_discussion_points', 'discussion_points', 'agenda_summaries', 'topic_summaries',
	'key_discussions', '议题要点', '讨论要点', '关键讨论', '议题摘要',
];
const OPINION_ARRAY_KEYS = [
	'speaker_opinions', 'expert_opinions', 'participant_opinions', 'meeting_opinions',
	'发言观点', '专家意见', '参会人员意见', '会议观点摘要',
];
const DECISION_ARRAY_KEYS = ['decisions', 'meeting_decisions', 'conclusions', 'key_conclusions', 'resolutions', '会议决定', '会议结论', '形成共识'];
const DISCUSSION_SUGGESTION_KEYS = ['discussion_suggestions', 'suggestions', 'discussion_points_to_confirm', '讨论建议', '待讨论事项'];
const ACTION_ARRAY_KEYS = ['action_items', 'todo_items', 'follow_up_actions', 'meeting_actions', '待办事项', '会后待办', '后续行动'];

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
	if (typeof parsed === 'string') return parsed === value ? [] : recordsIn(parsed, depth + 1);
	if (Array.isArray(parsed)) return parsed.flatMap((item) => recordsIn(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return [record, ...Object.entries(record).flatMap(([key, child]) =>
		SECRET_KEY.test(key) ? [] : recordsIn(child, depth + 1),
	)];
}

function textualLeaves(value: unknown, depth = 0): string[] {
	if (depth > 8 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (typeof parsed === 'string') return parsed === value ? [parsed] : textualLeaves(parsed, depth + 1);
	if (Array.isArray(parsed)) return parsed.flatMap((item) => textualLeaves(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return Object.entries(record).flatMap(([key, child]) => SECRET_KEY.test(key) ? [] : textualLeaves(child, depth + 1));
}

function scalarText(value: unknown): string | null {
	if (typeof value === 'string' || typeof value === 'number') return String(value).trim() || null;
	if (Array.isArray(value)) return value.map(scalarText).filter((item): item is string => Boolean(item)).join('、') || null;
	const record = asRecord(value);
	if (!record) return null;
	for (const key of ['text', 'content', 'summary', 'title', 'label', 'name', 'value', 'amount']) {
		const text = scalarText(record[key]);
		if (text) return text;
	}
	return null;
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
	const value = directValue(records, aliases);
	return value === undefined ? null : scalarText(value);
}

function cleanDisplayText(value: string): string | null {
	const text = value
		.replace(/<!--.*?-->/gs, ' ')
		.replace(/```[\s\S]*?```/g, ' ')
		.replace(/\[([^\]]*)\]\((?:https?:\/\/|\/)[^)]+\)/gi, '$1')
		.replace(/https?:\/\/\S+/gi, ' ')
		.replace(/\b(?:EV[-_:# ]?[A-Z0-9-]{6,}|[a-f0-9]{64})\b/gi, ' ')
		.replace(/[`*_>#]/g, '')
		.replace(/^\s*(?:[-*+•]|\d+[.)、])\s*/, '')
		.replace(/\s+/g, ' ')
		.trim();
	if (!text || /^(?:null|undefined|n\/a|unknown|待确认|待核实|未提供|无|暂无|—|-)$/i.test(text) || /^(?:\{|\[)/.test(text)) return null;
	return text;
}

function usableText(value: unknown): string | null {
	const text = scalarText(value);
	return text ? cleanDisplayText(text) : null;
}

function isSourceOrDisclaimer(text: string): boolean {
	return /(?:DEMO\s*[/_-]\s*SYNTHETIC|演示数据|数据状态|来源状态|来源标记|仅供演示|不代表实际|检索时间|evidence[_ -]?id|chunk[_ -]?id|document[_ -]?id)/i.test(text);
}

function conciseSummary(value: string, maxLength = 120): string | null {
	const paragraphs = value.split(/\n\s*\n/).map((item) => cleanDisplayText(item)).filter((item): item is string => Boolean(item));
	for (const paragraph of paragraphs) {
		if (isSourceOrDisclaimer(paragraph)) continue;
		const sentence = paragraph.match(/^.*?[。！？!?；](?:\s|$)/)?.[0]?.trim();
		const candidate = sentence && [...sentence].length <= maxLength ? sentence : [...paragraph].length <= maxLength ? paragraph : null;
		if (candidate) return candidate;
	}
	return null;
}

function listValue(value: unknown): string[] {
	const parsed = parsePayload(value);
	if (Array.isArray(parsed)) return parsed.map(usableText).filter((item): item is string => Boolean(item));
	if (typeof parsed !== 'string') {
		const text = usableText(parsed);
		return text ? [text] : [];
	}
	return parsed.split(/\r?\n|[;；]/)
		.map((item) => usableText(item))
		.filter((item): item is string => Boolean(item));
}

function parseLabelledText(text: string): DataRecord[] {
	const record: DataRecord = {};
	for (const line of text.split(/\r?\n/)) {
		const match = line.match(/^\s*(?:[-*•]\s*)?(?:\*\*)?([^|:：*]{2,48}?)(?:\*\*)?\s*[:：]\s*(.+)$/);
		if (match) record[match[1].replace(/[`*_#]/g, '').trim()] = match[2].replace(/\*\*$/, '').trim();
	}
	return Object.keys(record).length ? [record] : [];
}

function markdownTableRecords(text: string): DataRecord[] {
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

function recordsFromPayloads(payloads: unknown[]): DataRecord[] {
	const nested = payloads.flatMap((payload) => recordsIn(payload));
	const textRecords = payloads.flatMap((payload) => textualLeaves(payload)).flatMap((text) => [
		...parseLabelledText(text),
		...markdownTableRecords(text),
	]);
	return [...nested, ...textRecords];
}

function nodeDefinition(run: TaskRunRecord, nodeRun: NodeRunRecord) {
	return run.nodes.find((node) => node.id === nodeRun.node_id);
}

function successfulNodes(run: TaskRunRecord, matcher: RegExp, capabilityKey?: string): NodeRunRecord[] {
	return run.node_runs.filter((node) => {
		if (node.status !== 'succeeded') return false;
		const definition = nodeDefinition(run, node);
		const key = definition?.capability_key ?? '';
		return (capabilityKey && key === capabilityKey) || matcher.test(`${node.name} ${node.prompt} ${node.node_id} ${definition?.name ?? ''} ${definition?.prompt ?? ''}`);
	}).sort((left, right) => left.order - right.order);
}

function nodePayloads(nodes: NodeRunRecord[], includeInput = false): unknown[] {
	return nodes.flatMap((node) => {
		const values = [node.result, node.output, node.display_summary, ...(includeInput ? [node.input] : [])]
			.filter((value) => value !== undefined && value !== null && value !== '');
		if (values.length) return values;
		const lastAttempt = node.attempt_history?.at(-1);
		return lastAttempt ? [lastAttempt.result, lastAttempt.output].filter((value) => value !== undefined && value !== null && value !== '') : [];
	});
}

function markdownTexts(value: unknown, depth = 0): string[] {
	if (depth > 8 || value === null || value === undefined) return [];
	const parsed = parsePayload(value);
	if (typeof parsed === 'string') {
		if (parsed !== value) return markdownTexts(parsed, depth + 1);
		const text = parsed.trim().replace(/^```(?:markdown|md)\s*/i, '').replace(/\s*```$/, '').trim();
		return text.length > 60 && !/^(?:\{|\[)/.test(text) ? [text] : [];
	}
	if (Array.isArray(parsed)) return parsed.flatMap((item) => markdownTexts(item, depth + 1));
	const record = asRecord(parsed);
	if (!record) return [];
	return Object.entries(record).flatMap(([key, child]) =>
		SECRET_KEY.test(key) || !MARKDOWN_KEYS.has(normalizeKey(key)) ? [] : markdownTexts(child, depth + 1),
	);
}

function reportCandidates(run: TaskRunRecord, reportNodes: NodeRunRecord[]): string[] {
	const payloads = [
		...run.artifacts.filter((artifact) => artifact.format === 'markdown' || artifact.format === 'docx').map((artifact) => artifact.preview_text),
		run.final_output,
		run.final_summary,
		...nodePayloads(reportNodes),
	];
	return [...new Set(payloads.flatMap(markdownTexts).map((text) => text.trim()).filter(Boolean))];
}

function markdownScore(text: string): number {
	return Number(/^\s*#{1,6}\s/m.test(text)) * 4 +
		Number(/^\s*\|.+\|\s*$/m.test(text)) * 2 +
		Number(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/m.test(text)) +
		Math.min(text.length / 5000, 2);
}

function selectMarkdown(run: TaskRunRecord, reportNodes: NodeRunRecord[]): string | null {
	const candidates = reportCandidates(run, reportNodes);
	return candidates.sort((left, right) => markdownScore(right) - markdownScore(left) || right.length - left.length)[0] ?? null;
}

function markdownSections(markdown: string | null): MarkdownSection[] {
	if (!markdown) return [];
	const lines = markdown.split(/\r?\n/);
	const headings: Array<{ index: number; level: number; title: string }> = [];
	for (let index = 0; index < lines.length; index += 1) {
		const line = lines[index].trim();
		if (!line || line.startsWith('|') || line.startsWith('```')) continue;
		const markdownHeading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
		const plainHeading = line.match(/^(第\s*[一二三四五六七八九十百\d]+\s*[章节篇]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．)])\s*(.{2,80})$/);
		const title = markdownHeading?.[2] ?? plainHeading?.[0];
		if (!title) continue;
		headings.push({ index, level: markdownHeading ? markdownHeading[1].length : 2, title: title.replace(/[`*_#]/g, '').trim() });
	}
	return headings.map((heading, headingIndex) => {
		const next = headings.slice(headingIndex + 1).find((item) => item.level <= heading.level);
		return { title: heading.title, body: lines.slice(heading.index + 1, next?.index ?? lines.length).join('\n').trim() };
	});
}

function matchingSections(sections: MarkdownSection[], matcher: RegExp): MarkdownSection[] {
	return sections.filter((section) => matcher.test(section.title) && section.body);
}

function markdownSectionText(sections: MarkdownSection[], matcher: RegExp): string | null {
	const found = matchingSections(sections, matcher);
	return found.length ? found.map((section) => `## ${section.title}\n${section.body}`).join('\n\n') : null;
}

function markdownListItems(text: string, limit: number): string[] {
	const explicit = text.split(/\r?\n/).map((line) => line.trim())
		.filter((line) => /^(?:[-*+•]\s+|\d+[.)、]\s+|[一二三四五六七八九十]+[、.．]\s+)/.test(line))
		.map((line) => cleanDisplayText(line) ?? '')
		.filter(Boolean);
	if (explicit.length) return [...new Set(explicit)].slice(0, limit);
	const paragraphs = text.split(/\n\s*\n|\r?\n/).map((line) => conciseSummary(line, 120)).filter((line): line is string => Boolean(line));
	return [...new Set(paragraphs)].slice(0, limit);
}

function arrayFields(records: DataRecord[], aliases: string[]): ArrayField[] {
	const wanted = new Set(aliases.map(normalizeKey));
	return records.flatMap((record) => Object.entries(record)
		.filter(([key, value]) => wanted.has(normalizeKey(key)) && (Array.isArray(value) || typeof value === 'string'))
		.map(([key, value]) => ({ key, value })));
}

function unique(values: string[]): string[] {
	return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

function conclusionKind(base: S05MeetingConclusion['kind'], text: string): S05MeetingConclusion['kind'] {
	return base === 'discussion' || /建议|待讨论|待确认|供讨论|尚未形成决议|暂未形成共识/.test(text) ? 'discussion' : 'decision';
}

function fieldItems(records: DataRecord[], aliases: string[], maxItems = 4): string[] {
	const items = arrayFields(records, aliases).flatMap(({ value }) => listValue(value));
	if (!items.length) items.push(...listValue(directValue(records, aliases)));
	return unique(items).slice(0, maxItems);
}

function buildOverview(records: DataRecord[]): S05MeetingField[] {
	const fields: S05MeetingField[] = [];
	const add = (label: string, value: unknown) => {
		const text = usableText(value);
		if (!text || fields.some((field) => field.label === label)) return;
		fields.push({ label, value: text });
	};
	add('会议名称 / 主题', directValue(records, ['meeting_name', 'meeting_title', 'meeting_topic', 'subject', '会议名称', '会议主题', '主题']));
	add('会议时间', directValue(records, ['meeting_datetime', 'meeting_date', 'meeting_time', 'date_time', '会议时间', '会议日期', '召开时间']));
	add('会议地点 / 方式', directValue(records, ['meeting_location', 'location', 'venue', 'meeting_mode', 'meeting_method', '会议地点', '会议方式', '线上线下方式']));
	const agenda = fieldItems(records, ['agenda', 'agenda_items', 'meeting_agenda', 'topics', 'meeting_topics', '主要议题', '议程'], 3);
	if (agenda.length) add('主要议题', agenda.join('、'));
	const participants = fieldItems(records, ['attendees', 'participants', 'experts', 'speaker_names', '参会人员', '专家名单', '出席人员', '实际参会人员'], 6);
	if (participants.length) add('参会人员信息', participants.join('、'));
	return fields.slice(0, 5);
}

function meetingOpinions(records: DataRecord[]): S05MeetingOpinion[] {
	const opinions: S05MeetingOpinion[] = [];
	const addOpinion = (record: DataRecord | null, value: unknown) => {
		const text = usableText(record
			? directValue([record], ['opinion', 'viewpoint', 'view', 'statement', 'content', 'text', '核心观点', '观点', '意见', '发言内容'])
			: value);
		if (!text) return;
		const speaker = record ? usableText(directValue([record], ['speaker', 'speaker_name', 'expert_name', 'participant_name', '发言人', '发言者', '专家姓名'])) : null;
		const type = record ? usableText(directValue([record], ['opinion_type', 'type', 'category', '意见类型', '观点类型'])) : null;
		const dissent = Boolean(type && /异议|分歧|反对|保留|dissent|disagree/i.test(type)) || /不同意见|存在分歧|保留意见|明确反对|不赞同|有异议/.test(text);
		if (!opinions.some((opinion) => opinion.text === text && opinion.speaker === speaker)) opinions.push({ speaker, text, type, dissent });
	};
	for (const { value } of arrayFields(records, OPINION_ARRAY_KEYS)) {
		const parsed = parsePayload(value);
		const items = Array.isArray(parsed) ? parsed : typeof parsed === 'string' ? listValue(parsed) : [];
		for (const item of items) {
			addOpinion(asRecord(item), item);
			if (opinions.length >= 3) return opinions;
		}
	}
	for (const record of records) {
		addOpinion(record, directValue([record], ['speaker_opinion', 'expert_opinion', 'participant_opinion', 'viewpoint', 'opinion', '发言观点', '专家意见', '参会人员意见', '会议观点']));
		if (opinions.length >= 3) break;
	}
	return opinions;
}

function conclusionItems(records: DataRecord[], sections: MarkdownSection[]): S05MeetingConclusion[] {
	const items: S05MeetingConclusion[] = [];
	for (const { key, value } of [
		...arrayFields(records, DECISION_ARRAY_KEYS),
		...arrayFields(records, DISCUSSION_SUGGESTION_KEYS),
	]) {
		const kind = DISCUSSION_SUGGESTION_KEYS.some((alias) => normalizeKey(alias) === normalizeKey(key)) ? 'discussion' : 'decision';
		const parsed = parsePayload(value);
		const values = Array.isArray(parsed) ? parsed : listValue(parsed);
		for (const valueItem of values) {
			const record = asRecord(valueItem);
			const text = usableText(record
				? directValue([record], ['decision', 'conclusion', 'suggestion', 'recommendation', 'text', 'content', '事项', '结论', '决定', '建议'])
				: valueItem);
			if (text && !items.some((item) => item.text === text)) items.push({ text, kind: conclusionKind(kind, text) });
		}
	}
	for (const record of records) {
		const decision = usableText(directValue([record], ['meeting_decision', 'decision', 'meeting_conclusion', 'conclusion', 'resolution', '会议决定', '会议结论', '形成共识']));
		const suggestion = usableText(directValue([record], ['discussion_suggestion', 'suggestion', '待讨论事项', '讨论建议']));
		if (decision && !items.some((item) => item.text === decision)) items.push({ text: decision, kind: conclusionKind('decision', decision) });
		if (suggestion && !items.some((item) => item.text === suggestion)) items.push({ text: suggestion, kind: conclusionKind('discussion', suggestion) });
		if (items.length >= 2) break;
	}
	if (items.length < 2 && sections.length) {
		for (const section of sections) {
			const baseKind = /建议|待讨论|待确认/i.test(section.title) ? 'discussion' : 'decision';
			for (const text of markdownListItems(section.body, 2)) {
				if (!items.some((item) => item.text === text)) items.push({ text, kind: conclusionKind(baseKind, text) });
				if (items.length >= 2) return items;
			}
		}
	}
	return items.slice(0, 2);
}

function actionStatus(value: unknown): S05ActionStatus {
	const text = usableText(value);
	if (!text) return null;
	if (/^(?:complete|completed|done|已完成|已办结|完成)$/i.test(text)) return 'complete';
	if (/^(?:incomplete|blocked|overdue|未完成|未解决|受阻|逾期)$/i.test(text)) return 'incomplete';
	if (/^(?:pending|todo|in_progress|待办|待处理|待确认|进行中|未开始)$/i.test(text)) return 'pending';
	return null;
}

function meetingActions(records: DataRecord[], todoSections: MarkdownSection[]): S05MeetingAction[] {
	const actions: S05MeetingAction[] = [];
	for (const { value } of arrayFields(records, ACTION_ARRAY_KEYS)) {
		const parsed = parsePayload(value);
		const values = Array.isArray(parsed) ? parsed : listValue(parsed);
		for (const valueItem of values) {
			const record = asRecord(valueItem);
			const text = usableText(record
				? directValue([record], ['action', 'action_item', 'task', 'todo', 'title', 'description', 'content', 'text', '待办事项', '事项', '工作内容'])
				: valueItem);
			if (!text) continue;
			const owner = record ? usableText(directValue([record], ['owner', 'assignee', 'responsible_person', 'responsible', '负责人', '责任人', '承办人'])) : null;
			const deadline = record ? usableText(directValue([record], ['deadline', 'due_date', 'target_date', 'due', '截止时间', '完成期限', '期限'])) : null;
			const status = record ? actionStatus(directValue([record], ['status', 'state', 'completion_status', '处理状态', '完成状态'])) : null;
			if (!actions.some((action) => action.text === text)) actions.push({ text, owner, deadline, status });
			if (actions.length >= 3) return actions;
		}
	}
	for (const record of records) {
		const text = usableText(directValue([record], ['action_item', 'todo_item', 'meeting_action', 'action', 'todo', '待办事项', '会后待办', '后续行动', '行动项']));
		if (!text || actions.some((action) => action.text === text)) continue;
		actions.push({
			text,
			owner: usableText(directValue([record], ['owner', 'assignee', 'responsible_person', 'responsible', '负责人', '责任人', '承办人'])),
			deadline: usableText(directValue([record], ['deadline', 'due_date', 'target_date', 'due', '截止时间', '完成期限', '期限'])),
			status: actionStatus(directValue([record], ['status', 'state', 'completion_status', '处理状态', '完成状态'])),
		});
		if (actions.length >= 3) return actions;
	}
	if (!actions.length && todoSections.length) {
		for (const section of todoSections) {
			for (const text of markdownListItems(section.body, 3)) {
				actions.push({ text, owner: null, deadline: null, status: null });
				if (actions.length >= 3) return actions;
			}
		}
	}
	return actions.slice(0, 3);
}

function transcriptSource(inputRecords: DataRecord[], audioNodes: NodeRunRecord[], transcriptImportNodes: NodeRunRecord[]): string | null {
	const submittedTranscript = directText(inputRecords, ['transcript_text', 'transcript', 'transcribed_text', '会议转写文本', '转写文本']);
	if (transcriptImportNodes.length && submittedTranscript) return '用户提供的已转写文本 · 未调用 ASR';
	if (audioNodes.length) {
		const outputRecords = recordsFromPayloads(nodePayloads(audioNodes));
		const resultText = directText(outputRecords, ['transcript_text', 'transcription_text', 'transcription', 'transcript', '转写文本', '识别文本']);
		const hasTextOutput = nodePayloads(audioNodes).some((payload) => textualLeaves(payload).some((text) => text.trim().length > 20));
		if (resultText || hasTextOutput) return '系统 ASR 转写结果已获取';
	}
	return null;
}

function minutesDraftLabel(records: DataRecord[], markdown: string | null): string | null {
	const status = directText(records, ['minutes_status', 'minutes_state', 'draft_status', 'signoff_status', '纪要状态', '签发状态']);
	if (/draft|草稿/i.test(status ?? '') || /(?:会议纪要|纪要).{0,12}草稿|草稿.{0,12}(?:会议纪要|纪要)/i.test(markdown ?? '')) return '纪要草稿';
	if (/pending_review|待复核|待人工确认|未签发/i.test(status ?? '')) return '待人工复核';
	return null;
}

function buildResult(run: TaskRunRecord): S05MeetingResult {
	const inputNodes = run.node_runs.filter((node) => node.type === 'input' && /转写|会议文本|transcript|collect_transcript/i.test(`${node.name} ${node.prompt} ${node.node_id}`));
	const transcriptImportNodes = successfulNodes(run, /导入.*转写|transcript.*import/i, 'meeting.transcript_import');
	const audioNodes = successfulNodes(run, /语音转写|音频转写|audio.*transcri(?:be|ption)|asr/i, 'meeting.audio_transcribe');
	const topicNodes = successfulNodes(run, /议题分段与摘要|segment_meeting_topics|议题摘要/i);
	const actionNodes = successfulNodes(run, /提取待办事项|extract_meeting_actions|会后待办/i);
	const reviewNodes = successfulNodes(run, /人工复核纪要草稿|review_minutes_draft/i);
	const minutesNodes = successfulNodes(run, /整理会议纪要|produce_meeting_minutes|会议纪要草稿/i);
	const reportNodes = [...new Map([...topicNodes, ...actionNodes, ...reviewNodes, ...minutesNodes].map((node) => [node.node_id, node])).values()];
	const markdown = selectMarkdown(run, reportNodes);
	const sections = markdownSections(markdown);
	const inputRecords = recordsFromPayloads([run.input, ...nodePayloads(inputNodes, true)]);
	const topicRecords = recordsFromPayloads(nodePayloads(topicNodes));
	const actionRecords = recordsFromPayloads(nodePayloads(actionNodes));
	const minutesRecords = recordsFromPayloads(nodePayloads(minutesNodes));
	const reviewRecords = recordsFromPayloads(nodePayloads(reviewNodes));
	const reportRecords = markdown ? recordsFromPayloads([markdown]) : [];
	const meetingRecords = [...inputRecords, ...topicRecords, ...actionRecords, ...minutesRecords, ...reviewRecords, ...reportRecords];
	const overviewFields = buildOverview(meetingRecords);

	const minutesSources = [...topicRecords, ...minutesRecords, ...reportRecords];
	const minutesSummary = directText(minutesSources, SUMMARY_KEYS)
		? conciseSummary(directText(minutesSources, SUMMARY_KEYS) ?? '')
		: null;
	const minutesSectionMatches = matchingSections(sections, /会议纪要摘要|会议核心摘要|会议摘要|议题摘要|讨论要点|议题分段/i);
	const markdownMinutes = markdownSectionText(sections, /会议纪要摘要|会议核心摘要|会议摘要|议题摘要|讨论要点|议题分段/i);
	const finalMinutesSummary = minutesSummary ?? minutesSectionMatches.map((section) => conciseSummary(section.body)).find(Boolean) ?? null;
	const structuredDiscussionPoints = fieldItems(minutesSources, DISCUSSION_POINT_KEYS, 3);
	const discussionPoints = unique([
		...structuredDiscussionPoints,
		...(!structuredDiscussionPoints.length ? minutesSectionMatches.flatMap((section) => markdownListItems(section.body, 3)) : []),
	]).slice(0, 3);

	const opinionRecords = [...topicRecords, ...minutesRecords, ...reportRecords];
	const structuredOpinions = meetingOpinions(opinionRecords);
	const opinionSectionMatches = matchingSections(sections, /专家观点|专家意见|发言观点|发言摘要|参会人员意见|讨论意见/i);
	const markdownOpinions = markdownSectionText(sections, /专家观点|专家意见|发言观点|发言摘要|参会人员意见|讨论意见/i);
	const opinions = structuredOpinions.length ? structuredOpinions : opinionSectionMatches.length
		? opinionSectionMatches.flatMap((section) => markdownListItems(section.body, 3)).slice(0, 3).map((text) => ({
			speaker: null,
			text,
			type: null,
			dissent: /不同意见|存在分歧|保留意见|明确反对|不赞同|有异议/.test(text),
		}))
		: [];

	const outcomeSectionMatches = matchingSections(sections, /会议结论|会议决定|会议共识|讨论建议|待办事项|会后待办|行动项|后续安排/i);
	const markdownOutcomes = markdownSectionText(sections, /会议结论|会议决定|会议共识|讨论建议|待办事项|会后待办|行动项|后续安排/i);
	const todoSections = outcomeSectionMatches.filter((section) => /待办|会后事项|行动项|后续安排/i.test(section.title));
	const conclusions = conclusionItems([...minutesRecords, ...actionRecords, ...reportRecords], outcomeSectionMatches.filter((section) => !todoSections.includes(section)));
	const actions = meetingActions([...actionRecords, ...minutesRecords, ...reportRecords], todoSections);
	const transcriptNodes = transcriptSource(inputRecords, audioNodes, transcriptImportNodes);
	const draftLabel = minutesDraftLabel(minutesRecords, markdown);
	const artifacts = run.artifacts.filter((artifact) => Boolean(artifact.name));
	const markdownSectionsForSupplement = {
		overview: markdownSectionText(sections, /会议基本信息|会议信息|会议概况|会议安排/i),
		minutes: markdownMinutes,
		opinions: markdownOpinions,
		outcomes: markdownOutcomes,
	};
	const hasStructuredContent = overviewFields.length > 0 || Boolean(finalMinutesSummary) || discussionPoints.length > 0 || opinions.length > 0 || conclusions.length > 0 || actions.length > 0;

	return {
		overviewFields,
		transcriptSource: transcriptNodes,
		minutesSummary: finalMinutesSummary,
		discussionPoints,
		minutesDraftLabel: draftLabel,
		opinions: opinions.slice(0, 3),
		conclusions: conclusions.slice(0, 2),
		actions: actions.slice(0, 3),
		markdown,
		markdownSections: markdownSectionsForSupplement,
		artifacts,
		hasStructuredContent,
		hasFullResult: Boolean(markdown || run.final_output || run.final_summary || artifacts.length),
	};
}

export function adaptS05MeetingRun(run: TaskRunRecord): S05MeetingResult {
	return buildResult(run);
}
