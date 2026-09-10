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
