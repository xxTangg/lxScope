const PRIVATE_RESULT_KEYS = /^(authorization_subject|user_id|userId|access_token|refresh_token|token|secret|password|api[_-]?key|private[_-]?reasoning|chain[_-]?of[_-]?thought)$/i;

export function sanitizeTaskRunText(value: string): string {
	return value
		.replace(/\bBearer\s+\S+/gi, 'Bearer [已隐藏]')
		.replace(/\bsk-[A-Za-z0-9_-]{16,}\b/g, '[已隐藏密钥]')
		.replace(
			/((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret|private[_-]?reasoning|chain[_-]?of[_-]?thought)\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)/gi,
			'$1[已隐藏]',
		);
}

export function sanitizeTaskRunValue(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(sanitizeTaskRunValue);
	if (typeof value === 'string') return sanitizeTaskRunText(value);
	if (!value || typeof value !== 'object') return value;
	return Object.fromEntries(
		Object.entries(value as Record<string, unknown>)
			.filter(([key]) => !PRIVATE_RESULT_KEYS.test(key))
			.map(([key, child]) => [key, sanitizeTaskRunValue(child)]),
	);
}

export function sanitizeTaskRunContent(value: string): string {
	const trimmed = value.trim();
	if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
		try {
			return JSON.stringify(sanitizeTaskRunValue(JSON.parse(trimmed)), null, 2) ?? sanitizeTaskRunText(value);
		} catch {
			// Preserve non-JSON Markdown or text and redact known credential patterns below.
		}
	}
	return sanitizeTaskRunText(value);
}
