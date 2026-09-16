import { QueryClientProvider } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Onborda, OnbordaProvider } from 'onborda';
import { useMemo } from 'react';
import {
	createBrowserRouter,
	Navigate,
	RouterProvider,
	useLocation,
	useNavigate,
} from 'react-router-dom';
import { Toaster } from 'sonner';

import { MCPHubPage } from './pages/mcp';
import { SkillHubPage } from './pages/skill';
import { RouteError } from '@/components/error/RouteError';
import { AppLayout } from '@/components/layout/AppLayout';
import { buildChatTour } from '@/components/tour/chatTourSteps';
import { TourCard } from '@/components/tour/TourCard';
import { AuthProvider } from '@/context/AuthContext';
import { UploadProvider } from '@/context/UploadContext';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { queryClient } from '@/lib/query-client';
import { AccountPage } from '@/pages/account';
import { AdminAuditPage } from '@/pages/admin/audit';
import { AdminLayout } from '@/pages/admin/layout';
import { AdminMcpPage } from '@/pages/admin/mcp';
import { AdminMembersPage } from '@/pages/admin/members';
import { AdminModelsPage } from '@/pages/admin/models';
import { AdminOverviewPage } from '@/pages/admin/overview';
import { AdminPolicyPage } from '@/pages/admin/policy';
import { AdminQuotaPage } from '@/pages/admin/quota';
import { AdminSalesHubPage } from '@/pages/admin/sales-hub';
import { AdminSkillsPage } from '@/pages/admin/skills';
import { AdminUpgradesPage } from '@/pages/admin/upgrades';
import { ChannelPage } from '@/pages/channel';
import { ChatPage } from '@/pages/chat';
import { CredentialPage } from '@/pages/credential';
import { KnowledgePage } from '@/pages/knowledge';
import { LoginPage } from '@/pages/login';
import { SchedulePage } from '@/pages/schedule';
import { SetupPage } from '@/pages/setup';

function SetupPageRoute() {
	const navigate = useNavigate();
	return (
		<div className="h-screen">
			<SetupPage onComplete={() => navigate('/')} />
		</div>
	);
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
	const { status } = useAuth();
	const location = useLocation();

	if (status === 'loading') {
		return (
			<div className="flex h-screen items-center justify-center bg-canvas">
				<Loader2 className="size-5 animate-spin text-muted-foreground" />
			</div>
		);
	}
	if (status === 'anonymous') {
		return (
			<Navigate
				to="/login"
				replace
				state={{ from: `${location.pathname}${location.search}` }}
			/>
		);
	}
	return children;
}

function AdminOnlyRoute({ children }: { children: React.ReactNode }) {
	const { user } = useAuth();
	return user?.role === 'admin' ? children : <Navigate to="/chat" replace />;
}

const router = createBrowserRouter([
	{
		element: (
			<ProtectedRoute>
				<AppLayout />
			</ProtectedRoute>
		),
		errorElement: <RouteError />,
		children: [
			{
				// Content-level boundary: a crash in a page replaces only
				// the Outlet area, so AppLayout (the icon rail / nav) stays
				// usable. The parent route keeps its own errorElement as a
				// last-resort catch-all for AppLayout/AppSidebar crashes.
				errorElement: <RouteError />,
				children: [
					{ path: '/', element: <Navigate to="/chat" replace /> },
					{
						path: '/chat/:agentId?/:sessionId?/:memberId?',
						element: <ChatPage />,
					},
					{ path: '/schedule', element: <SchedulePage /> },
					{ path: '/channel', element: <ChannelPage /> },
					{
						path: '/credential',
						element: (
							<AdminOnlyRoute>
								<CredentialPage />
							</AdminOnlyRoute>
						),
					},
					{ path: '/mcp', element: <MCPHubPage /> },
					{ path: '/mcp/:hubId', element: <MCPHubPage /> },
					{ path: '/skill', element: <SkillHubPage /> },
					{ path: '/skill/:hubId', element: <SkillHubPage /> },
					{ path: '/knowledge', element: <KnowledgePage /> },
					{ path: '/knowledge/:kbId', element: <KnowledgePage /> },
					{ path: '/account', element: <AccountPage /> },
					{
						path: '/admin',
						element: (
							<AdminOnlyRoute>
								<AdminLayout />
							</AdminOnlyRoute>
						),
						children: [
							{ index: true, element: <Navigate to="overview" replace /> },
							{ path: 'overview', element: <AdminOverviewPage /> },
							{ path: 'members', element: <AdminMembersPage /> },
							{ path: 'quota', element: <AdminQuotaPage /> },
							{ path: 'models', element: <AdminModelsPage /> },
							{ path: 'models/config', element: <CredentialPage /> },
							{ path: 'skills', element: <AdminSkillsPage /> },
							{ path: 'mcp', element: <AdminMcpPage /> },
							{ path: 'policy', element: <AdminPolicyPage /> },
							{ path: 'audit', element: <AdminAuditPage /> },
							{ path: 'upgrades', element: <AdminUpgradesPage /> },
							{ path: 'sales-hub', element: <AdminSalesHubPage /> },
						],
					},
				],
			},
		],
	},
	{
		path: '/setup',
		element: (
			<ProtectedRoute>
				<SetupPageRoute />
			</ProtectedRoute>
		),
		errorElement: <RouteError />,
	},
	{ path: '/login', element: <LoginPage />, errorElement: <RouteError /> },
]);

function App() {
	const { t } = useTranslation();
	const tours = useMemo(() => [buildChatTour(t)], [t]);

	return (
		<QueryClientProvider client={queryClient}>
			<AuthProvider>
				<OnbordaProvider>
					<Onborda
						steps={tours}
						cardComponent={TourCard}
						shadowOpacity="0.6"
						cardTransition={{ type: 'spring', duration: 0.4 }}
					>
						<UploadProvider>
							<RouterProvider router={router} />
						</UploadProvider>
						<Toaster richColors position="top-right" />
					</Onborda>
				</OnbordaProvider>
			</AuthProvider>
		</QueryClientProvider>
	);
}

export default App;
