import { toast } from 'sonner';

const getDefaultBaseUrl = () => {
	const port = import.meta.env.VITE_AGENTSCOPE_API_PORT ?? '8001';
	return `${window.location.protocol}//${window.location.hostname}:${port}`;
};

export const getBaseUrl = () => localStorage.getItem('server_url') ?? getDefaultBaseUrl();
const ACCESS_TOKEN_KEY = 'agentscope_access_token';

export const getAccessToken = () => localStorage.getItem(ACCESS_TOKEN_KEY);
export const setAccessToken = (token: string) => localStorage.setItem(ACCESS_TOKEN_KEY, token);
export const clearAccessToken = () => localStorage.removeItem(ACCESS_TOKEN_KEY);

export const AUTH_UNAUTHORIZED_EVENT = 'agentscope:auth-unauthorized';

/**
 * Structured error thrown for non-2xx HTTP responses.
 * `message` contains the human-readable detail extracted from the backend.
 */
export class ApiError extends Error {
	readonly status: number;
	readonly detail: string;

	constructor(status: number, detail: string) {
		super(detail);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
	}
}

interface RequestOptions {
	method?: string;
	body?: unknown;
	params?: Record<string, string>;
	/** When true, suppresses the automatic error toast. Useful when the caller shows its own inline error UI. */
	silent?: boolean;
	signal?: AbortSignal;
	/** Overrides the stored server URL. Lets the setup page probe an address before persisting it. */
	baseUrl?: string;
	/** Public endpoints such as login opt out of the bearer token. */
	authenticated?: boolean;
	/** Optional correlation or idempotency headers for state-changing requests. */
	headers?: Record<string, string>;
	/** Gives up after this many ms and reports {@link TIMEOUT_STATUS}. Off by default — a streaming chat is meant to stay open. */
	timeoutMs?: number;
}

/** Reported when `timeoutMs` elapses. Real 408s come from a server, so either way the request did not complete in time. */
export const TIMEOUT_STATUS = 408;

function buildHeaders(hasJsonBody: boolean, authenticated: boolean): Record<string, string> {
	const headers: Record<string, string> = {};
	const token = getAccessToken();
	if (authenticated && token) headers.Authorization = `Bearer ${token}`;
	if (hasJsonBody) headers['Content-Type'] = 'application/json';
	return headers;
}

/** Parse the response body and extract the `detail` field if the backend returned JSON. */
async function extractErrorDetail(res: Response): Promise<string> {
	const text = await res.text();
	try {
		const json = JSON.parse(text) as { detail?: unknown };
		if (typeof json.detail === 'string') return json.detail;
		if (json.detail !== undefined) return JSON.stringify(json.detail);
	} catch {
		// not JSON – fall through
	}
	return text || res.statusText;
}

async function streamRequest(path: string, options: RequestOptions = {}): Promise<Response> {
	const {
		method = 'GET',
		body,
		params,
		signal,
		silent = false,
		baseUrl,
		authenticated = true,
		timeoutMs,
		headers: extraHeaders,
	} = options;
	const url = new URL(path, baseUrl ?? getBaseUrl());
	if (params) {
		Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
	}

	// AbortSignal.timeout aborts with a TimeoutError, which is what lets the
	// catch below tell "too slow" apart from the caller's own cancellation.
	const deadline = timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined;
	const combined =
		deadline && signal ? AbortSignal.any([signal, deadline]) : (deadline ?? signal);

	let res: Response;
	try {
		res = await fetch(url.toString(), {
			method,
			headers: {
				...buildHeaders(body !== undefined && !(body instanceof FormData), authenticated),
				...extraHeaders,
			},
			body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
			signal: combined,
		});
	} catch (e) {
		// An abort is the caller's own doing — pass it through untouched.
		if (e instanceof DOMException && e.name === 'AbortError') throw e;
		// A server that accepts the connection then stalls would otherwise
		// leave the caller waiting forever.
		const timedOut = e instanceof DOMException && e.name === 'TimeoutError';
		// Otherwise fetch only rejects when the request never reached the
		// server: wrong address, DNS failure, refused connection, blocked
		// preflight. Status 0 distinguishes that from any HTTP-level failure.
		const error = timedOut
			? new ApiError(TIMEOUT_STATUS, 'The server took too long to respond.')
			: new ApiError(
					0,
					'Cannot reach the server. Check the server address and your network.',
				);
		if (!silent) toast.error(error.detail);
		throw error;
	}

	if (!res.ok) {
		const detail = await extractErrorDetail(res);
		const error = new ApiError(res.status, detail);
		if (res.status === 401 && authenticated && getAccessToken()) {
			window.dispatchEvent(new Event(AUTH_UNAUTHORIZED_EVENT));
		}
		if (!silent) toast.error(detail);
		throw error;
	}

	return res;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
	const res = await streamRequest(path, options);
	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

export const client = {
	get: <T>(
		path: string,
		params?: Record<string, string>,
		options?: {
			silent?: boolean;
			baseUrl?: string;
			authenticated?: boolean;
			timeoutMs?: number;
			headers?: Record<string, string>;
		},
	) => request<T>(path, { method: 'GET', params, ...options }),
	post: <T>(
		path: string,
		body?: unknown,
		params?: Record<string, string>,
		options?: {
			silent?: boolean;
			authenticated?: boolean;
			headers?: Record<string, string>;
		},
	) => request<T>(path, { method: 'POST', body, params, ...options }),
	form: <T>(
		path: string,
		body: FormData,
		options?: {
			silent?: boolean;
			headers?: Record<string, string>;
		},
	) => request<T>(path, { method: 'POST', body, ...options }),
	patch: <T>(
		path: string,
		body?: unknown,
		params?: Record<string, string>,
		options?: { silent?: boolean; headers?: Record<string, string> },
	) =>
		request<T>(path, {
			method: 'PATCH',
			body,
			params,
			silent: options?.silent,
			headers: options?.headers,
		}),
	delete: <T = void>(path: string, params?: Record<string, string>) =>
		request<T>(path, { method: 'DELETE', params }),
	stream: (path: string, options?: RequestOptions) => streamRequest(path, options),
};
