import { createContext } from 'react';

import type { AuthUser } from '@/api';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';
export const ORGANIZATION_CHANGED_EVENT = 'lxscope:organization-changed';

export interface AuthOrganization {
	id: string;
	name: string;
	description?: string | null;
}

export interface AuthContextValue {
	status: AuthStatus;
	user: AuthUser | null;
	login: (username: string, password: string) => Promise<void>;
	register: (username: string, password: string) => Promise<void>;
	logout: () => Promise<void>;
	signIn: () => Promise<void>;
	isLogto: boolean;
	organizations: AuthOrganization[];
	activeOrganizationId: string | null;
	organizationSelectionRequired: boolean;
	noOrganizationAccess: boolean;
	setActiveOrganizationId: (organizationId: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
