/** Display helpers: Jalali dates, Persian digits, confidence/decision styling. */

const JALALI = new Intl.DateTimeFormat("fa-IR-u-ca-persian-nu-latn", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const JALALI_LONG = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
  year: "numeric",
  month: "long",
  day: "numeric",
});

const JALALI_DATETIME = new Intl.DateTimeFormat("fa-IR-u-ca-persian-nu-latn", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

/** Dates are stored UTC; display in the user's local timezone (Asia/Tehran). */
export function jalali(value?: string | Date | null): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return JALALI.format(date);
}

export function jalaliLong(value?: string | Date | null): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return JALALI_LONG.format(date);
}

export function jalaliDateTime(value?: string | Date | null): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return JALALI_DATETIME.format(date);
}

export function persianDigits(input: string | number): string {
  return String(input).replace(/\d/g, (digit) => "۰۱۲۳۴۵۶۷۸۹"[Number(digit)]);
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function percent(value: number): string {
  return persianDigits(Math.round(value * 100)) + "٪";
}

export const DECISION_STYLES: Record<string, { label: string; className: string }> = {
  GREEN: { label: "سبز — پذیرش خودکار", className: "bg-emerald-100 text-emerald-800" },
  YELLOW: { label: "زرد — نیازمند بازبینی", className: "bg-amber-100 text-amber-900" },
  RED: { label: "قرمز — رد/قرنطینه", className: "bg-rose-100 text-rose-800" },
  PENDING: { label: "در انتظار", className: "bg-slate-100 text-slate-700" },
};

/**
 * Comment disposition, in the customer's own vocabulary (بصری مشاهده‌شده در سامانه مرجع).
 * kept as the English screen value + a Persian label, like every other code/label pair.
 */
export const DISPOSITION_LABELS: Record<string, string> = {
  accepted: "پذیرفته (Accepted)",
  canceled: "ملغی (Canceled)",
  commented: "اصلاح با نظر (Commented)",
  no_comment: "بدون نظر (No Comment)",
  rejected: "رد‌شده (Rejected)",
};

export const STATUS_LABELS: Record<string, string> = {
  draft: "پیش‌نویس",
  pending_approval: "در انتظار تأیید",
  issued: "صادرشده",
  partially_acknowledged: "تأیید جزئی",
  acknowledged: "تأییدشده",
  closed: "بسته",
  cancelled: "ابطال‌شده",
  pending: "در انتظار",
  approved: "تأییدشده",
  corrected: "اصلاح‌شده",
  rejected: "رد‌شده",
  open: "باز",
  answered: "پاسخ داده‌شده",
  uploaded: "بارگذاری‌شده",
  processed: "پردازش‌شده",
  processing: "در حال پردازش",
  failed: "ناموفق",
  quarantined: "قرنطینه",
  completed: "تکمیل‌شده",
  cancelled_session: "لغو‌شده",
};

export const PURPOSE_LABELS: Record<string, string> = {
  IFR: "ارسال برای بررسی (IFR)",
  IFA: "ارسال برای تأیید (IFA)",
  IFC: "ارسال برای اجرا (IFC)",
  IFE: "ارسال برای مهندسی (IFE)",
  IFP: "ارسال برای خرید (IFP)",
  IPC: "ارسال برای تدارکات (IPC)",
  IFI: "ارسال برای اطلاع (IFI)",
  RTC: "پاسخ به نظرات (RTC)",
  ASB: "چون‌ساخت (As-Built)",
};

/** Referral action as printed by the reference system. */
export const REFERRAL_LABELS: Record<string, string> = {
  review: "For Follow Up",
  approve: "For Follow Up",
  info: "For Information",
  for_construction: "For Information",
};

export const ACTION_LABELS: Record<string, string> = {
  review: "بررسی",
  approve: "تأیید",
  info: "اطلاع",
  for_construction: "اجرا",
};

export const RULE_LABELS: Record<string, string> = {
  R01: "کد مدرک یافت نشد",
  R02: "کد مدرک با الگوی پروژه سازگار نیست",
  R03: "کد مدرک در MDL موجود نیست",
  R04: "تطبیق نزدیک با MDL",
  R05: "شماره بازنگری نامعتبر",
  R06: "بازنگری تکراری",
  R07: "بازنگری قدیمی‌تر از جاری",
  R08: "پرش در بازنگری",
  R09: "اختلاف عنوان با MDL",
  R10: "اختلاف دیسیپلین",
  R11: "اختلاف یونیت",
  R12: "وضعیت مدرک اجازه ارسال نمی‌دهد",
  R13: "نام فایل با کد نقشه نمی‌خواند",
  R14: "فایل تکراری",
  R15: "اطمینان پایین استخراج",
  R16: "استفاده از OCR",
  R17: "استفاده از مدل بینایی",
  R18: "کد تکراری در یک جلسه",
  R19: "اختلاف تعداد شیت با MDL",
  R20: "کادر مشخصات یافت نشد",
  R22: "خطا در استخراج",
};

export function confidenceClass(value: number): string {
  if (value >= 0.85) return "bg-emerald-100 text-emerald-800";
  if (value >= 0.6) return "bg-amber-100 text-amber-900";
  if (value > 0) return "bg-rose-100 text-rose-800";
  return "bg-slate-100 text-slate-500";
}
