import { STORAGE_KEYS } from "../config";

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

type Listener = (resolved: ResolvedTheme, choice: ThemeChoice) => void;

/**
 * Dark/light follows the system by default with a manual override in localStorage.
 * Every storage access is wrapped: Safari throws on localStorage in some private modes
 * and a theme preference is never worth breaking startup over.
 */
export class Theme {
  private choice: ThemeChoice = "system";
  private media: MediaQueryList | null = null;
  private listeners = new Set<Listener>();

  constructor() {
    this.choice = readChoice();
    if (typeof matchMedia === "function") {
      this.media = matchMedia("(prefers-color-scheme: dark)");
      this.media.addEventListener("change", () => {
        if (this.choice === "system") this.apply();
      });
    }
    this.apply();
  }

  get current(): ThemeChoice {
    return this.choice;
  }

  get resolved(): ResolvedTheme {
    if (this.choice !== "system") return this.choice;
    return this.media?.matches ? "dark" : "light";
  }

  set(choice: ThemeChoice): void {
    this.choice = choice;
    try {
      localStorage.setItem(STORAGE_KEYS.theme, choice);
    } catch {
      // Storage blocked; the choice still holds for this session.
    }
    this.apply();
  }

  onChange(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private apply(): void {
    const resolved = this.resolved;
    document.documentElement.dataset["theme"] = resolved;
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (meta) meta.content = resolved === "dark" ? "#0d1117" : "#f6f8fa";
    for (const fn of this.listeners) fn(resolved, this.choice);
  }
}

function readChoice(): ThemeChoice {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.theme);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // Ignore; fall through to system.
  }
  return "system";
}

/** Same try/catch discipline for the other small preferences. */
export function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeLocal(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore.
  }
}
