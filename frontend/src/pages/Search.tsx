import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import { Button, Card, EmptyState, Select, Spinner } from "@/components/ui";

interface SearchStatus {
  enabled: boolean;
  backend: string;
  degraded: boolean;
  semantic_supported: boolean;
  message?: string | null;
}

interface SearchResult {
  chunk_id: string;
  file_id: string;
  project_id: string;
  filename: string;
  page_no: number;
  source_type: "native" | "ocr";
  snippet: string;
  highlight: string;
  score: number;
  keyword_rank: number | null;
  semantic_rank: number | null;
  page_url: string;
}

interface SearchResponse {
  enabled: boolean;
  backend: string;
  degraded: boolean;
  semantic_used: boolean;
  query: string;
  items: SearchResult[];
  total: number;
  page: number;
  limit: number;
}

interface IndexJob {
  id: string;
  file_id: string;
  status: string;
  cursor_page: number;
  total_pages: number;
  failed_pages: unknown[];
  error_message: string | null;
  completed_at: string | null;
}

export function SearchPage() {
  const { user } = useAuth();
  const { project } = useProject();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState<SearchStatus | null>(null);
  const [value, setValue] = useState(params.get("q") ?? "");
  const [sourceType, setSourceType] = useState<"all" | "native" | "ocr">("all");
  const [scope, setScope] = useState<"project" | "all">("project");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [jobs, setJobs] = useState<Record<string, IndexJob[]>>({});
  const [loading, setLoading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState("");

  const query = (params.get("q") ?? "").trim();
  const page = Math.max(1, Number(params.get("page") ?? "1"));

  useEffect(() => {
    let active = true;
    api
      .get<SearchStatus>("/search/status")
      .then((data) => {
        if (active) setStatus(data);
      })
      .catch(() => {
        if (active) setStatus({ enabled: false, backend: "unknown", degraded: true, semantic_supported: false });
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!status?.enabled || !query) {
      setResponse(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    const search = new URLSearchParams({ q: query, page: String(page), limit: "10" });
    if (sourceType !== "all") search.set("source_type", sourceType);
    if (scope === "project" && project) search.set("project_id", project.id);
    api
      .get<SearchResponse>(`/search?${search.toString()}`)
      .then((data) => {
        if (active) setResponse(data);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof ApiError ? reason.detail : "جست‌وجو با خطا روبه‌رو شد.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [page, project, query, scope, sourceType, status?.enabled]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams(params);
    const trimmed = value.trim();
    if (trimmed) {
      next.set("q", trimmed);
      next.delete("page");
    } else {
      next.delete("q");
    }
    setParams(next);
  };

  const changeFilter = (setter: (value: "all" | "native" | "ocr") => void, next: "all" | "native" | "ocr") => {
    setter(next);
    const current = new URLSearchParams(params);
    current.delete("page");
    setParams(current);
  };

  const enqueueProjectReindex = async () => {
    if (!project) return;
    setReindexing(true);
    setError("");
    try {
      await api.post("/search/admin/reindex", { project_id: project.id, force: true, limit: 500 });
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "صف‌گذاری بازفهرست‌سازی ناموفق بود.");
    } finally {
      setReindexing(false);
    }
  };

  const loadFileStatus = async (fileId: string) => {
    try {
      const data = await api.get<IndexJob[]>(`/search/files/${fileId}/status`);
      setJobs((old) => ({ ...old, [fileId]: data }));
    } catch {
      // The result itself remains useful if a status refresh races with a job.
    }
  };

  const enqueueFileReindex = async (fileId: string) => {
    setError("");
    try {
      await api.post(`/search/admin/reindex/${fileId}`);
      await loadFileStatus(fileId);
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "بازفهرست‌سازی فایل ناموفق بود.");
    }
  };

  const pages = useMemo(() => {
    if (!response) return 0;
    return Math.max(1, Math.ceil(response.total / response.limit));
  }, [response]);

  if (status && !status.enabled) {
    return (
      <div dir="rtl">
        <Card className="p-6">
          <h2 className="text-sm font-bold text-[#243842]">جست‌وجوی محتوای مدارک</h2>
          <p className="mt-3 text-[11px] leading-6 text-[#5c6b78]">
            این قابلیت توسط مدیر سامانه فعال نشده است. بارگذاری و گردش فعلی مدارک بدون تغییر ادامه دارد.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-3" dir="rtl">
      <Card className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-bold text-[#243842]">جست‌وجوی محتوای مدارک</h1>
            <p className="mt-1 text-[10.5px] text-[#6c7b87]">
              جست‌وجوی کلیدواژه‌ای و معنایی در متن صفحات PDF با رعایت دسترسی پروژه.
            </p>
          </div>
          {user?.is_superuser && project ? (
            <Button type="button" variant="secondary" disabled={reindexing} onClick={enqueueProjectReindex}>
              {reindexing ? "در حال صف‌گذاری…" : "بازفهرست‌سازی پروژه"}
            </Button>
          ) : null}
        </div>
        <form className="mt-4 flex flex-wrap gap-2" onSubmit={submit}>
          <input
            className="input min-w-[240px] flex-1"
            value={value}
            maxLength={300}
            onChange={(event) => setValue(event.target.value)}
            placeholder="عبارت فارسی یا انگلیسی را وارد کنید…"
            aria-label="عبارت جست‌وجو"
          />
          <Button type="submit" variant="primary" disabled={!value.trim()}>جست‌وجو</Button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px]">
          <label className="flex items-center gap-1">
            <span>منبع:</span>
            <Select
              value={sourceType}
              onChange={(event) => changeFilter(setSourceType, event.target.value as "all" | "native" | "ocr")}
              aria-label="فیلتر منبع متن"
            >
              <option value="all">همه</option>
              <option value="native">لایه متن PDF</option>
              <option value="ocr">OCR</option>
            </Select>
          </label>
          <label className="flex items-center gap-1">
            <span>محدوده:</span>
            <Select
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as "project" | "all");
                const current = new URLSearchParams(params);
                current.delete("page");
                setParams(current);
              }}
              aria-label="محدوده پروژه جست‌وجو"
            >
              <option value="project">پروژه فعال</option>
              <option value="all">همه پروژه‌های مجاز</option>
            </Select>
          </label>
          {status?.degraded ? (
            <span className="rounded border border-[#e4c98d] bg-[#fff9e8] px-2 py-1 text-[#8b6415]">
              حالت Preview: فقط کلیدواژه‌ای و محدود؛ PostgreSQL/pgvector فعال نیست.
            </span>
          ) : null}
        </div>
      </Card>

      {error ? <div className="rounded border border-[#e7b5b5] bg-[#fff5f5] p-3 text-[11px] text-[#9b2c2c]" role="alert">{error}</div> : null}
      {loading ? <Card><Spinner label="در حال جست‌وجو…" /></Card> : null}
      {!loading && query && response && response.items.length === 0 ? (
        <Card><EmptyState title="نتیجه‌ای پیدا نشد" description="عبارت دیگری را امتحان کنید یا فیلتر پروژه را تغییر دهید." /></Card>
      ) : null}
      {!loading && !query ? (
        <Card><EmptyState title="عبارت جست‌وجو را وارد کنید" description="نتایج فقط از پروژه‌ها و مدارکی نمایش داده می‌شوند که به آن‌ها دسترسی دارید." /></Card>
      ) : null}

      {!loading && response?.items.map((item) => (
        <Card key={item.chunk_id} className="p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <a className="text-[12px] font-bold text-[#1f4e79] hover:underline" href={item.page_url} target="_blank" rel="noreferrer">
                {item.filename}
              </a>
              <div className="mt-1 flex flex-wrap gap-1.5 text-[9.5px] text-[#687783]">
                <span className="rounded bg-[#eef4fa] px-1.5 py-0.5">صفحه {item.page_no}</span>
                <span className="rounded bg-[#f2f5f7] px-1.5 py-0.5">{item.source_type === "ocr" ? "OCR" : "لایه متن"}</span>
                {item.semantic_rank ? <span className="rounded bg-[#edf7ef] px-1.5 py-0.5">معنایی</span> : null}
              </div>
            </div>
            <div className="flex gap-1">
              <button className="btn btn-sm" type="button" onClick={() => loadFileStatus(item.file_id)}>وضعیت فهرست</button>
              {user?.is_superuser ? (
                <button className="btn btn-sm" type="button" onClick={() => enqueueFileReindex(item.file_id)}>بازفهرست</button>
              ) : null}
            </div>
          </div>
          <p className="mt-2 text-[11px] leading-6 text-[#334955]" dangerouslySetInnerHTML={{ __html: item.highlight }} />
          {jobs[item.file_id]?.[0] ? (
            <div className="mt-2 border-t border-[#e1e7ec] pt-2 text-[9.5px] text-[#687783]">
              وضعیت فهرست: {jobs[item.file_id][0].status} · صفحه {jobs[item.file_id][0].cursor_page}/{jobs[item.file_id][0].total_pages}
              {jobs[item.file_id][0].failed_pages.length ? " · برخی صفحات خطا داشتند" : ""}
            </div>
          ) : null}
        </Card>
      ))}

      {response && pages > 1 ? (
        <div className="flex items-center justify-center gap-2 py-2 text-[10px]">
          <Button type="button" disabled={page <= 1} onClick={() => setParams(new URLSearchParams({ ...Object.fromEntries(params), page: String(page - 1) }))}>قبلی</Button>
          <span>صفحه {page} از {pages}</span>
          <Button type="button" disabled={page >= pages} onClick={() => setParams(new URLSearchParams({ ...Object.fromEntries(params), page: String(page + 1) }))}>بعدی</Button>
        </div>
      ) : null}
    </div>
  );
}
