import { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';

import { AppSidebar } from '@/components/layout/AppSidebar';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';
import { ORGANIZATION_CHANGED_EVENT } from '@/context/auth-context';
import { useAuth } from '@/hooks/useAuth';

export function AppLayout() {
	const { activeOrganizationId } = useAuth();
	const navigate = useNavigate();

	useEffect(() => {
		const resetOrganizationScopedRoute = () => navigate('/chat', { replace: true });
		window.addEventListener(ORGANIZATION_CHANGED_EVENT, resetOrganizationScopedRoute);
		return () =>
			window.removeEventListener(ORGANIZATION_CHANGED_EVENT, resetOrganizationScopedRoute);
	}, [navigate]);

	return (
		<div className="h-screen flex">
			<SidebarProvider>
				<AppSidebar />
				<SidebarInset className="flex-1 overflow-hidden bg-canvas">
					<div key={activeOrganizationId ?? 'local'} className="contents">
						<Outlet />
					</div>
				</SidebarInset>
			</SidebarProvider>
		</div>
	);
}
