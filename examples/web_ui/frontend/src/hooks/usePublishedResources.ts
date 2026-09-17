import { useCallback, useEffect, useState } from 'react';

import { publishedApi } from '@/api';
import type { PublishedResource, ResourceKind } from '@/api';

export function usePublishedResources(kind: ResourceKind) {
	const [resources, setResources] = useState<PublishedResource[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
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

	return { resources, loading, error, refetch };
}
