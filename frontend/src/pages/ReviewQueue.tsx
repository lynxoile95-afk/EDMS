import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { ExtractionResult } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Select, Spinner } from "@/components/ui";
import { DecisionBadge, ConfidenceBadge } from "@/components/DecisionBadge";
import { jalaliDateTime, persianDigits, RULE_LABELS } from "@/lib/format";

interface QueueItem extends ExtractionResult {
  file_name: string | null;
}

export function ReviewQueue() {
  const { project } = useProject();
  const [items, setItems] = useState<QueueItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [decision, setDecision] = useState("");
  const [status, setStatus] = useState("pending");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ review_status: status, size: "50" });
      if (decision) params.set("decision", decision);
      const data = await api.get<{ items: QueueItem[]; total: number; counts: Record<string, number> }>(
        `/projects/${project.id}/review-queue?${params.toString()}`,
      );
      setItems(data.items);
      setTotal(data.total);
      setCounts(data.counts ?? {});
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری صف بازبینی ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [project, decision, status]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;

  return (
    <Card className="is-main">
      <CardHeader
        title="صف بازبینی کنترل مدارک (DCC)"
        subtitle="تصمیم هوش مصنوعی پیشنهاد است؛ تأیید نهایی با کارشناس کنترل مدارک است."
        actions={
          <>
            <Select className="w-40 text-xs" value={decision} onChange={(event) => setDecision(event.target.value)}>
              <option value="">همه تصمیم‌ها</option>
              <option value="GREEN">سبز</option>
              <option value="YELLOW">زرد</option>
              <option value="RED">قرمز</option>
            </Select>
            <Select className="w-40 text-xs" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="pending">در انتظار بازبینی</option>
              <option value="approved">تأییدشده</option>
              <option value="rejected">رد‌شده</option>
              <option value="all">همه</option>
            </Select>
            <Button variant="ghost" onClick={() => void load()}>
              بازخوانی
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap gap-2 border-b border-slate-100 px-4 py-2 text-xs">
        <Badge className="bg-slate-100">کل: {persianDigits(total)}</Badge>
        <Badge className="bg-emerald-50 text-emerald-700">سبز: {persianDigits(counts.GREEN ?? 0)}</Badge>
        <Badge className="bg-amber-50 text-amber-800">زرد: {persianDigits(counts.YELLOW ?? 0)}</Badge>
        <Badge className="bg-rose-50 text-rose-700">قرمز: {persianDigits(counts.RED ?? 0)}</Badge>
      </div>

      {loading ? (
        <Spinner />
      ) : error ? (
        <div className="p-4">
          <ErrorBox message={error} onRetry={() => void load()} />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="صف بازبینی خالی است"
          description="پس از بارگذاری مدارک جدید، موارد نیازمند تأیید در این صفحه ظاهر می‌شوند."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>تصمیم</th>
                <th>کد مدرک</th>
                <th>عنوان</th>
                <th>Rev</th>
                <th>اطمینان</th>
                <th>ایرادها</th>
                <th>فایل</th>
                <th>زمان</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <DecisionBadge decision={item.decision} />
                  </td>
                  <td className="ltr font-mono text-xs">{item.doc_code ?? "—"}</td>
                  <td className="max-w-[18rem] truncate text-slate-700" title={item.title ?? ""}>
                    {item.title ?? "—"}
                  </td>
                  <td>{item.rev ?? "—"}</td>
                  <td>
                    <ConfidenceBadge value={item.overall_confidence} />
                  </td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {item.issues.slice(0, 3).map((issue) => (
                        <span
                          key={issue.id}
                          className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600"
                          title={issue.message_fa ?? issue.message}
                        >
                          {issue.rule_code} · {RULE_LABELS[issue.rule_code] ?? ""}
                        </span>
                      ))}
                      {item.issues.length > 3 ? (
                        <span className="text-[10px] text-slate-400">+{persianDigits(item.issues.length - 3)}</span>
                      ) : null}
                    </div>
                  </td>
                  <td className="max-w-[12rem] truncate text-[11px] text-slate-500" title={item.file_name ?? ""}>
                    {item.file_name ?? "—"}
                  </td>
                  <td className="whitespace-nowrap text-[11px] text-slate-500">
                    {jalaliDateTime(item.created_at)}
                  </td>
                  <td>
                    <Link className="text-xs font-medium text-brand-600 hover:underline" to={`/review/${item.id}`}>
                      بازبینی
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
