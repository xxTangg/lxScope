import { clearAccessToken, client, setAccessToken } from './client';

export interface AuthUser {
	id: string;
	username: string;
	role: 'user' | 'admin';
	status: 'active' | 'locked' | 'banned' | 'deleted';
	capabilities: string[];
	tenant_id?: string | null;
	subject_id?: string | null;
	organization_scopes?: string[];
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
	usage: () => client.get<TokenUsage>('/auth/usage', undefined, { silent: true }),
	logout: async () => {
		try {
			await client.post<void>('/auth/logout', undefined, undefined, { silent: true });
		} finally {
			clearAccessToken();
		}
	},
};
