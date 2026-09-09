import { useEffect, useState } from 'react';

import { chatApi } from '@/api';
import type { ListChatAttachmentContentTypesResponse } from '@/api';

/** Parser-backed chat attachment capabilities are fixed for one app run. */
let cached: ListChatAttachmentContentTypesResponse | null = null;
let inflight: Promise<ListChatAttachmentContentTypesResponse> | null = null;

async function fetchSupported(): Promise<ListChatAttachmentContentTypesResponse> {
	if (cached) return cached;
	if (inflight) return inflight;
	inflight = chatApi
		.supportedAttachmentContentTypes()
		.then((response) => {
			cached = response;
			return response;
		})
		.finally(() => {
			inflight = null;
		});
	return inflight;
}

/** Fetch document types that the server can extract into chat text. */
export function useChatAttachmentContentTypes(): {
	mediaTypes: string[];
	extensions: string[];
	loading: boolean;
	error: Error | null;
} {
	const [data, setData] = useState<ListChatAttachmentContentTypesResponse | null>(cached);
	const [loading, setLoading] = useState(cached === null);
	const [error, setError] = useState<Error | null>(null);

	useEffect(() => {
		if (cached) {
			setData(cached);
			return;
		}
		let cancelled = false;
		setLoading(true);
		fetchSupported()
			.then((response) => {
				if (!cancelled) setData(response);
			})
			.catch((cause) => {
				if (!cancelled) setError(cause as Error);
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, []);

	return {
		mediaTypes: data?.media_types ?? [],
		extensions: data?.extensions ?? [],
		loading,
		error,
	};
}
