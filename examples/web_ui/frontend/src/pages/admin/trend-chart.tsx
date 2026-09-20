import { useMemo } from 'react';

import type { ObservabilityDaily } from '@/api/admin';
import { formatNumber } from '@/utils/common';

export type TrendChartSeries = {
	key: string;
	label: string;
	colorClass: string;
	axis?: 'left' | 'right';
	value: (row: ObservabilityDaily) => number;
	formatValue?: (value: number) => string;
};

type TrendChartProps = {
	rows: ObservabilityDaily[];
	series: TrendChartSeries[];
	emptyText: string;
};

function niceMax(value: number): number {
	if (value <= 0) return 1;
	const magnitude = 10 ** Math.floor(Math.log10(value));
	const normalized = value / magnitude;
	const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
	return step * magnitude;
}

function ticks(max: number): number[] {
	return [1, 0.75, 0.5, 0.25, 0].map((ratio) => max * ratio);
}

function Axis({ max, side }: { max: number; side: 'left' | 'right' }) {
	return (
		<div className={`flex h-[220px] w-14 shrink-0 flex-col justify-between pt-10 pb-9 text-[10px] text-muted-foreground ${side === 'left' ? 'pr-2 text-right' : 'pl-2 text-left'}`}>
			{ticks(max).map((value) => <span key={value}>{formatNumber(value)}</span>)}
		</div>
	);
}

export function TrendChart({ rows, series, emptyText }: TrendChartProps) {
	const leftSeries = series.filter((item) => item.axis !== 'right');
	const rightSeries = series.filter((item) => item.axis === 'right');
	const leftMax = useMemo(() => niceMax(Math.max(...rows.flatMap((row) => leftSeries.map((item) => item.value(row))), 0)), [leftSeries, rows]);
	const rightMax = useMemo(() => niceMax(Math.max(...rows.flatMap((row) => rightSeries.map((item) => item.value(row))), 0)), [rightSeries, rows]);

	if (!rows.length) return <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">{emptyText}</div>;

	return (
		<div>
			<div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
				{series.map((item) => <span key={item.key} className="inline-flex items-center gap-1"><span className={`size-2 rounded-full ${item.colorClass}`} />{item.label}</span>)}
			</div>
			<div className="flex min-w-0 items-stretch">
				<Axis max={leftMax} side="left" />
				<div className="min-w-0 flex-1 overflow-x-auto">
					<div className="relative min-w-[640px]">
						<div className="pointer-events-none absolute inset-x-0 top-10 bottom-9">
							{ticks(leftMax).map((value, index) => <div key={value} className="absolute inset-x-0 border-t border-dashed border-muted-foreground/20" style={{ top: `${index * 25}%` }} />)}
						</div>
						<div className="relative flex h-[220px] items-end gap-2 px-2 pt-10 pb-9">
							{rows.map((row) => (
								<div key={row.date} className="group relative flex min-w-10 flex-1 items-end justify-center self-stretch" title={row.date}>
									<div className="pointer-events-none absolute left-1/2 top-0 z-20 hidden min-w-max -translate-x-1/2 rounded-md border bg-popover px-2.5 py-2 text-[10px] text-popover-foreground shadow-lg group-hover:block">
										<div className="mb-1 font-medium">{row.date}</div>
										{series.map((item) => {
											const value = item.value(row);
											return <div key={item.key} className="flex items-center justify-between gap-3"><span className="flex items-center gap-1.5"><span className={`size-1.5 rounded-full ${item.colorClass}`} />{item.label}</span><span className="font-mono">{(item.formatValue ?? formatNumber)(value)}</span></div>;
										})}
									</div>
									<div className="flex h-full items-end justify-center gap-1">
										{series.map((item) => {
											const value = Math.max(item.value(row), 0);
											const max = item.axis === 'right' ? rightMax : leftMax;
											return <div key={item.key} className={`w-3 rounded-t ${item.colorClass}`} style={{ height: `${value ? Math.max((value / max) * 100, 2) : 0}%` }} />;
										})}
									</div>
								</div>
							))}
						</div>
						<div className="flex gap-2 px-2 text-[10px] text-muted-foreground">
							{rows.map((row) => <span key={row.date} className="min-w-10 flex-1 text-center">{row.date.slice(5)}</span>)}
						</div>
					</div>
				</div>
				{rightSeries.length ? <Axis max={rightMax} side="right" /> : <div className="w-14 shrink-0" />}
			</div>
		</div>
	);
}
