import { createContext } from 'react';

import type { AuthUser } from '@/api';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'selecting_tenant';

export const MEMBER_DISABLED_LOGOUT_KEY = 'lxscope_member_disabled_logout';

export interface AuthContextValue {
	status: AuthStatus;
	user: AuthUser | null;
	login: (username: string, password: string) => Promise<void>;
	register: (username: string, password: string) => Promise<void>;
	logout: () => Promise<void>;
	switchAccount: () => Promise<void>;
	logtoEnabled: boolean;
	organizations: string[];
	organizationNames: Record<string, string>;
	beginLogin: () => Promise<void>;
	selectTenant: (tenantId: string) => Promise<void>;
	error?: string;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
