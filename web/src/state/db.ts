import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import { IDB } from "../config";

export interface RecentRecord {
  id: number;
  name: string | null;
  searched_at: number;
}

/** Phase 4 fills this in; the store is created now so the schema version never has to bump for it. */
export interface SavedRecord {
  id: number;
  saved_at: number;
  tags: string[];
  note: string;
  verified: boolean;
  last_landed: string | null;
}

interface SeaplaneDB extends DBSchema {
  saved: { key: number; value: SavedRecord };
  recent: { key: number; value: RecentRecord };
  wind: { key: string; value: { key: string; fetched_at: number; payload: unknown } };
}

let dbPromise: Promise<IDBPDatabase<SeaplaneDB>> | null = null;

/**
 * IndexedDB is unavailable in some private-browsing modes and can throw on open.
 * Every caller treats a null database as "no history", never as an error.
 */
export function db(): Promise<IDBPDatabase<SeaplaneDB>> | null {
  if (typeof indexedDB === "undefined") return null;
  if (!dbPromise) {
    try {
      dbPromise = openDB<SeaplaneDB>(IDB.name, IDB.version, {
        upgrade(database) {
          if (!database.objectStoreNames.contains("saved")) {
            database.createObjectStore("saved", { keyPath: "id" });
          }
          if (!database.objectStoreNames.contains("recent")) {
            database.createObjectStore("recent", { keyPath: "id" });
          }
          if (!database.objectStoreNames.contains("wind")) {
            database.createObjectStore("wind", { keyPath: "key" });
          }
        },
      });
    } catch (err) {
      console.warn("[db] IndexedDB unavailable, history disabled", err);
      return null;
    }
  }
  return dbPromise;
}
