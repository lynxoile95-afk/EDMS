import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import { Button, Card, EmptyState, Spinner } from "@/components/ui";

const NO_ANSWER = "بر اساس مدارک موجود پاسخی یافت نشد.";

interface QAStatus {
  enabled: boolean;
  backend: string;
  advanced_supported: boolean;
  message?: string | null;
  embedding_model: string;
  embedding_dimensions: number;
  indexed_files: number;
}

interface QADocument {
  id: string;
  project_id: string;
  filename: string;
  mime_type: string;
  page_count: number;
  indexed: boolean;
  updated_at?: string | null;
}

interface QASource {
  chunk_id: string;
  file_id: string | null;
  filename: string;
  page_no?: number | null;
  source: string;
  excerpt: string;
  score: number;
  page_url: string;
}

interface QAMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: QASource[];
  created_at?: string | null;
}

interface QAResponse {
  session_id: string;
  answer: string;
  sources: QASource[];
  semantic_used: boolean;
  history: QAMessage[];
}

interface Job {
  id: string;
  file_id: string;
  status: string;
  cursor_chunk: number;
  total_chunks: number;
}

export function FileQAPage() {
  const { user } = useAuth();
  const { project } = useProject();
  const [status, setStatus] = useState<QAStatus | null>(null);
  const [documents, setDocuments] = useState<QADocument[]>([]);
  const [scope, setScope] = useState<"project" | "all">("project");
  const [documentQuery, setDocumentQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [history, setHistory] = useState<QAMessage[]>([]);
  const [temporaryFile, setTemporaryFile] = useState<File | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState("");

  const loadDocuments = async (query = documentQuery) => {
    setDocumentsLoading(true);
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (scope === "project" && project) params.set("project_id", project.id);
      const rows = await api.get<QADocument[]>(`/file-qa/documents?${params.toString()}`);
      setDocuments(rows);
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "فهرست فایل‌های قابل پرسش دریافت نشد.");
    } finally {
      setDocumentsLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    api.get<QAStatus>("/file-qa/status")
      .then((value) => {
        if (active) setStatus(value);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason.detail : "وضعیت پرسش از فایل دریافت نشد.");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!status?.enabled) return;
    void loadDocuments("");
    // Project selection is part of the ACL scope, so a changed project must
    // clear stale selections rather than silently asking across projects.
    setSelected([]);
  }, [status?.enabled, project?.id, scope]);

  const visibleDocuments = useMemo(() => documents, [documents]);

  const toggleDocument = (id: string) => {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= 10) return current;
      return [...current, id];
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError("");
    try {
      let response: QAResponse;
      if (temporaryFile) {
        const form = new FormData();
        form.append("question", trimmed);
        form.append("file", temporaryFile, temporaryFile.name);
        selected.forEach((id) => form.append("file_ids", id));
        if (scope === "project" && project) form.append("project_id", project.id);
        if (sessionId) form.append("session_id", sessionId);
        response = await api.postForm<QAResponse>("/file-qa/ask", form);
      } else {
        response = await api.post<QAResponse>("/file-qa/ask", {
          question: trimmed,
          file_ids: selected,
          project_id: scope === "project" ? project?.id ?? null : null,
          session_id: sessionId,
        });
      }
      setSessionId(response.session_id);
      setHistory(response.history);
      setQuestion("");
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "پاسخ‌گویی از فایل‌ها ناموفق بود.");
    } finally {
      setLoading(false);
    }
  };

  const clearHistory = async () => {
    setError("");
    try {
      await api.post("/file-qa/clear", { session_id: sessionId });
      setSessionId(null);
      setHistory([]);
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "پاک کردن تاریخچه ناموفق بود.");
    }
  };

  const rebuildSelected = async () => {
    if (!user?.is_superuser || selected.length === 0) return;
    setRebuilding(true);
    setError("");
    try {
      const result = await api.post<Job[]>("/file-qa/admin/rebuild", {
        file_ids: selected,
        project_id: scope === "project" ? project?.id ?? null : null,
        force: true,
      });
      setJobs(result);
      await loadDocuments();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "بازفهرست‌سازی فایل‌ها ناموفق بود.");
    } finally {
      setRebuilding(false);
    }
  };

  if (status && !status.enabled) {
    return (
      <div dir="rtl">
        <Card className="p-6">
          <h1 className="text-sm font-bold text-[#243842]">پرسش و پاسخ از فایل‌ها</h1>
          <p className="mt-3 text-[11px] leading-6 text-[#5c6b78]">
            قابلیت پرسش و پاسخ از فایل‌ها فعال نشده است. بارگذاری و گردش مدارک بدون تغییر ادامه دارد.
          </p>
        </Card>
      </div>
    );
  }

  if (!status) return <Card><Spinner label="در حال بررسی قابلیت پرسش از فایل‌ها…" /></Card>;

  return (
    <div className="space-y-3" dir="rtl">
      <Card className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-bold text-[#243842]">پرسش و پاسخ از فایل‌ها</h1>
            <p className="mt-1 text-[10.5px] text-[#6c7b87]">
              پاسخ فقط از مدارک قابل دسترسی شما و قطعه‌های بازیابی‌شده تولید می‌شود.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-[9.5px] text-[#687783]">
            <span className="rounded bg-[#eef4fa] px-2 py-1">مدل برداری: {status.embedding_model}</span>
            <span className="rounded bg-[#eef4fa] px-2 py-1">{status.indexed_files} فایل فهرست شده</span>
          </div>
        </div>
        {status.message ? (
          <p className="mt-3 border border-[#e4c98d] bg-[#fff9e8] px-2 py-1.5 text-[10px] text-[#8b6415]" role="status">
            {status.message}
          </p>
        ) : null}
        {error ? <p className="mt-3 border border-[#e7b5b5] bg-[#fff5f5] p-2 text-[10.5px] text-[#9b2c2c]" role="alert">{error}</p> : null}
      </Card>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_310px]">
        <Card className="p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-xs font-bold text-[#243842]">گفت‌وگو</h2>
            <Button type="button" variant="ghost" disabled={!sessionId && history.length === 0} onClick={clearHistory}>پاک کردن تاریخچه</Button>
          </div>
          <div className="min-h-[280px] space-y-3 border-y border-[#e1e7ec] py-3">
            {history.length === 0 ? (
              <EmptyState title="هنوز پرسشی ثبت نشده است" description="یک یا چند فایل را انتخاب کنید یا یک فایل موقت PDF/DOCX اضافه کنید." />
            ) : history.map((message) => (
              <div key={message.id} className={message.role === "user" ? "mr-8" : "ml-8"}>
                <div className={message.role === "user" ? "bg-[#eef4fa]" : "bg-[#f7f8f9]"}>
                  <p className="border-b border-[#e1e7ec] px-2 py-1 text-[9px] font-bold text-[#687783]">
                    {message.role === "user" ? "پرسش شما" : "پاسخ مستند"}
                  </p>
                  <p className="whitespace-pre-wrap px-2 py-2 text-[11px] leading-6 text-[#334955]">{message.content}</p>
                </div>
                {message.role === "assistant" && message.sources.length > 0 ? (
                  <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
                    {message.sources.map((source) => (
                      <SourceCard key={source.chunk_id} source={source} />
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
            {loading ? <Spinner label="در حال بازیابی و تولید پاسخ مستند…" /> : null}
          </div>
          <form className="mt-3 flex flex-col gap-2" onSubmit={submit}>
            <textarea
              className="input min-h-[72px] resize-y"
              value={question}
              maxLength={1000}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="سؤال خود را درباره مدارک انتخاب‌شده بنویسید…"
              aria-label="پرسش از فایل‌ها"
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label className="btn btn-secondary cursor-pointer text-[10px]">
                افزودن فایل موقت
                <input
                  className="hidden"
                  type="file"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={(event) => setTemporaryFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <span className="text-[9.5px] text-[#687783]">
                {temporaryFile ? temporaryFile.name : `${selected.length} فایل EDMS انتخاب شده`}
              </span>
              <Button type="submit" variant="primary" disabled={loading || !question.trim()}>پرسیدن</Button>
            </div>
          </form>
        </Card>

        <Card className="p-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs font-bold text-[#243842]">انتخاب مدارک</h2>
            {user?.is_superuser ? (
              <Button type="button" disabled={rebuilding || selected.length === 0} onClick={rebuildSelected}>
                {rebuilding ? "در حال صف‌گذاری…" : "بازفهرست منتخب"}
              </Button>
            ) : null}
          </div>
          <label className="mt-3 block">
            <span className="label">محدوده جست‌وجوی فایل</span>
            <select
              className="input w-full"
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as "project" | "all");
                setSelected([]);
              }}
              aria-label="محدوده جست‌وجوی فایل"
            >
              <option value="project">پروژه فعال</option>
              <option value="all">همه پروژه‌های مجاز</option>
            </select>
          </label>
          <input
            className="input mt-3 w-full"
            value={documentQuery}
            onChange={(event) => {
              setDocumentQuery(event.target.value);
              void loadDocuments(event.target.value);
            }}
            placeholder="جست‌وجوی نام فایل…"
            aria-label="جست‌وجوی فایل‌های قابل پرسش"
          />
          {documentsLoading ? <Spinner label="در حال دریافت فایل‌ها…" /> : null}
          <div className="mt-2 max-h-[420px] space-y-1 overflow-y-auto">
            {!documentsLoading && visibleDocuments.length === 0 ? (
              <p className="px-2 py-5 text-center text-[10px] text-[#7a8794]">فایل PDF یا DOCX قابل دسترسی پیدا نشد.</p>
            ) : visibleDocuments.map((document) => (
              <label key={document.id} className="flex cursor-pointer items-start gap-2 border border-transparent px-2 py-1.5 hover:border-[#c6d5e1] hover:bg-[#f5f9fc]">
                <input type="checkbox" checked={selected.includes(document.id)} onChange={() => toggleDocument(document.id)} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[10.5px] text-[#334955]" title={document.filename}>{document.filename}</span>
                  <span className="mt-0.5 block text-[9px] text-[#7a8794]">
                    {document.indexed ? "فهرست شده" : "هنوز فهرست نشده"} · {document.page_count || 0} صفحه
                  </span>
                </span>
              </label>
            ))}
          </div>
          {jobs.length > 0 ? (
            <div className="mt-3 border-t border-[#e1e7ec] pt-2 text-[9px] text-[#687783]">
              {jobs.map((job) => <p key={job.id}>وضعیت {job.status} · {job.cursor_chunk}/{job.total_chunks}</p>)}
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

function SourceCard({ source }: { source: QASource }) {
  return (
    <div className="border border-[#dbe2e9] bg-white p-2 text-[9.5px] text-[#52636f]">
      <div className="flex items-start justify-between gap-1">
        {source.page_url ? (
          <a className="min-w-0 truncate font-bold text-[#1f4e79] hover:underline" href={source.page_url} target="_blank" rel="noreferrer">
            {source.filename}
          </a>
        ) : <span className="min-w-0 truncate font-bold">{source.filename}</span>}
        <span className="shrink-0">{source.page_no ? `صفحه ${source.page_no}` : source.source}</span>
      </div>
      <p className="mt-1 line-clamp-3 leading-5">{source.excerpt}</p>
    </div>
  );
}

export { NO_ANSWER };
