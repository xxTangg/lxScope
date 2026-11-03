import type { TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;
type S09Module = 'overview' | 'expenses' | 'checks' | 'materials';

export interface S09ExpenseCategory {
	name: string;
	count: number;
	amount: string | null;
	amountMinor: number | null;
}

export interface S09CheckItem {
	title: string;
	detail: string;
	status: '已完成' | '发现异常' | '未执行' | '待复核';
}

export interface S09TodoItem {
	title: string;
	detail: string;
	status: '已整理' | '待复核' | '待处理' | '未执行';
}

export interface S09ReimbursementResult {
	projectId: string | null;
	expenseCategory: string | null;
	accountingPeriod: string | null;
	amountLabel: string | null;
	amount: string | null;
	currency: string | null;
	invoiceCount: number | null;
	duplicateCount: number | null;
	expenseCategories: S09ExpenseCategory[];
	checkItems: S09CheckItem[];
	todoItems: S09TodoItem[];
	draftOutputAvailable: boolean;
	artifacts: TaskArtifactRecord[];
	markdown: string | null;
	markdownSupplement: string | null;
	moduleData: Record<S09Module, boolean>;
	hasStructuredContent: boolean;
	hasFullResult: boolean;
}

interface InvoiceRow {
	category: string | null;
	total: string | null;
	amount: string | null;
	tax: string | null;
	currency: string | null;
	duplicate: boolean | null;
	adjustment: boolean;
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
		.replace(/^\s*(?:[-*+•]\s+|\d+[.)、]\s+)/, '')
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

function summaryRecord(records: DataRecord[], status: string, aliases: string[]): DataRecord | null {
	return records.find((record) => directText([record], ['status']) === status)
		?? records.find((record) => aliases.filter((alias) => directValue([record], [alias]) !== undefined).length > 1)
		?? null;
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

function directCount(records: DataRecord[], aliases: string[]): number | null {
	const value = directValue(records, aliases);
	if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return value;
	if (typeof value === 'string' && /^\d+$/.test(value.trim())) {
		const count = Number(value.trim());
		return Number.isSafeInteger(count) ? count : null;
	}
	return null;
}

function directArray(records: DataRecord[], aliases: string[]): unknown[] | null {
	const parsed = parsePayload(directValue(records, aliases));
	return Array.isArray(parsed) ? parsed : null;
}

function nodeLabel(run: TaskRunRecord, node: TaskRunRecord['node_runs'][number]): string {
	const definition = run.nodes.find((item) => item.id === node.node_id);
	return [node.name, node.node_id, definition?.name, definition?.capability_key].filter(Boolean).join(' ').toLowerCase();
}

function matchingNodes(run: TaskRunRecord, capability: string, matcher: RegExp): TaskRunRecord['node_runs'] {
	return run.node_runs.filter((node) => {
		if (node.status !== 'succeeded') return false;
		const definition = run.nodes.find((item) => item.id === node.node_id);
		return definition?.capability_key === capability || matcher.test(nodeLabel(run, node));
	});
}

function nodePayloads(nodes: TaskRunRecord['node_runs']): unknown[] {
	return nodes.flatMap((node) => {
		const attempt = node.attempt_history?.[node.attempt_history.length - 1];
		return [node.result, node.output, node.metadata, attempt?.result, attempt?.output, attempt?.metadata]
			.filter((value) => value !== null && value !== undefined && value !== '');
	});
}

function hasNodeOutput(nodes: TaskRunRecord['node_runs']): boolean {
	return nodes.some((node) => Boolean(node.output?.trim()) || node.result !== null && node.result !== undefined);
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
	for (const key of ['markdown', 'report_markdown', 'content', 'text', 'final_output', 'body', 'output', 'result']) {
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

function parseSections(markdown: string): Array<{ title: string | null; body: string }> {
	const sections: Array<{ title: string | null; body: string }> = [];
	let current: { title: string | null; body: string[] } | null = null;
	let inCodeFence = false;
	for (const line of markdown.split('\n')) {
		if (/^\s*```/.test(line)) {
			inCodeFence = !inCodeFence;
			if (current) current.body.push(line);
			continue;
		}
		const heading = !inCodeFence ? line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/) : null;
		if (heading) {
			if (current) sections.push({ title: current.title, body: current.body.join('\n') });
			current = { title: heading[1].trim(), body: [line] };
		} else if (!current) {
			current = { title: null, body: [line] };
		} else {
			current.body.push(line);
		}
	}
	if (current) sections.push({ title: current.title, body: current.body.join('\n') });
	return sections;
}

function markdownSupplement(markdown: string | null, moduleData: Record<S09Module, boolean>): string | null {
	if (!markdown) return null;
	const sectionRules: Array<[S09Module, RegExp]> = [
		['overview', /报销(?:结算)?概览|费用概况|项目基本信息/],
		['expenses', /费用明细|费用分类|金额统计|票据明细/],
		['checks', /票据核对|发票(?:验真|核验|核对)|凭证核查|重复检查/],
		['materials', /报销材料|待提交|报销草稿|结算待办|待办事项/],
	];
	const remaining = parseSections(markdown).filter((section) => {
		if (!section.title) return true;
		const matched = sectionRules.find(([module, pattern]) => moduleData[module] && pattern.test(section.title ?? ''));
		return !matched;
	}).map((section) => section.body.trim()).filter(Boolean).join('\n\n');
	return remaining || null;
}

function parseMoneyMinor(value: unknown): number | null {
	if (typeof value !== 'string' && typeof value !== 'number') return null;
	const normalized = String(value)
		.trim()
		.replace(/,/g, '')
		.replace(/^(?:(?:人民币|CNY|RMB|USD|EUR|¥|￥)\s*)/i, '')
		.replace(/\s*(?:元|人民币|CNY|RMB|USD|EUR)$/i, '');
	const match = normalized.match(/^(-?)(\d+)(?:\.(\d{1,2}))?$/);
	if (!match) return null;
	const major = Number(match[2]);
	const minor = Number((match[3] ?? '').padEnd(2, '0'));
	const amount = major * 100 + minor;
	return Number.isSafeInteger(amount) ? (match[1] ? -amount : amount) : null;
}

function formatMoneyMinor(minor: number, currency: string | null): string {
	const amount = minor / 100;
	if (currency && /^[A-Z]{3}$/i.test(currency)) {
		try {
			return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: currency.toUpperCase(), minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
		} catch {
			// Fall through to the explicit currency label for unsupported codes.
		}
	}
	const formatted = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
	return currency ? `${currency} ${formatted}` : formatted;
}

function parseInvoiceRows(records: DataRecord[], rawInvoiceData?: unknown): InvoiceRow[] {
	const rawParsed = parsePayload(rawInvoiceData);
	const rows = directArray(records, ['invoices', 'invoice_records', 'invoice_items', '票据明细', '发票明细'])
		?? (Array.isArray(rawParsed) ? rawParsed : null);
	if (!rows) return [];
	return rows.flatMap((value) => {
		const row = asRecord(parsePayload(value));
		if (!row) return [];
		return [{
			category: directText([row], ['expense_category', 'category', 'category_name', '费用类别', '报销类别']),
			total: directText([row], ['total', 'total_amount', 'gross_amount', 'invoice_total', 'amount_total', '含税金额', '价税合计', '价税总额']),
			amount: directText([row], ['amount', 'net_amount', 'subtotal', '不含税金额', '金额']),
			tax: directText([row], ['tax', 'tax_amount', '税额']),
			currency: directText([row], ['currency', 'currency_code', '币种']),
			duplicate: directBoolean([row], ['duplicate', 'is_duplicate', '重复']),
			adjustment: directBoolean([row], ['is_refund', 'refund', 'is_reversal', 'is_adjustment', '冲销', '退款']) === true
				|| /refund|reversal|退款|冲销/i.test(directText([row], ['record_type', 'transaction_type', 'adjustment_type', '类型', '调整类型']) ?? ''),
		}];
	});
}

function invoiceTotalMinor(row: InvoiceRow): number | null {
	const total = row.total ? parseMoneyMinor(row.total) : null;
	const amount = row.amount ? parseMoneyMinor(row.amount) : null;
	const tax = row.tax ? parseMoneyMinor(row.tax) : null;

	// Prefer an explicit invoice total. When it is missing or a report placeholder
	// zero, use the actual amount fields available in the invoice row.
	if (total === null || total === 0) {
		if (amount !== null && amount > 0 && tax !== null && tax >= 0) {
			const gross = amount + tax;
			if (Number.isSafeInteger(gross)) return gross;
		}
		if (amount !== null && amount > 0) return amount;
	}
	return total;
}

function categorySummaries(rows: InvoiceRow[], currency: string | null, allowUnconfirmedDuplicateStatus = false): S09ExpenseCategory[] {
	const grouped = new Map<string, { count: number; minor: number; canSum: boolean; currency: string | null }>();
	for (const row of rows) {
		if (!row.category) continue;
		const current = grouped.get(row.category) ?? { count: 0, minor: 0, canSum: true, currency: null };
		current.count += 1;
		const rowCurrency = row.currency ?? currency;
		const minor = invoiceTotalMinor(row);
		if (
			(!allowUnconfirmedDuplicateStatus && row.duplicate !== false) ||
			row.adjustment ||
			minor === null ||
			minor < 0 ||
			!rowCurrency ||
			(current.currency && current.currency.toUpperCase() !== rowCurrency.toUpperCase()) ||
			(currency && currency.toUpperCase() !== rowCurrency.toUpperCase())
		) {
			current.canSum = false;
		} else {
			current.minor += minor;
			current.currency = rowCurrency;
			if (!Number.isSafeInteger(current.minor)) current.canSum = false;
		}
		grouped.set(row.category, current);
	}
	return [...grouped.entries()].map(([name, group]) => ({
		name,
		count: group.count,
		amount: group.canSum ? formatMoneyMinor(group.minor, currency) : null,
		amountMinor: group.canSum ? group.minor : null,
	}));
}

function tableCells(line: string): string[] {
	const trimmed = line.trim();
	if (!trimmed.includes('|')) return [];
	return trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim().replace(/\\\|/g, '|'));
}

function isTableDivider(line: string): boolean {
	const cells = tableCells(line);
	return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, '')));
}

function matchingColumn(headers: string[], aliases: string[]): number {
	const normalizeColumn = (value: string) => normalizeKey(value).replace(/(?:人民币|cny|rmb|usd|eur|元|张|笔|条|个|项)$/i, '');
	const wanted = new Set(aliases.map(normalizeColumn));
	return headers.findIndex((header) => wanted.has(normalizeColumn(header)));
}

function countCell(value: string | undefined): number | null {
	if (!value) return null;
	const match = value.replace(/,/g, '').match(/^\s*(\d+)\s*(?:笔|条|张|个|项)?\s*$/);
	if (!match) return null;
	const count = Number(match[1]);
	return Number.isSafeInteger(count) ? count : null;
}

function categoryCountMap(categories: S09ExpenseCategory[]): Map<string, number> {
	return new Map(categories.map((category) => [normalizeKey(category.name), category.count]));
}

function parseCategorySummaryTable(
	markdown: string,
	currency: string | null,
	fallbackCounts: Map<string, number>,
): S09ExpenseCategory[] {
	const lines = markdown.split('\n');
	let sectionTitle = '';
	for (let index = 0; index < lines.length; index += 1) {
		const heading = lines[index].match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
		if (heading) {
			sectionTitle = heading[1].trim();
			continue;
		}
		const headers = tableCells(lines[index]);
		if (!headers.length || !isTableDivider(lines[index + 1] ?? '')) continue;
		const categoryColumn = matchingColumn(headers, ['expense_category', 'category', 'category_name', '费用类别', '报销类别', '费用分类', '费用项目', '报销项目', '费用类型', '报销类型', '分类']);
		const amountColumn = matchingColumn(headers, ['total', 'total_amount', 'gross_amount', 'amount', 'amount_total', 'category_amount', '分类金额', '费用金额', '报销金额', '费用总额', '总额', '费用小计', '小计', '金额合计', '本次报销金额', '报销金额含税', '含税合计', '含税金额', '价税合计', '金额']);
		const countColumn = matchingColumn(headers, ['count', 'invoice_count', 'record_count', 'quantity', '票据数量', '记录数', '数量']);
		const currencyColumn = matchingColumn(headers, ['currency', 'currency_code', '币种']);
		const duplicateColumn = matchingColumn(headers, ['duplicate', 'is_duplicate', '重复']);
		const isSummaryTable = countColumn >= 0 || /费用.*(?:分类|汇总)|(?:分类|汇总).*费用|按.*类别|金额统计|分类金额/.test(sectionTitle)
			|| headers.some((header) => /分类金额|类别汇总/.test(header));
		if (categoryColumn < 0 || amountColumn < 0 || !isSummaryTable) continue;

		const grouped = new Map<string, { name: string; count: number; minor: number; currency: string | null; canSum: boolean }>();
		for (index += 2; index < lines.length && tableCells(lines[index]).length; index += 1) {
			const cells = tableCells(lines[index]);
			const name = cleanText(cells[categoryColumn]);
			if (!name || /^(?:合计|总计|费用总额|报销总额|总金额)$/i.test(name)) continue;
			const amountMinor = parseMoneyMinor(cleanText(cells[amountColumn]));
			if (amountMinor === null || amountMinor < 0) continue;
			if (duplicateColumn >= 0 && /^(?:true|是|重复)$/i.test(cells[duplicateColumn] ?? '')) continue;
			const rowCurrency = cleanText(currencyColumn >= 0 ? cells[currencyColumn] : null) ?? currency;
			const key = normalizeKey(name);
			const current = grouped.get(key) ?? {
				name,
				count: 0,
				minor: 0,
				currency: rowCurrency,
				canSum: true,
			};
			const parsedCount = countColumn >= 0 ? countCell(cells[countColumn]) : fallbackCounts.get(key) ?? null;
			if (parsedCount === null || !rowCurrency || (current.currency && current.currency.toUpperCase() !== rowCurrency.toUpperCase())) {
				current.canSum = false;
			} else {
				current.count += parsedCount;
			}
			current.minor += amountMinor;
			if (!Number.isSafeInteger(current.minor)) current.canSum = false;
			grouped.set(key, current);
		}
		const summaries = [...grouped.values()].map((group) => ({
			name: group.name,
			count: group.count || fallbackCounts.get(normalizeKey(group.name)) || 0,
			amount: group.canSum ? formatMoneyMinor(group.minor, group.currency) : null,
			amountMinor: group.canSum ? group.minor : null,
		})).filter((item) => item.count > 0 && item.amount !== null);
		if (summaries.length) return summaries;
	}
	return [];
}

function categorySummariesFromRecords(records: DataRecord[], currency: string | null, fallbackCounts: Map<string, number>): S09ExpenseCategory[] {
	const values = directArray(records, ['expense_categories', 'category_summaries', 'category_totals', '费用分类汇总', '费用类别汇总']);
	if (!values) return [];
	return values.flatMap((value) => {
		const record = asRecord(parsePayload(value));
		if (!record) return [];
		const name = directText([record], ['expense_category', 'category', 'category_name', '费用类别', '报销类别', '费用项目', '报销项目', '费用类型', '报销类型', '分类']);
		const amountMinor = parseMoneyMinor(directValue([record], ['total', 'total_amount', 'amount', 'amount_total', 'category_amount', '分类金额', '费用金额', '报销金额', '费用总额', '总额', '含税合计', '含税金额', '价税合计', '金额']));
		if (!name || amountMinor === null || amountMinor < 0 || /^(?:合计|总计|费用总额|报销总额|总金额)$/i.test(name)) return [];
		const count = directCount([record], ['count', 'invoice_count', 'record_count', 'quantity', '票据数量', '记录数', '数量'])
			?? fallbackCounts.get(normalizeKey(name));
		if (count === undefined || count === null) return [];
		const itemCurrency = directText([record], ['currency', 'currency_code', '币种']) ?? currency;
		if (!itemCurrency) return [];
		return [{ name, count, amount: formatMoneyMinor(amountMinor, itemCurrency), amountMinor }];
	});
}

function violationMessage(value: unknown): string | null {
	const record = asRecord(value);
	if (!record) return null;
	const code = directText([record], ['code', 'reason_code', 'type']);
	const messages: Record<string, string> = {
		category_not_allowed: '存在不在用户提供规则范围内的费用类别',
		amount_over_limit: '有单笔金额超过用户提供的限额',
		duplicate_in_input: '存在任务输入内重复票据',
		total_over_limit: '费用合计超过用户提供的总额限额',
		missing_receipt: '存在待补充凭证',
		amount_mismatch: '存在金额字段不一致',
	};
	return (code && messages[code]) || directText([record], ['message', 'detail', 'description', '说明']);
}

function todoRows(records: DataRecord[]): S09TodoItem[] {
	const values = directArray(records, ['pending_items', 'todo_items', 'missing_receipts', 'missing_documents', 'review_items', '待办事项', '待补凭证']);
	if (!values) return [];
	return values.flatMap((value) => {
		const record = asRecord(value);
		const title = record ? directText([record], ['title', 'name', 'item', 'document_name', '材料名称', '事项']) : cleanText(value);
		const detail = record ? directText([record], ['detail', 'reason', 'message', 'description', '说明']) : null;
		const sensitive = [title, detail].some((text) => text && (
			/(?:身份证|银行卡|银行账号|账户号码|税号|证件号)/.test(text) && /(?:\d[\s-]?){6,}/.test(text)
			|| /(?:\d[\s-]?){12,19}[Xx]?/.test(text)
		));
		if (!title || sensitive) return [];
		return [{ title, detail: detail ?? '本次结果记录为待处理事项。', status: '待处理' as const }];
	});
}

function unique<T>(items: T[], keyOf: (item: T) => string): T[] {
	const seen = new Set<string>();
	return items.filter((item) => {
		const key = keyOf(item);
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

function textResult(values: unknown[]): string | null {
	const candidates = values.map((value) => markdownText(value)).filter((item): item is string => typeof item === 'string' && !isJsonText(item));
	return candidates.sort((left, right) => right.length - left.length)[0] ?? null;
}

export function adaptS09ReimbursementRun(run: TaskRunRecord): S09ReimbursementResult {
	const importNodes = matchingNodes(run, 'expense.invoice_import', /invoice_import|recognize_invoice|导入结构化票据/);
	const dedupeNodes = matchingNodes(run, 'expense.invoice_verify_dedupe', /invoice_verify_dedupe|verify.*invoice|重复票据|验真与重复/);
	const ruleNodes = matchingNodes(run, 'expense.rule_check', /expense\.rule_check|check_expense_rules|费用规则/);
	const draftNodes = matchingNodes(run, '', /prepare_expense_draft|整理报销单草稿/);
	const packageNodes = matchingNodes(run, '', /produce_expense_submission_package|生成待提交报销材料|报销材料/);
	const importRecords = recordsFrom(nodePayloads(importNodes));
	const dedupeRecords = recordsFrom(nodePayloads(dedupeNodes));
	const ruleRecords = recordsFrom(nodePayloads(ruleNodes));
	const draftRecords = recordsFrom(nodePayloads([...draftNodes, ...packageNodes]));
	const artifacts = run.artifacts.filter((artifact) => Boolean(artifact.name));
	const reportTexts = [...new Set([run.final_output, ...artifacts.map((artifact) => artifact.preview_text), run.final_summary].map((text) => text.trim()).filter(Boolean))];
	const outputRecords = recordsFrom(reportTexts);
	const inputRecords = recordsIn(run.input);
	const ruleSummary = summaryRecord(ruleRecords, 'draft_rule_check', ['violations', 'total', 'total_amount']);
	const dedupeSummary = summaryRecord(dedupeRecords, 'deduplicated_within_task_input', ['duplicate_count', 'invoices']);

	const rulesValue = directValue(inputRecords, ['expense_rules_json', '费用规则']);
	const rulesRecord = asRecord(parsePayload(rulesValue));
	const projectId = directText(importRecords, ['project_id', 'projectId', '项目标识', '项目编号'])
		?? directText(outputRecords, ['project_id', 'projectId', '项目标识', '项目编号'])
		?? directText(inputRecords, ['project_id', 'projectId', '项目标识', '项目编号']);
	const expenseCategory = directText(inputRecords, ['expense_category', 'category', '报销类别', '费用类别'])
		?? directText(importRecords, ['expense_category', 'category', '费用类别']);
	const accountingPeriod = directText(inputRecords, ['accounting_period', 'period', '会计期间']);
	const currency = directText(ruleSummary ? [ruleSummary] : [], ['currency', 'currency_code', '币种'])
		?? directText(recordsIn(rulesRecord), ['currency', 'currency_code', '币种']);
	const rawTotal = directValue(ruleSummary ? [ruleSummary] : [], ['total', 'total_amount', 'expense_total', 'amount_total']);
	const totalMinor = parseMoneyMinor(rawTotal);
	const amount = totalMinor === null
		? directText(ruleSummary ? [ruleSummary] : [], ['total', 'total_amount', 'expense_total', 'amount_total'])
		: formatMoneyMinor(totalMinor, currency);
	const amountLabel = amount ? '费用规则计算合计（含税）' : null;

	const ruleInvoiceRows = parseInvoiceRows(ruleRecords);
	const dedupeInvoiceRows = parseInvoiceRows(dedupeRecords);
	const importedInvoiceRows = parseInvoiceRows(importRecords);
	const invoiceRows = ruleInvoiceRows.length ? ruleInvoiceRows : dedupeInvoiceRows.length ? dedupeInvoiceRows : parseInvoiceRows(importRecords);
	const inputInvoiceRows = parseInvoiceRows([], directValue(inputRecords, ['invoice_records_json', 'invoice_records', '票据明细', '发票明细']));
	const nodeExpenseCategories = categorySummaries(invoiceRows, currency);
	const importedExpenseCategories = categorySummaries(importedInvoiceRows, currency, true);
	const inputExpenseCategories = categorySummaries(inputInvoiceRows, currency, true);
	const fallbackCategoryCounts = categoryCountMap(nodeExpenseCategories);
	const reportCategoryTable = reportTexts
		.map((text) => parseCategorySummaryTable(text, currency, fallbackCategoryCounts))
		.find((items) => items.length > 0) ?? [];
	const reportCategoryRecords = categorySummariesFromRecords(outputRecords, currency, fallbackCategoryCounts);
	const reportInvoiceRows = parseInvoiceRows(outputRecords);
	const reportInvoiceCategories = categorySummaries(reportInvoiceRows, currency);
	const categorySources = [reportCategoryTable, reportCategoryRecords, reportInvoiceCategories, importedExpenseCategories, inputExpenseCategories, nodeExpenseCategories];
	const expenseCategories = categorySources.find((items) => items.some((item) => item.amountMinor !== null && item.amountMinor > 0))
		?? categorySources.find((items) => items.length > 0)
		?? [];
	const explicitInvoiceCount = directCount(ruleSummary ? [ruleSummary] : [], ['invoice_count', 'record_count', '票据数量'])
		?? directCount(dedupeRecords, ['invoice_count', 'record_count', '票据数量'])
		?? directCount(importRecords, ['invoice_count', 'record_count', '票据数量']);
	const invoiceCount = explicitInvoiceCount ?? (invoiceRows.length ? invoiceRows.length : null);
	const explicitDuplicateCount = directCount(dedupeSummary ? [dedupeSummary] : dedupeRecords, ['duplicate_count', 'duplicates_count', '重复数量'])
		?? directCount(ruleSummary ? [ruleSummary] : [], ['duplicate_count', 'duplicates_count', '重复数量']);
	const flaggedDuplicateCount = invoiceRows.filter((row) => row.duplicate === true).length;
	const duplicateCount = explicitDuplicateCount ?? (flaggedDuplicateCount > 0 ? flaggedDuplicateCount : null);
	const violationValues = directArray(ruleSummary ? [ruleSummary] : ruleRecords, ['violations', 'rule_violations', 'exceptions', '异常项']) ?? [];
	const violations = unique(violationValues.map(violationMessage).filter((item): item is string => Boolean(item)), (item) => item);
	const checkItems: S09CheckItem[] = [];

	const importStatus = directText(importRecords, ['status', 'import_status', '识别状态']);
	if (importNodes.length && importStatus) {
		const ocrPerformed = directBoolean(importRecords, ['ocr_performed', 'ocr_executed']);
		checkItems.push({
			title: '票据字段整理',
			detail: ocrPerformed === false ? '使用用户提供的结构化信息，未执行 OCR。' : '已读取本次返回的票据字段整理结果。',
			status: '已完成',
		});
	}
	const dedupeStatus = directText(dedupeRecords, ['status', 'check_status', '去重状态']);
	if (dedupeNodes.length && (dedupeStatus || duplicateCount !== null)) {
		checkItems.push({
			title: '任务输入内重复检查',
			detail: duplicateCount && duplicateCount > 0 ? `发现 ${duplicateCount} 条重复票据记录。` : '已完成输入内重复检查；不代表官方发票验真。',
			status: duplicateCount && duplicateCount > 0 ? '发现异常' : '已完成',
		});
	}
	const officialVerify = directBoolean([...importRecords, ...dedupeRecords, ...(ruleSummary ? [ruleSummary] : ruleRecords)], ['official_invoice_verification_performed', 'invoice_authenticity_verified', 'official_verification_performed']);
	if (officialVerify === false) {
		checkItems.push({ title: '发票官方验真', detail: '本次未接入税务或发票验真服务。', status: '未执行' });
	}
	const ruleStatus = directText(ruleSummary ? [ruleSummary] : ruleRecords, ['status', 'check_status', 'rule_check_status']);
	if (ruleNodes.length && ruleStatus) {
		checkItems.push({
			title: '费用规则核对',
			detail: violations.length ? violations.slice(0, 2).join('；') : '规则计算已完成；结果不代表财务审批或可报销金额。',
			status: violations.length ? '发现异常' : '已完成',
		});
	}
	for (const violation of violations) {
		checkItems.push({ title: '需核实的规则异常', detail: violation, status: '待复核' });
	}
	const safeCheckItems = unique(checkItems, (item) => `${item.title}|${item.detail}`).slice(0, 4);

	const todos = todoRows(draftRecords);
	if (duplicateCount !== null && duplicateCount > 0) {
		todos.push({ title: '重复票据待核实', detail: `本次输入中标记 ${duplicateCount} 条重复记录。`, status: '待复核' });
	}
	if (violations.length) {
		todos.push({ title: '费用规则异常待复核', detail: violations.slice(0, 2).join('；'), status: '待复核' });
	}
	const formalApproval = directBoolean(ruleSummary ? [ruleSummary] : ruleRecords, ['formal_approval', 'approved', 'approval_completed']);
	if (formalApproval === false) {
		todos.push({ title: '报销草稿尚未审批', detail: '需要按现有财务流程进行人工复核。', status: '待处理' });
	}
	const erpSubmitted = directBoolean(ruleSummary ? [ruleSummary] : ruleRecords, ['erp_submitted', 'submitted_to_erp']);
	if (erpSubmitted === false) {
		todos.push({ title: '尚未提交 ERP', detail: '本次结果仅整理报销草稿，不代表已提交或已付款。', status: '未执行' });
	}
	if (artifacts.length) {
		for (const artifact of artifacts.slice(0, 4)) {
			todos.push({ title: artifact.name, detail: `${artifact.format.toUpperCase()} 成果文件已生成。`, status: '已整理' });
		}
	}
	const safeTodos = unique(todos, (item) => `${item.title}|${item.detail}`).slice(0, 4);
	const draftProduced = hasNodeOutput([...draftNodes, ...packageNodes]);
	const markdown = textResult([run.final_output, ...nodePayloads([...importNodes, ...dedupeNodes, ...ruleNodes, ...draftNodes, ...packageNodes]), run.final_summary]);
	const moduleData: Record<S09Module, boolean> = {
		overview: Boolean(projectId || expenseCategory || accountingPeriod || amount || invoiceCount !== null),
		expenses: expenseCategories.length > 0,
		checks: safeCheckItems.length > 0,
		materials: artifacts.length > 0 || draftProduced || safeTodos.length > 0,
	};
	const hasStructuredContent = Object.values(moduleData).some(Boolean);
	const hasFullResult = Boolean(markdown || run.final_output.trim() || run.final_summary.trim() || artifacts.length);

	return {
		projectId,
		expenseCategory,
		accountingPeriod,
		amountLabel,
		amount,
		currency,
		invoiceCount,
		duplicateCount,
		expenseCategories,
		checkItems: safeCheckItems,
		todoItems: safeTodos,
		draftOutputAvailable: draftProduced,
		artifacts,
		markdown,
		markdownSupplement: hasStructuredContent ? markdownSupplement(markdown, moduleData) : null,
		moduleData,
		hasStructuredContent,
		hasFullResult,
	};
}
