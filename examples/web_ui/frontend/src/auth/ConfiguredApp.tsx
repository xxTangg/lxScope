import { LogtoProvider } from '@logto/react';

import App from '@/App';
import { authProvider, isLogtoConfigured, logtoConfig } from '@/auth/logto-config';

export function ConfiguredApp() {
	if (authProvider === 'logto') {
		if (!isLogtoConfigured) {
			return (
				<div className="flex h-screen items-center justify-center bg-canvas px-6 text-center text-sm text-muted-foreground">
					Logto authentication is enabled but VITE_LOGTO_ENDPOINT, VITE_LOGTO_APP_ID,
					and VITE_LOGTO_API_RESOURCE are not configured.
				</div>
			);
		}
		return (
			<LogtoProvider config={logtoConfig}>
				<App />
			</LogtoProvider>
		);
	}

	return <App />;
}
