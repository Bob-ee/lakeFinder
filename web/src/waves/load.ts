import { DATA_FILES } from "../config";
import { RECORD_BYTES, decodeWavePoints } from "@rules/waves/index.js";
import type { WavePoint, WavePointsIndex } from "@rules/waves/index.js";

/**
 * Reads the wave field: `wave_points.json` once, then one HTTP Range request per water
 * body against `wave_points.bin`.
 *
 * Everything here degrades to "this water body has no wave field", which is also the
 * answer for the ~99% of lakes that are too small to get one. A 404 (the pipeline has not
 * written the file yet), a server that ignores Range, and being offline all end up in the
 * same place rather than breaking the sheet.
 */

export interface WaveField {
  lakeId: number;
  points: WavePoint[];
  labels: string[];
  /** False when every point carries the unknown-depth sentinel. */
  hasDepth: boolean;
}

const DEPTH_UNKNOWN = 0xffff;

export class WaveFieldLoader {
  /** Single-flight: every caller during startup shares one request. */
  private indexPromise: Promise<WavePointsIndex | null> | null = null;
  private fields = new Map<number, Promise<WaveField | null>>();
  /** The whole .bin, kept only when the server ignored Range and sent it all. */
  private wholeFile: ArrayBuffer | null = null;

  /** null when there is no wave field at all (no file, or it could not be read). */
  index(): Promise<WavePointsIndex | null> {
    this.indexPromise ??= fetchIndex();
    return this.indexPromise;
  }

  /** True as soon as the index says this water body has points, before any Range request. */
  async has(lakeId: number): Promise<boolean> {
    const index = await this.index();
    return index != null && index.lakes[String(lakeId)] != null;
  }

  /** null when this water body has no points or the records could not be read. */
  field(lakeId: number): Promise<WaveField | null> {
    let pending = this.fields.get(lakeId);
    if (!pending) {
      pending = this.load(lakeId).catch((err: unknown) => {
        console.warn(`[waves] could not read the wave field for ${lakeId}`, err);
        return null;
      });
      this.fields.set(lakeId, pending);
    }
    return pending;
  }

  private async load(lakeId: number): Promise<WaveField | null> {
    const index = await this.index();
    if (!index) return null;
    const entry = index.lakes[String(lakeId)];
    if (!entry) return null;

    const [first, count] = entry;
    if (count <= 0) return null;
    const buffer = await this.bytes(first, count);
    if (!buffer) return null;

    const points = decodeWavePoints(buffer, index, lakeId);
    if (points.length === 0) return null;
    return {
      lakeId,
      points,
      labels: index.labels,
      hasDepth: points.some((p) => p.depth_dm !== DEPTH_UNKNOWN),
    };
  }

  /**
   * One water body's records. `decodeWavePoints` takes either the whole file or the slice,
   * so a server that answers 200 with everything is handled by handing the whole buffer
   * straight through (and keeping it, since it has already cost us the download).
   */
  private async bytes(first: number, count: number): Promise<ArrayBuffer | null> {
    if (this.wholeFile) return this.wholeFile;

    const start = first * RECORD_BYTES;
    const end = start + count * RECORD_BYTES - 1;
    let res: Response;
    try {
      res = await fetch(DATA_FILES.wavePoints, {
        headers: { Range: `bytes=${start}-${end}` },
        cache: "no-cache",
      });
    } catch (err) {
      console.warn("[waves] wave_points.bin unreachable", err);
      return null;
    }
    if (res.status === 404) return null;
    if (!res.ok) {
      console.warn(`[waves] wave_points.bin returned HTTP ${res.status}`);
      return null;
    }

    const buffer = await res.arrayBuffer();
    if (res.status === 206) return buffer;

    // 200: the server ignored the Range header and sent the file. Keep it so the next
    // water body is free, and let the decoder take its slice out of the whole thing.
    console.info("[waves] server ignored the Range request; caching the whole wave field");
    this.wholeFile = buffer;
    return buffer;
  }
}

async function fetchIndex(): Promise<WavePointsIndex | null> {
  try {
    const res = await fetch(DATA_FILES.wavePointsIndex, { cache: "no-cache" });
    if (res.status === 404) return null;
    if (!res.ok) {
      console.warn(`[waves] wave_points.json returned HTTP ${res.status}`);
      return null;
    }
    const body = (await res.json()) as WavePointsIndex;
    if (!body || typeof body !== "object" || !body.lakes || !Array.isArray(body.labels)) {
      console.warn("[waves] wave_points.json is not the shape the contract describes");
      return null;
    }
    if (body.record_bytes !== RECORD_BYTES) {
      console.warn(
        `[waves] wave_points.json record_bytes ${body.record_bytes} is not ${RECORD_BYTES}; ignoring the wave field`,
      );
      return null;
    }
    return body;
  } catch (err) {
    console.warn("[waves] wave_points.json could not be read", err);
    return null;
  }
}
