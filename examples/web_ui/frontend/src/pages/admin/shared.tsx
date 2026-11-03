import { ChevronLeft, ChevronRight, Loader2, RefreshCw, type LucideIcon } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';

export function AdminErrorNotice({ message }: { message?: string }) {
	if (!message) return null;
	return (
		<Alert variant="destructive">
			<AlertDescription>{message}</AlertDescription>
		</Alert>
	);
}

export function AdminHeader({
	title,
	description,
	onRefresh,
	loading = false,
}: {
	title: string;
	description?: string;
	onRefresh?: () => void;
	loading?: boolean;
}) {
	const { t } = useTranslation();
	return (
		<div className="flex flex-wrap items-start justify-between gap-4">
			<div>
				<h1 className="font-heading text-xl font-semibold">{title}</h1>
				{description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
			</div>
			{onRefresh && (
				<Button variant="outline" onClick={onRefresh} disabled={loading}>
					<RefreshCw className={loading ? 'animate-spin' : ''} />
					{t('admin.refresh')}
				</Button>
			)}
		</div>
	);
}

export function MetricCard({
	label,
	value,
	Icon,
	onClick,
	hint,
	emphasis = false,
}: {
	label: string;
	value: string | number;
	Icon: LucideIcon;
	onClick?: () => void;
	/** 次级说明：口径或构成，避免主数字被误读。 */
	hint?: string;
	/** 强调态：用于口径修正后需要突出对比的主指标。 */
	emphasis?: boolean;
}) {
	const interactive = Boolean(onClick);
	return (
		<Card
			className={[
				interactive ? 'cursor-pointer transition-colors hover:border-primary/50 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50' : '',
				emphasis ? 'border-primary/40 bg-primary/5' : '',
			].filter(Boolean).join(' ') || undefined}
			role={interactive ? 'button' : undefined}
			tabIndex={interactive ? 0 : undefined}
			onClick={onClick}
			onKeyDown={(event) => {
				if (interactive && (event.key === 'Enter' || event.key === ' ')) onClick?.();
			}}
		>
			<CardContent className="flex items-center justify-between">
				<div className="min-w-0">
					<div className="text-xs text-muted-foreground">{label}</div>
					<div className="mt-1 font-mono text-lg font-semibold">{value}</div>
					{hint && <div className="mt-1 truncate text-xs text-muted-foreground/80">{hint}</div>}
				</div>
				<Icon className={`size-5 flex-none ${emphasis ? 'text-primary' : 'text-muted-foreground'}`} />
			</CardContent>
		</Card>
	);
}

export function Pagination({
	page,
	pageSize,
	total,
	onPageChange,
	loading = false,
}: {
	page: number;
	pageSize: number;
	total: number;
	onPageChange: (page: number) => void;
	loading?: boolean;
}) {
	const pages = Math.max(1, Math.ceil(total / pageSize));
	return (
		<div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
			<span>{total} · {page}/{pages}</span>
			<div className="flex gap-1">
				<Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => onPageChange(page - 1)}>
					<ChevronLeft />
				</Button>
				<Button variant="outline" size="sm" disabled={page >= pages || loading} onClick={() => onPageChange(page + 1)}>
					<ChevronRight />
				</Button>
			</div>
		</div>
	);
}

export function AdminLoading() {
	return <div className="flex h-32 items-center justify-center"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
}
