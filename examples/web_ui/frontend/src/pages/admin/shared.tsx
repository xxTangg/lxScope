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
}: {
	label: string;
	value: string | number;
	Icon: LucideIcon;
}) {
	return (
		<Card>
			<CardContent className="flex items-center justify-between">
				<div>
					<div className="text-xs text-muted-foreground">{label}</div>
					<div className="mt-1 font-mono text-lg font-semibold">{value}</div>
				</div>
				<Icon className="size-5 text-muted-foreground" />
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
