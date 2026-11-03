import { client } from './client';
import type { PublishedResource, ResourceKind } from './types';

export interface PublishedResourceListResponse {
	resources: PublishedResource[];
	total: number;
}

/** The read-only catalog published by an administrator for this user. */
export const publishedApi = {
	list: (kind: ResourceKind) =>
		client.get<PublishedResourceListResponse>('/resources/published', { kind }),
};
