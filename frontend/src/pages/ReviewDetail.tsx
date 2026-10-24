import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ExtractionResultDetail, MatchCandidate, ReviewAction, ReviewOutcome } from "@/lib/types";
import { Badge, Button, Card, CardHeader, ErrorBox, Field, Input, Select, Spinner } from "@/components/ui";
import { ConfidenceBadge, DecisionBadge } from "@/components/DecisionBadge";
import { PdfViewer } from "@/components/PdfViewer";
import { PURPOSE_LABELS, RULE_LABELS, jalaliDateTime, percent } from "@/lib/format";

const FIELD_ROWS: { key: keyof ExtractionResultDetail; label: string; ltr?: boolean }[] = [
  { key: "doc_code", label: "کد مدرک", ltr: true },
  { key: "rev", label: "شماره بازنگری", ltr: true },
  { key: "title", label: "عنوان" },
  { key: "discipline_code", label: "دیسیپلین", ltr: true },
  { key: "unit_code", label: "یونیت", ltr: true },
  { key: "doc_status", label: "وضعیت مدرک" },
];

export function ReviewDetail() {
  const { resultId } = useParams<{ resultId: string }>();
  const { hasRole } = useAuth();
  const navigate = useNavigate();

  const [result, setResult] = useState<ExtractionResultDetail | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [selectedItem, setSelectedItem] = useState<string>("");
  const [rev, setRev] = useState("");
  const [title, setTitle] = useState("");
  const [createTransmittal, setCreateTransmittal] = useState(true);
  const [purpose, setPurpose] = useState("IFR");
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!resultId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<ExtractionResultDetail>(`/extraction-results/${resultId}`);
      setResult(data);
      setSelectedItem(data.matched_mdl_item_id ?? data.candidates[0]?.mdl_item_id ?? "");
      setRev(data.rev ?? "");
      setTitle(data.title ?? "");
      setNotes(data.review_note ?? "");
      try {
        setFileUrl(await api.blobUrl(`/files/source-files/${data.source_file_id}`));
        setPreviewError(null);
      } catch (previewFailure) {
        setFileUrl(null);
        setPreviewError(
          previewFailure instanceof ApiError ? previewFailure.detail : "دریافت فایل نقشه ناموفق بود.",
        );
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری نتیجه استخراج ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [resultId]);

  useEffect(() => {
    void load();
    return () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  const submit = async (action: ReviewAction["action"]) => {
    if (!result) return;
    setBusy(true);
    setActionError(null);
    const corrections: Record<string, string> = {};
    if (rev && rev !== (result.rev ?? "")) corrections.rev = rev;
    if (title && title !== (result.title ?? "")) corrections.title = title;
    const payload: ReviewAction = {
      action,
      notes: notes || null,
      corrections: Object.keys(corrections).length ? corrections : undefined,
      override_issues: override,
    };
    if (action === "register") {
      payload.mdl_item_id = selectedItem || null;
      payload.rev = rev || null;
      payload.title = title || null;
      payload.create_transmittal = createTransmittal;
      payload.purpose = purpose;
    }
    try {
      const outcome = await api.post<ReviewOutcome>(`/extraction-results/${result.id}/review`, payload);
      if (outcome.transmittal_ids.length > 0) {
        navigate(`/transmittals/${outcome.transmittal_ids[0]}`);
      } else {
        navigate("/review");
      }
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "ثبت تصمیم ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spinner label="در حال بارگذاری مدرک…" />;
  if (error) return <ErrorBox message={error} onRetry={() => void load()} />;
  if (!result) return null;

  const decided = result.review_status !== "pending";
  const canDecide = hasRole("dcc") || hasRole("project_manager") || hasRole("system_admin");
  const blockingIssues = result.issues.filter((issue) => issue.severity === "error" && issue.status === "open");
  const titleBlockBBox =
    (result.payload as { fields?: Record<string, { bbox?: number[] }> })?.fields?.doc_code?.bbox ?? null;

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              بازبینی مدرک
              <DecisionBadge decision={result.decision} />
              <Badge className="bg-slate-100">
                اطمینان کل: {percent(result.overall_confidence)}
              </Badge>
              {decided ? (
                <Badge className="bg-sky-100 text-sky-800">قبلاً بررسی شده ({result.review_status})</Badge>
              ) : null}
            </span>
          }
          subtitle={`${result.file_name ?? "—"} · ${jalaliDateTime(result.created_at)}`}
          actions={
            <Link to="/review" className="text-xs text-brand-600 hover:underline">
              بازگشت به صف
            </Link>
          }
        />
      </Card>

      <div className="grid gap-3 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader title="پیش‌نمایش نقشه" subtitle="ناحیه زرد رنگ، محل تشخیص کادر مشخصات است" />
          <div className="h-[70vh] p-2">
            {fileUrl ? (
              <PdfViewer url={fileUrl} highlight={titleBlockBBox && titleBlockBBox.length === 4 ? normalizeBBox(titleBlockBBox, result) : null} />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2">
                <p className="text-sm text-rose-700">{previewError ?? "فایل قابل نمایش نیست."}</p>
                <div className="flex gap-2">
                  <Button className="btn-sm" onClick={() => void load()}>
                    تلاش دوباره
                  </Button>
                  <Button
                    className="btn-sm"
                    onClick={() => void api.download(`/files/source-files/${result.source_file_id}`, result.file_name ?? "drawing.pdf")}
                  >
                    دریافت فایل
                  </Button>
                </div>
                <p className="text-[10px] text-[#5c6b78]">
                  اگر نمایش درون‌مروری مسدود باشد، فایل را دریافت و در مرورگر باز کنید.
                </p>
              </div>
            )}
          </div>
        </Card>

        <div className="space-y-3 xl:col-span-2">
          <Card>
            <CardHeader title="فیلدهای استخراج‌شده" subtitle="هر فیلد همراه با درصد اطمینان و روش استخراج" />
            <div className="divide-y divide-slate-100">
              {FIELD_ROWS.map((row) => {
                const value = result[row.key] as string | null;
                const confidence = result.field_confidence?.[row.key as string] ?? 0;
                const source = result.field_sources?.[row.key as string];
                return (
                  <div key={row.key as string} className="flex items-center justify-between gap-2 px-4 py-2">
                    <span className="text-xs text-slate-500">{row.label}</span>
                    <span className="flex items-center gap-2">
                      <span className={clsx("text-xs font-medium text-slate-800", row.ltr && "ltr font-mono")}>
                        {value || "—"}
                      </span>
                      <ConfidenceBadge value={confidence} source={source} />
                    </span>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card>
            <CardHeader title="ایرادهای اعتبارسنجی" subtitle={`${result.issues.length} مورد`} />
            <div className="max-h-56 divide-y divide-slate-100 overflow-y-auto">
              {result.issues.length === 0 ? (
                <p className="px-4 py-4 text-xs text-slate-500">هیچ ایرادی ثبت نشده است.</p>
              ) : (
                result.issues.map((issue) => (
                  <div key={issue.id} className="flex items-start gap-2 px-4 py-2">
                    <Badge
                      className={
                        issue.severity === "error"
                          ? "bg-rose-100 text-rose-800"
                          : issue.severity === "warning"
                            ? "bg-amber-100 text-amber-900"
                            : "bg-slate-100 text-slate-600"
                      }
                    >
                      {issue.rule_code}
                    </Badge>
                    <div className="min-w-0">
                      <p className="text-xs text-slate-700">{issue.message_fa ?? issue.message}</p>
                      <p className="text-[10px] text-slate-400">{RULE_LABELS[issue.rule_code] ?? ""}</p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="تطبیق با MDL" subtitle={result.match_method ? `روش: ${result.match_method}` : undefined} />
            <div className="space-y-2 p-3">
              {result.candidates.length === 0 ? (
                <p className="text-xs text-slate-500">کاندیدایی یافت نشد؛ مدرک باید به MDL اضافه شود.</p>
              ) : (
                result.candidates.map((candidate: MatchCandidate) => (
                  <label
                    key={candidate.mdl_item_id}
                    className={clsx(
                      "flex cursor-pointer items-start gap-2 rounded-lg border p-2 transition",
                      selectedItem === candidate.mdl_item_id
                        ? "border-brand-400 bg-brand-50"
                        : "border-slate-200 hover:border-slate-300",
                    )}
                  >
                    <input
                      type="radio"
                      name="candidate"
                      className="mt-1"
                      checked={selectedItem === candidate.mdl_item_id}
                      onChange={() => setSelectedItem(candidate.mdl_item_id)}
                      disabled={decided}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="ltr font-mono text-xs font-medium">{candidate.doc_code}</span>
                        <span className="text-[10px] text-slate-500">{percent(candidate.score)}</span>
                      </div>
                      <p className="line-clamp-1 text-[11px] text-slate-600">{candidate.title}</p>
                      <p className="text-[10px] text-slate-400">
                        {candidate.method} · {candidate.reasons[0] ?? ""}
                      </p>
                    </div>
                  </label>
                ))
              )}
            </div>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader title="تصمیم کارشناس کنترل مدارک" subtitle="هر تغییر نسبت به مقدار مدل، برای بهبود قواعد ذخیره می‌شود." />
        <div className="space-y-3 p-4">
          {actionError ? <ErrorBox message={actionError} /> : null}
          {blockingIssues.length > 0 ? (
            <label className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800">
              <input type="checkbox" checked={override} onChange={(event) => setOverride(event.target.checked)} />
              <span>
                این مدرک {blockingIssues.length} ایراد مسدودکننده دارد. با ثبت دلیل، نادیده‌گرفتن ایرادها را
                تأیید می‌کنم.
              </span>
            </label>
          ) : null}

          <div className="grid gap-3 md:grid-cols-4">
            <Field label="کد مدرک انتخابی (MDL)">
              <Select value={selectedItem} onChange={(event) => setSelectedItem(event.target.value)} disabled={decided}>
                <option value="">— انتخاب کنید —</option>
                {result.candidates.map((candidate) => (
                  <option key={candidate.mdl_item_id} value={candidate.mdl_item_id}>
                    {candidate.doc_code}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="بازنگری">
              <Input dir="ltr" value={rev} onChange={(event) => setRev(event.target.value)} disabled={decided} />
            </Field>
            <Field label="عنوان">
              <Input value={title} onChange={(event) => setTitle(event.target.value)} disabled={decided} />
            </Field>
            <Field label="هدف ارسال">
              <Select value={purpose} onChange={(event) => setPurpose(event.target.value)} disabled={decided}>
                {Object.entries(PURPOSE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <Field label="یادداشت / دلیل تصمیم">
            <textarea
              className="input min-h-[70px]"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              disabled={decided}
              placeholder="مثلاً: کد مدرک اصلاح شد؛ عنوان با MDL منطبق است."
            />
          </Field>

          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={createTransmittal}
              onChange={(event) => setCreateTransmittal(event.target.checked)}
              disabled={decided}
            />
            ساخت پیش‌نویس برگه ارسال برای گیرندگان DDM
          </label>

          <div className="flex flex-wrap gap-2">
            <Button disabled={!canDecide || busy || decided || !selectedItem || !rev} onClick={() => void submit("register")}>
              ثبت مدرک و ساخت برگه ارسال
            </Button>
            <Button variant="danger" disabled={!canDecide || busy || decided} onClick={() => void submit("quarantine")}>
              قرنطینه
            </Button>
            <Button variant="secondary" disabled={!canDecide || busy || decided} onClick={() => void submit("reject")}>
              رد مدرک
            </Button>
            <Button variant="ghost" disabled={!canDecide || busy || decided} onClick={() => void submit("ignore")}>
              بستن بدون ثبت
            </Button>
          </div>
          {!canDecide ? (
            <p className="text-[11px] text-amber-700">
              تنها کارشناس کنترل مدارک یا مدیر پروژه می‌تواند تصمیم نهایی را ثبت کند.
            </p>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

/** Convert a PDF bbox (points) to a normalised [x, y, w, h] overlay. */
function normalizeBBox(bbox: number[], result: ExtractionResultDetail): number[] {
  const page = (result.payload as { page?: { width?: number; height?: number } })?.page;
  const width = page?.width || 1;
  const height = page?.height || 1;
  const [x0, y0, x1, y1] = bbox;
  return [x0 / width, y0 / height, Math.max(0.001, (x1 - x0) / width), Math.max(0.001, (y1 - y0) / height)];
}
