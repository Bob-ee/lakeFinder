/** Wire shapes of `/api/wind/*` (docs/data-contract.md, "Wind proxy"). */

export type StationSource = "metar" | "ndbc" | "synoptic";

export interface Station {
  id: string;
  source: StationSource | (string & {});
  name: string | null;
  lat: number;
  lon: number;
  /** Wind FROM, true; null when variable or calm. */
  dir_deg: number | null;
  speed_kt: number | null;
  gust_kt: number | null;
  obs_time: string;
}

export interface StationsResponse {
  stations: Station[];
  fetched_at: string;
  errors?: string[];
}

export interface PointWind {
  lat: number;
  lon: number;
  dir_deg: number | null;
  speed_kt: number | null;
  gust_kt: number | null;
  time: string;
  source: "model";
}

/** A wind reading from either kind of source, for the text and the components. */
export interface WindReading {
  dir_deg: number | null;
  speed_kt: number | null;
  gust_kt: number | null;
}
