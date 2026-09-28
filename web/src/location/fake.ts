/**
 * DEV ONLY. `?fakefix=lat,lon[,speed_kt[,course[,accuracy_m]]]` emits a fix every second,
 * dead-reckoned along the course, so follow-me, heading-up and the Nearest throttle can be
 * exercised on a desk. Imported only from a `import.meta.env.DEV` branch in index.ts, so a
 * production build contains none of this.
 *
 *   ?fakefix=42.45,-82.75,80,45      Lake St. Clair, 80 kt, course 045
 *   ?fakefix=42.45,-82.75,0          parked (dot, north-up)
 *   ?fakefix=42.45,-82.75,80,45,hold same fix every second, no movement (steady screenshots)
 */
import type { Fix } from "./index";
import { advance } from "./geo";

export function startFakeFix(raw: string, emit: (fix: Fix) => void): void {
  const parts = raw.split(",").map((s) => s.trim());
  const nums = parts.map(Number);
  let lat = nums[0] ?? NaN;
  let lon = nums[1] ?? NaN;
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    console.warn(`[fakefix] ignoring "${raw}": want lat,lon[,speed_kt[,course[,accuracy_m|hold]]]`);
    return;
  }
  const speed = Number.isFinite(nums[2]) ? nums[2]! : 0;
  const course = Number.isFinite(nums[3]) ? nums[3]! : null;
  const hold = parts.includes("hold");
  const accuracy = Number.isFinite(nums[4]) ? nums[4]! : 12;
  console.info(`[fakefix] ${lat},${lon} ${speed} kt course ${course ?? "none"}${hold ? " (hold)" : ""}`);

  const tick = (): void => {
    emit({
      lat,
      lon,
      accuracy_m: accuracy,
      speed_kt: speed,
      course_deg: course,
      time: new Date().toISOString(),
    });
    if (!hold && course != null && speed > 0) [lat, lon] = advance(lat, lon, course, speed / 3600);
  };
  tick();
  window.setInterval(tick, 1000);
}
