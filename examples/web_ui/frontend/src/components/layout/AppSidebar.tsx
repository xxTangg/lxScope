import {
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
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';
import { useAuth } from '@/hooks/useAuth';
import i18n from '@/i18n';
import { useTranslation } from '@/i18n/useI18n';

export function AppSidebar() {
	const navigate = useNavigate();
	const location = useLocation();
	const { t } = useTranslation();
	const { startOnborda } = useOnborda();
	const { user, logout } = useAuth();

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
		navigate('/login', { replace: true });
	};

	return (
		<Sidebar
			collapsible="none"
			className="w-[calc(var(--sidebar-width-icon)+1px)]! bg-transparent"
		>
			<SidebarHeader>
				<AgentScope className="mt-2 size-8" />
			</SidebarHeader>
			<SidebarContent>
				<SidebarGroup>
					<SidebarGroupContent>
						<SidebarMenu>
							<SidebarMenuItem key={'chat'}>
								<SidebarMenuButton
									tooltip={{ children: t('common.chat'), hidden: false }}
									isActive={
										location.pathname === '/chat' ||
										location.pathname.startsWith('/chat/')
									}
									onClick={() => navigate('/chat')}
									className="justify-center"
								>
									<BotMessageSquare />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.tasks'), hidden: false }}
									isActive={location.pathname.startsWith('/task')}
									onClick={() => navigate('/task')}
									className="justify-center"
								>
									<ClipboardList />
								</SidebarMenuButton>
							</SidebarMenuItem>
							{user?.role === 'admin' && (
								<SidebarMenuItem>
									<SidebarMenuButton
										tooltip={{ children: t('admin.title'), hidden: false }}
										isActive={location.pathname.startsWith('/admin')}
										onClick={() => navigate('/admin/overview')}
										className="justify-center"
									>
										<ShieldCheck />
									</SidebarMenuButton>
								</SidebarMenuItem>
							)}
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.schedule'), hidden: false }}
									isActive={location.pathname === '/schedule'}
									onClick={() => navigate('/schedule')}
									className="justify-center"
								>
									<Calendars />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.channel'), hidden: false }}
									isActive={location.pathname === '/channel'}
									onClick={() => navigate('/channel')}
									className="px-2"
								>
									<Cable />
								</SidebarMenuButton>
							</SidebarMenuItem>
						</SidebarMenu>
					</SidebarGroupContent>
				</SidebarGroup>
				<SidebarGroup>
					<SidebarGroupContent>
						<SidebarMenu>
							{user?.role === 'admin' && (
								<SidebarMenuItem>
									<SidebarMenuButton
										tooltip={{ children: t('common.credential'), hidden: false }}
										isActive={location.pathname === '/credential' || location.pathname.startsWith('/admin/models')}
										onClick={() => navigate('/admin/models')}
										className="justify-center"
									>
										<KeyRound />
									</SidebarMenuButton>
								</SidebarMenuItem>
							)}
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{
										children:
											user?.role === 'admin'
												? t('common.mcp-hub')
												: t('resources.mcpTitle'),
										hidden: false,
									}}
									// Stays lit while browsing a hub under /mcp/:hubId.
									isActive={location.pathname.startsWith('/mcp')}
									onClick={() => navigate('/mcp')}
									className="justify-center"
								>
									<MCPSvg />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{
										children:
											user?.role === 'admin'
												? t('common.skill-hub')
												: t('resources.skillTitle'),
										hidden: false,
									}}
									isActive={location.pathname.startsWith('/skill')}
									onClick={() => navigate('/skill')}
									className="justify-center"
								>
									<BookText />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: t('common.knowledge'), hidden: false }}
									isActive={location.pathname === '/knowledge'}
									onClick={() => navigate('/knowledge')}
									className="justify-center"
								>
									<LibraryBig />
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
										hidden: false,
									}}
									isActive={location.pathname === '/account'}
									className="justify-center"
								>
									<UserRound />
								</SidebarMenuButton>
							</DropdownMenuTrigger>
							<DropdownMenuContent side="right" align="end" className="min-w-52">
								<DropdownMenuLabel>
									<div className="font-medium text-foreground">
										{user?.username}
									</div>
									<div className="mt-0.5 font-sans text-xs font-normal">
										{t('auth.brand')}
									</div>
								</DropdownMenuLabel>
								<DropdownMenuSeparator />
								<DropdownMenuItem onSelect={() => navigate('/account')}>
									<UserRound />
									{t('auth.accountAndUsage')}
								</DropdownMenuItem>
								<DropdownMenuItem onSelect={() => navigate('/setup')}>
									<Settings />
									{t('common.settings')}
								</DropdownMenuItem>
								<DropdownMenuSeparator />
								<DropdownMenuItem onSelect={() => void handleLogout()}>
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
								hidden: false,
							}}
							onClick={handleToggleLanguage}
							className="justify-center"
						>
							<Languages />
						</SidebarMenuButton>
					</SidebarMenuItem>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{ children: t('tour.trigger'), hidden: false }}
							onClick={handleStartTour}
							className="justify-center"
						>
							<Compass />
						</SidebarMenuButton>
					</SidebarMenuItem>
				</SidebarMenu>
			</SidebarFooter>
		</Sidebar>
	);
}
