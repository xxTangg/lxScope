import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { authApi, type AuthUser, type LoginCaptchaSubmission } from '@/api';
import {
	AUTH_MEMBER_DISABLED_EVENT,
	AUTH_UNAUTHORIZED_EVENT,
	ApiError,
	clearAccessToken,
	getAccessToken,
	setAccessToken,
	setAccessTokenProvider,
} from '@/api/client';
import { AuthContext, MEMBER_DISABLED_LOGOUT_KEY, type AuthStatus } from '@/context/auth-context';
import i18n from '@/i18n';
import { queryClient } from '@/lib/query-client';

const LOGTO_ENDPOINT = import.meta.env.VITE_LOGTO_ENDPOINT?.trim() ?? '';
const LOGTO_APP_ID = import.meta.env.VITE_LOGTO_APP_ID?.trim() ?? '';
const LOGTO_API_RESOURCE = import.meta.env.VITE_LOGTO_API_RESOURCE?.trim() ?? '';
const LOGTO_ENABLED = Boolean(LOGTO_ENDPOINT && LOGTO_APP_ID && LOGTO_API_RESOURCE);

export function clearUserState() {
	clearAccessToken();
	queryClient.clear();
	localStorage.removeItem('chat_last_agent');
	localStorage.removeItem('chat_last_session');
	localStorage.removeItem('chat_panel_layout');
	sessionStorage.removeItem('force_tour');
}

function LocalAuthProvider({ children }: { children: React.ReactNode }) {
	const [status, setStatus] = useState<AuthStatus>('loading');
	const [user, setUser] = useState<AuthUser | null>(null);

	const becomeAnonymous = useCallback(() => {
		clearUserState();
		setUser(null);
		setStatus('anonymous');
	}, []);

	useEffect(() => {
		const handleUnauthorized = () => becomeAnonymous();
		const handleMemberDisabled = () => {
			toast.error(i18n.t('auth.memberDisabled'));
			becomeAnonymous();
		};
		window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
		window.addEventListener(AUTH_MEMBER_DISABLED_EVENT, handleMemberDisabled);

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

		return () => {
			window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
			window.removeEventListener(AUTH_MEMBER_DISABLED_EVENT, handleMemberDisabled);
		};
	}, [becomeAnonymous]);

	const login = useCallback(async (
		username: string,
		password: string,
		captcha?: LoginCaptchaSubmission,
	) => {
		const currentUser = await authApi.login(username, password, captcha);
		queryClient.clear();
		setUser(currentUser);
		setStatus('authenticated');
	}, []);

	const register = useCallback(async (
		username: string,
		password: string,
		captcha?: LoginCaptchaSubmission,
	) => {
		const currentUser = await authApi.register(username, password, captcha);
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

	const switchAccount = useCallback(async () => {
		becomeAnonymous();
	}, [becomeAnonymous]);

	const value = useMemo(
		() => ({
			status,
			user,
			login,
			register,
			logout,
			switchAccount,
			logtoEnabled: false,
			organizations: [],
			organizationNames: {},
			beginLogin: async () => undefined,
			selectTenant: async () => undefined,
		}),
		[status, user, login, register, logout, switchAccount],
	);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

const LazyLogtoAuthProvider = lazy(() =>
	import('./logto-auth-provider').then((m) => ({ default: m.LogtoAuthProvider })),
);

export function AuthProvider({ children }: { children: React.ReactNode }) {
	return LOGTO_ENABLED ? (
		<Suspense fallback={null}>
			<LazyLogtoAuthProvider>{children}</LazyLogtoAuthProvider>
		</Suspense>
	) : (
		<LocalAuthProvider>{children}</LocalAuthProvider>
	);
}
