import {
	BarChart3,
	BookOpen,
	CheckCircle2,
	Eye,
	EyeOff,
	FileText,
	Presentation,
	Search,
	Trash2,
	UsersRound,
	Upload,
	type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { adminApi, skillApi } from '@/api';
import type { AdminUser, PublicationScope, ResourcePublication, SkillView } from '@/api';
import { AdminHeader } from './shared';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { ResourceDetailDrawer, type ResourceDetail } from '@/components/drawer/ResourceDetailDrawer';
import { Input } from '@/components/ui/input';
import {
	Popover,
	PopoverContent,
	PopoverDescription,
	PopoverHeader,
	PopoverTitle,
	PopoverTrigger,
} from '@/components/ui/popover';
import { Spinner } from '@/components/ui/spinner';
import { useTranslation } from '@/i18n/useI18n';

type BuiltinSkill = {
	id: string;
	title: string;
	type: string;
	category: string;
	description: string;
	Icon: LucideIcon;
	/** Optional fields used when the same card renders an admin-installed skill. */
	sourceRecordId?: string;
	resourceName?: string;
	tags?: string[];
};

const BUILTIN_SKILLS: BuiltinSkill[] = [
	{
		id: 'work-report',
		title: '工作汇报',
		type: 'PPT · 已预装 · 内容编写',
		category: '办公写作',
		description: '把工作进展组织成结论清晰的汇报。',
		Icon: FileText,
	},
	{
		id: 'project-initiation',
		title: '项目立项',
		type: 'PPT · 已预装 · 内容编写',
		category: '办公写作',
		description: '说明项目为什么做、如何做及需要的资源。',
		Icon: BookOpen,
	},
	{
		id: 'customer-solution',
		title: '客户方案',
		type: 'PPT · 已预装 · 内容编写',
		category: '办公写作',
		description: '以客户需求为中心组织销售解决方案。',
		Icon: Presentation,
	},
	{
		id: 'training-course',
		title: '培训课件',
		type: 'PPT · 已预装 · 内容编写',
		category: '办公写作',
		description: '把知识拆成可理解、可练习的课程。',
		Icon: BookOpen,
	},
	{
		id: 'research-report',
		title: '研究汇报',
		type: 'PPT · 已预装 · 内容编写',
		category: '分析与决策',
		description: '清晰呈现研究问题、依据和结论。',
		Icon: BarChart3,
	},
	{
		id: 'presentation-review',
		title: '演示内容审阅',
		type: 'PPT · 已预装 · 内容审阅',
		category: '汇报与演示',
		description: '检查演示结构、逻辑和信息密度。',
		Icon: Presentation,
	},
	{
		id: 'executive-brief',
		title: '领导简报',
		type: 'PPT · 已预装 · 内容编写',
		category: '汇报与演示',
		description: '把复杂材料压缩成快速决策所需的信息。',
		Icon: FileText,
	},
	{
		id: 'job-presentation',
		title: '述职演示',
		type: 'PPT · 已预装 · 内容编写',
		category: '汇报与演示',
		description: '把阶段成果、复盘和计划讲得有重点。',
		Icon: Presentation,
	},
	{
		id: 'roadmap-plan',
		title: '路线图与计划',
		type: 'PPT · 已预装 · 计划制定',
		category: '计划管理',
		description: '用阶段、依赖和验收点说明行动计划。',
		Icon: BookOpen,
	},
	{
		id: 'solution-comparison',
		title: '方案对比',
		type: 'PPT · 已预装 · 分析决策',
		category: '分析与决策',
		description: '用统一口径比较多个方案并给出建议。',
		Icon: BarChart3,
	},
	{
		id: 'speech-story',
		title: '演讲叙事',
		type: 'PPT · 已预装 · 内容编写',
		category: '汇报与演示',
		description: '增强开场、转场和收束的连续性。',
		Icon: Presentation,
	},
	{
		id: 'data-report',
		title: '数据汇报',
		type: 'PPT · 已预装 · 数据分析',
		category: '分析与决策',
		description: '让数据结论有口径、有依据、有行动。',
		Icon: BarChart3,
	},
];

const CATEGORY_ALL = 'all';

function publishRequest(
	skill: BuiltinSkill,
	scope: PublicationScope,
	userIds: string[] = [],
) {
	return {
		kind: 'skill' as const,
		source_id: skill.sourceRecordId ?? skill.id,
		source_record_id: skill.sourceRecordId,
		name: skill.resourceName ?? skill.title,
		display_name: skill.title,
		description: skill.description,
		tags: skill.tags ?? [skill.category],
		scope,
		user_ids: scope === 'selected' ? userIds : [],
	};
}

export function UserScopePicker({
	users,
	userIds,
	onChange,
}: {
	users: AdminUser[];
	userIds: string[];
	onChange: (userIds: string[]) => void;
}) {
	const { t } = useTranslation();
	const activeUsers = users.filter((user) => user.status === 'active');
	const selected = new Set(userIds);

	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button variant="outline" size="sm" className="max-w-full">
					<UsersRound className="size-3.5" />
					<span className="truncate">
						{t('admin.selectedUsers', { count: userIds.length })}
					</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent align="start" className="w-72">
				<PopoverHeader>
					<PopoverTitle>{t('admin.selectUsers')}</PopoverTitle>
					<PopoverDescription>{t('admin.selectedUsersHint')}</PopoverDescription>
				</PopoverHeader>
				<div className="flex items-center justify-between border-b pb-2">
					<span className="text-xs text-muted-foreground">
						{t('admin.selectedUsers', { count: userIds.length })}
					</span>
					<div className="flex gap-1">
						<Button
							variant="ghost"
							size="xs"
							onClick={() => onChange(activeUsers.map((user) => user.id))}
						>
							{t('admin.selectAllUsers')}
						</Button>
						<Button variant="ghost" size="xs" onClick={() => onChange([])}>
							{t('admin.clearUsers')}
						</Button>
					</div>
				</div>
				<div className="max-h-56 space-y-1 overflow-y-auto py-1">
					{activeUsers.map((user) => (
						<label
							key={user.id}
							className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted"
						>
							<Checkbox
								checked={selected.has(user.id)}
								onCheckedChange={(checked) => {
									const next = new Set(selected);
									if (checked === true) next.add(user.id);
									else next.delete(user.id);
									onChange(Array.from(next));
								}}
							/>
							<span className="truncate text-sm">{user.username}</span>
						</label>
					))}
				</div>
			</PopoverContent>
		</Popover>
	);
}

function BuiltinSkillCard({
	skill,
	publication,
	users,
	onSaved,
	onOpen,
	onRemove,
}: {
	skill: BuiltinSkill;
	publication?: ResourcePublication;
	users: AdminUser[];
	onSaved: () => void;
	onOpen: () => void;
	onRemove?: () => void;
}) {
	const { t } = useTranslation();
	const [scope, setScope] = useState<PublicationScope>(publication?.scope ?? 'none');
	const [userIds, setUserIds] = useState<string[]>(publication?.user_ids ?? []);
	const [saving, setSaving] = useState(false);
	const Icon = skill.Icon;

	useEffect(() => {
		setScope(publication?.scope ?? 'none');
		setUserIds(publication?.user_ids ?? []);
	}, [publication]);

	const save = async (nextScope = scope) => {
		if (nextScope === 'selected' && userIds.length === 0) return;
		setSaving(true);
		try {
			await adminApi.publishResource(publishRequest(skill, nextScope, userIds));
			onSaved();
		} finally {
			setSaving(false);
		}
	};

	return (
		<article className="group flex h-80 min-h-0 flex-col rounded-2xl border bg-card p-5 transition-shadow hover:shadow-md">
			<button
				type="button"
				onClick={onOpen}
				title={t('admin.viewSkillDetails')}
				className="flex min-w-0 items-start gap-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
			>
				<div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
					<Icon className="size-5" />
				</div>
				<div className="min-w-0">
					<h2 className="truncate font-heading text-base font-semibold">{skill.title}</h2>
					<p className="mt-1 text-xs text-muted-foreground">{skill.type}</p>
				</div>
			</button>
			<button
				type="button"
				onClick={onOpen}
				title={t('admin.viewSkillDetails')}
				className="mt-5 line-clamp-4 text-left text-sm leading-6 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
			>
				{skill.description}
			</button>
			<div className="mt-auto space-y-3 pt-5">
				<div className="flex flex-wrap items-center gap-2">
					<span className="text-xs text-muted-foreground">{t('admin.publishTo')}</span>
					<select
						value={scope}
						onChange={(event) => {
							const next = event.target.value as PublicationScope;
							setScope(next);
							if (next !== 'selected') {
								setUserIds([]);
								void save(next);
							}
						}}
						className="h-8 rounded-md border border-input bg-background px-2 text-xs"
					>
						<option value="none">{t('admin.publishNone')}</option>
						<option value="all">{t('admin.publishAll')}</option>
						<option value="selected">{t('admin.publishSelected')}</option>
					</select>
					{scope === 'selected' && (
						<>
							<UserScopePicker users={users} userIds={userIds} onChange={setUserIds} />
							<Button size="sm" disabled={saving || userIds.length === 0} onClick={() => void save()}>
								{saving ? <Spinner /> : <CheckCircle2 className="size-3.5" />}
								{t('admin.savePublication')}
							</Button>
						</>
					)}
				</div>
				<div className="flex items-center justify-between gap-2">
					<div className={`flex items-center gap-1 text-xs ${scope === 'none' ? 'text-muted-foreground' : 'text-emerald-600 dark:text-emerald-400'}`}>
						{scope === 'none' ? <EyeOff className="size-3.5" /> : <CheckCircle2 className="size-3.5" />}
						{scope === 'all'
							? t('admin.builtinSkillBadge')
							: scope === 'selected'
								? t('admin.selectedUsersBadge', { count: userIds.length })
								: t('admin.hiddenSkillBadge')}
					</div>
					{onRemove && (
						<Button
							variant="ghost"
							size="icon-sm"
							title={t('admin.removeInstalledSkill')}
							onClick={(event) => {
								event.stopPropagation();
								onRemove();
							}}
						>
							<Trash2 />
						</Button>
					)}
				</div>
			</div>
		</article>
	);
}

export function AdminSkillsPage() {
	const { t } = useTranslation();
	const navigate = useNavigate();
	const [query, setQuery] = useState('');
	const [category, setCategory] = useState(CATEGORY_ALL);
	const [publications, setPublications] = useState<ResourcePublication[]>([]);
	const [users, setUsers] = useState<AdminUser[]>([]);
	const [installedSkills, setInstalledSkills] = useState<SkillView[]>([]);
	const [loading, setLoading] = useState(true);
	const [bulkScope, setBulkScope] = useState<Exclude<PublicationScope, 'selected'> | null>(null);
	const [detailSkill, setDetailSkill] = useState<ResourceDetail | null>(null);
	const [detailLoading, setDetailLoading] = useState(false);
	const detailRequestRef = useRef(0);
	const [removeTarget, setRemoveTarget] = useState<BuiltinSkill | null>(null);

	const refetch = async () => {
		setLoading(true);
		try {
			const [nextPublications, nextUsers, nextInstalledSkills] = await Promise.all([
				adminApi.resourcePublications('skill'),
				adminApi.users({ page: 1, page_size: 100 }),
				skillApi.list(),
			]);
			setPublications(nextPublications.resources);
			setUsers(nextUsers.users);
			setInstalledSkills(nextInstalledSkills);
		} finally {
			setLoading(false);
		}
	};

	useEffect(() => {
		void refetch();
	}, []);

	const publicationBySource = useMemo(
		() => new Map(publications.map((publication) => [publication.source_id, publication])),
		[publications],
	);

	const categories = useMemo(
		() => [
			CATEGORY_ALL,
			...Array.from(new Set(BUILTIN_SKILLS.map((skill) => skill.category))),
		],
		[],
	);
	const filteredSkills = useMemo(() => {
		const normalizedQuery = query.trim().toLowerCase();
		return BUILTIN_SKILLS.filter((skill) => {
			const matchesCategory = category === CATEGORY_ALL || skill.category === category;
			const matchesQuery =
				!normalizedQuery ||
				[skill.title, skill.type, skill.category, skill.description]
					.join(' ')
					.toLowerCase()
					.includes(normalizedQuery);
			return matchesCategory && matchesQuery;
		});
	}, [category, query]);
	const installedSkillCards = useMemo<BuiltinSkill[]>(
		() =>
			installedSkills.map((skill) => ({
				id: skill.id,
				title: skill.display_name || skill.name,
				type: `${skill.hub_id || '本地'} · 已安装`,
				category: '已安装技能',
				description: skill.description,
				Icon: FileText,
				sourceRecordId: skill.id,
				resourceName: skill.name,
				tags: skill.tags,
			})),
		[installedSkills],
	);
	const allManagedSkills = useMemo(
		() => [...BUILTIN_SKILLS, ...installedSkillCards],
		[installedSkillCards],
	);

	const setAllSkillsScope = async (scope: Exclude<PublicationScope, 'selected'>) => {
		setBulkScope(scope);
		try {
			await Promise.all(
				allManagedSkills.map((skill) => adminApi.publishResource(publishRequest(skill, scope))),
			);
			await refetch();
		} finally {
			setBulkScope(null);
		}
	};

	const openSkillDetails = async (skill: BuiltinSkill) => {
		const requestId = ++detailRequestRef.current;
		const fallback: ResourceDetail = {
			name: skill.resourceName ?? skill.title,
			display_name: skill.title,
			description: skill.description,
			tags: skill.tags ?? [skill.category],
			version: skill.sourceRecordId ? null : 'builtin',
		};
		setDetailSkill(fallback);
		if (!skill.sourceRecordId) {
			setDetailLoading(false);
			return;
		}
		setDetailLoading(true);
		try {
			const record = await skillApi.get(skill.sourceRecordId);
			if (requestId !== detailRequestRef.current) return;
			setDetailSkill({
				name: record.name,
				display_name: record.display_name,
				description: record.description,
				tags: record.tags,
				author: record.author,
				icon_url: record.icon_url,
				hub_id: record.hub_id,
				version: record.version,
				url: record.url,
				markdown: record.markdown,
			});
		} finally {
			if (requestId === detailRequestRef.current) setDetailLoading(false);
		}
	};

	const removeInstalledSkill = async () => {
		if (!removeTarget?.sourceRecordId) return;
		// Withdraw the publication first so users do not retain a stale
		// published entry when the administrator removes the source record.
		await adminApi.removeSkill(removeTarget.sourceRecordId);
		setRemoveTarget(null);
		await refetch();
	};

	return (
		<>
			<AdminHeader
				title={t('admin.skillsPageTitle')}
				description={t('admin.skillsPageDescription')}
			/>
			<div className="flex flex-wrap items-center justify-between gap-3 border-b pb-5">
				<p className="text-sm text-muted-foreground">{t('admin.builtinSkillsNotice')}</p>
				<div className="flex flex-wrap items-center gap-2">
					<span className="text-xs text-muted-foreground">{t('admin.bulkVisibility')}</span>
					<Button
						variant="outline"
						size="sm"
						disabled={bulkScope !== null || allManagedSkills.length === 0}
						onClick={() => void setAllSkillsScope('all')}
					>
						{bulkScope === 'all' ? <Spinner /> : <Eye />}
						{t('admin.showAllSkills')}
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled={bulkScope !== null || allManagedSkills.length === 0}
						onClick={() => void setAllSkillsScope('none')}
					>
						{bulkScope === 'none' ? <Spinner /> : <EyeOff />}
						{t('admin.hideAllSkills')}
					</Button>
					<Button variant="outline" size="sm" onClick={() => navigate('/skill')}>
						<Upload />
						{t('admin.uploadSkill')}
					</Button>
				</div>
			</div>

			<div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(14rem,24rem)]">
				<div className="relative">
					<Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
					<Input
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						placeholder={t('admin.skillSearchPlaceholder')}
						className="h-11 pl-9"
					/>
				</div>
				<select
					value={category}
					onChange={(event) => setCategory(event.target.value)}
					className="h-11 rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
					aria-label={t('admin.skillCategory')}
				>
					{categories.map((item) => (
						<option key={item} value={item}>
							{item === CATEGORY_ALL ? t('admin.allCategories') : item}
						</option>
					))}
				</select>
			</div>

			{loading ? (
				<div className="flex justify-center py-16"><Spinner /></div>
			) : filteredSkills.length > 0 ? (
				<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
					{filteredSkills.map((skill) => {
						return (
							<BuiltinSkillCard
								key={skill.id}
								skill={skill}
								publication={publicationBySource.get(skill.id)}
								users={users}
								onSaved={() => void refetch()}
								onOpen={() => void openSkillDetails(skill)}
							/>
						);
					})}
				</div>
			) : (
				<div className="rounded-2xl border border-dashed p-12 text-center text-sm text-muted-foreground">
					{t('admin.noBuiltinSkills')}
				</div>
			)}
			{!loading && installedSkillCards.length > 0 && (
				<section className="mt-8 border-t pt-6">
					<h2 className="mb-4 font-heading text-lg font-semibold">{t('admin.installedSkillsTitle')}</h2>
					<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
						{installedSkillCards.map((skill) => (
							<BuiltinSkillCard
								key={`installed-${skill.id}`}
								skill={skill}
								publication={publicationBySource.get(skill.id)}
								users={users}
								onSaved={() => void refetch()}
								onOpen={() => void openSkillDetails(skill)}
								onRemove={() => setRemoveTarget(skill)}
							/>
						))}
					</div>
				</section>
			)}
			<ResourceDetailDrawer
				skill={detailSkill}
				loading={detailLoading}
				onOpenChange={(open) => {
					if (!open) {
						detailRequestRef.current += 1;
						setDetailSkill(null);
						setDetailLoading(false);
					}
				}}
			/>
			<DeleteDialog
				open={removeTarget !== null}
				onOpenChange={(open) => {
					if (!open) setRemoveTarget(null);
				}}
				title={t('admin.removeInstalledSkill')}
				description={t('admin.removeInstalledSkillDescription', {
					name: removeTarget?.title ?? '',
				})}
				confirmLabel={t('admin.removeSkill')}
				onConfirm={removeInstalledSkill}
			/>
		</>
	);
}
