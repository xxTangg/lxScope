import { ArrowUpRight, CalendarClock, Plus } from 'lucide-react';

import type { NextFire } from './schedule-utils';
import { formatDuration } from './schedule-utils';
import { Button } from '@/components/ui/button';
import i18n from '@/i18n';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	/** The soonest upcoming firing across all enabled schedules, or null. */
	next: NextFire | null;
	onOpen: (scheduleId: string) => void;
	onCreate: () => void;
}

/**
 * The time anchor of an agent calendar is the next run, not "what day is
 * today" — this bar puts that single most useful fact at the top instead of
 * a giant month caption.
 */
export function NextUpBar({ next, onOpen, onCreate }: Props) {
	const { t } = useTranslation();

	const dateLabel = next
		? next.at.toLocaleDateString(i18n.language, {
				month: 'long',
				day: 'numeric',
				weekday: 'short',
			})
		: '';
	const timeLabel = next
		? `${String(next.at.getHours()).padStart(2, '0')}:${String(next.at.getMinutes()).padStart(2, '0')}`
		: '';

	const remainingMs = next ? next.at.getTime() - Date.now() : 0;
	const countdown =
		remainingMs <= 60_000
			? t('schedule.nextUpDue')
			: t('schedule.nextUpIn', { value: formatDuration(remainingMs, i18n.language) });

	return (
		<div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-muted/40 px-3 py-2">
			<CalendarClock className="size-4 shrink-0 text-primary" />

			{next ? (
				<>
					<span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
						{t('schedule.nextUp')}
					</span>
					<span className="max-w-[16rem] truncate font-medium">{next.name}</span>
					<span className="font-mono text-xs text-muted-foreground">
						{dateLabel} · {timeLabel}
					</span>
					<span className="rounded-full bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
						{countdown}
					</span>
					<Button
						variant="ghost"
						size="sm"
						className="ml-auto"
						onClick={() => onOpen(next.scheduleId)}
					>
						{t('schedule.viewDetail')}
						<ArrowUpRight className="size-3.5" />
					</Button>
				</>
			) : (
				<>
					<span className="text-sm text-muted-foreground">
						{t('schedule.nextUpEmpty')}
					</span>
					<Button variant="outline" size="sm" className="ml-auto" onClick={onCreate}>
						<Plus className="size-3.5" />
						{t('common.create')}
					</Button>
				</>
			)}
		</div>
	);
}
