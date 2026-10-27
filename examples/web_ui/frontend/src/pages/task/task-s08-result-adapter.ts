import type { TaskArtifactRecord, TaskRunRecord } from '@/api';

type DataRecord = Record<string, unknown>;
type S08Module = 'overview' | 'directories' | 'integrity' | 'handover';
export type S08CheckStatus = '已核对' | '缺失材料' | '待补充' | '待人工确认' | '未检查';

export interface S08ArchiveFile {
	name: string;
	category: string | null;
	missingFields: string[];
	complete: boolean | null;
	duplicate: boolean | null;
}

export interface S08ArchiveDirectory {
	name: string;
	count: number | null;
	examples: string[];
	source: '建议分类' | '用户提供分类';
}

export interface S08IntegrityItem {
	title: string;
	detail: string;
	status: S08CheckStatus;
}

export interface S08ArchiveResult {
	projectName: string | null;
	projectId: string | null;
	archiveCategory: string | null;
	archiveScope: string | null;
	fileContentRead: boolean | null;
	files: S08ArchiveFile[];
	fileCount: number | null;
	fileCountLabel: string | null;
	directories: S08ArchiveDirectory[];
	directoryCount: number | null;
	integrityItems: S08IntegrityItem[];
	incompleteCount: number | null;
	handoverName: string | null;
	handoverCount: number | null;
	recipient: string | null;
	handoverMethod: string | null;
	handoverStatus: string | null;
	handoverSummary: string | null;
	statuses: string[];
	artifacts: TaskArtifactRecord[];
	markdown: string | null;
	markdownSupplement: string | null;
	moduleData: Record<S08Module, boolean>;
	hasStructuredContent: boolean;
	hasFullResult: boolean;
}

interface MarkdownSection {
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

function directCount(records: DataRecord[], aliases: string[]): number | null {
	const value = directValue(records, aliases);
	if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
	if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Number(value.trim());
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

function matchingNodes(run: TaskRunRecord, matcher: RegExp, succeededOnly = false): TaskRunRecord['node_runs'] {
	return run.node_runs.filter((node) => matcher.test(nodeLabel(run, node)) && (!succeededOnly || node.status === 'succeeded'));
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
	for (const key of ['markdown', 'report_markdown', 'manifest_markdown', 'content', 'text', 'final_output', 'body', 'output', 'result']) {
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

function selectMarkdown(run: TaskRunRecord, outputNodes: TaskRunRecord['node_runs']): string | null {
	const candidates = [run.final_output, ...nodePayloads(outputNodes), run.final_summary]
		.map((candidate) => markdownText(candidate))
		.filter((text): text is string => typeof text === 'string' && !isJsonText(text));
	return candidates.sort((left, right) => right.length - left.length)[0] ?? null;
}

function parseMarkdownSections(markdown: string): MarkdownSection[] {
	const sections: MarkdownSection[] = [];
	let current: { level: number; title: string; body: string[] } | null = null;
	let inCodeFence = false;
	for (const line of markdown.split('\n')) {
		if (/^\s*```/.test(line)) {
			inCodeFence = !inCodeFence;
			if (current) current.body.push(line);
			continue;
		}
		const match = !inCodeFence ? line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/) : null;
		if (match) {
			if (current) sections.push({ level: current.level, title: current.title, body: current.body.join('\n').trim() });
			current = { level: match[1].length, title: match[2].trim(), body: [] };
		} else if (current) {
			current.body.push(line);
		}
	}
	if (current) sections.push({ level: current.level, title: current.title, body: current.body.join('\n').trim() });
	return sections;
}

function sentenceSummary(value: unknown, maxLength = 180): string | null {
	const text = cleanText(value);
	if (!text || /^(?:```|\||>)/.test(text)) return null;
	const sentence = text.match(/^.*?[。！？!?；](?:\s|$)/)?.[0]?.trim();
	if (sentence && [...sentence].length <= maxLength) return sentence;
	return [...text].length <= maxLength ? text : null;
}

function textRows(value: unknown): DataRecord[] {
	const parsed = parsePayload(value);
	if (!Array.isArray(parsed)) return [];
	return parsed.map((item) => typeof item === 'string' ? { detail: item } : asRecord(item)).filter((item): item is DataRecord => Boolean(item));
}

function parseFileRows(value: unknown): S08ArchiveFile[] {
	const rows = textRows(value);
	return rows.flatMap((row) => {
		const name = directText([row], ['file_name', 'filename', 'name', '文件名', '材料名称']);
		if (!name || /[\\/]/.test(name)) return [];
		const missingValue = parsePayload(directValue([row], ['missing_fields', 'missing_metadata_fields', '缺失字段']));
		const missingFields = Array.isArray(missingValue)
			? missingValue.map(cleanText).filter((item): item is string => Boolean(item))
			: [];
		return [{
			name,
			category: directText([row], ['file_category', 'suggested_category', 'classified_category', 'category_name', 'category', '归档类别', '建议类别']),
			missingFields,
			complete: directBoolean([row], ['complete', 'metadata_complete', '完整']),
			duplicate: directBoolean([row], ['duplicate', 'is_duplicate', '重复']),
		}];
	});
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

function directoriesFromFiles(files: S08ArchiveFile[], source: S08ArchiveDirectory['source']): S08ArchiveDirectory[] {
	const groups = new Map<string, string[]>();
	for (const file of files) {
		if (!file.category) continue;
		const names = groups.get(file.category) ?? [];
		names.push(file.name);
		groups.set(file.category, names);
	}
	return [...groups.entries()].map(([name, names]) => ({
		name,
		count: names.length,
		examples: names.slice(0, 1),
		source,
	}));
}

function directoriesFromGroups(records: DataRecord[]): S08ArchiveDirectory[] {
	const groups = directArray(records, ['directories', 'categories', 'groups', 'classifications', 'file_classifications']);
	if (!groups?.length) return [];
	const directories = groups.flatMap((item) => {
		if (typeof item === 'string') {
			const name = cleanText(item);
			return name ? [{ name, count: null, examples: [], source: '建议分类' as const }] : [];
		}
		const record = asRecord(item);
		if (!record) return [];
		const name = directText([record], ['directory_name', 'suggested_category', 'category_name', 'category', 'name', 'title', '归档类别', '建议类别']);
		if (!name) return [];
		const files = parseFileRows(directValue([record], ['files', 'items', 'documents', 'file_list']));
		return [{
			name,
			count: directCount([record], ['file_count', 'count', 'item_count', '文件数量']) ?? (files.length ? files.length : null),
			examples: files.slice(0, 1).map((file) => file.name),
			source: '建议分类' as const,
		}];
	});
	if (directories.length) return directories;
	const rowFiles = records.flatMap((record) => {
		const name = directText([record], ['file_name', 'filename', '文件名']);
		const category = directText([record], ['suggested_category', 'classified_category', 'category_name', 'category', '建议类别', '归档类别']);
		return name && category && !/[\\/]/.test(name)
			? [{ name, category, missingFields: [], complete: null, duplicate: null }]
			: [];
	});
	return directoriesFromFiles(uniqueBy(rowFiles, (file) => `${file.category}|${file.name}`), '建议分类');
}

function markdownBullets(body: string): string[] {
	return body.split('\n')
		.filter((line) => /^\s*(?:[-*+]\s+|\d+[.)、]\s+)/.test(line))
		.map(cleanText)
		.filter((item): item is string => Boolean(item));
}

function directoriesFromMarkdown(sections: MarkdownSection[]): S08ArchiveDirectory[] {
	const section = sections.find((item) => /建议归档目录|智能归档目录|归档目录结构|档案分类建议|分类归档材料|文件分类建议|材料分类/.test(item.title));
	return section ? markdownBullets(section.body).slice(0, 4).map((name) => ({ name, count: null, examples: [], source: '建议分类' })) : [];
}

function integrityFromMarkdown(sections: MarkdownSection[]): S08IntegrityItem[] {
	const section = sections.find((item) => /归档完整性|材料完整性|完整性检查|完整性核对/.test(item.title));
	if (!section) return [];
	const bullets = markdownBullets(section.body);
	const summaries = bullets.length ? bullets : [sentenceSummary(section.body)].filter((item): item is string => Boolean(item));
	return summaries.slice(0, 4).map((detail) => ({
		title: /缺失|缺少/.test(detail) ? '发现缺失材料或字段' : /待补充/.test(detail) ? '待补充事项' : '检查摘要',
		detail,
		status: /缺失|缺少/.test(detail) ? '缺失材料' : /待补充/.test(detail) ? '待补充' : '待人工确认',
	}));
}

function moduleForHeading(title: string): S08Module | null {
	if (/归档概览|成果概览|归档基本信息/.test(title)) return 'overview';
	if (/归档目录|目录结构|档案分类建议|分类归档材料|建议分类|文件分类建议|材料分类/.test(title)) return 'directories';
	if (/完整性|材料核对|检查结果/.test(title)) return 'integrity';
	if (/移交清单|交接清单|待移交/.test(title)) return 'handover';
	return null;
}

function markdownSupplement(markdown: string | null, modules: Record<S08Module, boolean>): string | null {
	if (!markdown || Object.values(modules).every(Boolean)) return null;
	const sections = parseMarkdownSections(markdown);
	if (!sections.length) return markdown;
	const remaining: MarkdownSection[] = [];
	let consumedParentLevel: number | null = null;
	for (const section of sections) {
		if (consumedParentLevel !== null && section.level > consumedParentLevel) continue;
		consumedParentLevel = null;
		const module = moduleForHeading(section.title);
		if (module && modules[module]) {
			consumedParentLevel = section.level;
			continue;
		}
		remaining.push(section);
	}
	const body = remaining.map((section) => `## ${section.title}\n\n${section.body}`).filter((value) => value.trim().length > 0).join('\n\n');
	return body || null;
}

function buildResult(run: TaskRunRecord): S08ArchiveResult {
	const metadataNodes = matchingNodes(run, /extract_archive_metadata|archive\.document_metadata|提取文件元数据|规范化用户提交文件元数据/, true);
	const classificationNodes = matchingNodes(run, /classify_archive_materials|分类归档材料/, true);
	const checkNodes = matchingNodes(run, /check_archive_completeness|archive\.completeness_check|核对归档完整性/, true);
	const handoverNodes = matchingNodes(run, /prepare_transfer_manifest|produce_transfer_manifest|生成移交清单|生成待移交清单/, true);
	const writeNodes = matchingNodes(run, /write_archive_system|archive\.write|档案系统写入/, true).filter((node) => node.type === 'tool');
	const inputNodes = run.node_runs.filter((node) => node.type === 'input');
	const inputRecords = recordsFrom([run.input, ...inputNodes.map((node) => node.input), ...inputNodes.map((node) => node.output)]);
	const metadataRecords = recordsFrom(nodePayloads(metadataNodes));
	const classificationRecords = recordsFrom(nodePayloads(classificationNodes));
	const checkRecords = recordsFrom(nodePayloads(checkNodes));
	const handoverRecords = recordsFrom(nodePayloads(handoverNodes));
	const writeRecords = recordsFrom(nodePayloads(writeNodes));
	const allOutputRecords = recordsFrom([
		...nodePayloads([...metadataNodes, ...classificationNodes, ...checkNodes, ...handoverNodes]),
		run.final_output,
		run.final_summary,
	]);
	const rawInputFiles = parsePayload(directValue(inputRecords, ['archive_metadata_json', 'archive_files', 'file_metadata', 'files']));
	const inputFiles = parseFileRows(rawInputFiles);
	const metadataItems = parseFileRows(directArray(metadataRecords, ['items', 'files', 'file_records', 'assets']));
	const finalItems = parseFileRows(directArray(handoverRecords, ['items', 'files', 'file_records', 'manifest_items', 'handover_items']));
	const files = metadataItems.length ? metadataItems : inputFiles.length ? inputFiles : finalItems;
	const fileCount = metadataItems.length
		? metadataItems.length
		: inputFiles.length
			? inputFiles.length
			: directCount([...metadataRecords, ...handoverRecords], ['recognized_file_count', 'file_count', 'item_count', 'total_count', '文件数量'])
				?? (finalItems.length ? finalItems.length : null);
	const fileCountLabel = metadataItems.length
		? '已规范化元数据条目'
		: inputFiles.length
			? '用户提交元数据条目'
			: fileCount !== null
				? '结果返回文件条目'
				: null;
	let directories = directoriesFromGroups(classificationRecords);
	let directorySource: S08ArchiveDirectory['source'] = '建议分类';
	if (!directories.length && metadataItems.length) directories = directoriesFromFiles(metadataItems, '建议分类');
	if (!directories.length && inputFiles.some((file) => file.category)) {
		directories = directoriesFromFiles(inputFiles, '用户提供分类');
		directorySource = '用户提供分类';
	}
	const sections = run.final_output.trim() ? parseMarkdownSections(markdownText(run.final_output) ?? '') : [];
	const markdown = selectMarkdown(run, [...handoverNodes, ...classificationNodes, ...checkNodes, ...metadataNodes]);
	const markdownSections = markdown ? parseMarkdownSections(markdown) : sections;
	if (!directories.length) directories = directoriesFromMarkdown(markdownSections);
	const explicitCategoryCount = directCount(classificationRecords, ['classified_category_count', 'category_count', 'directory_count', '分类数量']);
	const directoryCount = directories.length > 0 ? directories.length : explicitCategoryCount;

	const checkItems = parseFileRows(directArray(checkRecords, ['items', 'files', 'file_records', 'results']));
	const requiredFields = directArray(checkRecords, ['required_fields', 'required_metadata_fields', '必需字段'])
		?.map(cleanText).filter((item): item is string => Boolean(item)) ?? [];
	const incompleteCount = directCount(checkRecords, ['incomplete_count', 'missing_count', 'incomplete_file_count', '缺失数量'])
		?? (checkItems.some((item) => item.missingFields.length || item.complete === false)
			? checkItems.filter((item) => item.missingFields.length || item.complete === false).length
			: null);
	const integrityItems: S08IntegrityItem[] = [];
	if (incompleteCount !== null && incompleteCount > 0) {
		integrityItems.push({
			title: `有 ${incompleteCount} 条文件元数据待补充`,
			detail: '依据本次返回的必需元数据字段检查结果。',
			status: '待补充',
		});
	}
	for (const item of checkItems) {
		if (item.missingFields.length) {
			integrityItems.push({
				title: `${item.name} · 元数据待补充`,
				detail: `缺少字段：${item.missingFields.join('、')}`,
				status: '待补充',
			});
		}
	}
	const checkStatus = directText(checkRecords, ['status', 'check_status', '状态']);
	if (!integrityItems.length && checkNodes.length > 0 && checkStatus && requiredFields.length > 0) {
		const fileContentRead = directBoolean([...metadataRecords, ...checkRecords], ['file_content_read', 'file_contents_read']);
		const sourceVerified = directBoolean(checkRecords, ['metadata_source_verified', 'source_verified']);
		const limitations = [
			fileContentRead === false ? '未读取文件内容' : null,
			sourceVerified === false ? '资料来源未验证' : null,
		].filter((item): item is string => Boolean(item));
		integrityItems.push({
			title: '必需元数据字段已核对',
			detail: `本次未发现必填字段缺项${limitations.length ? `；${limitations.join('，')}` : ''}。`,
			status: '已核对',
		});
	} else if (!integrityItems.length && (checkStatus || checkNodes.length > 0) && requiredFields.length === 0) {
		integrityItems.push({
			title: '完整性检查依据不足',
			detail: '本次结果未提供可确认的必需字段基准。',
			status: '待人工确认',
		});
	}
	const flaggedDuplicates = metadataItems.filter((item) => item.duplicate === true).length;
	const duplicateCount = directCount(metadataRecords, ['duplicate_count', 'duplicate_file_count', '重复数量'])
		?? (flaggedDuplicates > 0 ? flaggedDuplicates : null);
	if (duplicateCount !== null && duplicateCount > 0) {
		integrityItems.push({
			title: `发现 ${duplicateCount} 个重复文件名`,
			detail: '请人工确认是否为重复材料或不同版本。',
			status: '待人工确认',
		});
	}
	const safeIntegrityItems = uniqueBy(integrityItems, (item) => `${item.title}|${item.detail}`).slice(0, 4);
	const markdownIntegrity = safeIntegrityItems.length ? [] : integrityFromMarkdown(markdownSections);

	const artifacts = run.artifacts.filter((artifact) => Boolean(artifact.name));
	const transferName = directText(handoverRecords, ['manifest_name', 'handover_list_name', 'checklist_name', 'document_title', 'name', '清单名称', '移交清单名称'])
		?? (artifacts.length === 1 ? artifacts[0].name : null);
	const handoverCount = finalItems.length > 0
		? finalItems.length
		: directCount(handoverRecords, ['handover_count', 'transfer_count', 'file_count', 'item_count', 'total_count', '移交材料数量']);
	const recipient = directText(handoverRecords, ['receiving_unit', 'recipient', 'handover_to', 'receiver', '接收单位', '移交对象']);
	const handoverMethod = directText(handoverRecords, ['handover_method', 'transfer_method', '交接方式', '移交方式']);
	const systemRecords = recordsFrom(nodePayloads([...metadataNodes, ...checkNodes, ...writeNodes]));
	const writePerformed = directBoolean(writeRecords, ['archive_system_write_performed', 'archive_write_performed', 'system_write_completed'])
		?? directBoolean(systemRecords, ['archive_system_write_performed', 'archive_write_performed', 'system_write_completed']);
	const receiptId = directText(writeRecords, ['receipt_id', 'handover_id', 'transfer_id', '交接编号', '回执编号']);
	const completedFlag = directBoolean(writeRecords, ['handover_completed', 'transfer_completed', 'receipt_confirmed', '移交完成']);
	const transferArtifact = artifacts.some((artifact) => /归档|移交|档案|archive|handover|manifest/i.test(artifact.name));
	const manifestGenerated = handoverNodes.some((node) => nodePayloads([node]).length > 0)
		|| transferArtifact
		|| Boolean(transferName || finalItems.length);
	const handoverStatus = completedFlag === true && writePerformed === true && receiptId
		? '档案移交已完成'
		: writePerformed === true
			? '已提交写入，等待回执'
			: manifestGenerated && writePerformed === false
				? '待实际移交'
				: manifestGenerated
					? '移交准备清单已生成'
					: null;
	const handoverSummary = markdownSections.find((section) => /移交清单|交接清单|待移交/.test(section.title))
		? sentenceSummary(markdownSections.find((section) => /移交清单|交接清单|待移交/.test(section.title))?.body)
		: null;

	const projectName = directText(allOutputRecords, ['project_name', '项目名称']) ?? directText(inputRecords, ['project_name', 'projectName', '项目名称']);
	const fileContentRead = directBoolean([...metadataRecords, ...checkRecords], ['file_content_read', 'file_contents_read']);
	const projectId = directText(allOutputRecords, ['project_id', 'projectId', '项目标识', '项目编号'])
		?? directText(inputRecords, ['project_id', 'projectId', '项目标识', '项目编号']);
	const archiveCategory = directText(allOutputRecords, ['archive_category', '归档类别', '档案类别'])
		?? directText(inputRecords, ['archive_category', '归档类别', '档案类别']);
	const archiveScope = directText(allOutputRecords, ['archive_scope', 'scope_description', '归档范围'])
		?? directText(inputRecords, ['archive_scope', 'scope_description', '归档范围']);
	const statuses: string[] = [];
	if (metadataItems.length) statuses.push('文件元数据已整理');
	if (directories.length) statuses.push(directorySource === '用户提供分类' ? '已读取用户提供分类' : '归档分类建议已生成');
	if (manifestGenerated) statuses.push('待移交清单已生成');
	if (writePerformed === false) statuses.push('未写入档案系统');
	if (writePerformed === true && receiptId && completedFlag === true) statuses.push('档案移交已完成');
	const moduleData: Record<S08Module, boolean> = {
		overview: Boolean(projectName || projectId || archiveCategory || archiveScope || fileCount !== null || directoryCount !== null || statuses.length),
		directories: directories.length > 0 || explicitCategoryCount !== null,
		integrity: safeIntegrityItems.length > 0 || markdownIntegrity.length > 0,
		handover: Boolean(transferName || handoverCount !== null || recipient || handoverMethod || handoverStatus || handoverSummary || finalItems.length || artifacts.length),
	};
	const hasStructuredContent = Object.values(moduleData).some(Boolean);
	const supplement = hasStructuredContent ? markdownSupplement(markdown, moduleData) : null;
	const hasFullResult = Boolean(markdown || run.final_output.trim() || run.final_summary.trim() || artifacts.length);

	return {
		projectName,
		projectId,
		archiveCategory,
		archiveScope,
		fileContentRead,
		files,
		fileCount,
		fileCountLabel,
		directories: directories.slice(0, 4),
		directoryCount,
		integrityItems: (safeIntegrityItems.length ? safeIntegrityItems : markdownIntegrity).slice(0, 4),
		incompleteCount,
		handoverName: transferName,
		handoverCount: handoverCount,
		recipient,
		handoverMethod,
		handoverStatus,
		handoverSummary,
		statuses: uniqueBy(statuses, (status) => status),
		artifacts,
		markdown,
		markdownSupplement: supplement,
		moduleData,
		hasStructuredContent,
		hasFullResult,
	};
}

export function adaptS08ArchiveRun(run: TaskRunRecord): S08ArchiveResult {
	return buildResult(run);
}
