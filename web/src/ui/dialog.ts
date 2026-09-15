import { el } from "./format";
import { icon } from "./icons";

export interface DialogOptions {
  title: string;
  body: HTMLElement;
  /** Left as null for a dismissable dialog; set for a first-run acknowledgement. */
  primary?: { label: string; onClick: () => void };
  dismissable?: boolean;
}

/**
 * A single modal used for the first-run disclaimer and the Settings/About screen.
 * Plain DOM rather than <dialog> so the focus trap and the iOS Safari backdrop behave.
 */
export function openDialog(opts: DialogOptions): () => void {
  const dismissable = opts.dismissable ?? true;
  const scrim = el("div", "dialog-scrim");
  const panel = el("div", "dialog");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", opts.title);

  const head = el("header", "dialog-head");
  head.append(el("h2", "dialog-title", opts.title));
  if (dismissable) {
    const close = el("button", "icon-btn dialog-close");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.innerHTML = icon("close");
    close.addEventListener("click", () => teardown());
    head.append(close);
  }

  const bodyWrap = el("div", "dialog-body");
  bodyWrap.append(opts.body);

  panel.append(head, bodyWrap);

  if (opts.primary) {
    const foot = el("footer", "dialog-foot");
    const btn = el("button", "btn btn-primary", opts.primary.label);
    btn.type = "button";
    btn.addEventListener("click", () => {
      opts.primary?.onClick();
      teardown();
    });
    foot.append(btn);
    panel.append(foot);
  }

  scrim.append(panel);
  if (dismissable) {
    scrim.addEventListener("click", (e) => {
      if (e.target === scrim) teardown();
    });
  }

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" && dismissable) teardown();
  };
  document.addEventListener("keydown", onKey);
  document.body.append(scrim);
  (panel.querySelector<HTMLElement>("button, a, input") ?? panel).focus?.();

  let torn = false;
  function teardown(): void {
    if (torn) return;
    torn = true;
    document.removeEventListener("keydown", onKey);
    scrim.remove();
  }
  return teardown;
}
