import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { MDLImportReport, MDLItem, MDLPreview } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Modal, Select, Spinner } from "@/components/ui";
import { jalali, persianDigits } from "@/lib/format";


interface CodeSegment {
  key: string;
  label_fa: string;
  label_en: string;
  regex: string;
  example: string;
  note_fa: string;
}

interface CodeParse {
  code: string;
  ok: boolean;
  error: string | null;
  segments: Record<string, string>;
  fields: {
    unit_code?: string | null;
    doc_type?: string | null;
    discipline_code?: string | null;
    phase?: string | null;
    serial_no?: string | null;
  };
  labels: { discipline?: string | null; doc_type?: string | null; phase?: string | null };
  warnings: string[];
  resolved: {
    discipline_code: string | null;
    discipline_exists: boolean;
    discipline_is_new: boolean;
    unit_exists: boolean;
  };
  duplicate: boolean;
}

interface CodeStructure {
  pattern_label: string;
  example: string;
  short_pattern_label: string;
  short_example: string;
  segments: CodeSegment[];
  disciplines: { code: string; name_fa: string; name_en: string }[];
  doc_types: { code: string; name_fa: string }[];
  doc_classes: string[];
  basic_detail_values: string[];
  serial_blocks: { discipline: string; name_fa: string; from: number; to: number }[];
  known_disciplines?: { id: string; code: string; name: string }[];
}
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

  // manual row: the second door into the MDL, beside the Excel import
  const [rowOpen, setRowOpen] = useState(false);
  const [editing, setEditing] = useState<MDLItem | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  // the coding rule this project validates against
  const [structure, setStructure] = useState<CodeStructure | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  // The manual row asks only for what the operator actually knows: the code, the
  // title, the P.O.I and the weight. Unit, document type, discipline, phase and
  // serial are read back out of the code itself by GET .../mdl/parse-code, using
  // the same parser the create endpoint validates with.
  const emptyRow = { doc_code: "", title: "", poi: "IFR", weight: "1", unit_code: "", doc_type: "" };
  const [rowForm, setRowForm] = useState(emptyRow);
  const [parsed, setParsed] = useState<CodeParse | null>(null);
  // a value the operator typed by hand wins over the one read from the code
  const unitTouched = useRef(false);
  const typeTouched = useRef(false);

  useEffect(() => {
    if (!project) return;
    api
      .get<CodeStructure>(`/projects/${project.id}/mdl/code-structure`)
      .then(setStructure)
      .catch(() => setStructure(null));
  }, [project]);

  useEffect(() => {
    if (!rowOpen || !project) return;
    const code = rowForm.doc_code.trim();
    if (code.length < 5) {
      setParsed(null);
      return;
    }
    let alive = true;
    const timer = window.setTimeout(() => {
      api
        .get<CodeParse>(`/projects/${project.id}/mdl/parse-code?code=${encodeURIComponent(code)}`)
        .then((data) => {
          if (!alive) return;
          setParsed(data);
          if (!data.ok) return;
          setRowForm((prev) => ({
            ...prev,
            unit_code: unitTouched.current ? prev.unit_code : data.fields.unit_code ?? "",
            doc_type: typeTouched.current ? prev.doc_type : data.fields.doc_type ?? "",
          }));
        })
        .catch(() => {
          if (alive) setParsed(null);
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
    // `rowForm.doc_code` is the only trigger: the setRowForm above must not re-run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowForm.doc_code, rowOpen, project]);

  const openNewRow = () => {
    setEditing(null);
    setRowError(null);
    setParsed(null);
    unitTouched.current = false;
    typeTouched.current = false;
    setRowForm(emptyRow);
    setRowOpen(true);
  };

  const openEditRow = (item: MDLItem) => {
    setEditing(item);
    setRowError(null);
    setParsed(null);
    // an existing row keeps exactly the values it was saved with
    unitTouched.current = true;
    typeTouched.current = true;
    setRowForm({
      doc_code: item.doc_code,
      title: item.title,
      poi: item.poi ?? "",
      weight: String(item.weight ?? 1),
      unit_code: item.unit_code ?? "",
      doc_type: item.doc_type ?? "",
    });
    setRowOpen(true);
  };

  const saveRow = async () => {
    if (!project) return;
    setRowError(null);
    if (rowForm.doc_code.trim().length < 2 || rowForm.title.trim().length < 2) {
      setRowError("کد مدرک و عنوان الزامی هستند.");
      return;
    }
    if (parsed && !parsed.ok) {
      setRowError(parsed.error ?? "کد مدرک با ساختار شماره‌گذاری پروژه سازگار نیست.");
      return;
    }
    setBusy(true);
    try {
      // null means "read it from the code" on create, and "leave it as it is" on edit.
      const body = {
        title: rowForm.title.trim(),
        poi: rowForm.poi.trim().toUpperCase() || null,
        weight: Number(rowForm.weight) || 1,
        unit_code: rowForm.unit_code.trim().toUpperCase() || null,
        doc_type: rowForm.doc_type.trim().toUpperCase() || null,
      };
      if (editing) {
        await api.patch(`/projects/${project.id}/mdl/items/${editing.id}`, {
          ...body,
          reason: "ویرایش دستی از صفحه MDL",
        });
      } else {
        await api.post(`/projects/${project.id}/mdl/items`, {
          ...body,
          doc_code: rowForm.doc_code.trim().toUpperCase(),
        });
      }
      setRowOpen(false);
      await load();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.detail : "ثبت ردیف MDL ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const deleteRow = async (item: MDLItem) => {
    if (!project) return;
    if (!window.confirm(`ردیف «${item.doc_code}» حذف شود؟`)) return;
    try {
      await api.delete(`/projects/${project.id}/mdl/items/${item.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "حذف ردیف MDL ناموفق بود.");
    }
  };

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
              {!isDcc ? (
                <Button variant="secondary" onClick={() => setGuideOpen(true)}>
                  راهنمای کدینگ
                </Button>
              ) : null}
              {isDcc ? (
                <>
                  <Button variant="secondary" onClick={openNewRow}>
                    افزودن ردیف دستی
                  </Button>
                  <Button onClick={() => setWizardOpen(true)}>بارگذاری MDL (اکسل)</Button>
                  <Button variant="secondary" onClick={() => setGuideOpen(true)}>
                    راهنمای کدینگ
                  </Button>
                </>
              ) : null}
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
          {!isDcc ? (
            <span className="text-[10px] text-[#6b5b1f]">
              MDL فقط توسط <b>کارشناس کنترل اسناد مهندسی</b> بارگذاری و ویرایش می‌شود؛ دسترسی شما
              فقط مشاهده است.
            </span>
          ) : null}
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
                  <th>فاز</th>
                  <th>سریال</th>
                  <th>P.O.I</th>
                  <th>وضعیت</th>
                  <th>بازنگری‌های ثبت‌شده</th>
                  <th>موعد</th>
                  {isDcc ? <th>عملیات</th> : null}
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
                    <td className="text-center">{item.phase ?? "—"}</td>
                    <td className="cell-center ltr">{item.serial_no ?? "—"}</td>
                    <td className="text-center">{item.poi ?? "—"}</td>
                    <td className="text-xs text-slate-600">{item.status ?? "—"}</td>
                    <td>
                      {item.revision_count > 0 ? (
                        <Badge className="bg-emerald-50 text-emerald-700">{persianDigits(item.revision_count)}</Badge>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>
                    <td className="text-xs text-slate-600">{item.due_date ? jalali(item.due_date) : "—"}</td>
                    {isDcc ? (
                      <td className="whitespace-nowrap">
                        <button
                          type="button"
                          className="text-xs text-brand-600 hover:underline"
                          onClick={() => openEditRow(item)}
                        >
                          ویرایش
                        </button>
                        <span className="mx-1 text-slate-300">|</span>
                        <button
                          type="button"
                          className="text-xs text-rose-600 hover:underline"
                          onClick={() => void deleteRow(item)}
                        >
                          حذف
                        </button>
                      </td>
                    ) : null}
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

      {/* ---------------- راهنمای کدینگ مدارک ---------------- */}
      <Modal
        open={guideOpen}
        title="ساختار کدینگ مدارک — Document Numbering"
        onClose={() => setGuideOpen(false)}
        width="max-w-4xl"
        footer={
          <div className="flex items-center justify-between">
            <span className="ltr text-[10px] text-[#33475b]">{structure?.example ?? "2102-20-EB-GN-MDL-9001"}</span>
            <Button variant="ghost" onClick={() => setGuideOpen(false)}>
              بستن
            </Button>
          </div>
        }
      >
        {structure ? (
          <>
            <table className="table-base">
              <thead>
                <tr>
                  <th style={{ width: 120 }}>سگمنت</th>
                  <th style={{ width: 150 }}>برچسب برگه</th>
                  <th style={{ width: 130 }}>الگو</th>
                  <th style={{ width: 90 }}>مثال</th>
                  <th>توضیح</th>
                </tr>
              </thead>
              <tbody>
                {structure.segments.map((segment) => (
                  <tr key={segment.key}>
                    <td className="font-bold text-[#1f3864]">{segment.label_fa}</td>
                    <td className="ltr text-[10px]">{segment.label_en}</td>
                    <td className="cell-center ltr font-mono text-[10px]">{segment.regex}</td>
                    <td className="cell-center ltr font-mono text-[10px]">{segment.example}</td>
                    <td className="text-[10px]">{segment.note_fa}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="mt-2 flex items-center gap-2 border border-[#a8a284] bg-[#f7f4e3] px-2 py-1">
              <span className="text-[10px] font-bold text-[#20365c]">نمونه:</span>
              <span className="ltr font-mono text-[12px] font-bold text-[#17427a]">{structure.example}</span>
              <span className="text-[9.5px] text-[#5c6b78]">
                قالب کوتاه (سازگاری با پروژه‌های قدیمی): {structure.short_example}
              </span>
            </div>

            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-[10px] font-bold text-[#1f3864]">دیسیپلین‌ها</p>
                <table className="table-base">
                  <tbody>
                    {structure.disciplines.map((item) => (
                      <tr key={item.code}>
                        <td className="cell-center ltr" style={{ width: 46 }}>
                          {item.code}
                        </td>
                        <td className="text-[10px]">{item.name_fa}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <p className="mb-1 text-[10px] font-bold text-[#1f3864]">
                  انواع مدرک ({persianDigits(structure.doc_types.length)} نوع)
                </p>
                <div className="flex flex-wrap gap-1">
                  {structure.doc_types.map((item) => (
                    <span
                      key={item.code}
                      className="rounded-sm border border-[#a8a284] bg-white px-1 text-[9.5px]"
                      title={item.name_fa}
                    >
                      <b className="ltr">{item.code}</b>
                    </span>
                  ))}
                </div>
                <p className="mt-2 text-[9.5px] leading-5 text-[#5c6b78]">
                  CLASS: {structure.doc_classes.join(" / ")} · BASIC/DETAIL:{" "}
                  {structure.basic_detail_values.join(" / ")}
                </p>
              </div>
            </div>

            <p className="mb-1 mt-2 text-[10px] font-bold text-[#1f3864]">
              بازه شماره سریال هر دیسیپلین — بیرون از بازه فقط هشدار است، نه خطا
            </p>
            <table className="table-base">
              <thead>
                <tr>
                  <th style={{ width: 70 }}>دیسیپلین</th>
                  <th>نام</th>
                  <th style={{ width: 130 }}>بازه سریال</th>
                </tr>
              </thead>
              <tbody>
                {structure.serial_blocks.map((block) => (
                  <tr key={block.discipline}>
                    <td className="cell-center ltr font-bold text-[#1f3864]">{block.discipline}</td>
                    <td className="text-[10px]">{block.name_fa}</td>
                    <td className="cell-center ltr font-mono text-[10px]">
                      {persianDigits(String(block.from))} – {persianDigits(String(block.to))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <Spinner />
        )}
      </Modal>

      {/* ---------------- manual row: add or edit one MDL line ---------------- */}
      <Modal
        open={rowOpen}
        title={editing ? `ویرایش ردیف MDL — ${editing.doc_code}` : "افزودن ردیف MDL (دستی)"}
        onClose={() => setRowOpen(false)}
        width="max-w-3xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => setRowOpen(false)}>
              انصراف
            </Button>
            <Button disabled={busy} onClick={() => void saveRow()}>
              {editing ? "ذخیره تغییرات" : "افزودن ردیف"}
            </Button>
          </div>
        }
      >
        {rowError ? <ErrorBox message={rowError} /> : null}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="کد مدرک *">
            <Input
              className="ltr"
              value={rowForm.doc_code}
              disabled={Boolean(editing)}
              placeholder="2102-20-EB-GN-MDL-9001"
              onChange={(event) => setRowForm({ ...rowForm, doc_code: event.target.value })}
            />
          </Field>
          <Field label="عنوان *">
            <Input
              value={rowForm.title}
              placeholder="Plot Plan / پلان موقعیت"
              onChange={(event) => setRowForm({ ...rowForm, title: event.target.value })}
            />
          </Field>
          <Field label="یونیت (واحد)" hint="از کد خوانده می‌شود">
            <Input
              className="ltr"
              value={rowForm.unit_code}
              placeholder="20"
              onChange={(event) => {
                unitTouched.current = true;
                setRowForm({ ...rowForm, unit_code: event.target.value });
              }}
            />
          </Field>
          <Field label="نوع مدرک" hint="از کد خوانده می‌شود">
            <Input
              className="ltr"
              value={rowForm.doc_type}
              placeholder="DWG"
              onChange={(event) => {
                typeTouched.current = true;
                setRowForm({ ...rowForm, doc_type: event.target.value });
              }}
            />
          </Field>
          <Field label="P.O.I">
            <Input
              className="ltr"
              value={rowForm.poi}
              placeholder="IFR"
              onChange={(event) => setRowForm({ ...rowForm, poi: event.target.value })}
            />
          </Field>
          <Field label="وزن (Weight)">
            <Input
              className="ltr"
              type="number"
              min={0}
              step="0.1"
              value={rowForm.weight}
              onChange={(event) => setRowForm({ ...rowForm, weight: event.target.value })}
            />
          </Field>
        </div>

        {parsed ? (
          parsed.ok ? (
            <div className="mt-3 border border-[#a8a284] bg-[#f7f4e3] px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10px] font-bold text-[#20365c]">از خود کد خوانده شد:</span>
                {structure?.segments.map((segment) => {
                  const value = parsed.segments[segment.key];
                  if (!value) return null;
                  return (
                    <span
                      key={segment.key}
                      className="rounded-sm border border-[#a8a284] bg-white px-1 text-[9.5px]"
                      title={segment.note_fa}
                    >
                      <span className="text-[#5c6b78]">{segment.label_fa}: </span>
                      <b className="ltr">{value}</b>
                    </span>
                  );
                })}
              </div>
              <p className="mt-1 text-[9.5px] leading-5 text-[#33475b]">
                دیسیپلین: {parsed.labels.discipline ?? "—"}
                {parsed.resolved.discipline_is_new
                  ? " (در این پروژه تعریف نشده است و هنگام ثبت ایجاد می‌شود)"
                  : ""}
                {" · "}
                فاز: {parsed.labels.phase ?? "—"}
                {" · "}
                نوع مدرک: {parsed.labels.doc_type ?? "—"}
                {parsed.fields.unit_code && !parsed.resolved.unit_exists
                  ? " · یونیت جدید در این پروژه"
                  : ""}
              </p>
              {parsed.duplicate ? (
                <p className="mt-1 text-[9.5px] font-bold text-[#a02020]">
                  این کد قبلاً در MDL همین پروژه ثبت شده است.
                </p>
              ) : null}
              {parsed.warnings.map((warning) => (
                <p key={warning} className="mt-1 text-[9.5px] leading-5 text-[#8a5a00]">
                  ⚠ {warning}
                </p>
              ))}
            </div>
          ) : (
            <p className="mt-3 border border-[#c9a2a2] bg-[#fbeeee] px-2 py-1.5 text-[9.5px] leading-5 text-[#a02020]">
              {parsed.error}
            </p>
          )
        ) : null}

        <p className="mt-2 text-[10px] leading-5 text-[#5c6b78]">
          کد مدرک باید یکتا باشد. یونیت، نوع مدرک، دیسیپلین، فاز و شماره سریال از خود کد خوانده
          می‌شوند و لازم نیست دستی وارد شوند؛ ردیف دستی هم دقیقاً مثل ردیف‌های ایمپورت‌شده در تطبیق
          مدارک شرکت می‌کند.
        </p>
      </Modal>

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
