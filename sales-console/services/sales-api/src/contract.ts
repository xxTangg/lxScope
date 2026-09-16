const ISO_TIME_KEYS = new Set([
  'at',
  'generated_at',
  'created_at',
  'updated_at',
  'reported_at',
  'requested_at',
  'client_reported_at',
  'processed_at',
  'delivered_at',
  'last_delivery_at',
  'uploaded_at',
  'last_seen_at',
  'last_report_at',
  'issued_at',
  'expires_at',
  'checked_at',
]);

const MONEY_KEYS = new Set([
  'amount',
  'requested_amount',
  'total_recharged',
  'recharged_amount',
  'recharged_30d',
]);

const CANONICAL_INPUT_KEYS: Record<string, string> = {
  api_token: 'apiToken',
  artifact_type: 'artifactType',
  configured_ip: 'ip',
  customer_ids: 'customerIDs',
  download_url: 'downloadUrl',
  new_password: 'newPassword',
  public_key_pem: 'publicKeyPem',
  request_id: 'requestID',
  size_bytes: 'sizeBytes',
  system_id: 'systemId',
  token_exchange_rate: 'tokenExchangeRate',
};

function snakeToCamelKey(key: string): string {
  return (
    CANONICAL_INPUT_KEYS[key] ??
    key.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase())
  );
}

function camelToSnakeKey(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
    .toLowerCase()
    .replace(/^recharged30d$/, 'recharged_30d');
}

export function fromCanonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(fromCanonical);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [snakeToCamelKey(key), fromCanonical(item)]),
  );
}

export function toCanonical(value: unknown, key?: string): unknown {
  if (Array.isArray(value)) return value.map((item) => toCanonical(item));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .map(([itemKey, item]) => [camelToSnakeKey(itemKey), toCanonical(item, camelToSnakeKey(itemKey))]),
    );
  }
  if (typeof value === 'number' && ISO_TIME_KEYS.has(key ?? '')) {
    return new Date(value).toISOString();
  }
  if (typeof value === 'number' && MONEY_KEYS.has(key ?? '')) {
    return value.toFixed(2);
  }
  return value;
}

export function releaseTypeFromCanonical(value: string): 'app' | 'opencode' | null {
  if (value === 'app') return 'app';
  if (value === 'core' || value === 'opencode') return 'opencode';
  return null;
}

export function releaseTypeToCanonical(value: 'app' | 'opencode'): 'app' | 'core' {
  return value === 'app' ? 'app' : 'core';
}
