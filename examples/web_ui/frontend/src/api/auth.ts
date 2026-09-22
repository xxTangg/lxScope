import { clearAccessToken, client, setAccessToken } from './client';

export interface AuthUser {
	id: string;
	username: string;
	display_name?: string | null;
	external_user_id?: string | null;
	role: 'user' | 'admin';
	status: 'active' | 'locked' | 'banned' | 'deleted';
	capabilities: string[];
	permissions: string[];
	tenant_id?: string | null;
	membership_id?: string | null;
	membership_role?: string | null;
	membership_status?: string | null;
	identity_provider?: string | null;
}

export interface AuthContextResponse {
	provider: 'local' | 'logto';
	user: AuthUser;
	permissions: string[];
	tenant_id: string | null;
	membership_id: string | null;
	external_org_id: string | null;
	membership_role: string | null;
	tenant_status: string | null;
	user_status: string | null;
	membership_status: string | null;
}

export interface LoginResponse {
	access_token: string;
	token_type: 'bearer';
	expires_in: number;
	user: AuthUser;
}

export interface TokenUsage {
	input_tokens: number;
	output_tokens: number;
	cache_input_tokens: number;
	cache_creation_input_tokens: number;
	total_tokens: number;
	message_count: number;
	session_count: number;
}

async function authenticate(
	path: '/auth/login' | '/auth/register',
	username: string,
	password: string,
) {
	const response = await client.post<LoginResponse>(path, { username, password }, undefined, {
		authenticated: false,
		silent: true,
	});
	setAccessToken(response.access_token);
	return response.user;
}

export const authApi = {
	login: (username: string, password: string) => authenticate('/auth/login', username, password),
	register: (username: string, password: string) =>
		authenticate('/auth/register', username, password),
	me: () => client.get<AuthUser>('/auth/me', undefined, { silent: true }),
	context: () => client.get<AuthContextResponse>('/auth/context', undefined, { silent: true }),
	usage: () => client.get<TokenUsage>('/auth/usage', undefined, { silent: true }),
	logout: async () => {
		try {
			await client.post<void>('/auth/logout', undefined, undefined, { silent: true });
		} finally {
			clearAccessToken();
		}
	},
};
