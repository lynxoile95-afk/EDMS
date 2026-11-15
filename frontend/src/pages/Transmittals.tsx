import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { Transmittal } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Select, Spinner } from "@/components/ui";
import { PURPOSE_LABELS, STAGE_LABELS, STATUS_LABELS, jalali, persianDigits } from "@/lib/format";

const STATUS_CLASS: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  pending_approval: "bg-amber-100 text-amber-900",
  issued: "bg-brand-100 text-brand-800",
  partially_acknowledged: "bg-sky-100 text-sky-800",
  acknowledged: "bg-emerald-100 text-emerald-800",
  closed: "bg-slate-100 text-slate-500",
  cancelled: "bg-rose-100 text-rose-800",
};

export function Transmittals() {
  const { project } = useProject();
  const [items, setItems] = useState<Transmittal[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("");
  /**
   * کارتابل من — only the sheets waiting on the signed-in user.
   * Seeded from `?mine=1` so the menu entry can open straight into the inbox.
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const [mine, setMine] = useState(searchParams.get("mine") === "1");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ size: "50" });
      if (status) params.set("status", status);
      if (mine) params.set("mine", "true");
      const data = await api.get<{ items: Transmittal[]; total: number }>(
        `/projects/${project.id}/transmittals?${params.toString()}`,
      );
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری برگه‌های ارسال ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [project, status, mine]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;

  return (
    <Card className="is-main">
      <CardHeader
        title="برگه‌های ارسال (Transmittals)"
        subtitle={`${persianDigits(total)} برگه ثبت شده`}
        actions={
          <>
            <Select className="w-44 text-xs" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">همه وضعیت‌ها</option>
              <option value="draft">پیش‌نویس</option>
              <option value="pending_approval">در انتظار تأیید</option>
              <option value="issued">صادرشده</option>
              <option value="acknowledged">تأییدشده</option>
              <option value="cancelled">ابطال‌شده</option>
            </Select>
            <label className="flex cursor-pointer items-center gap-1 text-[11px] font-bold text-[#20365c]">
              <input
                type="checkbox"
                checked={mine}
                onChange={(event) => {
                  const next = event.target.checked;
                  setMine(next);
                  const params = new URLSearchParams(searchParams);
                  if (next) params.set("mine", "1");
                  else params.delete("mine");
                  setSearchParams(params, { replace: true });
                }}
              />
              کارتابل من
            </label>
            <Button variant="ghost" onClick={() => void load()}>
              بازخوانی
            </Button>
          </>
        }
      />

      {loading ? (
        <Spinner />
      ) : error ? (
        <div className="p-4">
          <ErrorBox message={error} onRetry={() => void load()} />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title={mine ? "در کارتابل شما چیزی نیست" : "برگه ارسالی وجود ندارد"}
          description={
            mine
              ? "برگه‌ای که منتظر اقدام شما باشد وجود ندارد؛ تیک «کارتابل من» را بردارید تا همه برگه‌ها را ببینید."
              : "با تأیید مدارک در صف بازبینی و انتخاب «ساخت برگه ارسال»، اولین برگه ساخته می‌شود."
          }
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>شماره</th>
                <th>هدف</th>
                <th>نوع</th>
                <th>وضعیت</th>
                <th>مرحله گردش</th>
                <th>تعداد مدارک</th>
                <th>گیرندگان</th>
                <th>تأییدیه</th>
                <th>مهلت پاسخ</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="ltr font-mono text-xs">{item.number ?? "— پیش‌نویس —"}</td>
                  <td className="text-xs">{PURPOSE_LABELS[item.purpose] ?? item.purpose}</td>
                  <td className="text-xs">{item.type === "external" ? "خارجی" : "داخلی"}</td>
                  <td>
                    <Badge className={STATUS_CLASS[item.status] ?? "bg-slate-100"}>
                      {STATUS_LABELS[item.status] ?? item.status}
                    </Badge>
                  </td>
                  <td className="text-xs">
                    {item.stage ? (STAGE_LABELS[item.stage] ?? item.stage) : "—"}
                  </td>
                  <td>{persianDigits(item.line_count)}</td>
                  <td>{persianDigits(item.recipient_count)}</td>
                  <td>
                    {persianDigits(item.acknowledged_count)} / {persianDigits(item.recipient_count)}
                  </td>
                  <td className="text-xs text-slate-600">{item.due_date ? jalali(item.due_date) : "—"}</td>
                  <td>
                    <Link className="text-xs text-brand-600 hover:underline" to={`/transmittals/${item.id}`}>
                      مشاهده
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
