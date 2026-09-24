import { ApiError } from '@/api/client';

/** Keep a compatibility path for older callers that still pass serialized validation details. */
export function formatApiErrorForAlert(err: unknown): string {
	if (err instanceof ApiError) {
		const { detail } = err;
		try {
			const parsed = JSON.parse(detail) as unknown;
			if (Array.isArray(parsed)) {
				const msgs = parsed
					.map((item) =>
						item && typeof item === 'object' && 'msg' in item
							? String((item as { msg: unknown }).msg)
							: null,
					)
					.filter((m): m is string => !!m);
				if (msgs.length > 0) return msgs.join('\n');
			}
		} catch {
			// not JSON — fall through to raw detail
		}
		return detail;
	}
	if (err instanceof Error) return err.message;
	return String(err);
}
