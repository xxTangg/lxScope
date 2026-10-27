import type { NodeRunRecord, TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;

export interface S04MeetingField {
	label: string;
	value: string;
}

export interface S04MaterialItem {
	name: string;
	status: 'file' | 'content';
	detail: string | null;
	format?: string;
}

export interface S04BudgetLine {
	label: string;
	amount: string | null;
	calculation: string | null;
}

export type S04TodoStatus = 'complete' | 'pending' | 'incomplete';

export interface S04TodoItem {
	text: string;
	status: S04TodoStatus;
}

export interface S04MeetingPreparationResult {
	meetingFields: S04MeetingField[];
	materials: S04MaterialItem[];
	participantCount: string | null;
	budgetTotal: string | null;
	currency: string | null;
	budgetLines: S04BudgetLine[];
	feeBasis: string | null;
	todos: S04TodoItem[];
	markdown: string | null;
	markdownSections: {
		meeting: string | null;
		materials: string | null;
		budget: string | null;
		todos: string | null;
	};
	artifacts: TaskArtifactRecord[];
	hasStructuredContent: boolean;
	hasFullResult: boolean;
}

const SECRET_KEY = /(authorization|access.?token|refresh.?token|secret|password|api.?key|private.?reasoning|chain.?of.?thought)/i;
const MARKDOWN_KEYS = new Set(['markdown', 'reportmarkdown', 'report', 'content', 'text', 'body', 'documentcontent', 'finaloutput', 'finalsummary', 'summary', 'output', 'result']);

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
	return value.toLowerCase().replace(/[\s_\-.:：/\\（）()【】[\]]/g, '');
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

function scalarText(value: unknown): string | null {
	if (typeof value === 'string' || typeof value === 'number') return String(value).trim() || null;
	if (Array.isArray(value)) return value.map(scalarText).filter((item): item is string => Boolean(item)).join('、') || null;
	const record = asRecord(value);
	if (!record) return null;
	for (const key of ['label', 'title', 'name', 'text', 'value', 'content', 'summary', 'amount', 'total']) {
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

function listValue(value: unknown): string[] {
	const parsed = parsePayload(value);
	if (Array.isArray(parsed)) {
		return parsed.map((item) => scalarText(item)).filter((item): item is string => Boolean(item));
	}
	if (typeof parsed !== 'string') {
		const text = scalarText(parsed);
		return text ? [text] : [];
	}
	return parsed.split(/\r?\n|[、;；]/).map((item) => item.replace(/^\s*(?:[-*•]|\d+[.)、])\s*/, '').trim()).filter(Boolean);
}

function nodeDefinition(run: TaskRunRecord, nodeRun: NodeRunRecord) {
	return run.nodes.find((node) => node.id === nodeRun.node_id);
}

function matchingNodes(run: TaskRunRecord, capabilityKey: string, fallback: RegExp): NodeRunRecord[] {
	return run.node_runs.filter((node) => {
		if (node.status !== 'succeeded') return false;
		const definition = nodeDefinition(run, node);
		return definition?.capability_key === capabilityKey || fallback.test(`${node.name} ${node.prompt} ${node.node_id} ${definition?.name ?? ''} ${definition?.prompt ?? ''}`);
	}).sort((left, right) => left.order - right.order);
}

function nodePayloads(nodes: NodeRunRecord[], includeInput = false): unknown[] {
	return nodes.flatMap((node) => {
		const payloads = [node.result, node.output, node.display_summary, ...(includeInput ? [node.input] : [])]
			.filter((value) => value !== undefined && value !== null && value !== '');
		if (payloads.length) return payloads;
		const attempt = node.attempt_history?.at(-1);
		return attempt ? [attempt.result, attempt.output].filter((value) => value !== undefined && value !== null && value !== '') : [];
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

function reportCandidates(run: TaskRunRecord): string[] {
	const reportNodes = run.node_runs.filter((node) => node.status === 'succeeded' && node.type === 'agent' &&
		/会前|会议|材料|通知|准备|meeting|prepare|assemble|draft/i.test(`${node.name} ${node.prompt}`));
	const payloads = [
		...run.artifacts.map((artifact) => artifact.preview_text),
		run.final_output,
		run.final_summary,
		...reportNodes.flatMap((node) => [node.result, node.output, node.display_summary]),
	];
	return [...new Set(payloads.flatMap(markdownTexts).map((text) => text.trim()).filter(Boolean))];
}

function markdownScore(text: string): number {
	return Number(/^\s*#{1,6}\s/m.test(text)) * 4 +
		Number(/^\s*\|.+\|\s*$/m.test(text)) * 2 +
		Number(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/m.test(text)) +
		Math.min(text.length / 5000, 2);
}

function selectMarkdown(run: TaskRunRecord): string | null {
	const candidates = reportCandidates(run);
	return candidates.sort((left, right) => markdownScore(right) - markdownScore(left) || right.length - left.length)[0] ?? null;
}

interface MarkdownSection {
	title: string;
	body: string;
}

function markdownSections(markdown: string | null): MarkdownSection[] {
	if (!markdown) return [];
	const lines = markdown.split(/\r?\n/);
	const headings: Array<{ index: number; level: number; title: string }> = [];
	for (let index = 0; index < lines.length; index += 1) {
		const line = lines[index].trim();
		const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/) ??
			line.match(/^(第\s*[一二三四五六七八九十百\d]+\s*[章节篇]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．)])\s*(.{2,80})$/);
		if (!heading) continue;
		headings.push({ index, level: heading[1].startsWith('#') ? heading[1].length : 2, title: (heading[2] ?? heading[0]).replace(/[`*_#]/g, '').trim() });
	}
	return headings.map((heading, headingIndex) => {
		const next = headings.slice(headingIndex + 1).find((item) => item.level <= heading.level);
		return { title: heading.title, body: lines.slice(heading.index + 1, next?.index ?? lines.length).join('\n').trim() };
	});
}

function sectionExcerpt(sections: MarkdownSection[], matcher: RegExp, limit = 1150): string | null {
	const section = sections.find((item) => matcher.test(item.title));
	if (!section?.body) return null;
	const lines = section.body.split(/\r?\n/);
	let excerpt = '';
	for (const line of lines) {
		const next = excerpt ? `${excerpt}\n${line}` : line;
		if (next.length > limit) {
			if (excerpt) excerpt = `${excerpt.trimEnd()}\n…`;
			break;
		}
		excerpt = next;
	}
	return excerpt.trim() || null;
}

function numberText(value: unknown): string | null {
	if (typeof value !== 'number' && typeof value !== 'string') return null;
	const text = String(value).trim();
	return text && Number.isFinite(Number(text)) ? text : null;
}

function formatCalculation(value: unknown): string | null {
	const record = asRecord(value);
	if (!record) return scalarText(value);
	const quantity = numberText(record.quantity ?? record.count ?? record.数量);
	const rate = numberText(record.unit_rate ?? record.rate ?? record.unit_price ?? record.单价 ?? record.费率);
	if (quantity && rate) return `${quantity} × ${rate}`;
	return null;
}

function rowsFrom(records: DataRecord[], aliases: string[]): DataRecord[] {
	const value = directValue(records, aliases);
	const parsed = parsePayload(value);
	return Array.isArray(parsed) ? parsed.map(asRecord).filter((item): item is DataRecord => Boolean(item)) : [];
}

function formatAmount(value: unknown, currency: string | null): string | null {
	const number = numberText(value);
	if (number === null) return null;
	const numericValue = Number(number);
	const formatted = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(numericValue);
	if (!currency || /^(?:CNY|RMB|人民币)$/i.test(currency)) return currency ? `¥${formatted}` : formatted;
	return `${currency} ${formatted}`;
}

function statusForTodo(value: unknown, fallbackText: string): S04TodoStatus {
	const status = scalarText(value)?.toLowerCase() ?? '';
	if (/complete|completed|done|succeed|passed|已完成|已整理|已生成/.test(status)) return 'complete';
	if (/incomplete|blocked|failed|未完成|未生成|阻断|失败/.test(status)) return 'incomplete';
	if (/pending|unverified|todo|待|需确认|需核实|未发送/.test(status)) return 'pending';
	if (/尚未|未发送|待确认|待核实/.test(fallbackText)) return 'pending';
	return 'pending';
}

function todoArray(records: DataRecord[], aliases: string[]): S04TodoItem[] {
	const value = directValue(records, aliases);
	const parsed = parsePayload(value);
	if (!Array.isArray(parsed)) return [];
	return parsed.flatMap((item) => {
		const record = asRecord(item);
		const text = scalarText(item) ?? (record ? directText([record], ['item', 'task', 'title', 'name', 'description', 'content', 'text', '事项', '待办事项']) : null);
		if (!text) return [];
		const status = record ? directValue([record], ['status', 'state', 'completion_status', '核验状态', '状态']) : undefined;
		return [{ text, status: statusForTodo(status, text) }];
	});
}

function formatLabel(format: TaskArtifactRecord['format']): string {
	return format === 'docx' ? 'DOCX' : format === 'xlsx' ? 'XLSX' : 'Markdown';
}

function buildResult(run: TaskRunRecord): S04MeetingPreparationResult {
	const inputNodes = run.node_runs.filter((node) => node.type === 'input' || /会议参数|meeting parameters|collect_meeting_parameters/i.test(`${node.name} ${node.prompt} ${node.node_id}`));
	const costNodes = matchingNodes(run, 'meeting.cost_calculate', /核算会议费用|calculate_meeting_costs/i);
	const materialNodes = matchingNodes(run, 'meeting.material_generate', /生成会议物料|generate_meeting_materials/i);
	const checkNodes = matchingNodes(run, 'meeting.material_check', /检查会前材料|check_meeting_materials/i);
	const notificationNodes = run.node_runs.filter((node) => node.status === 'succeeded' && /准备通知草稿|prepare_notification/i.test(`${node.name} ${node.prompt} ${node.node_id}`));
	const finalNodes = run.node_runs.filter((node) => node.status === 'succeeded' && /整理会前材料草稿包|assemble_meeting_preparation_pack/i.test(`${node.name} ${node.prompt} ${node.node_id}`));
	const markdown = selectMarkdown(run);
	const sections = markdownSections(markdown);
	const inputRecords = [run.input, ...nodePayloads(inputNodes, true)].flatMap(recordsIn);
	const costRecords = nodePayloads(costNodes).flatMap(recordsIn);
	const materialRecords = nodePayloads(materialNodes).flatMap(recordsIn);
	const checkRecords = nodePayloads(checkNodes).flatMap(recordsIn);
	const notificationRecords = nodePayloads(notificationNodes).flatMap(recordsIn);
	const finalRecords = nodePayloads(finalNodes).flatMap(recordsIn);
	const reportRecords = markdown ? recordsIn(markdown) : [];
	const inputAndReport = [...inputRecords, ...reportRecords];
	const meetingRecords = [...inputRecords, ...costRecords, ...materialRecords, ...checkRecords, ...finalRecords, ...reportRecords];

	const meetingFields: S04MeetingField[] = [];
	const addField = (label: string, value: unknown) => {
		const text = scalarText(value);
		if (!text || /^(?:待确认|待核实|待提供|未提供|未知|unknown|n\/a|none|null|无|暂无|—|-)$/i.test(text)) return;
		if (!meetingFields.some((field) => field.label === label)) meetingFields.push({ label, value: text });
	};
	addField('会议名称 / 主题', directValue(inputAndReport, ['meeting_name', 'meeting_topic', 'topic', 'meeting_title', '会议名称', '会议主题', '主题']));
	addField('会议时间', directValue(meetingRecords, ['meeting_date', 'meeting_datetime', 'meeting_time', 'date_time', 'date', 'time', '会议时间', '会议日期']));
	addField('会议地点', directValue(meetingRecords, ['meeting_location', 'location', 'venue', 'room', 'place', '会议地点', '地点', '会场']));
	const rawParticipants = directValue(meetingRecords, ['participant_count', 'attendee_count', 'participants_count', 'expert_count', '参会人数', '专家人数', '参会人员数量']);
	const participantCount = Array.isArray(rawParticipants) ? String(rawParticipants.length) : scalarText(rawParticipants);
	if (participantCount) addField('参会人数', participantCount);
	const agendaValue = directValue([...materialRecords, ...inputAndReport], ['agenda', 'meeting_agenda', 'agenda_items', 'topics', 'meeting_topics', '主要议题', '议程']);
	const agenda = listValue(agendaValue);
	if (agenda.length) addField('主要议题', agenda.slice(0, 3).join('、'));

	const currency = directText(costRecords, ['currency', 'currency_code', '币种']) ?? null;
	const budgetTotal = formatAmount(directValue(costRecords, ['total', 'total_amount', 'grand_total', 'amount_total', '费用合计', '合计金额', '预算合计']), currency);
	const budgetRows = rowsFrom(costRecords, ['lines', 'items', 'cost_items', 'fee_items', '费用明细', '费用项目']);
	const budgetLines: S04BudgetLine[] = budgetRows.slice(0, 3).flatMap((row) => {
		const label = directText([row], ['label', 'name', 'title', 'item', 'fee_item', '费用项目', '项目', '名称']);
		const amount = formatAmount(directValue([row], ['amount', 'total', 'line_total', '金额', '费用']), currency);
		const calculation = formatCalculation(row);
		return label || amount ? [{ label: label ?? '费用项目', amount, calculation }] : [];
	});
	const costSource = directText(costRecords, ['source_kind', 'source', '费率来源']);
	const officialPolicyVerified = directValue(costRecords, ['official_fee_policy_verified', 'fee_policy_verified', '权威费率已验证']);
	const feeBasis = costRecords.length
		? officialPolicyVerified === false || /user_provided_fee_rule|user_supplied_rates|用户提供|任务输入/i.test(costSource ?? '')
			? '按任务输入费率计算，费率权威性尚未核验。'
			: '计算依据来自本次费用核算结果，仍需业务复核。'
		: null;

	const artifacts = run.artifacts.filter((artifact) => Boolean(artifact.name));
	const materials: S04MaterialItem[] = [];
	const addMaterial = (item: S04MaterialItem) => {
		const key = normalizeKey(item.name);
		if (!materials.some((existing) => normalizeKey(existing.name) === key)) materials.push(item);
	};
	for (const artifact of artifacts) {
		addMaterial({ name: artifact.name, status: 'file', detail: '本次运行已生成文件', format: formatLabel(artifact.format) });
	}
	const structuredMaterialRows = rowsFrom([...finalRecords, ...materialRecords, ...checkRecords], ['generated_materials', 'meeting_materials', 'material_items', 'document_list', 'documents', 'materials', '材料清单', '会议材料']);
	for (const row of structuredMaterialRows) {
		const name = directText([row], ['name', 'title', 'label', 'material_name', 'document_name', '文件名', '材料名称', '名称']);
		if (!name) continue;
		const detail = directText([row], ['description', 'summary', 'purpose', '用途', '说明']);
		addMaterial({ name, status: 'content', detail: detail ?? '内容已整理' });
	}
	if (agenda.length) addMaterial({ name: '会议议程草稿', status: 'content', detail: agenda.slice(0, 3).join('、') });
	const noticeDraft = directText([...notificationRecords, ...finalRecords], ['notification_draft', 'meeting_notice_draft', 'notice_draft', 'notification_text', '会议通知草稿', '通知草稿']);
	if (noticeDraft) addMaterial({ name: '会议通知草稿', status: 'content', detail: '内容已整理，尚未发送' });
	const preparationItems = listValue(directValue(materialRecords, ['preparation_checklist', 'preparation_items', 'meeting_checklist', '会前准备清单', '准备事项清单']));
	if (preparationItems.length) addMaterial({ name: '会前准备清单', status: 'content', detail: preparationItems.slice(0, 2).join('、') });

	const todos: S04TodoItem[] = [];
	const addTodo = (item: S04TodoItem) => {
		const key = normalizeKey(item.text);
		if (key && !todos.some((existing) => normalizeKey(existing.text) === key)) todos.push(item);
	};
	if (agenda.length && materialNodes.length) addTodo({ text: '会议议程草稿已整理', status: 'complete' });
	if (costRecords.length && (officialPolicyVerified === false || /user_provided_fee_rule|user_supplied_rates|用户提供|任务输入/i.test(costSource ?? ''))) {
		addTodo({ text: '费用费率来源需人工核实', status: 'pending' });
	}
	const notificationState = directValue([...notificationRecords, ...finalRecords, ...materialRecords], ['notification_status', 'status']);
	const notificationSent = directValue([...notificationRecords, ...finalRecords, ...materialRecords], ['notification_sent', 'sent', '已发送']);
	if (/draft_only|draft|草稿/i.test(scalarText(notificationState) ?? '') || notificationSent === false) {
		addTodo({ text: '会议通知仅为草稿，尚未发送', status: 'pending' });
	}
	const checkStatus = directText(checkRecords, ['status', '检查状态']);
	if (checkNodes.length && /ready_for_ai_review|待复核|待人工检查/i.test(checkStatus ?? '')) {
		addTodo({ text: '会前材料与费用待人工复核', status: 'pending' });
	}
	const structuredTodos = todoArray([...finalRecords, ...checkRecords, ...notificationRecords], ['pending_items', 'todo_items', 'action_items', 'items_to_confirm', 'preparation_todos', '待办事项', '待核实事项', '待确认事项']);
	structuredTodos.forEach(addTodo);
	preparationItems.forEach((text) => addTodo({ text, status: 'pending' }));
	const markdownTodo = sectionExcerpt(sections, /会前准备|待办|待核实|待确认|准备事项|后续安排/i, 700);
	if (!todos.length && markdownTodo) {
		const lines = markdownTodo.split(/\r?\n/).filter((line) => /^\s*(?:[-*•]|\d+[.)、])\s+/.test(line));
		lines.slice(0, 4).forEach((line) => {
			const text = line.replace(/^\s*(?:[-*•]|\d+[.)、])\s+/, '').replace(/[`*_]/g, '').trim();
			if (text) addTodo({ text, status: statusForTodo(undefined, text) });
		});
	}

	const markdownSectionsForCards = {
		meeting: sectionExcerpt(sections, /会议基本信息|会议信息|会议安排|会议概况/i),
		materials: sectionExcerpt(sections, /会前材料|会议材料|材料清单|通知草稿|议程|参会指南/i),
		budget: sectionExcerpt(sections, /专家费用预算|费用预算|预算核算|费用明细|专家费用|费用测算/i),
		todos: markdownTodo,
	};
	const hasStructuredContent = meetingFields.length > 0 || materials.length > 0 || Boolean(budgetTotal) || budgetLines.length > 0 || todos.length > 0;
	return {
		meetingFields: meetingFields.slice(0, 5),
		materials: materials.slice(0, 4),
		participantCount: participantCount ?? null,
		budgetTotal,
		currency,
		budgetLines,
		feeBasis,
		todos: todos.slice(0, 4),
		markdown,
		markdownSections: markdownSectionsForCards,
		artifacts,
		hasStructuredContent,
		hasFullResult: Boolean(run.final_output || run.final_summary || artifacts.length),
	};
}

export function adaptS04MeetingPreparationRun(run: TaskRunRecord): S04MeetingPreparationResult {
	return buildResult(run);
}
