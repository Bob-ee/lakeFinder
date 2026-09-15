import { SEARCH } from "../config";
import { db, type RecentRecord } from "../state/db";
import type { Lake } from "../types";

/** Most recent first, capped at SEARCH.maxRecent. Never throws. */
export async function listRecent(): Promise<RecentRecord[]> {
  const handle = db();
  if (!handle) return [];
  try {
    const rows = await (await handle).getAll("recent");
    rows.sort((a, b) => b.searched_at - a.searched_at);
    return rows.slice(0, SEARCH.maxRecent);
  } catch (err) {
    console.warn("[recent] read failed", err);
    return [];
  }
}

export async function pushRecent(lake: Lake): Promise<void> {
  const handle = db();
  if (!handle) return;
  try {
    const database = await handle;
    await database.put("recent", { id: lake.id, name: lake.name, searched_at: Date.now() });
    // Trim so the store does not grow without bound.
    const rows = await database.getAll("recent");
    if (rows.length > SEARCH.maxRecent * 3) {
      rows.sort((a, b) => b.searched_at - a.searched_at);
      const tx = database.transaction("recent", "readwrite");
      await Promise.all(rows.slice(SEARCH.maxRecent).map((r) => tx.store.delete(r.id)));
      await tx.done;
    }
  } catch (err) {
    console.warn("[recent] write failed", err);
  }
}

export async function clearRecent(): Promise<void> {
  const handle = db();
  if (!handle) return;
  try {
    await (await handle).clear("recent");
  } catch (err) {
    console.warn("[recent] clear failed", err);
  }
}
