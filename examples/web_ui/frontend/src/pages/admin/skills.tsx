import {
	BarChart3,
	BookOpen,
	CheckCircle2,
	FileText,
	Presentation,
	Search,
	Upload,
	type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { adminApi, skillApi } from '@/api';
import type { AdminUser, PublicationScope, ResourcePublication, SkillView } from '@/api';
import { AdminHeader } from './shared';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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

function BuiltinSkillCard({
	skill,
	publication,
	users,
	onSaved,
}: {
	skill: BuiltinSkill;
	publication?: ResourcePublication;
	users: AdminUser[];
	onSaved: () => void;
}) {
	const { t } = useTranslation();
	const [scope, setScope] = useState<PublicationScope>(publication?.scope ?? 'none');
	const [userIds, setUserIds] = useState<string[]>(publication?.user_ids ?? []);
	const [saving, setSaving] = useState(false);
	const Icon = skill.Icon;
	const sourceId = skill.sourceRecordId ?? skill.id;
	const resourceName = skill.resourceName ?? skill.title;

	useEffect(() => {
		setScope(publication?.scope ?? 'none');
		setUserIds(publication?.user_ids ?? []);
	}, [publication]);

	const save = async (nextScope = scope) => {
		if (nextScope === 'selected' && userIds.length === 0) return;
		setSaving(true);
		try {
			await adminApi.publishResource({
				kind: 'skill',
				source_id: sourceId,
				source_record_id: skill.sourceRecordId,
				name: resourceName,
				display_name: skill.title,
				description: skill.description,
				tags: skill.tags ?? [skill.category],
				scope: nextScope,
				user_ids: userIds,
			});
			onSaved();
		} finally {
			setSaving(false);
		}
	};

	return (
		<article className="group flex min-h-64 flex-col rounded-2xl border bg-card p-5 transition-shadow hover:shadow-md">
			<div className="flex items-start gap-3">
				<div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
					<Icon className="size-5" />
				</div>
				<div className="min-w-0">
					<h2 className="truncate font-heading text-base font-semibold">{skill.title}</h2>
					<p className="mt-1 text-xs text-muted-foreground">{skill.type}</p>
				</div>
			</div>
			<p className="mt-5 text-sm leading-6 text-muted-foreground">{skill.description}</p>
			<div className="mt-auto space-y-3 pt-5">
				<div className="flex flex-wrap items-center gap-2">
					<span className="text-xs text-muted-foreground">{t('admin.publishTo')}</span>
					<select
						value={scope}
						onChange={(event) => {
							const next = event.target.value as PublicationScope;
							setScope(next);
							if (next !== 'selected') void save(next);
						}}
						className="h-8 rounded-md border border-input bg-background px-2 text-xs"
					>
						<option value="none">{t('admin.publishNone')}</option>
						<option value="all">{t('admin.publishAll')}</option>
						<option value="selected">{t('admin.publishSelected')}</option>
					</select>
					{scope === 'selected' && (
						<Button size="sm" disabled={saving || userIds.length === 0} onClick={() => void save()}>
							{saving ? <Spinner /> : <CheckCircle2 className="size-3.5" />}
							{t('admin.savePublication')}
						</Button>
					)}
				</div>
				{scope === 'selected' && (
					<select
						multiple
						value={userIds}
						onChange={(event) =>
							setUserIds(Array.from(event.target.selectedOptions, (option) => option.value))
						}
						className="min-h-16 w-full rounded-md border border-input bg-background p-1 text-xs"
					>
						{users.map((user) => (
							<option key={user.id} value={user.id}>{user.username}</option>
						))}
					</select>
				)}
				<div className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
					<CheckCircle2 className="size-3.5" />
					{t('admin.builtinSkillBadge')}
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

	return (
		<>
			<AdminHeader
				title={t('admin.skillsPageTitle')}
				description={t('admin.skillsPageDescription')}
			/>
			<div className="flex flex-wrap items-center justify-between gap-3 border-b pb-5">
				<p className="text-sm text-muted-foreground">{t('admin.builtinSkillsNotice')}</p>
				<Button variant="outline" onClick={() => navigate('/skill')}>
					<Upload />
					{t('admin.uploadSkill')}
				</Button>
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
							/>
						))}
					</div>
				</section>
			)}
		</>
	);
}
