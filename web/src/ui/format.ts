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
