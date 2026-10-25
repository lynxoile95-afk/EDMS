/**
 * «ثبت و بارگذاری مدرک» — the transmittal registration screen.
 *
 * One row per uploaded PDF. Every field the model reads can be edited before it is
 * written to the register; anything the model was unsure about (confidence below the
 * project threshold, or a reported conflict) is painted yellow and must be confirmed
 * by the operator before the sheet can be saved. The PDF itself is shown beside the
 * grid, first page, exactly as it was received — the file is never altered.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { Registration, RegistrationRow, RegistrationSettings } from "@/lib/types";
import { Badge, Button, EmptyState, ErrorBox, Spinner } from "@/components/ui";
import { SectionBar } from "@/components/ui";
import { PdfViewer } from "@/components/PdfViewer";
import { persianDigits, confidenceClass, PURPOSE_LABELS } from "@/lib/format";

/* ------------------------------------------------------------------ constants */

const MAX_FILES = 20;

/** The four columns the model is never allowed to fill in. */
const STATUS_CHOICES = [
  { value: "", label: "—" },
  { value: "Draft", label: "در دست تهیه (Draft)" },
  { value: "For Review", label: "برای بررسی (For Review)" },
  { value: "For Approval", label: "برای تأیید (For Approval)" },
  { value: "Approved", label: "تأییدشده (Approved)" },
  { value: "Rejected", label: "ردشده (Rejected)" },
  { value: "As-Built", label: "چون‌ساخت (As-Built)" },
  { value: "Superseded", label: "جایگزین‌شده (Superseded)" },
  { value: "Cancelled", label: "ابطال‌شده (Cancelled)" },
];

const CONFIDENTIALITY_CHOICES = [
  { value: "Normal", label: "عادی (Normal)" },
  { value: "Confidential", label: "محرمانه (Confidential)" },
  { value: "Restricted", label: "محدود (Restricted)" },
];

const VALIDATION_LABELS: Record<string, { label: string; tone: string }> = {
  new: { label: "مدرک نو", tone: "bg-[#e8f3e6] text-[#235c22] border-[#9dc493]" },
  supersede: { label: "جایگزین بازنگری", tone: "bg-[#e8eefb] text-[#1f3f8a] border-[#9db4e3]" },
  duplicate: { label: "تکراری", tone: "bg-[#fdf2dc] text-[#8a5a12] border-[#e0bd7c]" },
  blocked: { label: "قابل ثبت نیست", tone: "bg-[#fdeaea] text-[#8a2b25] border-[#dfa29d]" },
};

const COLUMNS: { key: string; fa: string; en: string; width: string; center?: boolean }[] = [
  { key: "row_no", fa: "ردیف", en: "Row", width: "46px", center: true },
  { key: "doc_no", fa: "شماره مدرک", en: "Doc. No.", width: "172px" },
  { key: "title", fa: "عنوان مدرک", en: "Doc. Title", width: "auto" },
  { key: "discipline", fa: "دیسیپلین", en: "Discipline", width: "118px" },
  { key: "version", fa: "بازنگری", en: "Version", width: "74px", center: true },
  { key: "poi", fa: "هدف صدور", en: "P.O.I", width: "92px", center: true },
  { key: "doc_status", fa: "وضعیت مدرک", en: "Doc. Status", width: "132px" },
  { key: "doc_percent", fa: "درصد پیشرفت", en: "Doc. %", width: "74px", center: true },
  { key: "mdl_status", fa: "وضعیت در MDL", en: "MDL Status", width: "112px" },
  { key: "confidentiality", fa: "محرمانگی", en: "Confidentiality", width: "108px" },
];

/* -------------------------------------------------------------- file progress */

interface QueuedFile {
  id: string;
  name: string;
  size: number;
  status: "queued" | "uploading" | "done" | "failed";
  error?: string;
}

/* ------------------------------------------------------------------- helpers */

function messageOf(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function shaOf(row: RegistrationRow): string {
  return row.sha256 ?? "";
}

/* --------------------------------------------------------------------- page */

export function RegistrationPage() {
  const { project, isDcc } = useProject();
  const [sheet, setSheet] = useState<Registration | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyRow, setBusyRow] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, Partial<RegistrationRow>>>({});
  const inputRef = useRef<HTMLInputElement | null>(null);
  const urlRef = useRef<string | null>(null);

  const settings: RegistrationSettings | undefined = sheet?.settings;

  /* ------------------------------------------------------------------ loading */
  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<Registration>(`/projects/${project.id}/registration`);
      setSheet(data);
      setDraft({});
      setSelectedId((current) => {
        if (current && data.rows.some((row) => row.id === current)) return current;
        return data.rows[0]?.id ?? null;
      });
    } catch (err) {
      setError(messageOf(err, "دریافت برگهٔ ثبت مدارک ناموفق بود."));
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = sheet?.rows ?? [];
  const unsaved = rows.filter((row) => !row.saved);
  const pendingReview = unsaved.filter((row) => row.needs_review && !row.review_confirmed);
  const selected = rows.find((row) => row.id === selectedId) ?? null;

  /* --------------------------------------------------------------- preview pdf */
  useEffect(() => {
    let cancelled = false;
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setPreviewUrl(null);
    setPreviewError(null);
    if (!selected?.source_file_id) return;
    api
      .blobUrl(`/files/source-files/${selected.source_file_id}`)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        urlRef.current = url;
        setPreviewUrl(url);
      })
      .catch((err) => !cancelled && setPreviewError(messageOf(err, "نمایش فایل ناموفق بود.")));
    return () => {
      cancelled = true;
    };
  }, [selected?.source_file_id]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  /* -------------------------------------------------------------------- upload */
  const upload = useCallback(
    async (files: File[]) => {
      if (!project || files.length === 0) return;
      const accepted = files.filter((file) => file.name.toLowerCase().endsWith(".pdf")).slice(0, MAX_FILES);
      const rejected = files.length - accepted.length;
      setError(null);
      setNotice(null);
      if (accepted.length === 0) {
        setError("فقط فایل PDF پذیرفته می‌شود.");
        return;
      }
      if (rejected > 0) {
        setNotice(`${persianDigits(rejected)} فایل غیر PDF یا بیش از سقف ${persianDigits(MAX_FILES)} فایل نادیده گرفته شد.`);
      }

      const queued: QueuedFile[] = accepted.map((file) => ({
        id: `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`,
        name: file.name,
        size: file.size,
        status: "queued",
      }));
      setQueue(queued);

      // one request per file, so the progress bar belongs to that file alone
      for (let index = 0; index < accepted.length; index += 1) {
        const file = accepted[index];
        const id = queued[index].id;
        setQueue((current) =>
          current.map((item) => (item.id === id ? { ...item, status: "uploading" } : item)),
        );
        const body = new FormData();
        body.append("files", file);
        try {
          const data = await api.postForm<Registration>(`/projects/${project.id}/registration/extract`, body);
          setSheet(data);
          setDraft({});
          setQueue((current) =>
            current.map((item) => (item.id === id ? { ...item, status: "done" } : item)),
          );
          const newest = data.rows[data.rows.length - 1];
          if (newest) setSelectedId(newest.id);
        } catch (err) {
          setQueue((current) =>
            current.map((item) =>
              item.id === id
                ? { ...item, status: "failed", error: messageOf(err, "استخراج ناموفق بود.") }
                : item,
            ),
          );
        }
      }
      // leave the finished queue visible for a moment, it is the upload report
      setTimeout(() => setQueue((current) => (current.some((item) => item.status === "uploading") ? current : current.filter((item) => item.status === "failed"))), 4000);
    },
    [project],
  );

  const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    void upload(Array.from(event.dataTransfer.files ?? []));
  };

  /* ------------------------------------------------------------------ editing */
  const valueOf = <K extends keyof RegistrationRow>(row: RegistrationRow, key: K): RegistrationRow[K] => {
    const patch = draft[row.id];
    return (patch && key in patch ? (patch[key] as RegistrationRow[K]) : row[key]) ?? row[key];
  };

  const setValue = (row: RegistrationRow, key: keyof RegistrationRow, value: unknown) => {
    setDraft((current) => ({ ...current, [row.id]: { ...(current[row.id] ?? {}), [key]: value } }));
  };

  const patchRow = useCallback(
    async (row: RegistrationRow, patch: Record<string, unknown>) => {
      if (!project) return;
      setBusyRow(row.id);
      setError(null);
      try {
        const updated = await api.patch<RegistrationRow>(
          `/projects/${project.id}/registration/rows/${row.id}`,
          patch,
        );
        setSheet((current) =>
          current ? { ...current, rows: current.rows.map((item) => (item.id === updated.id ? updated : item)) } : current,
        );
        setDraft((current) => {
          const next = { ...current };
          delete next[row.id];
          return next;
        });
      } catch (err) {
        setError(messageOf(err, "ذخیرهٔ ویرایش این ردیف ناموفق بود."));
        await load();
      } finally {
        setBusyRow(null);
      }
    },
    [project, load],
  );

  const commitField = (row: RegistrationRow, key: string) => {
    const patch = draft[row.id];
    if (!patch || !(key in patch)) return;
    const value = patch[key as keyof RegistrationRow];
    void patchRow(row, { [key]: value === "" ? null : value });
  };

  const confirmRow = async (row: RegistrationRow) => {
    if (!project) return;
    setBusyRow(row.id);
    setError(null);
    try {
      const updated = await api.post<RegistrationRow>(
        `/projects/${project.id}/registration/rows/${row.id}/confirm`,
        { confirmed: true },
      );
      setSheet((current) =>
        current ? { ...current, rows: current.rows.map((item) => (item.id === updated.id ? updated : item)) } : current,
      );
    } catch (err) {
      setError(messageOf(err, "تأیید ردیف ناموفق بود."));
    } finally {
      setBusyRow(null);
    }
  };

  const reextractRow = async (row: RegistrationRow) => {
    if (!project) return;
    setBusyRow(row.id);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.post<RegistrationRow>(
        `/projects/${project.id}/registration/rows/${row.id}/re-extract`,
      );
      setSheet((current) =>
        current ? { ...current, rows: current.rows.map((item) => (item.id === updated.id ? updated : item)) } : current,
      );
      setDraft((current) => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
    } catch (err) {
      setError(messageOf(err, "استخراج مجدد ناموفق بود."));
    } finally {
      setBusyRow(null);
    }
  };

  const removeRow = async (row: RegistrationRow) => {
    if (!project) return;
    setBusyRow(row.id);
    setError(null);
    try {
      await api.delete(`/projects/${project.id}/registration/rows/${row.id}`);
      setSheet((current) =>
        current ? { ...current, rows: current.rows.filter((item) => item.id !== row.id) } : current,
      );
      setSelectedId((current) => (current === row.id ? null : current));
    } catch (err) {
      setError(messageOf(err, "حذف ردیف ناموفق بود."));
    } finally {
      setBusyRow(null);
    }
  };

  const save = async () => {
    if (!project) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.post<{ count: number; saved: { doc_no: string }[] }>(
        `/projects/${project.id}/registration/save`,
        {},
      );
      setNotice(
        `${persianDigits(result.count)} مدرک در دفتر مدارک ثبت شد: ` +
          result.saved.map((item) => item.doc_no).join("، "),
      );
      await load();
    } catch (err) {
      setError(messageOf(err, "ثبت برگه ناموفق بود."));
    } finally {
      setSaving(false);
    }
  };

  const discardAll = async () => {
    if (!project) return;
    setSaving(true);
    setError(null);
    try {
      await api.post(`/projects/${project.id}/registration/discard`, {});
      setNotice("پیش‌نویس برگه پاک شد.");
      await load();
    } catch (err) {
      setError(messageOf(err, "پاک کردن پیش‌نویس ناموفق بود."));
    } finally {
      setSaving(false);
    }
  };

  const disciplines = useMemo(
    () => Object.entries(settings?.discipline_labels ?? {}),
    [settings?.discipline_labels],
  );

  const canWrite = isDcc;

  /* -------------------------------------------------------------------- render */
  return (
    <div className="window">
      <div className="window-title">
        <span className="window-title-text">Transmittal Registration — ثبت و بارگذاری مدرک</span>
        <span className="ml-auto flex items-center gap-1">
          <Badge className="bg-white/70">برگه {sheet?.number ?? "—"}</Badge>
          <Badge className="bg-white/70">{persianDigits(unsaved.length)} ردیف ویرایش‌نشده</Badge>
        </span>
      </div>

      <div className="p-1.5">
        {notice ? (
          <div className="mb-1.5 border border-[#9dc493] bg-[#eef7ec] px-2 py-1 text-[10.5px] leading-5 text-[#235c22]">
            {notice}
          </div>
        ) : null}
        {error ? (
          <div className="mb-1.5">
            <ErrorBox message={error} onRetry={() => void load()} />
          </div>
        ) : null}

        {/* ------------------------------------------------------- drop area */}
        <div
          className={clsx("reg-drop", dragging && "is-over")}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            className="hidden"
            onChange={(event) => {
              void upload(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
          <div className="text-[13px] font-bold text-[#17427a]">
            فایل‌های مهندسی را اینجا رها کنید یا برای انتخاب کلیک کنید
          </div>
          <div className="mt-0.5 text-[10.5px] text-[#4a5a67]">
            PDF — فارسی یا انگلیسی، حداکثر {persianDigits(MAX_FILES)} فایل در هر بار. عنوان‌بلوک صفحهٔ ۱ خوانده
            می‌شود؛ اگر مدرکی نداشت، صفحهٔ ۲ دوباره بررسی می‌شود. فایل اصلی دست‌نخورده بایگانی می‌شود.
          </div>
          {!canWrite ? (
            <div className="mt-1 text-[10.5px] font-bold text-[#8a5a12]">
              فقط کارشناس کنترل مدارک (DCC) و مدیر پروژه می‌توانند این برگه را ثبت کنند؛ شما فقط می‌بینید.
            </div>
          ) : null}
        </div>

        {queue.length > 0 ? (
          <div className="mt-1.5 space-y-1">
            {queue.map((item) => (
              <div key={item.id} className="reg-progress">
                <span className="w-52 truncate text-[10.5px]" title={item.name}>
                  {item.name}
                </span>
                <span className="reg-progress-track">
                  <span
                    className={clsx(
                      "reg-progress-bar",
                      item.status === "failed" && "is-failed",
                      item.status === "done" && "is-done",
                    )}
                    style={{ width: item.status === "queued" ? "4%" : item.status === "uploading" ? "65%" : "100%" }}
                  />
                </span>
                <span className="w-40 shrink-0 text-[10px] text-[#4a5a67]">
                  {item.status === "queued" ? "در صف" : null}
                  {item.status === "uploading" ? "در حال خواندن عنوان‌بلوک…" : null}
                  {item.status === "done" ? "استخراج شد" : null}
                  {item.status === "failed" ? item.error : null}
                </span>
              </div>
            ))}
          </div>
        ) : null}

        {/* --------------------------------------------------------- toolbar */}
        <div className="mt-1.5 flex flex-wrap items-center gap-1 border border-[#b9c3cd] bg-[#eef2f6] px-1.5 py-1">
          <Button variant="primary" className="btn-sm" disabled={!canWrite || saving || unsaved.length === 0} onClick={() => void save()}>
            ذخیرهٔ برگه (Save Transmittal)
          </Button>
          <Button
            className="btn-sm"
            disabled={!selected || !canWrite || busyRow === selected.id || selected.saved}
            onClick={() => selected && void reextractRow(selected)}
          >
            استخراج مجدد (Re-extract)
          </Button>
          <Button
            variant="secondary"
            className="btn-sm"
            disabled={!selected || !canWrite || busyRow === selected?.id || selected?.saved}
            onClick={() => selected && void removeRow(selected)}
          >
            حذف ردیف (Remove Row)
          </Button>
          <Button
            variant="ghost"
            className="btn-sm"
            disabled={!canWrite || saving || unsaved.length === 0}
            onClick={() => void discardAll()}
          >
            انصراف و پاک کردن پیش‌نویس
          </Button>
          <span className="ml-auto flex items-center gap-1 text-[10.5px]">
            {pendingReview.length > 0 ? (
              <Badge className="border-[#e0bd7c] bg-[#fdf2dc] text-[#8a5a12]">
                {persianDigits(pendingReview.length)} ردیف نیازمند بررسی
              </Badge>
            ) : (
              <Badge className="border-[#9dc493] bg-[#e8f3e6] text-[#235c22]">همهٔ ردیف‌ها بررسی شد</Badge>
            )}
            <Badge>آستانهٔ اطمینان {persianDigits(Math.round((settings?.confidence_threshold ?? 0.8) * 100))}٪</Badge>
            <Button variant="ghost" className="btn-sm" onClick={() => void load()}>
              بازخوانی
            </Button>
          </span>
        </div>

        {/* ------------------------------------------------------- grid + pdf */}
        {loading ? (
          <Spinner label="در حال خواندن برگهٔ ثبت مدارک…" />
        ) : (
          <div className="mt-1.5 grid gap-1.5 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="min-w-0 overflow-x-auto border border-[#b9c3cd]">
              {rows.length === 0 ? (
                <EmptyState
                  title="هنوز مدرکی بارگذاری نشده است"
                  description="PDF‌ها را در کادر بالا رها کنید؛ برای هر فایل یک ردیف ساخته می‌شود و می‌توانید پیش از ذخیره همهٔ فیلدها را اصلاح کنید."
                />
              ) : (
                <table className="table-base reg-table">
                  <thead>
                    <tr>
                      <th className="cell-center w-6" />
                      {COLUMNS.map((column) => (
                        <th key={column.key} style={{ width: column.width }} className={clsx(column.center && "cell-center")}>
                          <span className="block leading-3">{column.fa}</span>
                          <span className="block text-[8px] font-normal opacity-75" dir="ltr">
                            {column.en}
                          </span>
                        </th>
                      ))}
                      <th style={{ width: "150px" }}>اقدام‌ها / Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const lowConfidence = Object.entries(row.confidence ?? {})
                        .filter(([, value]) => typeof value === "number" && value < (settings?.confidence_threshold ?? 0.8))
                        .map(([key]) => key);
                      const needsReview = row.needs_review && !row.review_confirmed;
                      const validation = VALIDATION_LABELS[row.validation_state] ?? VALIDATION_LABELS.new;
                      const tagFor = (field: string): string | null => {
                        const value = row.confidence?.[field];
                        if (typeof value === "number") return `${Math.round(value * 100)}٪`;
                        return null;
                      };
                      const cellClass = (field: string) =>
                        clsx(
                          "reg-cell",
                          lowConfidence.includes(field) || (row.conflicts?.length ?? 0) > 0
                            ? needsReview
                              ? "is-review"
                              : "is-reviewed"
                            : null,
                        );
                      const title = (row.validation_messages ?? []).concat(row.warnings ?? []).join(" • ");

                      return (
                        <tr
                          key={row.id}
                          className={clsx(row.id === selectedId && "is-selected", row.saved && "opacity-70")}
                          onClick={() => setSelectedId(row.id)}
                        >
                          <td className="cell-center">
                            <input
                              type="radio"
                              name="reg-row"
                              checked={row.id === selectedId}
                              onChange={() => setSelectedId(row.id)}
                              aria-label={`ردیف ${row.row_no}`}
                            />
                          </td>
                          <td className="cell-center">{persianDigits(row.row_no)}</td>

                          <td className={cellClass("doc_number")}>
                            <div className="flex items-center gap-1">
                              <input
                                className="reg-input font-mono"
                                dir="ltr"
                                disabled={row.saved}
                                value={(valueOf(row, "doc_no") as string) ?? ""}
                                onChange={(event) => setValue(row, "doc_no", event.target.value)}
                                onBlur={() => commitField(row, "doc_no")}
                              />
                              {tagFor("doc_number") ? (
                                <span className={clsx("reg-conf", confidenceClass(row.confidence.doc_number))}>
                                  {persianDigits(tagFor("doc_number") as string)}
                                </span>
                              ) : null}
                            </div>
                          </td>

                          <td className={cellClass("title")}>
                            <div className="flex items-start gap-1">
                              <textarea
                                className="reg-input min-h-[20px] resize-y"
                                dir="auto"
                                rows={1}
                                disabled={row.saved}
                                value={(valueOf(row, "title") as string) ?? ""}
                                onChange={(event) => setValue(row, "title", event.target.value)}
                                onBlur={() => commitField(row, "title")}
                                title={row.filename}
                              />
                              {tagFor("title") ? (
                                <span className={clsx("reg-conf", confidenceClass(row.confidence.title))}>
                                  {persianDigits(tagFor("title") as string)}
                                </span>
                              ) : null}
                            </div>
                            {(row.conflicts?.length ?? 0) > 0 ? (
                              <span
                                className="mt-0.5 block cursor-help text-[9px] font-bold text-[#8a5a12]"
                                title={row.conflicts.join("\n")}
                              >
                                ⚠ {persianDigits(row.conflicts.length)} مغایرت — نشانگر را روی این خط نگه دارید
                              </span>
                            ) : null}
                          </td>

                          <td>
                            <select
                              className="reg-input"
                              disabled={row.saved}
                              value={(valueOf(row, "discipline_code") as string) ?? ""}
                              onChange={(event) => {
                                const code = event.target.value;
                                setValue(row, "discipline_code", code);
                                void patchRow(row, { discipline_code: code || null });
                              }}
                            >
                              <option value="">—</option>
                              {disciplines.map(([code, label]) => (
                                <option key={code} value={code}>
                                  {code} — {label}
                                </option>
                              ))}
                              {row.discipline_code && !disciplines.some(([code]) => code === row.discipline_code) ? (
                                <option value={row.discipline_code}>{row.discipline_code}</option>
                              ) : null}
                            </select>
                          </td>

                          <td className={clsx("cell-center", cellClass("revision"))}>
                            <div className="flex items-center justify-center gap-1">
                              <input
                                className="reg-input w-12 text-center font-mono"
                                dir="ltr"
                                disabled={row.saved}
                                value={(valueOf(row, "version") as string) ?? ""}
                                onChange={(event) => setValue(row, "version", event.target.value.toUpperCase())}
                                onBlur={() => commitField(row, "version")}
                              />
                              {tagFor("revision") ? (
                                <span className={clsx("reg-conf", confidenceClass(row.confidence.revision))}>
                                  {persianDigits(tagFor("revision") as string)}
                                </span>
                              ) : null}
                            </div>
                          </td>

                          <td className={clsx("cell-center", cellClass("purpose_of_issue"))}>
                            <input
                              className="reg-input w-16 text-center font-mono"
                              dir="ltr"
                              disabled={row.saved}
                              title={row.poi ? PURPOSE_LABELS[row.poi] ?? row.poi : ""}
                              value={(valueOf(row, "poi") as string) ?? ""}
                              onChange={(event) => setValue(row, "poi", event.target.value.toUpperCase())}
                              onBlur={() => commitField(row, "poi")}
                            />
                          </td>

                          <td>
                            <select
                              className="reg-input"
                              disabled={row.saved}
                              value={(valueOf(row, "doc_status") as string) ?? ""}
                              onChange={(event) => {
                                const value = event.target.value;
                                setValue(row, "doc_status", value);
                                void patchRow(row, { doc_status: value || null });
                              }}
                            >
                              {STATUS_CHOICES.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </td>

                          <td className="cell-center">
                            <input
                              type="number"
                              min={0}
                              max={100}
                              className="reg-input w-14 text-center font-mono"
                              dir="ltr"
                              disabled={row.saved}
                              value={Number(valueOf(row, "doc_percent") ?? 0)}
                              onChange={(event) => setValue(row, "doc_percent", Number(event.target.value))}
                              onBlur={() => commitField(row, "doc_percent")}
                            />
                          </td>

                          <td className="text-[10px]">
                            <span className="font-mono" dir="ltr">
                              {row.mdl_status ?? "—"}
                            </span>
                            <span
                              className={clsx("mt-0.5 block border px-1 text-[9px] font-bold", validation.tone)}
                              title={title}
                            >
                              {validation.label}
                            </span>
                          </td>

                          <td>
                            <select
                              className="reg-input"
                              disabled={row.saved}
                              value={(valueOf(row, "confidentiality") as string) ?? "Normal"}
                              onChange={(event) => {
                                const value = event.target.value;
                                setValue(row, "confidentiality", value);
                                void patchRow(row, { confidentiality: value });
                              }}
                            >
                              {CONFIDENTIALITY_CHOICES.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </td>

                          <td>
                            <div className="row-actions flex-wrap">
                              {needsReview ? (
                                <Button
                                  variant="primary"
                                  className="btn-sm"
                                  disabled={!canWrite || row.validation_state === "blocked" || busyRow === row.id}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    void confirmRow(row);
                                  }}
                                >
                                  تأیید و رفع نیاز به بررسی
                                </Button>
                              ) : row.review_confirmed ? (
                                <Badge className="border-[#9dc493] bg-[#e8f3e6] text-[#235c22]">تأییدشده</Badge>
                              ) : null}
                              <Button
                                variant="ghost"
                                className="btn-sm"
                                title={title}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setSelectedId(row.id);
                                }}
                              >
                                نمایش فایل
                              </Button>
                              {row.saved ? <Badge className="bg-[#e8eefb] text-[#1f3f8a]">ثبت‌شده</Badge> : null}
                              {busyRow === row.id ? <Spinner label="" /> : null}
                            </div>
                            {row.extraction_status === "failed" || row.extraction_status === "manual" ? (
                              <span className="mt-0.5 block text-[9px] text-[#8a2b25]" title={row.extraction_error ?? ""}>
                                {row.extraction_status === "manual"
                                  ? "خوانده نشد — دستی وارد کنید"
                                  : "استخراج ناموفق — دستی وارد کنید"}
                              </span>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>

            {/* ------------------------------------------------- side preview */}
            <div className="min-w-0 border border-[#b9c3cd] bg-[#f7f9fb]">
              <SectionBar>پیش‌نمایش مدرک / Document Preview</SectionBar>
              {!selected ? (
                <div className="px-2 py-6 text-center text-[10.5px] text-[#5c6b78]">
                  برای دیدن فایل، یک ردیف را انتخاب کنید.
                </div>
              ) : (
                <div className="p-1.5">
                  <div className="mb-1 space-y-0.5 text-[10px] text-[#33454f]">
                    <div className="truncate font-bold" title={selected.filename}>
                      {selected.filename}
                    </div>
                    <div dir="ltr" className="truncate font-mono text-[9.5px]">
                      {shaOf(selected).slice(0, 16) || "—"} · {persianDigits(selected.page_count ?? 0)} صفحه
                    </div>
                    {selected.extraction_model ? (
                      <div className="text-[9px] text-[#5c6b78]">
                        مدل: <span dir="ltr">{selected.extraction_model}</span> · تلاش{" "}
                        {persianDigits(selected.extraction_attempts ?? 0)} · صفحهٔ{" "}
                        {persianDigits((selected.extracted_pages ?? []).join("، ") || 0)}
                      </div>
                    ) : null}
                  </div>
                  {previewError ? (
                    <ErrorBox message={previewError} />
                  ) : previewUrl ? (
                    <div className="reg-preview">
                      <PdfViewer url={previewUrl} page={1} />
                    </div>
                  ) : (
                    <Spinner label="در حال آماده‌سازی پیش‌نمایش…" />
                  )}
                  {(selected.validation_messages ?? []).length > 0 ? (
                    <ul className="mt-1 list-disc space-y-0.5 pr-4 text-[10px] leading-4 text-[#33454f]">
                      {(selected.validation_messages ?? []).map((message) => (
                        <li key={message}>{message}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      <div className="strip-green" />
    </div>
  );
}
