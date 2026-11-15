/**
 * Window primitives that reproduce the reference system's chrome.
 *
 * Every screen in the customer's EDMS is a window with a grey title bar, a small
 * icon ribbon, a khaki-labelled field grid and a green bottom strip. These four
 * components are the only place that look is defined, so every page stays
 * consistent — which is exactly what the screenshots ask for.
 */

import clsx from "clsx";
import type { ReactNode } from "react";
import { IconFolder } from "./icons";

export function Window({
  title,
  subtitle,
  onClose,
  actions,
  children,
  strip = true,
  dialog = false,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  /** Renders the ✕ button. Omit on a page-level window that cannot be closed. */
  onClose?: () => void;
  /** Right-hand slot inside the title bar (window menu buttons). */
  actions?: ReactNode;
  children: ReactNode;
  /** The reference draws a green strip at the bottom of every window. */
  strip?: boolean;
  dialog?: boolean;
  className?: string;
}) {
  return (
    <section className={clsx("window", dialog && "is-dialog", className)}>
      <header className="window-title">
        <button type="button" className="wt-btn is-close" aria-label="بستن" onClick={onClose} disabled={!onClose}>
          ✕
        </button>
        <button type="button" className="wt-btn" aria-label="فهرست" disabled>
          ▣
        </button>
        <div className="wt-text" title={typeof title === "string" ? title : undefined}>
          {title}
          {subtitle ? <span className="mr-2 font-normal text-[#4a5a67]">{subtitle}</span> : null}
        </div>
        {actions}
        <span className="flex items-center gap-1 text-[9px] text-[#5c6b78]">
          <IconFolder className="h-3 w-3" />
        </span>
      </header>
      <div className="window-body">{children}</div>
      {strip ? <div className="window-strip" /> : null}
    </section>
  );
}

export interface RibbonItem {
  label: string;
  icon: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  danger?: boolean;
  /** Draws a group separator before this item, like the reference ribbon. */
  groupStart?: boolean;
  title?: string;
}

export function Ribbon({ items }: { items: RibbonItem[] }) {
  return (
    <div className="ribbon" role="toolbar">
      {items.map((item) => (
        <span key={item.label} className="contents">
          {item.groupStart ? <span className="ribbon-sep" aria-hidden /> : null}
          <button
            type="button"
            className={clsx("ribbon-btn", item.danger && "is-danger")}
            onClick={item.onClick}
            disabled={item.disabled}
            title={item.title ?? item.label}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        </span>
      ))}
    </div>
  );
}

/** One labelled cell pair. Rendered inside <FieldGrid>. */
export function FField({
  label,
  children,
  full,
  muted,
  className,
}: {
  label: string;
  children?: ReactNode;
  /** Let the value cell span the rest of the row. */
  full?: boolean;
  muted?: boolean;
  className?: string;
}) {
  return (
    <>
      <div className="fg-label">{label}</div>
      <div className={clsx("fg-value", full && "span-full", muted && "is-muted", className)}>
        {children ?? <span className="text-[#9aa8b4]">—</span>}
      </div>
    </>
  );
}

export function FieldGrid({
  children,
  three = false,
  className,
}: {
  children: ReactNode;
  three?: boolean;
  className?: string;
}) {
  return <div className={clsx("field-grid", three && "is-3col", className)}>{children}</div>;
}

/** Navy band that titles a group of fields ("Transmittal Information"). */
export function FormCaption({ children }: { children: ReactNode }) {
  return <div className="section-bar">{children}</div>;
}

/** Full-width navy band that groups rows inside a grid (reference "شماره گردش مدرک: 1"). */
export function GroupBand({ children }: { children: ReactNode }) {
  return <div className="group-band">{children}</div>;
}

/** Small square icon button used inside grid cells. */
export function IconButton({
  icon,
  label,
  onClick,
  disabled,
  className,
}: {
  icon: ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={clsx("icon-btn", className)}
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
    >
      {icon}
    </button>
  );
}

/** Green / yellow / red status lamp from the reference grids. */
export function StatusLamp({ tone }: { tone: "green" | "yellow" | "red" | "grey" }) {
  const colour = {
    green: "#2f8f3a",
    yellow: "#e3b341",
    red: "#c0392b",
    grey: "#9aa8b4",
  }[tone];
  return <span className="status-dot" style={{ background: colour }} />;
}
