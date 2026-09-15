/**
 * Location and flight mode (design.md 7.7) are phase 2: watchPosition with high accuracy,
 * follow-me that any manual pan pauses, heading-up above a speed threshold, wake lock, and
 * the own-position marker with a course arrow.
 *
 * Phase 1 ships the recenter button as a slot. It returns the camera to the data's home
 * view; phase 2 changes `recenter` to resume follow-me without moving the button.
 */

export interface LocationState {
  supported: boolean;
  /** Always false in phase 1; phase 2 flips this once permission is granted. */
  following: boolean;
}

export function locationState(): LocationState {
  return {
    supported: typeof navigator !== "undefined" && "geolocation" in navigator,
    following: false,
  };
}
