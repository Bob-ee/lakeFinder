/**
 * Saved lakes (design.md 7.6) land in phase 4: star on the sheet, IndexedDB store `saved`,
 * tag filter, distinct map marker, export/import, and a sync endpoint on the homelab.
 *
 * Phase 1 ships the slots only. The `saved` object store already exists in state/db.ts so
 * the schema version does not have to bump when this is filled in, and the star button in
 * sheet/detail.ts is wired to a no-op with a "phase 4" tooltip.
 */
import { db, type SavedRecord } from "../state/db";

/** Reads whatever is already in the store. Returns [] in phase 1. */
export async function listSaved(): Promise<SavedRecord[]> {
  const handle = db();
  if (!handle) return [];
  try {
    return await (await handle).getAll("saved");
  } catch {
    return [];
  }
}

export async function isSaved(id: number): Promise<boolean> {
  const handle = db();
  if (!handle) return false;
  try {
    return (await (await handle).get("saved", id)) != null;
  } catch {
    return false;
  }
}
