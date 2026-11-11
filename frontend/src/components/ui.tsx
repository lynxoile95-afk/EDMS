/** Small, dependency-free UI primitives (RTL-first, classic EDMS look). */

import clsx from "clsx";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export function Button({
  variant = "secondary",
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}) {
  const classes = {
    primary: "btn-primary",
    secondary: "btn-secondary",
    danger: "btn-danger",
    ghost: "btn-ghost",
  }[variant];
  return (
    <button className={clsx(classes, className)} {...props}>
      {children}
    </button>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx("card", className)}>{children}</div>;
}

export function CardHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="card-header">
      <div className="card-header-content">
        <h2 className="card-header-title truncate">{title}</h2>
        {subtitle ? <p className="card-header-subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="card-header-actions">{actions}</div> : null}
    </div>
  );
}

/** Blue section bar used inside forms ("Transmittal Information", …). */
export function SectionBar({ children }: { children: ReactNode }) {
  return <div className="section-bar">{children}</div>;
}

export function Badge({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span className={clsx("badge", className)} title={title}>
      {children}
    </span>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={clsx("input", className)} {...props} />;
}

export function Select({
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select className={clsx("input", className)} {...props}>
      {children}
    </select>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint ? <span className="mt-0.5 block text-[9.5px] text-[#7a8794]">{hint}</span> : null}
    </label>
  );
}

export function Spinner({ label = "در حال بارگذاری…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-8 text-[11px] text-[#4a5a67]">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#b9c9d8] border-t-[#1f4e79]" />
      {label}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state flex flex-col items-center justify-center gap-1.5 px-4 py-10 text-center">
      <div className="empty-state-icon">▤</div>
      <p className="text-[11.5px] font-bold text-[#2b3c4b]">{title}</p>
      {description ? <p className="max-w-md text-[10.5px] leading-5 text-[#5c6b78]">{description}</p> : null}
      {action}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <span className={clsx("skeleton", className)} aria-hidden="true" />;
}

export function Toast({ message, tone = "info" }: { message: string; tone?: "info" | "success" | "error" }) {
  return (
    <div className={clsx("toast", `toast-${tone}`)} role="status" aria-live="polite">
      <span className="toast-dot" />
      {message}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-start justify-between gap-2 border border-[#c98a86] bg-[#fdeeee] px-2 py-1.5 text-[10.5px] leading-5 text-[#8a2b25]">
      <span>{message}</span>
      {onRetry ? (
        <button className="shrink-0 text-[10px] font-bold underline" onClick={onRetry}>
          تلاش دوباره
        </button>
      ) : null}
    </div>
  );
}

export function Modal({
  open,
  title,
  children,
  onClose,
  footer,
  width = "max-w-lg",
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  width?: string;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[#0b1a26]/45 p-4 pt-10">
      <div className={clsx("window is-dialog w-full", width)} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}>
        <div className="window-title">
          <button type="button" className="wt-btn is-close" onClick={onClose} aria-label="بستن">
            ✕
          </button>
          <button type="button" className="wt-btn" aria-hidden disabled>
            ▣
          </button>
          <span className="wt-text">{title}</span>
        </div>
        <div className="window-body max-h-[74vh] overflow-y-auto">{children}</div>
        {footer ? <div className="window-footer">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
  icon?: ReactNode;
}) {
  const tones = {
    default: "#2563EB",
    good: "#0D9488",
    warn: "#F59E0B",
    bad: "#EF4444",
  };
  return (
    <div className={clsx("stat-card", `stat-card-${tone}`)}>
      {icon ? <span className="stat-card-icon">{icon}</span> : null}
      <div className="stat-card-content">
        <p className="stat-card-label">{label}</p>
        <p className="stat-card-value" style={{ color: tones[tone] }}>
          {value}
        </p>
        {hint ? <p className="stat-card-hint">{hint}</p> : null}
      </div>
    </div>
  );
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={clsx("h-2 w-full overflow-hidden border border-[#9aa8b4] bg-[#eef2f6]", className)}>
      <div
        className="h-full bg-gradient-to-b from-[#5a9b5f] to-[#2f6b38] transition-all"
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}
