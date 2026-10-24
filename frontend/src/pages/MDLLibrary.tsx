import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { MDLImportReport, MDLItem, MDLPreview } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Modal, Select, Spinner } from "@/components/ui";
import { jalali, persianDigits } from "@/lib/format";

const FIELD_LABELS: Record<string, string> = {
  doc_code: "کد مدرک *",
  title: "عنوان *",
  discipline: "دیسیپلین",
  unit: "یونیت",
  doc_type: "نوع مدرک",
  current_rev: "بازنگری جاری",
  status: "وضعیت",
  responsible: "مسئول",
  due_date: "تاریخ موعد",
};

const PAGE_SIZE = 25;

export function MDLLibrary() {
  const { project, isDcc } = useProject();
  const [items, setItems] = useState<MDLItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [wizardOpen, setWizardOpen] = useState(false);
  const [preview, setPreview] = useState<MDLPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, number>>({});
  const [sheetName, setSheetName] = useState<string | null>(null);
  const [report, setReport] = useState<MDLImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [wizardError, setWizardError] = useState<string | null>(null);
  const fileRef = useRef<File | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) });
      if (query.trim()) params.set("q", query.trim());
      const data = await api.get<{ items: MDLItem[]; total: number }>(
        `/projects/${project.id}/mdl/items?${params.toString()}`,
      );
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری فهرست مدارک ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [project, page, query]);

  useEffect(() => {
    void load();
  }, [load]);

  const pickFile = async (file: File) => {
    if (!project) return;
    fileRef.current = file;
    setBusy(true);
    setWizardError(null);
    setReport(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const data = await api.postForm<MDLPreview>(`/projects/${project.id}/mdl/preview`, form);
      setPreview(data);
      setMapping(data.suggested_mapping);
      setSheetName(data.active_sheet);
    } catch (err) {
      setWizardError(err instanceof ApiError ? err.detail : "خواندن فایل اکسل ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const runImport = async (dryRun = false) => {
    if (!project || !fileRef.current) return;
    setBusy(true);
    setWizardError(null);
    try {
      const form = new FormData();
      form.append("file", fileRef.current);
      form.append(
        "config",
        JSON.stringify({
          sheet_name: sheetName,
          header_row: preview ? preview.header_row - 1 : null,
          mapping,
          auto_create_lookups: true,
          dry_run: dryRun,
        }),
      );
      const result = await api.postForm<MDLImportReport>(`/projects/${project.id}/mdl/import`, form);
      setReport(result);
      if (result.status === "completed") void load();
    } catch (err) {
      setWizardError(err instanceof ApiError ? err.detail : "ایمپورت ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const closeWizard = () => {
    setWizardOpen(false);
    setPreview(null);
    setReport(null);
    setWizardError(null);
    fileRef.current = null;
    if (inputRef.current) inputRef.current.value = "";
  };

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <Card className="is-main">
        <CardHeader
          title="فهرست مدارک پروژه (MDL)"
          subtitle={`${persianDigits(total)} ردیف — مرجع رسمی تطبیق مدارک`}
          actions={
            <>
              <Button variant="secondary" onClick={() => api.download(`/projects/${project.id}/mdl/export`, `MDL-${project.code}.xlsx`)}>
                خروجی اکسل
              </Button>
              {isDcc ? <Button onClick={() => setWizardOpen(true)}>بارگذاری MDL</Button> : null}
            </>
          }
        />
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2">
          <Input
            className="max-w-xs"
            placeholder="جست‌وجو در کد یا عنوان…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
          />
          <Button variant="ghost" onClick={() => void load()}>
            بازخوانی
          </Button>
        </div>

        {loading ? (
          <Spinner />
        ) : error ? (
          <div className="p-4">
            <ErrorBox message={error} onRetry={() => void load()} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title="هیچ مدرکی در MDL ثبت نشده است"
            description="برای شروع، فایل اکسل MDL پروژه را با نگاشت ستون‌ها بارگذاری کنید."
            action={isDcc ? <Button className="mt-3" onClick={() => setWizardOpen(true)}>بارگذاری MDL</Button> : undefined}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>کد مدرک</th>
                  <th>عنوان</th>
                  <th>دیسیپلین</th>
                  <th>یونیت</th>
                  <th>بازنگری</th>
                  <th>وضعیت</th>
                  <th>بازنگری‌های ثبت‌شده</th>
                  <th>موعد</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="ltr font-mono text-xs font-medium text-slate-800">{item.doc_code}</td>
                    <td className="max-w-[22rem] truncate text-slate-700" title={item.title}>
                      {item.title}
                    </td>
                    <td>{item.discipline_code ?? "—"}</td>
                    <td>{item.unit_code ?? "—"}</td>
                    <td>
                      <Badge className="bg-brand-50 text-brand-700">{item.current_rev ?? "—"}</Badge>
                    </td>
                    <td className="text-xs text-slate-600">{item.status ?? "—"}</td>
                    <td>
                      {item.revision_count > 0 ? (
                        <Badge className="bg-emerald-50 text-emerald-700">{persianDigits(item.revision_count)}</Badge>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>
                    <td className="text-xs text-slate-600">{item.due_date ? jalali(item.due_date) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 ? (
          <div className="flex items-center justify-between px-4 py-3 text-xs text-slate-500">
            <span>
              صفحه {persianDigits(page)} از {persianDigits(pages)}
            </span>
            <div className="flex gap-1">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                قبلی
              </Button>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>
                بعدی
              </Button>
            </div>
          </div>
        ) : null}
      </Card>

      <Modal
        open={wizardOpen}
        title="بارگذاری فهرست مدارک (MDL)"
        onClose={closeWizard}
        width="max-w-3xl"
        footer={
          <>
            <Button variant="secondary" onClick={closeWizard}>
              بستن
            </Button>
            <Button variant="secondary" disabled={!preview || busy} onClick={() => void runImport(true)}>
              بررسی بدون ثبت
            </Button>
            <Button disabled={!preview || busy} onClick={() => void runImport(false)}>
              {busy ? "در حال پردازش…" : "ایمپورت نهایی"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-dashed border-slate-300 p-4 text-center">
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.xlsm"
              className="mx-auto block text-xs"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void pickFile(file);
              }}
            />
            <p className="mt-2 text-[11px] text-slate-400">
              فایل اکسل MDL پروژه (.xlsx). پس از خواندن، ستون‌ها به‌صورت خودکار پیشنهاد می‌شوند.
            </p>
          </div>

          {wizardError ? <ErrorBox message={wizardError} /> : null}

          {preview ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="شیت">
                  <Select value={sheetName ?? ""} onChange={(event) => setSheetName(event.target.value)}>
                    {preview.sheets.map((sheet) => (
                      <option key={sheet} value={sheet}>
                        {sheet}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="ردیف سرستون" hint="به‌صورت خودکار تشخیص داده شد">
                  <Input value={preview.header_row} readOnly disabled dir="ltr" />
                </Field>
              </div>

              <div>
                <p className="mb-2 text-xs font-semibold text-slate-700">نگاشت ستون‌ها</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {Object.entries(FIELD_LABELS).map(([field, label]) => (
                    <label key={field} className="flex items-center gap-2 text-xs">
                      <span className="w-28 shrink-0 text-slate-600">{label}</span>
                      <select
                        className="input py-1 text-xs"
                        value={mapping[field] ?? -1}
                        onChange={(event) => {
                          const value = Number(event.target.value);
                          setMapping((current) => {
                            const next = { ...current };
                            if (value < 0) delete next[field];
                            else next[field] = value;
                            return next;
                          });
                        }}
                      >
                        <option value={-1}>— انتخاب نشده —</option>
                        {preview.headers.map((header, index) => (
                          <option key={`${header}-${index}`} value={index}>
                            {header || `ستون ${index + 1}`}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              </div>

              {preview.sample_rows.length > 0 ? (
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="table-base text-xs">
                    <thead>
                      <tr>
                        {preview.headers.map((header, index) => (
                          <th key={`${header}-${index}`}>{header || `#${index + 1}`}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.sample_rows.map((row, rowIndex) => (
                        <tr key={rowIndex}>
                          {preview.headers.map((_, cellIndex) => (
                            <td key={cellIndex} className="whitespace-nowrap">
                              {String(row[cellIndex] ?? "—")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : null}

          {report ? (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge
                  className={
                    report.status === "completed"
                      ? "bg-emerald-100 text-emerald-800"
                      : report.status === "validated"
                        ? "bg-sky-100 text-sky-800"
                        : "bg-rose-100 text-rose-800"
                  }
                >
                  وضعیت: {report.status === "completed" ? "ثبت شد" : report.status === "validated" ? "اعتبارسنجی شد" : "رد شد"}
                </Badge>
                <Badge className="bg-slate-100">ردیف‌ها: {persianDigits(report.total_rows)}</Badge>
                <Badge className="bg-emerald-50 text-emerald-700">جدید: {persianDigits(report.imported)}</Badge>
                <Badge className="bg-sky-50 text-sky-700">به‌روزرسانی: {persianDigits(report.updated)}</Badge>
                <Badge className="bg-rose-50 text-rose-700">خطا: {persianDigits(report.errors)}</Badge>
                <Badge className="bg-amber-50 text-amber-800">هشدار: {persianDigits(report.warnings)}</Badge>
              </div>
              {report.entries.length > 0 ? (
                <div className="max-h-56 overflow-y-auto rounded-lg border border-slate-200">
                  <table className="table-base text-xs">
                    <thead>
                      <tr>
                        <th>ردیف</th>
                        <th>شدت</th>
                        <th>پیام</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.entries.map((entry, index) => (
                        <tr key={index}>
                          <td>{persianDigits(entry.row)}</td>
                          <td>
                            <Badge
                              className={
                                entry.severity === "error"
                                  ? "bg-rose-100 text-rose-800"
                                  : "bg-amber-100 text-amber-900"
                              }
                            >
                              {entry.severity === "error" ? "خطا" : "هشدار"}
                            </Badge>
                          </td>
                          <td>{entry.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </Modal>
    </div>
  );
}
