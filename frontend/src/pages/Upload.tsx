import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import { useAuth } from "@/lib/auth";
import type { SourceFile, UploadSession } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Input, Modal, Select, Spinner } from "@/components/ui";
import { FField, FieldGrid } from "@/components/window";
import { DecisionBadge } from "@/components/DecisionBadge";
import { IconEye, IconSearch } from "@/components/icons";
import { AI_PROVIDERS, providerById, providerHasVisionModel } from "@/lib/aiProviders";
import { PURPOSE_LABELS, STATUS_LABELS, fileSize, persianDigits } from "@/lib/format";

interface AICredential {
  id: string;
  provider: string;
  providerName: string;
  apiType: string;
  name: string;
  baseUrl: string;
  model: string;
  extra: Record<string, string>;
  keyMasked: string | null;
  keySet: boolean;
  status: "untested" | "connected" | "failed" | string;
  lastTestedAt: string | null;
  latencyMs: number | null;
  lastTestStatus: number | null;
  lastError: string | null;
  isActive: boolean;
  isFallback: boolean;
  remoteConsent: boolean;
  isLocal: boolean;
  visionCapableModels: string[];
}

interface AIConnectionTest {
  ok: boolean;
  status: number | null;
  message: string;
  latencyMs: number | null;
  model: string;
  models: string[] | null;
  details: string | null;
}

interface AIForm {
  provider: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  extra: Record<string, string>;
  remoteConsent: boolean;
  isActive: boolean;
}

function newAIForm(providerId: string, credential?: AICredential | null): AIForm {
  const provider = providerById(providerId);
  const extra = Object.fromEntries(
    (provider.extraFields ?? []).map((field) => [field.id, credential?.extra?.[field.id] ?? field.default ?? ""]),
  );
  return {
    provider: provider.id,
    name: credential?.name ?? `${provider.name} – main`,
    baseUrl: credential?.baseUrl ?? provider.defaultBaseUrl,
    apiKey: "",
    model: credential?.model ?? provider.defaultModel,
    extra,
    remoteConsent: credential?.remoteConsent ?? provider.isLocal,
    isActive: credential?.isActive ?? false,
  };
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
  // The AI panel keeps the key only as a transient form value for the authenticated
  // request; it is never loaded from, persisted in, or returned by the API.
  const [aiCredentials, setAiCredentials] = useState<AICredential[]>([]);
  const [aiSelectedCredentialId, setAiSelectedCredentialId] = useState<string | null>(null);
  const [aiSelectedProvider, setAiSelectedProvider] = useState("gemini");
  const [aiSearch, setAiSearch] = useState("");
  const [aiFallbackId, setAiFallbackId] = useState<string | null>(null);
  const [aiForm, setAiForm] = useState<AIForm>(() => newAIForm("gemini"));
  const [aiProbe, setAiProbe] = useState<AIConnectionTest | null>(null);
  const [aiModels, setAiModels] = useState<string[]>([]);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiDetailsOpen, setAiDetailsOpen] = useState(false);
  const [aiShowKey, setAiShowKey] = useState(false);

  const selectedAIProvider = providerById(aiSelectedProvider);
  const selectedAICredential = aiCredentials.find((credential) => credential.id === aiSelectedCredentialId) ?? null;
  const filteredAIProviders = useMemo(() => {
    const query = aiSearch.trim().toLowerCase();
    if (!query) return AI_PROVIDERS;
    return AI_PROVIDERS.filter((provider) => `${provider.name} ${provider.id} ${provider.apiType}`.toLowerCase().includes(query));
  }, [aiSearch]);
  const credentialsForSelectedProvider = aiCredentials.filter((credential) => credential.provider === aiSelectedProvider);

  const statusForProvider = (providerId: string): "empty" | "untested" | "connected" | "failed" => {
    const credentials = aiCredentials.filter((credential) => credential.provider === providerId);
    if (credentials.some((credential) => credential.status === "connected")) return "connected";
    if (credentials.some((credential) => credential.status === "failed")) return "failed";
    if (credentials.length > 0) return "untested";
    return "empty";
  };

  const selectAICredential = (credential: AICredential | null, providerId = credential?.provider ?? aiSelectedProvider) => {
    const nextProvider = providerById(providerId);
    setAiSelectedProvider(nextProvider.id);
    setAiSelectedCredentialId(credential?.id ?? null);
    setAiForm(newAIForm(nextProvider.id, credential));
    setAiProbe(null);
    setAiModels([]);
    setAiDetailsOpen(false);
    setAiShowKey(false);
  };

  const loadAICredentials = async (preferredId?: string | null) => {
    setAiLoading(true);
    try {
      const credentials = await api.get<AICredential[]>("/ai/credentials");
      setAiCredentials(credentials);
      setAiFallbackId(credentials.find((credential) => credential.isFallback)?.id ?? null);
      const preferred = (preferredId && credentials.find((credential) => credential.id === preferredId))
        ?? credentials.find((credential) => credential.isActive)
        ?? credentials[0]
        ?? null;
      if (preferred) selectAICredential(preferred);
      else selectAICredential(null, "gemini");
    } finally {
      setAiLoading(false);
    }
  };

  const openAiPanel = async () => {
    setCapsOpen(true);
    setAiSearch("");
    setAiError(null);
    setAiProbe(null);
    if (!isAdmin) return;
    try {
      await loadAICredentials();
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "دریافت Credentialهای هوش مصنوعی ناموفق بود.");
    }
  };

  const chooseAIProvider = (providerId: string) => {
    const existing = aiCredentials.find((credential) => credential.provider === providerId && credential.isActive)
      ?? aiCredentials.find((credential) => credential.provider === providerId)
      ?? null;
    selectAICredential(existing, providerId);
    setAiError(null);
  };

  const validateAIForm = (): boolean => {
    const provider = providerById(aiForm.provider);
    if (!aiForm.name.trim()) {
      setAiError("نام Credential را وارد کنید.");
      return false;
    }
    try {
      const url = new URL(aiForm.baseUrl.trim());
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error();
    } catch {
      setAiError("base URL معتبر نیست؛ آدرس باید با http:// یا https:// شروع شود.");
      return false;
    }
    if (!aiForm.model.trim()) {
      setAiError("نام مدل را وارد کنید یا از فهرست مدل‌ها انتخاب کنید.");
      return false;
    }
    if (provider.keyRequired && !aiForm.apiKey.trim() && !selectedAICredential?.keySet) {
      setAiError("برای این سرویس‌دهنده API Key الزامی است.");
      return false;
    }
    const missing = (provider.extraFields ?? []).find((field) => field.required && !aiForm.extra[field.id]?.trim());
    if (missing) {
      setAiError(`فیلد «${missing.label}» الزامی است.`);
      return false;
    }
    if (aiForm.isActive && !provider.isLocal && !aiForm.remoteConsent) {
      setAiError("برای فعال کردن سرویس ابری، ابتدا تأیید ارسال به سرور عمومی را انتخاب کنید.");
      return false;
    }
    setAiError(null);
    return true;
  };

  const applyAIProbe = (result: AIConnectionTest, credentialId = aiSelectedCredentialId) => {
    setAiProbe(result);
    setAiDetailsOpen(!result.ok && Boolean(result.details));
    if (credentialId) {
      setAiCredentials((current) => current.map((credential) => credential.id === credentialId
        ? {
            ...credential,
            status: result.ok ? "connected" : "failed",
            latencyMs: result.latencyMs,
            lastTestStatus: result.status,
            lastError: result.ok ? null : result.message,
          }
        : credential));
    }
  };

  const testAiConfig = async () => {
    if (!validateAIForm()) return;
    setAiBusy(true);
    setAiError(null);
    setAiProbe(null);
    try {
      const key = aiForm.apiKey.trim();
      const result = aiSelectedCredentialId && !key
        ? await api.post<AIConnectionTest>(`/ai/credentials/${aiSelectedCredentialId}/test`)
        : await api.post<AIConnectionTest>("/ai/test", {
            provider: aiForm.provider,
            name: aiForm.name.trim(),
            base_url: aiForm.baseUrl.trim(),
            ...(key ? { api_key: key } : {}),
            model: aiForm.model.trim(),
            extra: aiForm.extra,
            remote_consent: aiForm.remoteConsent,
          });
      applyAIProbe(result);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "آزمایش اتصال ناموفق بود.");
    } finally {
      setAiBusy(false);
    }
  };

  const saveAiConfig = async () => {
    if (!validateAIForm()) return;
    setAiBusy(true);
    setAiError(null);
    setAiProbe(null);
    try {
      const key = aiForm.apiKey.trim();
      const saved = await api.post<AICredential>("/ai/credentials", {
        provider: aiForm.provider,
        name: aiForm.name.trim(),
        base_url: aiForm.baseUrl.trim(),
        ...(key ? { api_key: key } : {}),
        model: aiForm.model.trim(),
        extra: aiForm.extra,
        remote_consent: aiForm.remoteConsent,
        is_active: aiForm.isActive,
      });
      const currentActive = aiCredentials.find((credential) => credential.isActive)?.id ?? null;
      const activeId = aiForm.isActive
        ? saved.id
        : aiSelectedCredentialId === currentActive
          ? null
          : currentActive;
      await api.put("/ai/active", {
        credential_id: activeId,
        fallback_credential_id: aiFallbackId,
      });
      await loadAICredentials(saved.id);
      const result = await api.post<AIConnectionTest>(`/ai/credentials/${saved.id}/test`);
      applyAIProbe(result, saved.id);
      setAiForm((current) => ({ ...current, apiKey: "" }));
      await api.get<typeof caps>("/system/capabilities").then(setCaps).catch(() => undefined);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "ذخیره Credential ناموفق بود.");
    } finally {
      setAiBusy(false);
    }
  };

  const clearAiKey = async () => {
    if (!aiSelectedCredentialId) return;
    setAiBusy(true);
    setAiError(null);
    try {
      await api.delete(`/ai/credentials/${aiSelectedCredentialId}/key`);
      await loadAICredentials(aiSelectedCredentialId);
      setAiProbe(null);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "پاک کردن کلید ناموفق بود.");
    } finally {
      setAiBusy(false);
    }
  };

  const fetchAIModels = async () => {
    if (!aiSelectedCredentialId) {
      setAiError("ابتدا Credential را ذخیره کنید تا فهرست مدل‌ها از سرویس دریافت شود.");
      return;
    }
    setAiBusy(true);
    setAiError(null);
    try {
      const data = await api.get<{ models: string[] }>(`/ai/models?provider=${encodeURIComponent(aiSelectedProvider)}&credentialId=${encodeURIComponent(aiSelectedCredentialId)}`);
      setAiModels(data.models);
      if (data.models.length > 0 && !data.models.includes(aiForm.model)) {
        setAiForm((current) => ({ ...current, model: data.models[0] }));
      }
    } catch (err) {
      setAiError(err instanceof ApiError ? err.detail : "دریافت مدل‌ها ناموفق بود.");
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
    <div className="space-y-3">
      <Card>
        <CardHeader
          title="بارگذاری مدارک با هوش مصنوعی"
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

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-[#d5dde5] pt-2">
            <Button onClick={() => void start()} disabled={busy || staged.length === 0 || phase === "done"}>
              {busy ? "در حال پردازش…" : "شروع پردازش هوشمند"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setStaged([]);
                setSession(null);
                setPhase("idle");
                setError(null);
              }}
            >
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

      <Card className="is-main">
        <CardHeader title="بارگذاری دستی مدارک" />
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
          <div className="flex flex-wrap items-center justify-center gap-3 border-x border-[#a8a284] bg-white px-3 py-3">
            <Button variant="secondary" onClick={() => navigate("/transmittals/new")}>
              ایجاد ترانسمیتال جدید
            </Button>
          </div>
          <div className="border border-[#a8a284] bg-white px-3 py-2 text-right">
            <p className="text-[11px] font-bold text-[#1f3864]">ثبت ترانسمیتال دستی</p>
            <p className="text-[9.5px] leading-4 text-[#5c6b78]">
              بدون استخراج هوش مصنوعی، مدارک MDL و بازنگری آن‌ها را انتخاب و ترانسمیتال جدید ایجاد کنید.
            </p>
          </div>
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

      {/* ---------------- where the AI key lives ---------------- */}
      <Modal
        open={capsOpen}
        title="کلید هوش مصنوعی — VLM"
        onClose={() => setCapsOpen(false)}
        width="max-w-6xl"
        footer={
          <div className="flex items-center justify-between gap-2">
            <span className="text-[9.5px] text-[#64748b]">کلیدها فقط به‌صورت رمزنگاری‌شده روی سرور نگه‌داری می‌شوند.</span>
            <Button variant="ghost" onClick={() => setCapsOpen(false)}>
              بستن
            </Button>
          </div>
        }
      >
        <div className="space-y-3 text-[11px] leading-6 text-[#33475b]">
          <p className="border border-[#a8a284] bg-[#f7f4e3] px-2 py-1 font-bold text-[#20365c]">
            بارگذاری اسناد به کلید هوش مصنوعی نیاز ندارد. استخراج با لایه متن PDF، قواعد
            (R01–R22) و OCR فارسی/انگلیسی انجام می‌شود؛ کلید فقط برای لایه کمکی VLM است که در
            موارد مبهم به کار می‌رود.
          </p>

          {!isAdmin ? (
            <p className="border border-[#c9a2a2] bg-[#fbeeee] px-2 py-1 text-[#8a2020]">
              تنظیم Credential و کلید هوش مصنوعی فقط با حساب مدیر سیستم امکان‌پذیر است.
            </p>
          ) : aiLoading ? (
            <Spinner label="در حال دریافت Credentialها…" />
          ) : (
            <>
              <fieldset disabled={aiBusy} className="contents">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[240px] flex-1">
                  <IconSearch className="pointer-events-none absolute right-2 top-2.5 h-3.5 w-3.5 text-[#64748b]" />
                  <Input
                    value={aiSearch}
                    onChange={(event) => setAiSearch(event.target.value)}
                    placeholder="جستجوی سرویس..."
                    aria-label="جستجوی سرویس‌دهنده هوش مصنوعی"
                    className="pr-7"
                  />
                </div>
                <span className="text-[9.5px] text-[#64748b]">{persianDigits(aiCredentials.length)} Credential ذخیره‌شده</span>
              </div>

              <div className="grid max-h-52 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3 lg:grid-cols-5" role="listbox" aria-label="سرویس‌دهنده‌های هوش مصنوعی">
                {filteredAIProviders.map((provider) => {
                  const status = statusForProvider(provider.id);
                  const statusColor = status === "connected" ? "#16803c" : status === "failed" ? "#b42318" : status === "untested" ? "#d28a12" : "#94a3b8";
                  const statusLabel = status === "connected" ? "اتصال برقرار است" : status === "failed" ? "آزمایش ناموفق" : status === "untested" ? "ذخیره‌شده و آزمایش‌نشده" : "تنظیم نشده";
                  const providerCredentials = aiCredentials.filter((credential) => credential.provider === provider.id);
                  return (
                    <button
                      key={provider.id}
                      type="button"
                      role="option"
                      aria-selected={provider.id === aiSelectedProvider}
                      onClick={() => chooseAIProvider(provider.id)}
                      className={clsx(
                        "relative flex min-h-[76px] flex-col items-start gap-1 border px-2 py-2 text-right transition",
                        provider.id === aiSelectedProvider
                          ? "border-[#1f4e79] bg-[#edf5fb] shadow-sm"
                          : "border-[#d4dde6] bg-white hover:border-[#7ea4c5] hover:bg-[#f8fbfd]",
                      )}
                    >
                      <span className="flex w-full items-center justify-between gap-1">
                        <span className="flex h-7 min-w-7 items-center justify-center rounded-md px-1 text-[9px] font-bold text-white" style={{ backgroundColor: provider.iconColor }}>
                          {provider.avatar}
                        </span>
                        <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: statusColor }} title={statusLabel} aria-label={statusLabel} />
                      </span>
                      <span className="line-clamp-2 text-[10px] font-bold text-[#1f3864]">{provider.name}</span>
                      {providerCredentials.length > 0 ? <span className="text-[8.5px] text-[#64748b]">{persianDigits(providerCredentials.length)} Credential</span> : null}
                    </button>
                  );
                })}
              </div>
              {filteredAIProviders.length === 0 ? <EmptyState title="سرویسی پیدا نشد" description="عبارت جستجو را تغییر دهید." /> : null}

              <div className="border border-[#c7d5e1] bg-[#fbfdff]">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#dbe5ee] bg-[#eef5fa] px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="flex h-8 min-w-8 items-center justify-center rounded-md px-1 text-[10px] font-bold text-white" style={{ backgroundColor: selectedAIProvider.iconColor }}>
                      {selectedAIProvider.avatar}
                    </span>
                    <div>
                      <h3 className="text-[12px] font-bold text-[#1f3864]">{selectedAIProvider.name}</h3>
                      <p className="ltr text-[9px] text-[#64748b]">{selectedAIProvider.apiType}</p>
                    </div>
                  </div>
                  {selectedAICredential?.isActive ? <span className="rounded-full bg-[#dcfce7] px-2 py-0.5 text-[9px] font-bold text-[#166534]">Active</span> : null}
                </div>

                <div className="space-y-3 p-3">
                  {credentialsForSelectedProvider.length > 0 ? (
                    <label className="block max-w-md">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">Credential ذخیره‌شده</span>
                      <Select
                        value={aiSelectedCredentialId ?? ""}
                        onChange={(event) => {
                          const credential = credentialsForSelectedProvider.find((item) => item.id === event.target.value) ?? null;
                          selectAICredential(credential, aiSelectedProvider);
                        }}
                      >
                        <option value="">Credential جدید</option>
                        {credentialsForSelectedProvider.map((credential) => (
                          <option key={credential.id} value={credential.id}>{credential.name}{credential.isActive ? " — Active" : ""}</option>
                        ))}
                      </Select>
                    </label>
                  ) : null}

                  <div className="grid gap-2 md:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">Provider</span>
                      <Input className="ltr bg-[#f1f5f9]" value={selectedAIProvider.name} readOnly />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">نام Credential</span>
                      <Input value={aiForm.name} onChange={(event) => setAiForm((current) => ({ ...current, name: event.target.value }))} placeholder="Gemini – main" />
                    </label>
                    <label className="block md:col-span-2">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">Base URL</span>
                      <Input
                        className="ltr"
                        value={aiForm.baseUrl}
                        readOnly={!selectedAIProvider.baseUrlEditable}
                        onChange={(event) => setAiForm((current) => ({ ...current, baseUrl: event.target.value }))}
                      />
                      {!selectedAIProvider.baseUrlEditable ? <span className="mt-0.5 block text-[9px] text-[#64748b]">آدرس این سرویس ثابت است.</span> : null}
                    </label>
                    <label className="block md:col-span-2">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">API Key {selectedAICredential?.keySet ? `(${selectedAICredential.keyMasked ?? "کلید ذخیره‌شده"})` : ""}</span>
                      <span className="flex items-center gap-1">
                        <Input
                          className="ltr flex-1"
                          type={aiShowKey ? "text" : "password"}
                          autoComplete="new-password"
                          value={aiForm.apiKey}
                          onChange={(event) => setAiForm((current) => ({ ...current, apiKey: event.target.value }))}
                          placeholder={selectedAIProvider.keyRequired ? "کلید را وارد کنید؛ برای حفظ کلید فعلی خالی بگذارید" : "برای سرویس محلی معمولاً لازم نیست"}
                        />
                        <button type="button" className="btn btn-sm" onClick={() => setAiShowKey((visible) => !visible)} aria-label={aiShowKey ? "پنهان کردن کلید" : "نمایش کلید"} title={aiShowKey ? "پنهان کردن کلید" : "نمایش کلید"}>
                          <IconEye className="h-4 w-4" />
                        </button>
                      </span>
                      <span className="mt-0.5 block text-[9px] text-[#64748b]">کلید هرگز به مرورگر یا پاسخ API برگردانده نمی‌شود و در پایگاه داده با AES-256-GCM رمزنگاری می‌شود.</span>
                    </label>
                    <label className="block md:col-span-2">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">نام مدل</span>
                      <span className="flex items-center gap-1">
                        <Input
                          className="ltr flex-1"
                          list="ai-model-suggestions"
                          value={aiForm.model}
                          onChange={(event) => setAiForm((current) => ({ ...current, model: event.target.value }))}
                          placeholder="نام مدل را انتخاب یا تایپ کنید"
                        />
                        <Button variant="secondary" disabled={!aiSelectedCredentialId || aiBusy || !selectedAIProvider.supportsModelList} onClick={() => void fetchAIModels()}>
                          {aiBusy ? "…" : "دریافت مدل‌ها"}
                        </Button>
                        <datalist id="ai-model-suggestions">
                          {[...new Set([...selectedAIProvider.suggestedModels, ...aiModels])].map((model) => <option key={model} value={model} />)}
                        </datalist>
                      </span>
                    </label>
                    {(selectedAIProvider.extraFields ?? []).map((field) => (
                      <label key={field.id} className="block">
                        <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">{field.label}{field.required ? " *" : ""}</span>
                        <Input
                          className="ltr"
                          value={aiForm.extra[field.id] ?? ""}
                          placeholder={field.placeholder}
                          onChange={(event) => setAiForm((current) => ({ ...current, extra: { ...current.extra, [field.id]: event.target.value } }))}
                        />
                      </label>
                    ))}
                  </div>

                  {!providerHasVisionModel(selectedAIProvider, aiForm.model) ? (
                    <p className="border border-[#e4c57a] bg-[#fff8df] px-2 py-1 text-[10px] leading-5 text-[#805b12]">
                      هشدار: مدل انتخاب‌شده در فهرست مدل‌های vision این سرویس نیست. برای OCR/VLM یک مدل vision-capable انتخاب کنید؛ امکان تایپ نام مدل سفارشی همچنان فعال است.
                    </p>
                  ) : null}

                  <div className="grid gap-2 md:grid-cols-2">
                    <label className="flex items-start gap-2 border border-[#c7d5e1] bg-white px-2 py-1.5">
                      <input type="checkbox" className="mt-1" checked={aiForm.isActive} onChange={(event) => setAiForm((current) => ({ ...current, isActive: event.target.checked }))} />
                      <span>
                        <b className="block text-[10.5px] text-[#1f3864]">Set as active provider for VLM/OCR</b>
                        <span className="text-[9px] text-[#64748b]">فقط یک Credential فعال است؛ با ذخیره، فعال قبلی جایگزین می‌شود.</span>
                      </span>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[10px] font-bold text-[#1f3864]">Fallback provider (اختیاری)</span>
                      <Select value={aiFallbackId ?? ""} onChange={(event) => setAiFallbackId(event.target.value || null)}>
                        <option value="">بدون جایگزین</option>
                        {aiCredentials.filter((credential) => credential.id !== aiSelectedCredentialId).map((credential) => (
                          <option key={credential.id} value={credential.id}>{credential.providerName} — {credential.name}</option>
                        ))}
                      </Select>
                    </label>
                  </div>

                  {!selectedAIProvider.isLocal ? (
                    <label className="flex items-start gap-2 border border-[#c9a2a2] bg-[#fbeeee] px-2 py-1.5">
                      <input type="checkbox" className="mt-1" checked={aiForm.remoteConsent} onChange={(event) => setAiForm((current) => ({ ...current, remoteConsent: event.target.checked }))} />
                      <span className="text-[10px] leading-5 text-[#8a2020]">
                        تأیید ارسال به سرور عمومی: با فعال بودن این گزینه، برش کادر مشخصات مدارک می‌تواند به سرویس بیرونی ارسال شود. برای قراردادن این Provider به‌عنوان Active الزامی است؛ Providerهای محلی مانند Ollama و LM Studio به این تأیید نیاز ندارند.
                      </span>
                    </label>
                  ) : null}

                  {aiError ? <ErrorBox message={aiError} /> : null}
                  {aiProbe ? (
                    <div className={aiProbe.ok ? "border border-[#9fb48a] bg-[#f0f6e8] px-2 py-1.5" : "border border-[#c9a2a2] bg-[#fbeeee] px-2 py-1.5"} role="status" aria-live="polite">
                      <p className="text-[10.5px] font-bold">{aiProbe.ok ? "✓ اتصال برقرار است — Connection tested successfully" : "✕ اتصال برقرار نشد"}</p>
                      <p className="mt-0.5 text-[9.5px] leading-5">{aiProbe.message}{aiProbe.latencyMs !== null ? ` · زمان پاسخ: ${persianDigits(String(aiProbe.latencyMs))} ms` : ""} · مدل: <span className="ltr font-mono">{aiProbe.model}</span></p>
                      {!aiProbe.ok && aiProbe.details ? (
                        <details open={aiDetailsOpen} onToggle={(event) => setAiDetailsOpen((event.target as HTMLDetailsElement).open)} className="mt-1 text-[9px]">
                          <summary className="cursor-pointer font-bold">جزئیات خطا</summary>
                          <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap rounded border border-[#e4c7c7] bg-white/70 p-1 ltr text-left">{aiProbe.details}</pre>
                        </details>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-2">
                    {aiBusy ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#b9c9d8] border-t-[#1f4e79]" role="status" aria-label="در حال آزمایش اتصال" /> : null}
                    <Button disabled={aiBusy} onClick={() => void saveAiConfig()}>
                      {aiBusy ? "در حال ذخیره و آزمایش…" : "ذخیره و آزمایش اتصال"}
                    </Button>
                    <Button variant="secondary" disabled={aiBusy} onClick={() => void testAiConfig()}>
                      {aiBusy ? "در حال آزمایش…" : "آزمایش اتصال"}
                    </Button>
                    {selectedAICredential?.keySet ? <Button variant="ghost" disabled={aiBusy} onClick={() => void clearAiKey()}>پاک کردن کلید</Button> : null}
                    {selectedAICredential ? <span className={clsx("text-[9.5px] font-bold", selectedAICredential.status === "connected" ? "text-[#16803c]" : selectedAICredential.status === "failed" ? "text-[#b42318]" : "text-[#9a6a13]") }>
                      وضعیت: {selectedAICredential.status === "connected" ? "اتصال برقرار است" : selectedAICredential.status === "failed" ? "آزمایش ناموفق" : "ذخیره‌شده و آزمایش‌نشده"}
                    </span> : null}
                  </div>
                </div>
              </div>
              </fieldset>
            </>
          )}
        </div>
      </Modal>
    </div>
  );
}
