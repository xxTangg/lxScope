import {
	BarChart3,
	BookOpen,
	Bot,
	Cable,
	Coins,
	Database,
	KeyRound,
	LayoutDashboard,
	Network,
	Plug,
	ScrollText,
	Settings2,
	ShieldCheck,
	TriangleAlert,
	Users,
	Wrench,
	type LucideIcon,
} from 'lucide-react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useTranslation } from '@/i18n/useI18n';

type AdminNavItem = { path: string; label: string; Icon: LucideIcon };

export function AdminLayout() {
	const { t } = useTranslation();
	const navigate = useNavigate();
	const location = useLocation();
	const adminItems: AdminNavItem[] = [
		{ path: 'overview', label: t('admin.nav.overview'), Icon: LayoutDashboard },
		{ path: 'members', label: t('admin.nav.members'), Icon: Users },
		{ path: 'quota', label: t('admin.nav.quota'), Icon: Database },
		{ path: 'skills', label: t('admin.nav.skills'), Icon: BookOpen },
		{ path: 'policy', label: t('admin.nav.policy'), Icon: Settings2 },
		{ path: 'audit', label: t('admin.nav.audit'), Icon: ScrollText },
		{ path: 'upgrades', label: t('admin.nav.upgrades'), Icon: Wrench },
		{ path: 'sales-hub', label: t('admin.nav.salesHub'), Icon: Cable },
	];
	const observabilityItems: AdminNavItem[] = [
		{ path: '/admin/observability', label: t('admin.nav.observabilityOverview'), Icon: LayoutDashboard },
		{ path: '/admin/observability/requests', label: t('admin.nav.observabilityRequests'), Icon: BarChart3 },
		{ path: '/admin/observability/tokens', label: t('admin.nav.observabilityTokens'), Icon: Coins },
		{ path: '/admin/observability/users', label: t('admin.nav.observabilityUsers'), Icon: Users },
		{ path: '/admin/observability/model', label: t('admin.nav.observabilityModels'), Icon: Network },
		{ path: '/admin/observability/agent', label: t('admin.nav.observabilityAgents'), Icon: Bot },
		{ path: '/admin/observability/tool', label: t('admin.nav.observabilityTools'), Icon: Plug },
		{ path: '/admin/observability/skills', label: t('admin.nav.observabilitySkills'), Icon: BookOpen },
		{ path: '/admin/observability/failures', label: t('admin.nav.observabilityFailures'), Icon: TriangleAlert },
	];
	const isObservability = location.pathname.startsWith('/admin/observability');
	const observabilityGroup: AdminNavItem = observabilityItems[0];
	const observabilitySubItems = observabilityItems.slice(1);
	const mobileOptions: { path: string; label: string }[] = [
		...adminItems.map((item) => ({ path: `/admin/${item.path}`, label: item.label })),
		{ path: observabilityGroup.path, label: observabilityGroup.label },
		...observabilitySubItems.map((item) => ({ path: item.path, label: item.label })),
	];
	const panelTitle = t('admin.title');
	const panelSubtitle = t('admin.nav.subtitle');

	return (
		<div className="flex h-full min-h-0 flex-col overflow-hidden md:flex-row">
			<aside className="shrink-0 border-b bg-muted/20 md:w-56 md:border-r md:border-b-0">
				<div className="flex items-center gap-2 px-4 py-4">
					<ShieldCheck className="size-5" />
					<div>
						<div className="font-heading text-sm font-semibold">{panelTitle}</div>
						<div className="text-xs text-muted-foreground">{panelSubtitle}</div>
					</div>
				</div>
				<nav className="hidden space-y-1 px-2 pb-3 md:block">
					{adminItems.map(({ path, label, Icon }) => (
						<NavLink
							key={path}
							to={`/admin/${path}`}
							className={({ isActive }) =>
								`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
									isActive ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
								}`
							}
						>
							<Icon className="size-4" />
							{label}
						</NavLink>
					))}
					<div className="my-2 border-t border-border" />
					<div className="flex items-center gap-2 px-3 pb-1 pt-1">
						<BarChart3 className="size-4" />
						<span className="font-heading text-sm font-semibold">{t('admin.analyticsTitle')}</span>
					</div>
					{[observabilityGroup, ...observabilitySubItems].map(({ path, label, Icon }) => (
							<NavLink
								key={path}
								to={path}
								end={path === observabilityGroup.path}
								className={({ isActive }) =>
									`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
										isActive ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
									}`
								}
							>
								<Icon className="size-4" />
								{label}
							</NavLink>
						))}
					</nav>
				<div className="px-3 pb-3 md:hidden">
					<select
						value={location.pathname}
						onChange={(event) => navigate(event.target.value)}
						className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
					>
						{mobileOptions.map((item) => <option key={item.path} value={item.path}>{item.label}</option>)}
					</select>
				</div>
			</aside>
			<main className="min-w-0 flex-1 overflow-auto">
				<div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
					<Outlet />
				</div>
			</main>
		</div>
	);
}
