import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import { useAuth } from "@/lib/auth";
import type { SourceFile, UploadSession } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Input, Modal, Select, Spinner } from "@/components/ui";
import { FField, FieldGrid } from "@/components/window";
import { DecisionBadge } from "@/components/DecisionBadge";
import { PURPOSE_LABELS, STATUS_LABELS, fileSize, persianDigits } from "@/lib/format";

interface AIConfig {
  provider: string;
  base_url: string;
  model: string;
  enabled: boolean;
  allow_remote: boolean;
  key_set: boolean;
  endpoint_local: boolean;
  endpoint_trusted: boolean;
  applies_until: string;
  note_fa: string;
}

interface AIProbe {
  provider: string;
  base_url: string;
  model: string;
  key_set: boolean;
  enabled: boolean;
  endpoint_local: boolean;
  remote_allowed: boolean;
  ok: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
  models_seen: string[] | null;
}

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
  // what this deployment can extract with, and whether an AI key is present
  const [caps, setCaps] = useState<{
    ocr_enabled: boolean;
    ocr_engine: string;
    ocr_langs: string;
    vlm_enabled: boolean;
    vlm_api_key_set: boolean;
    vlm_model: string;
    note_fa: string;
  } | null>(null);
  const [capsOpen, setCapsOpen] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.is_superuser === true;
  // the AI panel: a key entered here lives in the API process only, and is never
  // displayed back — no endpoint returns it.
  const [aiConfig, setAiConfig] = useState<AIConfig | null>(null);
  const [aiForm, setAiForm] = useState({
    provider: "gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta",
    model: "gemini-2.0-flash",
    api_key: "",
    allow_remote: true,
    confirm_remote_acknowledged: false,
  });
  const [aiProbe, setAiProbe] = useState<AIProbe | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const openAiPanel = async () => {
    setCapsOpen(true);
    setAiProbe(null);
    setAiError(null);
    try {
      const data = await api.get<AIConfig>("/admin/ai-config");
      setAiConfig(data);
      setAiForm((prev) => ({
        ...prev,
        provider: data.provider,
        base_url: data.base_url,
        model: data.model,
        allow_remote: data.allow_remote,
        api_key: "",
      }));
    } catch {
      setAiConfig(null); // not an admin: the panel stays informational
    }
  };

  const testAiConfig = async () => {
    setAiBusy(true);
    setAiError(null);
    try {
      setAiProbe(await api.post<AIProbe>("/admin/ai-config/test"));
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "آزمایش اتصال ناموفق بود.");
    } finally {
      setAiBusy(false);
    }
  };

  const saveAiConfig = async () => {
    setAiBusy(true);
    setAiError(null);
    try {
      const data = await api.put<AIConfig>("/admin/ai-config", {
        provider: aiForm.provider,
        base_url: aiForm.base_url.trim(),
        model: aiForm.model.trim(),
        ...(aiForm.api_key.trim() ? { api_key: aiForm.api_key.trim() } : {}),
        enabled: true,
        allow_remote: aiForm.allow_remote,
        confirm_remote_acknowledged: aiForm.confirm_remote_acknowledged,
      });
      setAiConfig(data);
      setAiForm((prev) => ({ ...prev, api_key: "" })); // never keep the key in the form
      await testAiConfig();
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "ذخیره تنظیمات هوش مصنوعی ناموفق بود.");
    } finally {
      setAiBusy(false);
    }
  };

  useEffect(() => {
    api
      .get<typeof caps>("/system/capabilities")
      .then(setCaps)
      .catch(() => setCaps(null));
  }, []);

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
          {/* Extraction capability strip — answers "is an AI key needed here?" */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border border-[#a8a284] bg-[#f7f4e3] px-2 py-1 text-[10px] text-[#20365c]">
            <span className="font-bold">موتور استخراج:</span>
            <span>
              OCR:{" "}
              {caps
                ? caps.ocr_enabled
                  ? `${caps.ocr_engine} (${caps.ocr_langs})`
                  : "خاموش"
                : "—"}
            </span>
            <span className="flex items-center gap-1">
              هوش مصنوعی (VLM):{" "}
              {caps?.vlm_enabled ? (
                <b className="text-[#1e6b34]">
                  فعال — {caps.vlm_model}
                  {caps.vlm_api_key_set ? " (کلید تنظیم شده)" : " (بدون کلید)"}
                </b>
              ) : (
                <b className="text-[#8a5a10]">غیرفعال — لازم هم نیست</b>
              )}
            </span>
            <button
              type="button"
              className="ms-auto font-bold text-[#1f4e79] underline"
              onClick={() => void openAiPanel()}
            >
              کلید هوش مصنوعی کجا وارد می‌شود؟
            </button>
          </div>

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

      {/* ---------------- where the AI key lives ---------------- */}
      <Modal
        open={capsOpen}
        title="کلید هوش مصنوعی — VLM"
        onClose={() => setCapsOpen(false)}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end">
            <Button variant="ghost" onClick={() => setCapsOpen(false)}>
              بستن
            </Button>
          </div>
        }
      >
        <div className="space-y-2 text-[11px] leading-6 text-[#33475b]">
          <p className="border border-[#a8a284] bg-[#f7f4e3] px-2 py-1 font-bold text-[#20365c]">
            بارگذاری اسناد به کلید هوش مصنوعی نیاز ندارد. استخراج با لایه متن PDF، قواعد
            (R01–R22) و OCR فارسی/انگلیسی انجام می‌شود؛ کلید فقط برای لایه کمکی VLM است که در
            موارد مبهم به کار می‌رود.
          </p>

          {isAdmin ? (
            <>
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">سرویس‌دهنده</span>
                  <Select
                    value={aiForm.provider}
                    onChange={(event) => {
                      const provider = event.target.value;
                      setAiForm({
                        ...aiForm,
                        provider,
                        base_url:
                          provider === "gemini"
                            ? "https://generativelanguage.googleapis.com/v1beta"
                            : "http://localhost:8000/v1",
                        model:
                          provider === "gemini" ? "gemini-2.0-flash" : "Qwen/Qwen2.5-VL-7B-Instruct",
                      });
                    }}
                  >
                    <option value="gemini">Google Gemini</option>
                    <option value="openai">OpenAI-compatible (vLLM / Ollama / LM Studio)</option>
                  </Select>
                </label>
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">نام مدل</span>
                  <Input
                    className="ltr"
                    value={aiForm.model}
                    onChange={(event) => setAiForm({ ...aiForm, model: event.target.value })}
                  />
                </label>
                <label className="block sm:col-span-2">
                  <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">
                    آدرس سرویس (base URL)
                  </span>
                  <Input
                    className="ltr"
                    value={aiForm.base_url}
                    onChange={(event) => setAiForm({ ...aiForm, base_url: event.target.value })}
                  />
                </label>
                <label className="block sm:col-span-2">
                  <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">
                    کلید API{" "}
                    {aiConfig?.key_set ? "(کلید فعلی تنظیم است؛ برای تغییر، کلید تازه را وارد کنید)" : ""}
                  </span>
                  <Input
                    className="ltr"
                    type="password"
                    autoComplete="off"
                    placeholder="••••••••••••"
                    value={aiForm.api_key}
                    onChange={(event) => setAiForm({ ...aiForm, api_key: event.target.value })}
                  />
                  <span className="mt-0.5 block text-[9px] text-[#5c6b78]">
                    کلید فقط در حافظهٔ همین سرویس نگه داشته می‌شود، جایی نوشته نمی‌شود و در هیچ
                    پاسخی برنمی‌گردد. برای دائمی شدن، همان مقدار را در فایل{" "}
                    <span className="ltr font-mono">.env</span> بگذارید.
                  </span>
                </label>
              </div>

              <label className="flex items-start gap-2 border border-[#c9a2a2] bg-[#fbeeee] px-2 py-1">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={aiForm.confirm_remote_acknowledged}
                  onChange={(event) =>
                    setAiForm({
                      ...aiForm,
                      confirm_remote_acknowledged: event.target.checked,
                      allow_remote: event.target.checked,
                    })
                  }
                />
                <span className="text-[10px] leading-5 text-[#8a2020]">
                  تأیید ارسال به سرور عمومی: با فعال کردن این گزینه، برش کادر مشخصات نقشه‌ها به
                  سرویس بیرونی ارسال می‌شود. برای داده‌های محرمانه فقط سرور داخلی مجاز است.
                </span>
              </label>

              {aiError ? <ErrorBox message={aiError} /> : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button disabled={aiBusy} onClick={() => void saveAiConfig()}>
                  {aiBusy ? "در حال ذخیره…" : "ذخیره و آزمایش اتصال"}
                </Button>
                <Button variant="secondary" disabled={aiBusy} onClick={() => void testAiConfig()}>
                  آزمایش اتصال
                </Button>
                {aiConfig ? (
                  <span className="text-[9.5px] text-[#5c6b78]">
                    وضعیت: {aiConfig.enabled ? "فعال" : "غیرفعال"} · کلید:{" "}
                    {aiConfig.key_set ? "تنظیم شده" : "تنظیم نشده"} · آدرس:{" "}
                    {aiConfig.endpoint_trusted ? "مجاز" : "مسدود (تأیید نشده)"}
                  </span>
                ) : null}
              </div>

              {aiProbe ? (
                <div
                  className={
                    aiProbe.ok
                      ? "border border-[#9fb48a] bg-[#f0f6e8] px-2 py-1"
                      : "border border-[#c9a2a2] bg-[#fbeeee] px-2 py-1"
                  }
                >
                  <p className="text-[10px] font-bold">
                    {aiProbe.ok ? "اتصال برقرار شد" : "اتصال برقرار نشد"}
                    {aiProbe.latency_ms !== null
                      ? ` — ${persianDigits(String(aiProbe.latency_ms))} میلی‌ثانیه`
                      : ""}
                    {aiProbe.status_code
                      ? ` (HTTP ${persianDigits(String(aiProbe.status_code))})`
                      : ""}
                  </p>
                  {aiProbe.error ? (
                    <p className="mt-0.5 text-[9.5px] leading-5">{aiProbe.error}</p>
                  ) : null}
                  {aiProbe.models_seen && aiProbe.models_seen.length > 0 ? (
                    <p className="mt-0.5 ltr font-mono text-[9px] text-[#33475b]">
                      {aiProbe.models_seen.slice(0, 4).join(" · ")}
                    </p>
                  ) : null}
                  <p className="mt-0.5 text-[9px] text-[#5c6b78]">
                    سرویس: {aiProbe.provider} · مدل:{" "}
                    <span className="ltr font-mono">{aiProbe.model}</span>
                  </p>
                </div>
              ) : null}

              <p className="text-[10px] leading-5">
                <b>برای اجرای واقعی روی مدارک</b> دو چیز لازم است: همین کلید (که با «ذخیره» فعال
                می‌شود) و کلید <span className="ltr font-mono">vlm_fallback</span> در تنظیمات همان
                پروژه. بدون دومی، حتی با کلید هم لایه کمکی اجرا نمی‌شود.
              </p>
            </>
          ) : (
            <p className="text-[10px] leading-5">
              تنظیم کلید و آدرس هوش مصنوعی فقط با حساب مدیر امکان‌پذیر است. کلید در هیچ صفحه‌ای
              نمایش داده نمی‌شود؛ تنها «تنظیم شده / تنظیم نشده» بودن آن از مسیر{" "}
              <span className="ltr font-mono">GET /api/v1/system/capabilities</span> قابل بررسی
              است.
            </p>
          )}

          <p>
            <b>راه دائمی:</b> در ریشهٔ پروژه فایل <span className="ltr font-mono">.env</span> را از
            روی <span className="ltr font-mono">.env.example</span> بسازید و این‌ها را پر کنید،
            سپس <span className="ltr font-mono">docker compose up -d api worker</span>:
          </p>
          <pre className="ltr overflow-x-auto border border-[#a8a284] bg-[#fdfcf4] p-2 font-mono text-[10px]">{`VLM_ENABLED=true
VLM_PROVIDER=gemini
VLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
VLM_API_KEY=<کلید-شما>
VLM_MODEL=gemini-2.0-flash
VLM_ALLOW_REMOTE=true   # فقط با تأیید صریح ارسال به بیرون`}</pre>
          <p className="text-[#8a5a10]">
            نکتهٔ امنیتی: آدرس داخلی (localhost، vlm، 10.x، 192.168.x، 172.x،{" "}
            <span className="ltr">.local</span> / <span className="ltr">.internal</span>) بدون
            تأیید پذیرفته می‌شود؛ آدرس عمومی فقط با تیک «تأیید ارسال به سرور عمومی».
          </p>
        </div>
      </Modal>
    </div>
  );
}
