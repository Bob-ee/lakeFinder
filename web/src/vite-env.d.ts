/// <reference types="vite/client" />

declare const __APP_VERSION__: string;

/**
 * Provided by the `seaplane:rules-engine-shim` plugin in vite.config.ts. It re-exports
 * ../rules/engine/index.js when that file exists and nulls when it does not, so the
 * client builds and runs either way.
 */
declare module "virtual:rules-engine" {
  export const engineAvailable: boolean;
  export const evaluateLake: ((...args: unknown[]) => unknown) | null;
  export const evaluateAll: ((...args: unknown[]) => unknown) | null;
  export const loadRules: ((...args: unknown[]) => unknown) | null;
  export const normalizeName: ((raw: string | null | undefined) => string) | null;
}
