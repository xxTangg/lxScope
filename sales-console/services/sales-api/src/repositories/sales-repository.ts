import { db, readJson, transactJson, updateJson, writeJson } from '../store.js';
import type { IdempotencyRecord } from '../store.js';
import type { Customer, RechargeOrder, Settings, Staff, UsageReport } from '../types.js';

export interface SalesCollections {
  staff: Staff[];
  customers: Customer[];
  orders: RechargeOrder[];
  reports: UsageReport[];
  settings: Settings;
  audit: unknown[];
  idempotency: IdempotencyRecord[];
}

export type SalesCollectionName = keyof SalesCollections;

export interface SalesTransaction {
  get<K extends SalesCollectionName>(collection: K): SalesCollections[K];
  set<K extends SalesCollectionName>(collection: K, value: SalesCollections[K]): void;
}

export interface SalesRepository {
  read<K extends SalesCollectionName>(collection: K): Promise<SalesCollections[K]>;
  save<K extends SalesCollectionName>(collection: K, value: SalesCollections[K]): Promise<void>;
  update<K extends SalesCollectionName>(
    collection: K,
    mutate: (current: SalesCollections[K]) => SalesCollections[K] | Promise<SalesCollections[K]>,
  ): Promise<SalesCollections[K]>;
  transaction<T>(
    collections: readonly SalesCollectionName[],
    work: (transaction: SalesTransaction) => Promise<T>,
  ): Promise<T>;
}

const definitions: {
  [K in SalesCollectionName]: { file: string; fallback: SalesCollections[K] };
} = {
  staff: { file: db.files.staff, fallback: [] },
  customers: { file: db.files.customers, fallback: [] },
  orders: { file: db.files.orders, fallback: [] },
  reports: { file: db.files.reports, fallback: [] },
  settings: { file: db.files.settings, fallback: { tokenExchangeRate: 41841 } },
  audit: { file: db.files.audit, fallback: [] },
  idempotency: { file: db.files.idempotency, fallback: [] },
};

export const salesRepository: SalesRepository = {
  read<K extends SalesCollectionName>(collection: K): Promise<SalesCollections[K]> {
    const definition = definitions[collection];
    return readJson(definition.file, definition.fallback);
  },

  save<K extends SalesCollectionName>(collection: K, value: SalesCollections[K]): Promise<void> {
    return writeJson(definitions[collection].file, value);
  },

  update<K extends SalesCollectionName>(
    collection: K,
    mutate: (current: SalesCollections[K]) => SalesCollections[K] | Promise<SalesCollections[K]>,
  ): Promise<SalesCollections[K]> {
    const definition = definitions[collection];
    return updateJson(definition.file, definition.fallback, mutate);
  },

  transaction<T>(
    collections: readonly SalesCollectionName[],
    work: (transaction: SalesTransaction) => Promise<T>,
  ): Promise<T> {
    const uniqueCollections = [...new Set(collections)];
    const entries = uniqueCollections.map((name) => {
      const definition = definitions[name];
      return [definition.file, definition.fallback] as const;
    });
    return transactJson(entries, (state) =>
      work({
        get<K extends SalesCollectionName>(collection: K): SalesCollections[K] {
          const definition = definitions[collection];
          return state.read(definition.file, definition.fallback);
        },
        set<K extends SalesCollectionName>(collection: K, value: SalesCollections[K]): void {
          state.write(definitions[collection].file, value);
        },
      }),
    );
  },
};
