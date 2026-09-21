import { UserScope, type LogtoConfig } from '@logto/react';

export type AuthProvider = 'local' | 'logto';

const configuredProvider = import.meta.env.VITE_AUTH_PROVIDER?.trim().toLowerCase();

export const authProvider: AuthProvider = configuredProvider === 'logto' ? 'logto' : 'local';

export const LOGTO_ENDPOINT = import.meta.env.VITE_LOGTO_ENDPOINT?.trim() ?? '';
export const LOGTO_APP_ID = import.meta.env.VITE_LOGTO_APP_ID?.trim() ?? '';
export const LOGTO_API_RESOURCE = import.meta.env.VITE_LOGTO_API_RESOURCE?.trim() ?? '';

export const logtoConfig: LogtoConfig = {
	endpoint: LOGTO_ENDPOINT,
	appId: LOGTO_APP_ID,
	resources: LOGTO_API_RESOURCE ? [LOGTO_API_RESOURCE] : [],
	scopes: [UserScope.Organizations, UserScope.OrganizationRoles],
};

export const isLogtoConfigured =
	Boolean(LOGTO_ENDPOINT) && Boolean(LOGTO_APP_ID) && Boolean(LOGTO_API_RESOURCE);

export const ACTIVE_ORGANIZATION_STORAGE_KEY = 'lxscope.activeOrganizationId';
export const LOGTO_CALLBACK_PATH = '/callback';

export function getLogtoRedirectUri() {
	return new URL(LOGTO_CALLBACK_PATH, window.location.origin).toString();
}
