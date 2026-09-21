import { ApiError, client, getBaseUrl, getRequestAccessToken } from './client';
import type {
	ChatRequest,
	ListChatAttachmentContentTypesResponse,
	ParseChatAttachmentResponse,
} from './types';

/** Upload one document for server-side text extraction. */
async function parseAttachment(file: File): Promise<ParseChatAttachmentResponse> {
	const formData = new FormData();
	formData.append('file', file);
	const token = await getRequestAccessToken();

	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		const url = new URL('/chat/attachments/parse', getBaseUrl());
		xhr.open('POST', url.toString(), true);
		if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

		xhr.onload = () => {
			if (xhr.status >= 200 && xhr.status < 300) {
				try {
					resolve(JSON.parse(xhr.responseText) as ParseChatAttachmentResponse);
				} catch (error) {
					reject(error);
				}
				return;
			}
			let detail = xhr.responseText || xhr.statusText;
			try {
				const json = JSON.parse(xhr.responseText) as { detail?: unknown };
				if (typeof json.detail === 'string') detail = json.detail;
				else if (json.detail !== undefined) detail = JSON.stringify(json.detail);
			} catch {
				// Keep the raw response when it is not JSON.
			}
			reject(new ApiError(xhr.status, detail));
		};
		xhr.onerror = () => reject(new ApiError(0, 'Network error'));
		xhr.send(formData);
	});
}

/**
 * Chat API — fire-and-forget trigger for chat runs.
 *
 * Events produced by the run are delivered via the session's SSE
 * stream endpoint (``GET /sessions/{sid}/stream``), not in the
 * response body of this POST.
 */
export const chatApi = {
	supportedAttachmentContentTypes: () =>
		client.get<ListChatAttachmentContentTypesResponse>(
			'/chat/attachments/supported_content_types',
			undefined,
			{ silent: true },
		),

	parseAttachment,

	/**
	 * Trigger a chat run for the specified session.
	 *
	 * Accepts user messages, human-in-the-loop confirmation events,
	 * or ``null`` (continue from current state). Returns immediately;
	 * the caller should already be subscribed to the session's SSE
	 * stream to receive the resulting events.
	 *
	 * @param body - The chat request payload.
	 * @returns A confirmation object ``{ status, session_id }``.
	 */
	trigger: (body: ChatRequest) =>
		client.post<{ status: string; session_id: string }>('/chat/', body),
};
