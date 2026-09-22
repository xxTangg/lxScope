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
import { OrganizationGate } from '@/components/auth/OrganizationGate';
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
import { AdminAgentDetailPage } from '@/pages/admin/agent-detail';
import { AdminObservabilityPage } from '@/pages/admin/analytics';
import { AdminAuditPage } from '@/pages/admin/audit';
import { AdminLayout } from '@/pages/admin/layout';
import { AdminMcpPage } from '@/pages/admin/mcp';
import { AdminMembersPage } from '@/pages/admin/members';
import { AdminModelObservabilityPage } from '@/pages/admin/model-analysis';
import { AdminModelsPage } from '@/pages/admin/models';
import { AdminObservabilityDetailPage } from '@/pages/admin/observability-detail';
import { AdminObservabilityFailuresPage } from '@/pages/admin/observability-failures';
import { AdminObservabilityFocusPage } from '@/pages/admin/observability-focus';
import { AdminOverviewPage } from '@/pages/admin/overview';
import { AdminPolicyPage } from '@/pages/admin/policy';
import { AdminQuotaPage } from '@/pages/admin/quota';
import { AdminSalesHubPage } from '@/pages/admin/sales-hub';
import { AdminSkillAnalyticsPage } from '@/pages/admin/skill-analytics';
import { AdminSkillsPage } from '@/pages/admin/skills';
import { AdminToolObservabilityPage } from '@/pages/admin/tool-analysis';
import { AdminTraceDetailPage } from '@/pages/admin/trace-detail';
import { AdminUpgradesPage } from '@/pages/admin/upgrades';
import { ChannelPage } from '@/pages/channel';
import { ChatPage } from '@/pages/chat';
import { CredentialPage } from '@/pages/credential';
import { KnowledgePage } from '@/pages/knowledge';
import { LoginPage } from '@/pages/login';
import { SchedulePage } from '@/pages/schedule';
import { SetupPage } from '@/pages/setup';
import { TaskPage } from '@/pages/task';

function SetupPageRoute() {
	const navigate = useNavigate();
	return (
		<div className="h-screen">
			<SetupPage onComplete={() => navigate('/')} />
		</div>
	);
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
	const {
		status,
		isLogto,
		noOrganizationAccess,
		organizationSelectionRequired,
	} = useAuth();
	const location = useLocation();

	if (status === 'loading') {
		return (
			<div className="flex h-screen items-center justify-center bg-canvas">
				<Loader2 className="size-5 animate-spin text-muted-foreground" />
			</div>
		);
	}
	if (status === 'anonymous') {
		// Logto owns the hosted sign-in experience. Rendering the entry route
		// directly lets it start the OIDC redirect without showing a second
		// lxScope login page first.
		if (isLogto) return <LoginPage />;
		return (
			<Navigate
				to="/login"
				replace
				state={{ from: `${location.pathname}${location.search}` }}
			/>
		);
	}
	if (noOrganizationAccess) return <OrganizationGate mode="none" />;
	if (organizationSelectionRequired) return <OrganizationGate mode="select" />;
	return children;
}

function LogtoCallbackRoute() {
	const { status } = useAuth();
	if (status === 'loading') {
		return (
			<div className="flex h-screen items-center justify-center bg-canvas">
				<Loader2 className="size-5 animate-spin text-muted-foreground" />
			</div>
		);
	}
	return <Navigate to={status === 'authenticated' ? '/chat' : '/login'} replace />;
}

function AdminOnlyRoute({ children }: { children: React.ReactNode }) {
	const { hasPermission } = useAuth();
	const canAccess =
		hasPermission('tenant:manage') ||
		 hasPermission('platform:manage') ||
		hasPermission('platform:upgrade') ||
		hasPermission('platform:integration') ||
		hasPermission('platform:observe');
	return canAccess ? children : <Navigate to="/chat" replace />;
}

function PermissionRoute({
	permission,
	children,
}: {
	permission: string;
	children: React.ReactNode;
}) {
	const { hasPermission } = useAuth();
	return hasPermission(permission) ? children : <Navigate to="/chat" replace />;
}

function AdminFeatureRoute({
	permission,
	children,
}: {
	permission: string;
	children: React.ReactNode;
}) {
	const { hasPermission } = useAuth();
	const canAccess =
		hasPermission('tenant:manage') || hasPermission(permission);
	return canAccess ? children : <Navigate to="/chat" replace />;
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
					{ path: '/task/:taskId?', element: <TaskPage /> },
					{ path: '/channel', element: <ChannelPage /> },
					{
						path: '/credential',
						element: (
							<PermissionRoute permission="platform:integration">
								<CredentialPage />
							</PermissionRoute>
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
							{ path: 'observability', element: <AdminObservabilityPage /> },
							{ path: 'observability/failures', element: <AdminObservabilityFailuresPage /> },
							{ path: 'observability/skills', element: <AdminSkillAnalyticsPage /> },
							{ path: 'observability/requests', element: <AdminObservabilityFocusPage /> },
							{ path: 'observability/tokens', element: <AdminObservabilityFocusPage /> },
							{ path: 'observability/users', element: <AdminObservabilityFocusPage /> },
							{ path: 'observability/agent/:agentName', element: <AdminAgentDetailPage /> },
							{ path: 'observability/trace/:traceId', element: <AdminTraceDetailPage /> },
							{ path: 'observability/model', element: <AdminModelObservabilityPage /> },
							{ path: 'observability/model/:modelName', element: <AdminModelObservabilityPage /> },
							{ path: 'observability/tool', element: <AdminToolObservabilityPage /> },
							{ path: 'observability/tool/:toolName', element: <AdminToolObservabilityPage /> },
							{ path: 'observability/:component', element: <AdminObservabilityDetailPage /> },
							{ path: 'analytics/skills', element: <AdminSkillAnalyticsPage /> },
							{ path: 'members', element: <AdminMembersPage /> },
							{ path: 'quota', element: <AdminQuotaPage /> },
							{ path: 'models', element: <AdminModelsPage /> },
							{
								path: 'models/config',
								element: (
									<PermissionRoute permission="platform:integration">
										<CredentialPage />
									</PermissionRoute>
								),
							},
							{ path: 'skills', element: <AdminSkillsPage /> },
							{ path: 'mcp', element: <AdminMcpPage /> },
							{ path: 'policy', element: <AdminPolicyPage /> },
							{ path: 'audit', element: <AdminAuditPage /> },
							{
								path: 'upgrades',
								element: (
									<AdminFeatureRoute permission="platform:upgrade">
										<AdminUpgradesPage />
									</AdminFeatureRoute>
								),
							},
							{
								path: 'sales-hub',
								element: (
									<AdminFeatureRoute permission="platform:integration">
										<AdminSalesHubPage />
									</AdminFeatureRoute>
								),
							},
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
	{ path: '/callback', element: <LogtoCallbackRoute />, errorElement: <RouteError /> },
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
