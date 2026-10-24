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
      <div className="min-w-0">
        <h2 className="truncate">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-[10px] font-normal text-[#5c6b78]">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-1">{actions}</div> : null}
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
    <div className="flex flex-col items-center justify-center gap-1.5 px-4 py-10 text-center">
      <div className="grid h-8 w-8 place-items-center border border-[#b8c4cf] bg-[#eef3f8] text-[#456]">▤</div>
      <p className="text-[11.5px] font-bold text-[#2b3c4b]">{title}</p>
      {description ? <p className="max-w-md text-[10.5px] leading-5 text-[#5c6b78]">{description}</p> : null}
      {action}
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
      <div className={clsx("win-window", width)} role="dialog" aria-modal="true" aria-label={title}>
        <div className="win-titlebar">
          <span className="truncate">{title}</span>
          <button onClick={onClose} aria-label="بستن">
            ✕
          </button>
        </div>
        <div className="win-body max-h-[74vh] overflow-y-auto">{children}</div>
        {footer ? <div className="win-footer">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const tones = {
    default: "#1f3864",
    good: "#1e6b34",
    warn: "#8a5a10",
    bad: "#97282b",
  };
  return (
    <div className="border border-[#9aa8b4] bg-white px-2 py-1.5">
      <p className="text-[10px] text-[#4a5a67]">{label}</p>
      <p className="mt-0.5 text-[15px] font-bold" style={{ color: tones[tone] }}>
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-[9px] text-[#7a8794]">{hint}</p> : null}
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
