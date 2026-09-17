import { Blocks, CheckCircle2, Plug, Search, Wrench } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import type { PublishedResource, ResourceKind } from '@/api';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { usePublishedResources } from '@/hooks/usePublishedResources';
import { useTranslation } from '@/i18n/useI18n';
import { avatarTint } from '@/utils/common';

interface PublishedResourcePageProps {
	kind: ResourceKind;
}

function ResourceCard({ resource, kind }: { resource: PublishedResource; kind: ResourceKind }) {
	const { t } = useTranslation();
	const navigate = useNavigate();
	const Icon = kind === 'mcp' ? Plug : Blocks;

	return (
		<article className="flex min-h-48 flex-col rounded-2xl border bg-card p-5 transition-shadow hover:shadow-md">
			<div className="flex items-start gap-3">
				<Avatar className="rounded-xl">
					<AvatarImage src={resource.icon_url ?? undefined} alt={resource.display_name ?? resource.name} />
					<AvatarFallback className="rounded-xl" style={avatarTint(resource.name)}>
						<Icon className="size-5" />
					</AvatarFallback>
				</Avatar>
				<div className="min-w-0">
					<h2 className="truncate font-heading text-base font-semibold">
						{resource.display_name || resource.name}
					</h2>
					<p className="mt-1 text-xs text-muted-foreground">
						{kind === 'mcp' ? t('resources.mcpConfigured') : t('resources.skillPublished')}
					</p>
				</div>
			</div>
			<p className="mt-4 line-clamp-3 text-sm leading-6 text-muted-foreground">
				{resource.description || t('resources.noDescription')}
			</p>
			<div className="mt-auto flex items-center justify-between gap-2 pt-4">
				<div className="flex min-w-0 flex-wrap items-center gap-1.5">
					<span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
						<CheckCircle2 className="size-3.5" />
						{t('resources.adminApproved')}
					</span>
					{resource.tags.slice(0, 2).map((tag) => (
						<Badge key={tag} variant="secondary" className="text-[10px]">
							{tag}
						</Badge>
					))}
				</div>
				<Button size="sm" onClick={() => navigate('/chat')}>
					<Wrench className="size-3.5" />
					{t('resources.useInChat')}
				</Button>
			</div>
		</article>
	);
}

export function PublishedResourcePage({ kind }: PublishedResourcePageProps) {
	const { t } = useTranslation();
	const { resources, loading, error, refetch } = usePublishedResources(kind);
	const [query, setQuery] = useState('');
	const isMcp = kind === 'mcp';

	const filtered = useMemo(() => {
		const needle = query.trim().toLowerCase();
		if (!needle) return resources;
		return resources.filter((resource) =>
			[
				resource.name,
				resource.display_name ?? '',
				resource.description,
				...resource.tags,
			]
				.join(' ')
				.toLowerCase()
				.includes(needle),
		);
	}, [query, resources]);

	return (
		<div className="flex size-full flex-col overflow-y-auto p-8">
			<div className="flex flex-wrap items-start justify-between gap-4 border-b pb-6">
				<div>
					<h1 className="font-heading text-2xl font-semibold">
						{isMcp ? t('resources.mcpTitle') : t('resources.skillTitle')}
					</h1>
					<p className="mt-2 text-sm text-muted-foreground">
						{isMcp ? t('resources.mcpDescription') : t('resources.skillDescription')}
					</p>
				</div>
				<Badge variant="outline">{t('resources.adminControlled')}</Badge>
			</div>

			<div className="relative mt-6 max-w-xl">
				<Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
				<Input
					value={query}
					onChange={(event) => setQuery(event.target.value)}
					placeholder={t('resources.searchPlaceholder')}
					className="h-11 pl-9"
				/>
			</div>

			<div className="mt-6 flex-1">
				{loading ? (
					<div className="flex justify-center py-16"><Spinner /></div>
				) : error ? (
					<Empty className="border-none py-16">
						<EmptyHeader>
							<EmptyMedia variant="icon"><Plug /></EmptyMedia>
							<EmptyTitle>{t('resources.loadFailedTitle')}</EmptyTitle>
							<EmptyDescription>{t('resources.loadFailedDescription')}</EmptyDescription>
						</EmptyHeader>
						<Button variant="outline" onClick={() => void refetch()}>{t('resources.retry')}</Button>
					</Empty>
				) : filtered.length === 0 ? (
					<Empty className="border-none py-16">
						<EmptyHeader>
							<EmptyMedia variant="icon"><Blocks /></EmptyMedia>
							<EmptyTitle>{t('resources.emptyTitle')}</EmptyTitle>
							<EmptyDescription>
								{query ? t('resources.emptySearchDescription') : t('resources.emptyDescription')}
							</EmptyDescription>
						</EmptyHeader>
					</Empty>
				) : (
					<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
						{filtered.map((resource) => (
							<ResourceCard key={resource.id} resource={resource} kind={kind} />
						))}
					</div>
				)}
			</div>
		</div>
	);
}
