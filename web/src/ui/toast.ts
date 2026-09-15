import { el } from "./format";

let host: HTMLElement | null = null;
let timer: number | null = null;

/** One transient line at the bottom of the screen. Used for stubs and copy feedback. */
export function toast(message: string, ms = 2400): void {
  if (!host) {
    host = el("div", "toast-host");
    host.setAttribute("role", "status");
    host.setAttribute("aria-live", "polite");
    document.body.append(host);
  }
  host.textContent = message;
  host.classList.add("is-visible");
  if (timer != null) clearTimeout(timer);
  timer = window.setTimeout(() => {
    host?.classList.remove("is-visible");
  }, ms);
}
