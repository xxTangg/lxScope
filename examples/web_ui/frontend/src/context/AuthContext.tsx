import {
	useHandleSignInCallback,
	useLogto,
	type UserInfoResponse,
} from '@logto/react';
import {
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
} from 'react';

import { authApi, type AuthUser } from '@/api';
import {
	AUTH_UNAUTHORIZED_EVENT,
	clearAccessToken,
	getAccessToken,
	setAccessTokenProvider,
} from '@/api/client';
import {
	ACTIVE_ORGANIZATION_STORAGE_KEY,
	authProvider,
	getLogtoRedirectUri,
	LOGTO_API_RESOURCE,
	LOGTO_CALLBACK_PATH,
} from '@/auth/logto-config';
import {
	AuthContext,
	ORGANIZATION_CHANGED_EVENT,
	type AuthContextValue,
	type AuthOrganization,
	type AuthStatus,
} from '@/context/auth-context';
import { queryClient } from '@/lib/query-client';

function clearSessionPageState() {
	queryClient.clear();
	localStorage.removeItem('chat_last_agent');
	localStorage.removeItem('chat_last_session');
	localStorage.removeItem('chat_panel_layout');
	localStorage.removeItem('chat_open_skill_panel');
	for (const key of Object.keys(localStorage)) {
		if (key.startsWith('chat_skill_selection:')) localStorage.removeItem(key);
	}
	sessionStorage.removeItem('force_tour');
}

function clearLocalUserState() {
	clearAccessToken();
	setAccessTokenProvider(null);
	clearSessionPageState();
	localStorage.removeItem(ACTIVE_ORGANIZATION_STORAGE_KEY);
}

function hasPermission(permissions: string[], required: string) {
	if (permissions.includes(required)) return true;
	const separator = required.indexOf(':');
	if (separator < 0) return false;
	const namespace = required.slice(0, separator);
	return permissions.includes(`${namespace}:manage`);
}

function emptyOrganizationState(): Pick<
	AuthContextValue,
	'isLogto' | 'organizations' | 'activeOrganizationId' | 'organizationSelectionRequired' | 'noOrganizationAccess'
> {
	return {
		isLogto: false,
		organizations: [],
		activeOrganizationId: null,
		organizationSelectionRequired: false,
		noOrganizationAccess: false,
	};
}

function LocalAuthProvider({ children }: { children: React.ReactNode }) {
	const [status, setStatus] = useState<AuthStatus>('loading');
	const [user, setUser] = useState<AuthUser | null>(null);

	const becomeAnonymous = useCallback(() => {
		clearLocalUserState();
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

	const signIn = useCallback(async () => {
		await login('', '');
	}, [login]);

	const value = useMemo<AuthContextValue>(
		() => ({
			status,
			user,
			permissions: user?.permissions ?? [],
			hasPermission: (permission: string) =>
				hasPermission(user?.permissions ?? [], permission),
			tenantId: user?.tenant_id ?? null,
			membershipId: user?.membership_id ?? null,
			login,
			register,
			logout,
			signIn,
			...emptyOrganizationState(),
			setActiveOrganizationId: async () => undefined,
		}),
		[status, user, login, register, logout, signIn],
	);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function normalizeOrganizations(userInfo: UserInfoResponse): AuthOrganization[] {
	const details = new Map(
		(userInfo.organization_data ?? []).map((organization) => [organization.id, organization]),
	);
	const ids = new Set([
		...(userInfo.organizations ?? []),
		...details.keys(),
	]);

	return [...ids].map((id) => ({
		id,
		name: details.get(id)?.name ?? id,
		description: details.get(id)?.description ?? null,
	}));
}

function hasLogtoCallbackParameters() {
	if (window.location.pathname !== LOGTO_CALLBACK_PATH) return false;
	const params = new URLSearchParams(window.location.search);
	return Boolean(params.get('code') && params.get('state'));
}

function mergeLogtoProfile(user: AuthUser, userInfo: UserInfoResponse): AuthUser {
	const profile = userInfo as UserInfoResponse & {
		username?: string;
		name?: string;
		email?: string;
		sub?: string;
	};
	const displayName =
		profile.name?.trim() ||
		profile.username?.trim() ||
		profile.email?.trim() ||
		user.display_name ||
		user.username;
	return {
		...user,
		display_name: displayName,
		external_user_id: user.external_user_id || profile.sub || user.id,
	};
}

function LogtoAuthProvider({ children }: { children: React.ReactNode }) {
	const {
		isAuthenticated,
		isLoading: sdkLoading,
		error: sdkError,
		signIn: logtoSignIn,
		signOut: logtoSignOut,
		clearAllTokens,
		fetchUserInfo,
		getAccessToken,
	} = useLogto();
	const [sdkReady, setSdkReady] = useState(false);
	const [callbackHandled, setCallbackHandled] = useState(
		() => !hasLogtoCallbackParameters(),
	);
	const callback = useHandleSignInCallback(() => setCallbackHandled(true));
	const [status, setStatus] = useState<AuthStatus>('loading');
	const [user, setUser] = useState<AuthUser | null>(null);
	const [organizations, setOrganizations] = useState<AuthOrganization[]>([]);
	const [activeOrganizationId, setActiveOrganizationIdState] = useState<string | null>(
		() => localStorage.getItem(ACTIVE_ORGANIZATION_STORAGE_KEY),
	);
	const activeOrganizationRef = useRef(activeOrganizationId);

	// Logto's isLoading covers both the initial SDK bootstrap and every later
	// fetch/refresh operation. Keep only the first transition as bootstrap
	// readiness; otherwise fetching user info or an organization token would
	// retrigger the authentication effect indefinitely.
	useEffect(() => {
		if (!sdkLoading) setSdkReady(true);
	}, [sdkLoading]);

	useEffect(() => {
		activeOrganizationRef.current = activeOrganizationId;
	}, [activeOrganizationId]);

	useEffect(() => {
		setAccessTokenProvider(async () => {
			const organizationId = activeOrganizationRef.current;
			if (!organizationId || !LOGTO_API_RESOURCE) return null;
			return (await getAccessToken(LOGTO_API_RESOURCE, organizationId)) ?? null;
		});

		return () => setAccessTokenProvider(null);
	}, [getAccessToken]);

	const becomeAnonymous = useCallback(() => {
		// A backend 401 can leave the browser with an otherwise-valid ID token
		// but an access token signed/configured for a previous server state. Clear
		// the SDK cache so the next sign-in starts a fresh OIDC transaction.
		void clearAllTokens().catch(() => undefined);
		clearSessionPageState();
		localStorage.removeItem(ACTIVE_ORGANIZATION_STORAGE_KEY);
		activeOrganizationRef.current = null;
		setActiveOrganizationIdState(null);
		setOrganizations([]);
		setUser(null);
		setStatus('anonymous');
	}, [clearAllTokens]);

	useEffect(() => {
		const handleUnauthorized = () => becomeAnonymous();
		window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
		return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
	}, [becomeAnonymous]);

	useEffect(() => {
		let cancelled = false;

		if (!sdkReady || !callbackHandled) {
			setStatus('loading');
			return () => {
				cancelled = true;
			};
		}

		const authenticated = isAuthenticated || callback.isAuthenticated;
		if (!authenticated || sdkError || callback.error) {
			becomeAnonymous();
			return () => {
				cancelled = true;
			};
		}

		setStatus('loading');
		void fetchUserInfo()
			.then(async (userInfo) => {
				if (cancelled) return;
				if (!userInfo) throw new Error('Logto user information was unavailable.');

				const nextOrganizations = normalizeOrganizations(userInfo);
				const storedOrganizationId = localStorage.getItem(ACTIVE_ORGANIZATION_STORAGE_KEY);
				const desiredOrganizationId =
					nextOrganizations.length === 1
						? nextOrganizations[0].id
						: nextOrganizations.some(
								(organization) => organization.id === storedOrganizationId,
							)
							? storedOrganizationId
							: null;

				if (desiredOrganizationId) {
					const token = await getAccessToken(LOGTO_API_RESOURCE, desiredOrganizationId);
					if (!token) throw new Error('Logto did not return an organization access token.');
					if (cancelled) return;
					activeOrganizationRef.current = desiredOrganizationId;
					localStorage.setItem(ACTIVE_ORGANIZATION_STORAGE_KEY, desiredOrganizationId);
					setActiveOrganizationIdState(desiredOrganizationId);
					const backendContext = await authApi.context();
					if (backendContext.provider !== 'logto') {
						throw new Error('The backend authentication provider is not Logto.');
					}
					setUser(mergeLogtoProfile(backendContext.user, userInfo));
				} else {
					activeOrganizationRef.current = null;
					localStorage.removeItem(ACTIVE_ORGANIZATION_STORAGE_KEY);
					setActiveOrganizationIdState(null);
					setUser(null);
				}

				setOrganizations(nextOrganizations);
				setStatus('authenticated');
			})
			.catch(() => {
				if (!cancelled) becomeAnonymous();
			});

		return () => {
			cancelled = true;
		};
	}, [
		callback.error,
		callback.isAuthenticated,
		callbackHandled,
		becomeAnonymous,
		fetchUserInfo,
		getAccessToken,
		isAuthenticated,
		sdkError,
		sdkReady,
	]);

	const activateOrganization = useCallback(
		async (organizationId: string) => {
			if (!organizations.some((organization) => organization.id === organizationId)) {
				throw new Error('The selected organization is not available to this user.');
			}
			if (activeOrganizationRef.current === organizationId) return;

			const token = await getAccessToken(LOGTO_API_RESOURCE, organizationId);
			if (!token) throw new Error('Logto did not return an organization access token.');
			const backendContext = await authApi.context();
			if (backendContext.provider !== 'logto') {
				throw new Error('The backend authentication provider is not Logto.');
			}

			localStorage.setItem(ACTIVE_ORGANIZATION_STORAGE_KEY, organizationId);
			setActiveOrganizationIdState(organizationId);
			setUser(mergeLogtoProfile(backendContext.user, await fetchUserInfo()));
			clearSessionPageState();
			window.dispatchEvent(
				new CustomEvent(ORGANIZATION_CHANGED_EVENT, {
					detail: { organizationId },
				}),
			);
		},
		[getAccessToken, organizations],
	);

	const signIn = useCallback(async () => {
		await logtoSignIn({
			redirectUri: getLogtoRedirectUri(),
			postRedirectUri: new URL('/chat', window.location.origin).toString(),
		});
	}, [logtoSignIn]);

	const login = useCallback(async () => signIn(), [signIn]);
	const register = useCallback(async () => signIn(), [signIn]);

	const logout = useCallback(async () => {
		try {
			// Logto validates this URI against the application's registered
			// post-logout redirect URIs. The root URI is registered for both
			// localhost and 127.0.0.1; the router then redirects anonymous users
			// to /login.
			await logtoSignOut(new URL('/', window.location.origin).toString());
		} finally {
			becomeAnonymous();
		}
	}, [becomeAnonymous, logtoSignOut]);

	const value = useMemo<AuthContextValue>(
		() => ({
			status,
			user,
			permissions: user?.permissions ?? [],
			hasPermission: (permission: string) =>
				hasPermission(user?.permissions ?? [], permission),
			tenantId: user?.tenant_id ?? null,
			membershipId: user?.membership_id ?? null,
			login,
			register,
			logout,
			signIn,
			isLogto: true,
			organizations,
			activeOrganizationId,
			organizationSelectionRequired:
				status === 'authenticated' && organizations.length > 1 && !activeOrganizationId,
			noOrganizationAccess: status === 'authenticated' && organizations.length === 0,
			setActiveOrganizationId: activateOrganization,
		}),
		[
			activateOrganization,
			activeOrganizationId,
			login,
			logout,
			organizations,
			register,
			signIn,
			status,
			user,
		],
	);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	if (authProvider === 'logto') return <LogtoAuthProvider>{children}</LogtoAuthProvider>;
	return <LocalAuthProvider>{children}</LocalAuthProvider>;
}
