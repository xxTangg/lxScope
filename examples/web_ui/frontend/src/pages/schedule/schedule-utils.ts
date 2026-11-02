import { CronExpressionParser } from 'cron-parser';

import type { ScheduleRecord } from '@/api';

/** One upcoming (future) firing of a schedule. */
export interface NextFire {
	scheduleId: string;
	name: string;
	at: Date;
}

/**
 * Resolve the next firing time of every enabled schedule after `from`.
 *
 * The calendar is a view over cron jobs, so the anchor the user cares about
 * is "what runs next" — that has to be derived from the cron expression
 * itself, not from the month currently on screen.
 */
export function getNextFireTimes(schedules: ScheduleRecord[], from: Date): NextFire[] {
	const fromTs = from.getTime();
	const result: NextFire[] = [];

	for (const schedule of schedules) {
		if (!schedule.data.enabled) continue;
		try {
			const interval = CronExpressionParser.parse(schedule.data.cron_expression, {
				currentDate: from,
			});
			const startTs = new Date(schedule.data.started_at).getTime();
			const endTs = schedule.data.ended_at
				? new Date(schedule.data.ended_at).getTime()
				: null;

			for (let guard = 0; guard < 500 && interval.hasNext(); guard += 1) {
				const date = interval.next().toDate();
				const ts = date.getTime();
				if (ts <= fromTs) continue;
				if (ts < startTs) continue;
				if (endTs && ts > endTs) break;
				result.push({
					scheduleId: schedule.id,
					name: schedule.data.name,
					at: date,
				});
				break;
			}
		} catch {
			// invalid cron expr — skip, same policy as the calendar expansion
		}
	}

	return result.sort((a, b) => a.at.getTime() - b.at.getTime());
}

/** Human-readable countdown, e.g. "2 小时 15 分钟" / "2h 15m". */
export function formatDuration(ms: number, language: string): string {
	const totalMinutes = Math.max(1, Math.round(ms / 60_000));
	const days = Math.floor(totalMinutes / 1440);
	const hours = Math.floor((totalMinutes % 1440) / 60);
	const minutes = totalMinutes % 60;

	if (language.startsWith('zh')) {
		if (days > 0) return `${days} 天 ${hours} 小时`;
		if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
		return `${minutes} 分钟`;
	}

	if (days > 0) return `${days}d ${hours}h`;
	if (hours > 0) return `${hours}h ${minutes}m`;
	return `${minutes}m`;
}

export interface ParsedSchedule {
	frequency: 'daily' | 'weekly' | 'monthly' | 'once' | 'custom';
	time: string; // HH:mm format
	weekday?: number; // 0-6 for weekly
	dayOfMonth?: number; // 1-31 for monthly
	date?: Date; // for once
}

function getTimeZoneOffsetMs(date: Date, timeZone: string): number {
	const parts = new Intl.DateTimeFormat('en-US', {
		timeZone,
		calendar: 'iso8601',
		numberingSystem: 'latn',
		year: 'numeric',
		month: '2-digit',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit',
		hourCycle: 'h23',
	}).formatToParts(date);
	const values = Object.fromEntries(
		parts
			.filter((part) => part.type !== 'literal')
			.map((part) => [part.type, Number(part.value)]),
	);
	const asUtc = Date.UTC(
		values.year,
		values.month - 1,
		values.day,
		values.hour,
		values.minute,
		values.second,
	);
	return asUtc - date.getTime();
}

/**
 * Convert a date picked in the browser into the end of that calendar day in
 * the schedule's IANA timezone. The API stores an instant, while the picker
 * represents a timezone-independent calendar date.
 */
export function endOfDayInTimeZone(date: Date, timeZone: string): string {
	const wallTime = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59);
	let utcTime = wallTime;

	// Recalculate once after applying the offset so dates near a DST change use
	// the offset that is actually in effect at the requested local time.
	for (let i = 0; i < 3; i += 1) {
		utcTime = wallTime - getTimeZoneOffsetMs(new Date(utcTime), timeZone);
	}

	return new Date(utcTime + 999).toISOString();
}

export function parseCronExpression(cronExpression: string, startedAt: string): ParsedSchedule {
	const parts = cronExpression.trim().split(/\s+/);
	if (parts.length !== 5) {
		return { frequency: 'custom', time: '00:00' };
	}

	const [minute, hour, day, month, weekday] = parts;
	const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;

	if (day === '*' && month === '*' && weekday === '*') {
		return { frequency: 'daily', time };
	}

	if (day === '*' && month === '*' && weekday !== '*') {
		return { frequency: 'weekly', time, weekday: parseInt(weekday) };
	}

	if (day !== '*' && month === '*' && weekday === '*') {
		return { frequency: 'monthly', time, dayOfMonth: parseInt(day) };
	}

	if (day !== '*' && month !== '*') {
		return { frequency: 'once', time, date: new Date(startedAt) };
	}

	return { frequency: 'custom', time };
}

export function getFrequencyLabel(parsed: ParsedSchedule, t: (key: string) => string): string {
	switch (parsed.frequency) {
		case 'daily':
			return t('schedule.freqDaily');
		case 'weekly':
			return t('schedule.freqWeekly');
		case 'monthly':
			return t('schedule.freqMonthly');
		case 'once':
			return t('schedule.freqOnce');
		default:
			return 'Custom';
	}
}
