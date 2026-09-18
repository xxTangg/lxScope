import { FileX, PlusCircle, Search, SearchX, Trash } from 'lucide-react';
import { useState } from 'react';

import type { PublishedResource, Skill } from '@/api';
import type { UploadOptions } from '@/api/workspace';
import { AddSkillDialog } from '@/components/dialog/AddSkillDialog.tsx';
import { ResourceDetailDrawer } from '@/components/drawer/ResourceDetailDrawer.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Button } from '@/components/ui/button';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import {
	Item,
	ItemActions,
	ItemContent,
	ItemDescription,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item';
import { useTranslation } from '@/i18n/useI18n.ts';

interface SkillPanelProps {
	/** The skills currently equipped in the workspace, used for detail bodies. */
	skills: Skill[];
	/** The administrator-published catalog visible to the current user. */
	publishedSkills?: PublishedResource[];
	/** The chat-side panel is a read-only view; admin changes happen elsewhere. */
	readOnly?: boolean;
	/** Whether the skill list is still loading. */
	loading?: boolean;
	/**
	 * Upload a picked folder as a skill.
	 *
	 * @param files - The folder's files, carrying `webkitRelativePath`.
	 * @param options - Progress and abort hooks.
	 */
	onUpload?: (files: File[], options?: UploadOptions) => Promise<void>;
	/**
	 * Install skills the user already has.
	 *
	 * @param skillIds - The library record ids to add.
	 */
	onAddFromLibrary?: (skillIds: string[]) => Promise<void>;
	/**
	 * Remove a skill by name.
	 *
	 * @param name - The skill name to remove.
	 */
	onRemove?: (name: string) => Promise<void>;
}

type DisplaySkill = {
	name: string;
	display_name: string;
	description: string;
	markdown: string;
	updated_at: number;
	tags: string[];
	author: string | null;
	icon_url: string | null;
	version: string | null;
	hub_id: string | null;
};

/**
 * Pure content body for the Skill dock panel: a search box and the
 * administrator-published list visible to the current user. In chat it is
 * intentionally read-only; all data arrives via props so it owns no data
 * fetching.
 *
 * Renders without its own header/border — the surrounding `Panel`
 * chrome (from `PanelDock`) provides those.
 *
 * @param skills - The skills to list.
 * @param loading - Whether the list is loading.
 * @param onUpload - Optional folder-upload callback for editable contexts.
 * @param onAddFromLibrary - Optional library-install callback.
 * @param onRemove - Optional remove-skill callback.
 * @returns The skill panel body.
 */
export function SkillPanel({
	skills,
	readOnly = true,
	loading = false,
	publishedSkills,
	onUpload,
	onAddFromLibrary,
	onRemove,
}: SkillPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [detailSkill, setDetailSkill] = useState<DisplaySkill | null>(null);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
	// The publication catalog is the source of truth for what this user may
	// see. The workspace is only used to enrich an item with its markdown
	// body when that skill has already been equipped for the current session.
	const workspaceByName = new Map(skills.map((skill) => [skill.name, skill]));
	const displayedSkills: DisplaySkill[] = publishedSkills
		? publishedSkills.map((resource) => {
				const workspaceSkill = workspaceByName.get(resource.name);
				return {
					name: resource.name,
					display_name: resource.display_name || resource.name,
					description: resource.description || workspaceSkill?.description || '',
					markdown: workspaceSkill?.markdown || '',
					updated_at: workspaceSkill?.updated_at || 0,
					tags: resource.tags,
					author: resource.author,
					icon_url: resource.icon_url,
					version: resource.version,
					hub_id: null,
				};
			})
		: skills.map((skill) => ({
				name: skill.name,
				display_name: skill.name,
				description: skill.description,
				markdown: skill.markdown,
				updated_at: skill.updated_at,
				tags: [],
				author: null,
				icon_url: null,
				version: null,
				hub_id: null,
			}));

	const filtered = search
		? displayedSkills.filter((skill) =>
				[skill.name, skill.display_name, skill.description]
					.join(' ')
					.toLowerCase()
					.includes(search.toLowerCase()),
			)
		: displayedSkills;

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-2">
			<span className="text-muted-foreground text-sm">{t('panel.skill.description')}</span>
			<InputGroup>
				<InputGroupInput
					placeholder={t('panel.skill.searchPlaceholder')}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
				/>
				<InputGroupAddon align="inline-end">
					<Search />
				</InputGroupAddon>
			</InputGroup>

			{loading ? (
				<div className="flex flex-1 items-center justify-center">
					<p className="text-muted-foreground text-sm">{t('panel.loading')}</p>
				</div>
			) : filtered.length === 0 ? (
				<PanelEmpty
					icon={search ? SearchX : FileX}
					title={search ? t('panel.search.emptyTitle') : t('panel.skill.emptyTitle')}
					description={
						search
							? t('panel.search.emptyDescription', { query: search })
							: t('panel.skill.emptyDescription')
					}
				/>
			) : (
				<div className="flex flex-col flex-1 min-h-0 overflow-y-auto scroll-fade gap-y-2">
					{filtered.map((skill) => {
						return (
							<Item
								key={skill.name}
								variant="outline"
								className="group/skill cursor-pointer transition-colors hover:bg-accent/50"
								role="button"
								tabIndex={0}
								onClick={() => setDetailSkill(skill)}
								onKeyDown={(event) => {
									if (event.key === 'Enter' || event.key === ' ') {
										event.preventDefault();
										setDetailSkill(skill);
									}
								}}
							>
								<ItemMedia>
									<Avatar className="rounded-md">
										<AvatarImage
												src={skill.icon_url ?? undefined}
												alt={skill.name}
												loading="lazy"
										/>
										<AvatarFallback className="rounded-md">
											{skill.name.slice(0, 1).toUpperCase()}
										</AvatarFallback>
									</Avatar>
								</ItemMedia>
								<ItemContent>
									<ItemTitle>
										<span className="truncate font-medium">{skill.display_name}</span>
										{skill.author && (
											<span className="text-xs text-muted-foreground">
												@{skill.author}
											</span>
										)}
									</ItemTitle>
									<ItemDescription className="line-clamp-2">
										{skill.description}
									</ItemDescription>
								</ItemContent>
								<ItemActions>
									{/* Only on hover: deleting is rare, and a
									    button on every row competes with the
									    content for attention. */}
											{!readOnly && onRemove && (
										<Button
											variant="secondary"
											size="icon-sm"
											className="opacity-0 transition-opacity group-hover/skill:opacity-100 focus-visible:opacity-100"
											onClick={(event) => {
												event.stopPropagation();
												setDeleteTarget(skill.name);
												setDeleteOpen(true);
											}}
											title={t('common.delete')}
										>
											<Trash className="size-3" />
										</Button>
									)}
								</ItemActions>
							</Item>
						);
					})}
				</div>
			)}

			{!readOnly && onUpload && onAddFromLibrary && (
				<AddSkillDialog
					present={new Set(skills.map((s) => s.name))}
					onUpload={onUpload}
					onAddFromLibrary={onAddFromLibrary}
				>
					<Button variant="default">
						<PlusCircle />
						{t('panel.skill.add')}
					</Button>
				</AddSkillDialog>
			)}

			{!readOnly && (
				<DeleteDialog
					open={deleteOpen}
					onOpenChange={setDeleteOpen}
					title={t('common.deleteTitle', {
						entity: t('dialog-mcp-delete.skillEntity'),
						name: deleteTarget ?? '',
					})}
					description={t('dialog-mcp-delete.skillDescription')}
					onConfirm={async () => {
						if (deleteTarget && onRemove) await onRemove(deleteTarget);
					}}
				/>
			)}

			<ResourceDetailDrawer
				skill={
					detailSkill
						? {
								name: detailSkill.name,
								display_name: detailSkill.display_name,
								description: detailSkill.description,
								tags: detailSkill.tags,
								updated_at: detailSkill.updated_at,
								version: detailSkill.version,
								author: detailSkill.author,
								icon_url: detailSkill.icon_url,
								hub_id: detailSkill.hub_id,
								markdown: detailSkill.markdown,
							}
						: null
				}
				onOpenChange={(open) => {
					if (!open) setDetailSkill(null);
				}}
			/>
		</div>
	);
}
