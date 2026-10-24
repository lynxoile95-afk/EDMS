import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { SourceFile, UploadSession } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Spinner } from "@/components/ui";
import { FField, FieldGrid } from "@/components/window";
import { DecisionBadge } from "@/components/DecisionBadge";
import { PURPOSE_LABELS, STATUS_LABELS, fileSize, persianDigits } from "@/lib/format";

interface StagedFile {
  file: File;
  status: "pending" | "uploading" | "done" | "failed";
  error?: string;
  result?: SourceFile;
}

export function UploadPage() {
  const { project } = useProject();
  const navigate = useNavigate();
  const [purpose, setPurpose] = useState("IFR");
  const [mode, setMode] = useState("revision");
  const [type, setType] = useState("internal");
  const [note, setNote] = useState("");
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [session, setSession] = useState<UploadSession | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploaded" | "processing" | "done">("idle");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const addFiles = useCallback((files: FileList | File[]) => {
    const incoming = Array.from(files).filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    setStaged((current) => [
      ...current,
      ...incoming.map((file) => ({ file, status: "pending" as const })),
    ]);
  }, []);

  const start = async () => {
    if (!project || staged.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<UploadSession>(`/projects/${project.id}/upload-sessions`, {
        purpose,
        mode,
        transmittal_type: type,
        note: note || null,
      });
      setSession(created);
      setPhase("uploaded");

      const form = new FormData();
      staged.forEach((entry) => form.append("files", entry.file));
      setStaged((current) => current.map((entry) => ({ ...entry, status: "uploading" })));
      const uploaded = await api.postForm<SourceFile[]>(`/upload-sessions/${created.id}/files`, form);

      setStaged((current) =>
        current.map((entry, index) => ({
          ...entry,
          status: uploaded[index] ? "done" : "failed",
          result: uploaded[index],
        })),
      );

      await api.post(`/upload-sessions/${created.id}/commit`);
      setPhase("processing");

      // poll until the worker finishes
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const detail = await api.get<UploadSession>(`/upload-sessions/${created.id}`);
        setSession(detail);
        if (detail.status === "completed" || detail.status === "failed") {
          setPhase("done");
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 2500));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader
          title="بارگذاری مدرک"
          subtitle="فایل‌های PDF را اضافه کنید؛ سیستم کادر مشخصات را می‌خواند و با MDL تطبیق می‌دهد."
        />
        <div className="space-y-4 p-4">
          <div
            className={clsx(
              "flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-10 text-center transition",
              dragging ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-slate-50",
            )}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              addFiles(event.dataTransfer.files);
            }}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
          >
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf"
              multiple
              className="hidden"
              onChange={(event) => event.target.files && addFiles(event.target.files)}
            />
            <p className="text-sm font-medium text-slate-700">فایل‌ها را اینجا رها کنید یا کلیک کنید</p>
            <p className="mt-1 text-[11px] text-slate-400">فقط PDF — حداکثر ۲۰۰ مگابایت برای هر فایل</p>
          </div>

          {staged.length > 0 ? (
            <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {staged.map((entry, index) => (
                <div key={`${entry.file.name}-${index}`} className="flex items-center justify-between gap-2 px-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-slate-700">{entry.file.name}</p>
                    <p className="text-[11px] text-slate-400">
                      {fileSize(entry.file.size)}
                      {entry.result ? ` · SHA-256: ${entry.result.sha256.slice(0, 12)}…` : ""}
                    </p>
                    {entry.error ? <p className="text-[11px] text-rose-600">{entry.error}</p> : null}
                  </div>
                  <Badge
                    className={
                      entry.status === "done"
                        ? "bg-emerald-100 text-emerald-800"
                        : entry.status === "failed"
                          ? "bg-rose-100 text-rose-800"
                          : "bg-slate-100 text-slate-600"
                    }
                  >
                    {entry.status === "done"
                      ? "بارگذاری شد"
                      : entry.status === "uploading"
                        ? "در حال ارسال"
                        : entry.status === "failed"
                          ? "ناموفق"
                          : "در انتظار"}
                  </Badge>
                </div>
              ))}
            </div>
          ) : null}

          {error ? <ErrorBox message={error} /> : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void start()} disabled={busy || staged.length === 0 || phase === "done"}>
              {busy ? "در حال پردازش…" : "شروع پردازش هوشمند"}
            </Button>
            <Button variant="secondary" onClick={() => { setStaged([]); setSession(null); setPhase("idle"); setError(null); }}>
              پاک کردن
            </Button>
            {phase === "done" ? (
              <Button variant="secondary" onClick={() => navigate("/review")}>
                رفتن به صف بازبینی
              </Button>
            ) : null}
          </div>
        </div>
      </Card>

      <div className="space-y-4">
        <Card className="is-main">
          <CardHeader title="تنظیمات ارسال" />
          <div className="p-[3px]">
            <FieldGrid>
              <FField label="هدف ارسال (P.O.I):">
                <select className="input" value={purpose} onChange={(event) => setPurpose(event.target.value)}>
                  {Object.entries(PURPOSE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </FField>
              <FField label="نوع ارسال:">
                <select className="input" value={mode} onChange={(event) => setMode(event.target.value)}>
                  <option value="new_document">سند جدید</option>
                  <option value="revision">بازنگری سند موجود</option>
                  <option value="response_to_comment">پاسخ به نظرات</option>
                </select>
              </FField>
              <FField label="نوع برگه ارسال:">
                <select className="input" value={type} onChange={(event) => setType(event.target.value)}>
                  <option value="internal">داخلی — Internal</option>
                  <option value="external">خارجی — External (نیازمند تأیید مدیر پروژه)</option>
                </select>
              </FField>
              <FField label="توضیحات (Remarks):">
                <textarea
                  className="input min-h-[20px] w-full"
                  rows={2}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="اختیاری"
                />
              </FField>
            </FieldGrid>
          </div>
        </Card>

        {session ? (
          <Card>
            <CardHeader
              title="نتیجه پردازش"
              subtitle={`وضعیت جلسه: ${STATUS_LABELS[session.status] ?? session.status}`}
            />
            <div className="space-y-2 p-4">
              {phase === "processing" ? <Spinner label="در حال استخراج و تطبیق…" /> : null}
              {(session.results ?? []).length === 0 && phase === "done" ? (
                <p className="text-xs text-slate-500">نتیجه‌ای برای نمایش وجود ندارد.</p>
              ) : null}
              {(session.results ?? []).map((result) => (
                <button
                  key={result.id}
                  className="w-full rounded-lg border border-slate-200 p-2 text-right transition hover:border-brand-300"
                  onClick={() => navigate(`/review/${result.id}`)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="ltr font-mono text-xs">{result.doc_code ?? "—"}</span>
                    <DecisionBadge decision={result.decision} />
                  </div>
                  <p className="mt-1 line-clamp-1 text-[11px] text-slate-500">
                    {result.title ?? "بدون عنوان"} · Rev {result.rev ?? "—"}
                  </p>
                </button>
              ))}
              <p className="text-[11px] text-slate-400">
                تعداد فایل‌ها: {persianDigits(session.files.length)}
              </p>
            </div>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
