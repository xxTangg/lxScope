import { createContext } from 'react';

import type { AuthUser } from '@/api';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

export interface AuthContextValue {
	status: AuthStatus;
	user: AuthUser | null;
	login: (username: string, password: string) => Promise<void>;
	register: (username: string, password: string) => Promise<void>;
	logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
