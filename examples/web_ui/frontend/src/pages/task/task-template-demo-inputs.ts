// These copies keep demo inputs available in the standalone frontend container.
import s01ProjectInit from './demo-inputs/s01-project-init.v2.json?raw';
import s02ProjectPlanning from './demo-inputs/s02-project-planning.v2.json?raw';
import s03ExpertMatching from './demo-inputs/s03-expert-matching.v3.json?raw';
import s04MeetingPreparation from './demo-inputs/s04-meeting-preparation.v3.json?raw';
import s05MeetingMinutes from './demo-inputs/s05-meeting-minutes.v3.json?raw';
import s06OpinionProcessing from './demo-inputs/s06-opinion-processing.v3.json?raw';
import s07ReportGeneration from './demo-inputs/s07-report-generation.v3.json?raw';
import s08ArchiveTransfer from './demo-inputs/s08-archive-transfer.v3.json?raw';
import s09ExpenseSettlement from './demo-inputs/s09-expense-settlement.v3.json?raw';

export interface TaskTemplateDemoInput {
	template_version: string;
	inputs: Record<string, unknown>;
}

const demoInputSources: Record<string, string> = {
	s01_project_init: s01ProjectInit,
	s02_project_planning: s02ProjectPlanning,
	s03_expert_matching: s03ExpertMatching,
	s04_meeting_preparation: s04MeetingPreparation,
	s05_meeting_minutes: s05MeetingMinutes,
	s06_opinion_processing: s06OpinionProcessing,
	s07_report_generation: s07ReportGeneration,
	s08_archive_transfer: s08ArchiveTransfer,
	s09_expense_settlement: s09ExpenseSettlement,
};

export function getTaskTemplateDemoInput(templateId: string): TaskTemplateDemoInput | null {
	const raw = demoInputSources[templateId];
	if (!raw) return null;

	const parsed: unknown = JSON.parse(raw);
	if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
	const candidate = parsed as { template_version?: unknown; inputs?: unknown };
	if (
		typeof candidate.template_version !== 'string' ||
		!candidate.inputs ||
		typeof candidate.inputs !== 'object' ||
		Array.isArray(candidate.inputs)
	) {
		return null;
	}

	return {
		template_version: candidate.template_version,
		inputs: candidate.inputs as Record<string, unknown>,
	};
}

export function formatTaskTemplateDemoAnswer(key: string, value: unknown): string | null {
	if (key.endsWith('_json')) {
		let parsed = value;
		if (typeof parsed === 'string') {
			try {
				parsed = JSON.parse(parsed) as unknown;
			} catch {
				return null;
			}
		}
		if (!parsed || typeof parsed !== 'object') return null;
		return JSON.stringify(parsed, null, 2) ?? null;
	}

	if (Array.isArray(value)) {
		if (
			value.some(
				(item) =>
					typeof item !== 'string' &&
					typeof item !== 'number' &&
					typeof item !== 'boolean',
			)
		) {
			return JSON.stringify(value, null, 2) ?? null;
		}
		return value.map(String).join('\n');
	}
	if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
		return String(value);
	}
	if (value && typeof value === 'object') return JSON.stringify(value, null, 2) ?? null;
	return null;
}

export function containsTaskTemplateDemoPlaceholder(value: unknown): boolean {
	if (typeof value === 'string') {
		return value.includes('__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__');
	}
	if (!value || typeof value !== 'object') return false;
	return Object.values(value).some(containsTaskTemplateDemoPlaceholder);
}
