import { useCallback, useEffect, useMemo, useState } from 'react';

import { authApi, type AuthUser } from '@/api';
import { AUTH_UNAUTHORIZED_EVENT, clearAccessToken, getAccessToken } from '@/api/client';
import { AuthContext, type AuthStatus } from '@/context/auth-context';
import { queryClient } from '@/lib/query-client';

function clearUserState() {
	clearAccessToken();
	queryClient.clear();
	localStorage.removeItem('chat_last_agent');
	localStorage.removeItem('chat_last_session');
	localStorage.removeItem('chat_panel_layout');
	sessionStorage.removeItem('force_tour');
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	const [status, setStatus] = useState<AuthStatus>('loading');
	const [user, setUser] = useState<AuthUser | null>(null);

	const becomeAnonymous = useCallback(() => {
		clearUserState();
		setUser(null);
		setStatus('anonymous');
	}, []);

	useEffect(() => {
		const handleUnauthorized = () => becomeAnonymous();
		window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);

		if (!getAccessToken()) {
			setStatus('anonymous');
		} else {
			authApi
				.me()
				.then((currentUser) => {
					setUser(currentUser);
					setStatus('authenticated');
				})
				.catch(() => becomeAnonymous());
		}

		return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
	}, [becomeAnonymous]);

	const login = useCallback(async (username: string, password: string) => {
		const currentUser = await authApi.login(username, password);
		queryClient.clear();
		setUser(currentUser);
		setStatus('authenticated');
	}, []);

	const register = useCallback(async (username: string, password: string) => {
		const currentUser = await authApi.register(username, password);
		queryClient.clear();
		setUser(currentUser);
		setStatus('authenticated');
	}, []);

	const logout = useCallback(async () => {
		try {
			await authApi.logout();
		} finally {
			becomeAnonymous();
		}
	}, [becomeAnonymous]);

	const value = useMemo(
		() => ({ status, user, login, register, logout }),
		[status, user, login, register, logout],
	);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
