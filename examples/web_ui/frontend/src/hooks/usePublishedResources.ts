import { useCallback, useEffect, useState } from 'react';

import { publishedApi } from '@/api';
import type { PublishedResource, ResourceKind } from '@/api';

export function usePublishedResources(kind: ResourceKind) {
	const [resources, setResources] = useState<PublishedResource[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async (options?: { silent?: boolean }) => {
		if (!options?.silent) setLoading(true);
		setError(null);
		try {
			const response = await publishedApi.list(kind);
			setResources(response.resources);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, [kind]);

	useEffect(() => {
		void refetch();
	}, [refetch]);

	useEffect(() => {
		const refresh = () => {
			if (document.visibilityState === 'visible') void refetch({ silent: true });
		};
		window.addEventListener('focus', refresh);
		// Publication changes are application data, not an AgentScope
		// configuration change. Poll frequently enough that an admin's scope
		// update reaches an already-open chat without a page reload or process
		// restart, while keeping the read-only endpoint inexpensive.
		const timer = window.setInterval(refresh, 3_000);
		return () => {
			window.removeEventListener('focus', refresh);
			window.clearInterval(timer);
		};
	}, [refetch]);

	return { resources, loading, error, refetch };
}
