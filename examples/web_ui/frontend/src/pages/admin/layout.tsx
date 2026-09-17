import {
	BookOpen,
	Cable,
	Database,
	KeyRound,
	LayoutDashboard,
	Plug,
	ScrollText,
	Settings2,
	ShieldCheck,
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
	const items: AdminNavItem[] = [
		{ path: 'overview', label: t('admin.nav.overview'), Icon: LayoutDashboard },
		{ path: 'members', label: t('admin.nav.members'), Icon: Users },
		{ path: 'quota', label: t('admin.nav.quota'), Icon: Database },
		{ path: 'models', label: t('admin.nav.models'), Icon: KeyRound },
		{ path: 'skills', label: t('admin.nav.skills'), Icon: BookOpen },
		{ path: 'mcp', label: t('admin.nav.mcp'), Icon: Plug },
		{ path: 'policy', label: t('admin.nav.policy'), Icon: Settings2 },
		{ path: 'audit', label: t('admin.nav.audit'), Icon: ScrollText },
		{ path: 'upgrades', label: t('admin.nav.upgrades'), Icon: Wrench },
		{ path: 'sales-hub', label: t('admin.nav.salesHub'), Icon: Cable },
	];
	const current = items.find((item) => location.pathname.startsWith(`/admin/${item.path}`))?.path ?? 'overview';

	return (
		<div className="flex h-full min-h-0 flex-col overflow-hidden md:flex-row">
			<aside className="shrink-0 border-b bg-muted/20 md:w-56 md:border-r md:border-b-0">
				<div className="flex items-center gap-2 px-4 py-4">
					<ShieldCheck className="size-5" />
					<div>
						<div className="font-heading text-sm font-semibold">{t('admin.title')}</div>
						<div className="text-xs text-muted-foreground">{t('admin.nav.subtitle')}</div>
					</div>
				</div>
				<nav className="hidden space-y-1 px-2 pb-3 md:block">
					{items.map(({ path, label, Icon }) => (
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
				</nav>
				<div className="px-3 pb-3 md:hidden">
					<select
						value={current}
						onChange={(event) => navigate(`/admin/${event.target.value}`)}
						className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
					>
						{items.map((item) => <option key={item.path} value={item.path}>{item.label}</option>)}
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
