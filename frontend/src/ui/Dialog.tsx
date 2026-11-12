import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";

/**
 * One accessible dialog for every overlay in the app (plan §4):
 * - "modal": centred card (register a flat, close a flat)
 * - "drawer": full-height panel from the right (closing and invoice)
 * - "sheet": slides up from the bottom on phones, a centred card on desktop
 * - "menu": a small panel under the top bar on desktop, a bottom sheet on phones (avatar, alerts, create)
 * Focus moves in and is trapped, Esc and the scrim close it, focus returns to the opener, and the
 * page behind does not scroll.
 */
export type DialogVariant = "modal" | "drawer" | "sheet" | "menu";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({ open, onClose, title, variant = "modal", children, footer, hideTitle }: {
  open: boolean;
  onClose: () => void;
  title: string;
  variant?: DialogVariant;
  children: ReactNode;
  footer?: ReactNode;
  /** Keep the title for screen readers but don't show it (menus). */
  hideTitle?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    // Focus the first field or button inside, else the panel itself.
    const first = panel.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      panel.current?.querySelector<HTMLElement>("input, select, textarea") ??
      panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === firstEl || document.activeElement === panel.current)) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    }
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = overflow;
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className={`dlg-scrim dlg-${variant}`} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={panel} className="dlg-panel" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className={`dlg-head${hideTitle ? " visually-hidden-head" : ""}`}>
          <h2 id={titleId} className={hideTitle ? "visually-hidden" : undefined}>{title}</h2>
          {!hideTitle && (
            <button className="icon-btn plain" aria-label="Close" onClick={onClose}><Icon name="x" size={20} /></button>
          )}
        </header>
        <div className="dlg-body">{children}</div>
        {footer && <footer className="dlg-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
