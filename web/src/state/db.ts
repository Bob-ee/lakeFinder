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

/**
 * Last good `/data/briefing.json`, so the card still reads offline. One row, key "latest"
 * (data-contract "Client URL and storage"). `payload` is the raw parsed file; the loader
 * owns its shape so a schema bump does not have to touch the database layer.
 */
export interface BriefingRecord {
  key: string;
  stored_at: number;
  payload: unknown;
}

interface SeaplaneDB extends DBSchema {
  saved: { key: number; value: SavedRecord };
  recent: { key: number; value: RecentRecord };
  wind: { key: string; value: { key: string; fetched_at: number; payload: unknown } };
  briefing: { key: string; value: BriefingRecord };
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
        // Every store is created behind a `contains` check, so this one upgrade function
        // serves a fresh database and every version bump alike; existing stores are left
        // untouched when the version moves (v1 -> v2 added `briefing`).
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
          if (!database.objectStoreNames.contains("briefing")) {
            database.createObjectStore("briefing", { keyPath: "key" });
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
