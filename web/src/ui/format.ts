/** Display formatting. Aviation units: feet, acres, degrees magnetic-agnostic (true). */

export function formatFeet(ft: number | null | undefined): string {
  if (ft == null || !Number.isFinite(ft)) return "—";
  return `${Math.round(ft).toLocaleString("en-US")} ft`;
}

export function formatAcres(acres: number | null | undefined): string {
  if (acres == null || !Number.isFinite(acres)) return "—";
  const rounded = acres >= 100 ? Math.round(acres) : Math.round(acres * 10) / 10;
  return `${rounded.toLocaleString("en-US")} ac`;
}

/** A chord runs both ways, so show the reciprocal pair: 47 -> "047deg / 227deg". */
export function formatChordBearings(deg: number | null | undefined): string {
  if (deg == null || !Number.isFinite(deg)) return "";
  const a = ((Math.round(deg) % 360) + 360) % 360;
  const b = (a + 180) % 360;
  return `${pad3(a)}°/${pad3(b)}°`;
}

function pad3(n: number): string {
  return String(n).padStart(3, "0");
}

/** Thousands separators only. The briefing's numbers arrive pre-rounded; never re-round. */
export function formatThousands(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US");
}

/** Whole minutes between an ISO instant and now; null when unparseable. */
export function ageMinutes(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.floor((now - t) / 60_000);
}

/** "just now" / "12 min ago" / "3 h ago" / "2 d ago". Used by the briefing age badge. */
export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  const mins = ageMinutes(iso, now);
  if (mins == null) return "—";
  if (mins < 0) return "just now";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/** The same clock, squeezed for a chip: "now" / "31m" / "8h" / "2d". */
export function formatRelativeShort(iso: string | null | undefined, now = Date.now()): string {
  const mins = ageMinutes(iso, now);
  if (mins == null) return "—";
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

/** Builds an element with text content already escaped by the DOM. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
