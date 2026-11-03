import { Plus } from 'lucide-react';

import type { ScheduleEvent } from './event';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet';
import i18n from '@/i18n';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	date: Date | null;
	events: ScheduleEvent[];
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onEventClick: (event: ScheduleEvent) => void;
	onCreateOnDate: (date: Date) => void;
}

function startOfDay(value: Date): number {
	return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

/**
 * Clicking a day answers "what runs on this day" first — the opposite of a
 * human calendar, where clicking a blank slot starts creating an event.
 * Creating is kept as a secondary action at the bottom.
 */
export function DaySheet({
	date,
	events,
	open,
	onOpenChange,
	onEventClick,
	onCreateOnDate,
}: Props) {
	const { t } = useTranslation();

	const sorted = [...events].sort((a, b) => a.time.localeCompare(b.time));
	const todayTs = startOfDay(new Date());
	const dateTs = date ? startOfDay(date) : 0;
	const dayOffset = Math.round((dateTs - todayTs) / 86_400_000);

	const weekday = date
		? date.toLocaleDateString(i18n.language, { weekday: 'long' })
		: '';
	const title = date
		? date.toLocaleDateString(i18n.language, { month: 'long', day: 'numeric' })
		: '';

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent side="right" className="flex flex-col sm:max-w-md">
				<SheetHeader>
					<SheetTitle className="flex items-center gap-2">
						{title}
						<span className="text-sm font-normal text-muted-foreground">
							{weekday}
						</span>
						{dayOffset === 0 && <Badge variant="default">{t('schedule.today')}</Badge>}
						{dayOffset === 1 && (
							<Badge variant="secondary">{t('schedule.tomorrow')}</Badge>
						)}
					</SheetTitle>
					<SheetDescription>
						{sorted.length > 0
							? t('schedule.daySheetCount', { count: sorted.length })
							: t('schedule.daySheetEmpty')}
					</SheetDescription>
				</SheetHeader>

				<div className="-mx-4 flex-1 overflow-y-auto px-4">
					{sorted.length === 0 ? (
						<div className="flex h-full flex-col items-center justify-center gap-1 text-center text-sm text-muted-foreground">
							<span>{t('schedule.daySheetEmpty')}</span>
						</div>
					) : (
						<div className="space-y-1">
							{sorted.map((event) => (
								<button
									key={event.id}
									type="button"
									className="flex w-full flex-col gap-0.5 rounded-md px-2.5 py-2 text-left ring ring-border transition-colors hover:bg-muted/60"
									onClick={() => onEventClick(event)}
								>
									<div className="flex w-full items-center gap-2">
										<span className="font-mono text-xs text-muted-foreground">
											{event.time}
										</span>
										<span className="truncate font-medium">{event.title}</span>
									</div>
									{event.content && (
										<span className="line-clamp-2 text-xs text-muted-foreground">
											{event.content}
										</span>
									)}
								</button>
							))}
						</div>
					)}
				</div>

				<SheetFooter>
					<Button
						variant="outline"
						size="sm"
						disabled={!date}
						onClick={() => date && onCreateOnDate(date)}
					>
						<Plus className="size-3.5" />
						{t('schedule.createOnDay')}
					</Button>
				</SheetFooter>
			</SheetContent>
		</Sheet>
	);
}
