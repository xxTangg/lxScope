import {
	BarChart3,
	BookText,
	BotMessageSquare,
	Cable,
	Calendars,
	ClipboardList,
	Compass,
	KeyRound,
	Languages,
	LibraryBig,
	LogOut,
	Repeat2,
	Settings,
	ShieldCheck,
	UserRound,
} from 'lucide-react';
import { useOnborda } from 'onborda';
import { useNavigate, useLocation } from 'react-router-dom';

import AgentScope from '@/assets/images/agentscope.svg?react';
import MCPSvg from '@/assets/images/mcp.svg?react';
import { Badge } from '@/components/ui/badge';
import { CHAT_TOUR_NAME } from '@/components/tour/chatTourSteps';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
	SidebarTrigger,
} from '@/components/ui/sidebar';
import { useAuth } from '@/hooks/useAuth';
import i18n from '@/i18n';
import { useTranslation } from '@/i18n/useI18n';

export function AppSidebar() {
	const navigate = useNavigate();
	const location = useLocation();
	const { t } = useTranslation();
	const { startOnborda } = useOnborda();
	const { user, logout, switchAccount, logtoEnabled } = useAuth();
	const isObservability = location.pathname.startsWith('/admin/observability');
	const isAdmin = user?.role === 'admin';

	const handleStartTour = () => {
		if (!location.pathname.startsWith('/chat')) {
			// Page not mounted yet — leave a flag, navigate, and let the
			// ChatTourController auto-trigger after ChatPage mounts.
			sessionStorage.setItem('force_tour', '1');
			navigate('/chat');
		} else {
			startOnborda(CHAT_TOUR_NAME);
		}
	};

	const handleToggleLanguage = () => {
		const next = i18n.language.startsWith('zh') ? 'en' : 'zh';
		i18n.changeLanguage(next);
	};

	const handleLogout = async () => {
		await logout();
		if (!logtoEnabled) navigate('/login', { replace: true });
	};

	const handleSwitchAccount = async () => {
		await switchAccount();
		if (!logtoEnabled) navigate('/login', { replace: true });
	};

	return (
		<Sidebar collapsible="icon" className="bg-transparent">
			<SidebarHeader>
				<div className="flex items-center gap-1.5 px-1 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
					<AgentScope className="size-8 shrink-0 group-data-[collapsible=icon]:hidden" />
					<SidebarTrigger className="shrink-0" />
				</div>
			</SidebarHeader>
			<SidebarContent>
				<SidebarGroup>
					<SidebarGroupLabel>{t('common.sidebarWorkspace')}</SidebarGroupLabel>
					<SidebarGroupContent>
						<SidebarMenu>
							<SidebarMenuItem key={'chat'}>
								<SidebarMenuButton
									tooltip={{ children: t('common.chat') }}
									isActive={
										location.pathname === '/chat' ||
										location.pathname.startsWith('/chat/')
									}
									onClick={() => navigate('/chat')}
								>
									<BotMessageSquare />
									<span>{t('common.chat')}</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.tasks') }}
									isActive={location.pathname.startsWith('/task')}
									onClick={() => navigate('/task')}
								>
									<ClipboardList />
									<span>{t('common.tasks')}</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
							{isAdmin && (
								<SidebarMenuItem>
									<SidebarMenuButton
										tooltip={{ children: t('admin.title') }}
										isActive={
											location.pathname.startsWith('/admin') && !isObservability
										}
										onClick={() => navigate('/admin/overview')}
									>
										<ShieldCheck />
										<span>{t('admin.title')}</span>
										<Badge
											variant="secondary"
											className="ml-auto shrink-0 px-1.5 text-[10px] font-medium group-data-[collapsible=icon]:hidden"
										>
											{t('common.adminBadge')}
										</Badge>
									</SidebarMenuButton>
								</SidebarMenuItem>
							)}
							{isAdmin && (
								<SidebarMenuItem>
									<SidebarMenuButton
										tooltip={{ children: t('admin.analyticsTitle') }}
										isActive={isObservability}
										onClick={() => navigate('/admin/observability')}
									>
										<BarChart3 />
										<span>{t('admin.analyticsTitle')}</span>
										<Badge
											variant="secondary"
											className="ml-auto shrink-0 px-1.5 text-[10px] font-medium group-data-[collapsible=icon]:hidden"
										>
											{t('common.adminBadge')}
										</Badge>
									</SidebarMenuButton>
								</SidebarMenuItem>
							)}
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.schedule') }}
									isActive={location.pathname === '/schedule'}
									onClick={() => navigate('/schedule')}
								>
									<Calendars />
									<span>{t('common.schedule')}</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.channel') }}
									isActive={location.pathname === '/channel'}
									onClick={() => navigate('/channel')}
								>
									<Cable />
									<span>{t('common.channel')}</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
						</SidebarMenu>
					</SidebarGroupContent>
				</SidebarGroup>
				<SidebarGroup>
					<SidebarGroupLabel>{t('common.sidebarResources')}</SidebarGroupLabel>
					<SidebarGroupContent>
						<SidebarMenu>
							{isAdmin && (
								<SidebarMenuItem>
									<SidebarMenuButton
										tooltip={{ children: t('common.credential') }}
										isActive={
											location.pathname === '/credential' ||
											location.pathname.startsWith('/admin/models')
										}
										onClick={() => navigate('/admin/models')}
									>
										<KeyRound />
										<span>{t('common.credential')}</span>
									</SidebarMenuButton>
								</SidebarMenuItem>
							)}
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{
											children:
												isAdmin ? t('common.mcp-hub') : t('resources.mcpTitle'),
										}}
									// Stays lit while browsing a hub under /mcp/:hubId.
									isActive={location.pathname.startsWith('/mcp')}
									onClick={() => navigate('/mcp')}
								>
									<MCPSvg />
									<span>
										{isAdmin ? t('common.mcp-hub') : t('resources.mcpTitle')}
									</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{
											children:
												isAdmin ? t('common.skill-hub') : t('resources.skillTitle'),
										}}
									isActive={location.pathname.startsWith('/skill')}
									onClick={() => navigate('/skill')}
								>
									<BookText />
									<span>
										{isAdmin ? t('common.skill-hub') : t('resources.skillTitle')}
									</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.knowledge') }}
									isActive={location.pathname === '/knowledge'}
									onClick={() => navigate('/knowledge')}
								>
									<LibraryBig />
									<span>{t('common.knowledge')}</span>
								</SidebarMenuButton>
							</SidebarMenuItem>
						</SidebarMenu>
					</SidebarGroupContent>
				</SidebarGroup>
			</SidebarContent>
			<SidebarFooter>
				<SidebarMenu>
					<SidebarMenuItem>
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<SidebarMenuButton
									tooltip={{
										children: user?.username ?? t('auth.accountCenter'),
									}}
									isActive={location.pathname === '/account'}
								>
									<UserRound />
									<span>{user?.username ?? t('auth.accountCenter')}</span>
								</SidebarMenuButton>
							</DropdownMenuTrigger>
							<DropdownMenuContent side="right" align="end" className="min-w-52">
								<DropdownMenuLabel>
									<div className="font-medium text-foreground">
										{user?.username}
									</div>
									{user && (user.subject_id ?? user.id) !== user.username && (
										<div
											className="mt-0.5 truncate font-mono text-xs font-normal text-muted-foreground"
											title={user.subject_id ?? user.id}
										>
											{t('auth.userId')}: {user.subject_id ?? user.id}
										</div>
									)}
									<div className="mt-0.5 font-sans text-xs font-normal">
										{t('auth.brand')}
									</div>
								</DropdownMenuLabel>
								<DropdownMenuSeparator />
								<DropdownMenuItem onSelect={() => navigate('/account')}>
									<UserRound />
									{t('auth.accountAndUsage')}
								</DropdownMenuItem>
								{isAdmin && (
									<DropdownMenuItem onSelect={() => navigate('/admin/observability')}>
										<BarChart3 />
										{t('admin.analyticsTitle')}
									</DropdownMenuItem>
								)}
								<DropdownMenuItem onSelect={() => navigate('/setup')}>
									<Settings />
									{t('common.settings')}
								</DropdownMenuItem>
								<DropdownMenuSeparator />
								<DropdownMenuItem onSelect={() => void handleSwitchAccount()}>
									<Repeat2 />
									{t('auth.switchAccount')}
								</DropdownMenuItem>
								<DropdownMenuItem
									variant="destructive"
									onSelect={() => void handleLogout()}
								>
									<LogOut />
									{t('auth.logout')}
								</DropdownMenuItem>
							</DropdownMenuContent>
						</DropdownMenu>
					</SidebarMenuItem>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{
								children: i18n.language.startsWith('zh')
									? t('common.switchToEn')
									: t('common.switchToZh'),
							}}
							onClick={handleToggleLanguage}
						>
							<Languages />
							<span>
								{i18n.language.startsWith('zh')
									? t('common.switchToEn')
									: t('common.switchToZh')}
							</span>
						</SidebarMenuButton>
					</SidebarMenuItem>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{ children: t('tour.trigger') }}
							onClick={handleStartTour}
						>
							<Compass />
							<span>{t('tour.trigger')}</span>
						</SidebarMenuButton>
					</SidebarMenuItem>
				</SidebarMenu>
			</SidebarFooter>
		</Sidebar>
	);
}
