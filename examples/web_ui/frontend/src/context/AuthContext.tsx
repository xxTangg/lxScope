import { LogtoProvider, Prompt, UserScope, useHandleSignInCallback, useLogto, type LogtoConfig } from '@logto/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { authApi, type AuthUser } from '@/api';
import {
	AUTH_UNAUTHORIZED_EVENT,
	ApiError,
	clearAccessToken,
	getAccessToken,
	setAccessToken,
	setAccessTokenProvider,
} from '@/api/client';
import { AuthContext, type AuthStatus } from '@/context/auth-context';
import { queryClient } from '@/lib/query-client';

const LOGTO_ENDPOINT = import.meta.env.VITE_LOGTO_ENDPOINT?.trim() ?? '';
const LOGTO_APP_ID = import.meta.env.VITE_LOGTO_APP_ID?.trim() ?? '';
const LOGTO_API_RESOURCE = import.meta.env.VITE_LOGTO_API_RESOURCE?.trim() ?? '';
const LOGTO_ENABLED = Boolean(LOGTO_ENDPOINT && LOGTO_APP_ID && LOGTO_API_RESOURCE);
const ACTIVE_TENANT_KEY = 'lxscope_active_tenant';
const SCOPE_REFRESH_TENANT_KEY = 'lxscope_scope_refresh_tenant';
const LOGTO_OPERATION_TIMEOUT_MS = 15_000;
const LOGTO_DISPLAY_INFO_TIMEOUT_MS = 5_000;

const logtoConfig: LogtoConfig = {
	endpoint: LOGTO_ENDPOINT,
	appId: LOGTO_APP_ID,
	resources: [LOGTO_API_RESOURCE],
	scopes: ['offline_access', UserScope.Organizations, 'agent:use', 'tenant:manage'],
};

function withLogtoTimeout<T>(
	operation: Promise<T>,
	message: string,
	timeoutMs = LOGTO_OPERATION_TIMEOUT_MS,
): Promise<T> {
	return new Promise((resolve, reject) => {
		const timeout = window.setTimeout(
			() => reject(new Error(message)),
			timeoutMs,
		);
		operation.then(
			(value) => {
				window.clearTimeout(timeout);
				resolve(value);
			},
			(reason) => {
				window.clearTimeout(timeout);
				reject(reason);
			},
		);
	});
}

function clearUserState() {
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

function LogtoCallbackHandler() {
	useHandleSignInCallback();
	return null;
}

function LogtoAuthProviderContent({ children }: { children: React.ReactNode }) {
	const {
		isAuthenticated,
		isLoading,
		getAccessToken,
		getIdTokenClaims,
		fetchUserInfo,
		clearAllTokens,
		signIn,
		signOut,
	} = useLogto();
	const [status, setStatus] = useState<AuthStatus>('loading');
	const [user, setUser] = useState<AuthUser | null>(null);
	const [organizations, setOrganizations] = useState<string[]>([]);
	const [organizationNames, setOrganizationNames] = useState<Record<string, string>>({});
	const [error, setError] = useState('');
	const activeTenant = useRef<string | null>(null);
	const claimsRequested = useRef(false);
	const authGeneration = useRef(0);

	useEffect(() => {
		if (!isLoading) return;
		const timeout = window.setTimeout(() => {
			setError('Logto 登录状态读取超时，请重试登录。');
			setStatus('anonymous');
		}, LOGTO_OPERATION_TIMEOUT_MS);
		return () => window.clearTimeout(timeout);
	}, [isLoading]);

	const selectTenant = useCallback(
		async (tenantId: string) => {
			setStatus('loading');
			setError('');
			queryClient.clear();
			try {
				const token = await withLogtoTimeout(
					getAccessToken(LOGTO_API_RESOURCE, tenantId),
					'Logto 获取组织令牌超时，请重试。',
				);
				if (!token) {
					throw new Error(
						'Logto 已返回组织列表，但未签发该组织的 API 访问令牌。仅凭此结果无法判断是权限不足还是登录令牌刷新失败，请检查 Logto 本次令牌交换的结果。',
					);
				}
				activeTenant.current = tenantId;
				localStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
				setAccessToken(token);
				setAccessTokenProvider(async () => {
					const currentTenant = activeTenant.current;
					if (!currentTenant) return null;
					return (await getAccessToken(LOGTO_API_RESOURCE, currentTenant)) ?? null;
				});
				const currentUser = await withLogtoTimeout(
					authApi.me(),
					'读取组织用户信息超时，请重试。',
				);
				if (currentUser.tenant_id !== tenantId) {
					throw new Error('Logto 返回的组织身份与当前选择不一致。');
				}
				sessionStorage.removeItem(SCOPE_REFRESH_TENANT_KEY);
				setUser(currentUser);
				setStatus('authenticated');
			} catch (reason) {
				if (
					reason instanceof ApiError &&
					reason.status === 403 &&
					reason.detail.includes('does not grant lxScope access') &&
					sessionStorage.getItem(SCOPE_REFRESH_TENANT_KEY) !== tenantId
				) {
					sessionStorage.setItem(SCOPE_REFRESH_TENANT_KEY, tenantId);
					activeTenant.current = null;
					localStorage.removeItem(ACTIVE_TENANT_KEY);
					setAccessTokenProvider(null);
					clearAccessToken();
					setUser(null);
					setStatus('loading');
					setError('正在更新 Logto 组织权限…');
					await clearAllTokens();
					try {
						await signIn({
							redirectUri: `${window.location.origin}/auth/callback`,
							postRedirectUri: `${window.location.origin}/login`,
							prompt: Prompt.Consent,
						});
					} catch (signInError) {
						sessionStorage.removeItem(SCOPE_REFRESH_TENANT_KEY);
						throw signInError;
					}
					return;
				}
				activeTenant.current = null;
				localStorage.removeItem(ACTIVE_TENANT_KEY);
				setAccessTokenProvider(null);
				clearAccessToken();
				setUser(null);
				setStatus('selecting_tenant');
				setError(reason instanceof Error ? reason.message : '无法进入所选组织。');
				throw reason;
			}
		},
		[clearAllTokens, getAccessToken, signIn],
	);

	useEffect(() => {
		if (isLoading) return;
		if (!isAuthenticated) {
			authGeneration.current += 1;
			claimsRequested.current = false;
			activeTenant.current = null;
			setAccessTokenProvider(null);
			clearUserState();
			setUser(null);
			setOrganizations([]);
			setOrganizationNames({});
			setError('');
			setStatus('anonymous');
			return;
		}
		if (claimsRequested.current) return;

		claimsRequested.current = true;
		const currentAuthGeneration = authGeneration.current;
		void (async () => {
			try {
				const claims = await withLogtoTimeout(
					getIdTokenClaims(),
					'读取 Logto 登录信息超时，请重试登录。',
				);
				if (currentAuthGeneration !== authGeneration.current) return;
				const ids = Array.isArray(claims?.organizations)
					? claims.organizations.filter((item): item is string => typeof item === 'string')
					: [];
				setOrganizations(ids);
				setOrganizationNames({});
				if (ids.length === 0) {
					setStatus('selecting_tenant');
					setError('此 Logto 用户尚未加入任何组织。');
					return;
				}
				try {
					const userInfo = await withLogtoTimeout(
						fetchUserInfo(),
						'读取 Logto 组织名称超时。',
						LOGTO_DISPLAY_INFO_TIMEOUT_MS,
					);
					if (currentAuthGeneration !== authGeneration.current) return;
					const organizationData = (
						userInfo as { organization_data?: unknown } | undefined
					)?.organization_data;
					if (Array.isArray(organizationData)) {
						const allowedIds = new Set(ids);
						const names = Object.fromEntries(
							organizationData.flatMap((item) => {
								if (!item || typeof item !== 'object') return [];
								const organization = item as { id?: unknown; name?: unknown };
								return typeof organization.id === 'string' &&
									allowedIds.has(organization.id) &&
									typeof organization.name === 'string' &&
									organization.name.trim()
									? [[organization.id, organization.name.trim()]]
									: [];
							}),
						);
						setOrganizationNames(names);
					}
				} catch {
					// Organization names are a display enhancement; keep ID labels as fallback.
				}
				if (currentAuthGeneration !== authGeneration.current) return;
				setError('');
				setStatus('selecting_tenant');
			} catch (reason) {
				if (currentAuthGeneration !== authGeneration.current) return;
				setStatus('selecting_tenant');
				setError(reason instanceof Error ? reason.message : '无法读取 Logto 组织信息。');
			}
		})();
	}, [fetchUserInfo, getIdTokenClaims, isAuthenticated, isLoading]);

	const beginLogin = useCallback(async () => {
		await signIn({
			redirectUri: `${window.location.origin}/auth/callback`,
			postRedirectUri: `${window.location.origin}/login`,
		});
	}, [signIn]);

	const switchAccount = useCallback(async () => {
		activeTenant.current = null;
		localStorage.removeItem(ACTIVE_TENANT_KEY);
		sessionStorage.removeItem(SCOPE_REFRESH_TENANT_KEY);
		setAccessTokenProvider(null);
		clearUserState();
		try {
			await clearAllTokens();
			await signIn({
				redirectUri: `${window.location.origin}/auth/callback`,
				postRedirectUri: `${window.location.origin}/login`,
				prompt: Prompt.Login,
			});
		} catch (reason) {
			setUser(null);
			setOrganizations([]);
			setOrganizationNames({});
			setError(reason instanceof Error ? reason.message : 'Logto 登录失败。');
			setStatus('anonymous');
			throw reason;
		}
	}, [clearAllTokens, signIn]);

	const logout = useCallback(async () => {
		try {
			await authApi.logout();
		} finally {
			activeTenant.current = null;
			localStorage.removeItem(ACTIVE_TENANT_KEY);
			sessionStorage.removeItem(SCOPE_REFRESH_TENANT_KEY);
			setAccessTokenProvider(null);
			clearUserState();
			setUser(null);
			setStatus('loading');
			await signOut(window.location.origin);
		}
	}, [signOut]);

	const value = useMemo(
		() => ({
			status,
			user,
			login: async () => undefined,
			register: async () => undefined,
			logout,
			switchAccount,
			logtoEnabled: true,
			organizations,
			organizationNames,
			beginLogin,
			selectTenant,
			error,
		}),
		[status, user, logout, switchAccount, organizations, organizationNames, beginLogin, selectTenant, error],
	);

	return (
		<AuthContext.Provider value={value}>
			<LogtoCallbackHandler />
			{children}
		</AuthContext.Provider>
	);
}

function LogtoAuthProvider({ children }: { children: React.ReactNode }) {
	return (
		<LogtoProvider config={logtoConfig}>
			<LogtoAuthProviderContent>{children}</LogtoAuthProviderContent>
		</LogtoProvider>
	);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	return LOGTO_ENABLED ? (
		<LogtoAuthProvider>{children}</LogtoAuthProvider>
	) : (
		<LocalAuthProvider>{children}</LocalAuthProvider>
	);
}
