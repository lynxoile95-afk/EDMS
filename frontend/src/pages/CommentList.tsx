import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Comment, CommentList as CommentListType } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Field, Select, Spinner } from "@/components/ui";
import { DISPOSITION_LABELS, jalaliDateTime, persianDigits } from "@/lib/format";

const CODE_LABELS: Record<string, string> = {
  "1": "کد ۱ — رد / اصلاح اساسی",
  "2": "کد ۲ — بازنگری و ارسال مجدد",
  "3": "کد ۳ — اطلاع / جزئی",
};

export function CommentList() {
  const { revisionId } = useParams<{ revisionId: string }>();
  const { hasRole } = useAuth();
  const [lists, setLists] = useState<CommentListType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [code, setCode] = useState("2");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [closing, setClosing] = useState<{ id: string; disposition: string } | null>(null);

  const load = useCallback(async () => {
    if (!revisionId) return;
    setLoading(true);
    setError(null);
    try {
      setLists(await api.get<CommentListType[]>(`/revisions/${revisionId}/comment-lists`));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری فهرست نظرها ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [revisionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const addComment = async () => {
    if (!revisionId || text.trim().length < 2) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.post(`/revisions/${revisionId}/comments`, {
        revision_id: revisionId,
        text: text.trim(),
        code,
      });
      setText("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "ثبت نظر ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const respond = async (comment: Comment) => {
    const response = window.prompt("پاسخ خود را وارد کنید:");
    if (!response) return;
    try {
      await api.post(`/comments/${comment.id}/respond`, { response_text: response });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "ثبت پاسخ ناموفق بود.");
    }
  };

  const close = async (comment: Comment, disposition: string) => {
    // The coordinator records the disposition the reference system prints, so the
    // contractor learns *why* the comment is closed, not only that it was.
    try {
      await api.post(`/comments/${comment.id}/close`, { disposition });
      setClosing(null);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "بستن نظر ناموفق بود.");
    }
  };

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} onRetry={() => void load()} />;

  const comments = lists.flatMap((list) => list.comments);

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title="فهرست نظرها (Comment List)"
          subtitle={`${persianDigits(comments.length)} نظر ثبت شده برای این بازنگری`}
          actions={
            <Link className="text-xs text-brand-600 hover:underline" to="/transmittals">
              بازگشت به برگه‌های ارسال
            </Link>
          }
        />
        {actionError ? (
          <div className="px-4 pt-3">
            <ErrorBox message={actionError} />
          </div>
        ) : null}
        {comments.length === 0 ? (
          <EmptyState title="نظری ثبت نشده است" description="کارشناسان می‌توانند نظر خود را روی این مدرک ثبت کنند." />
        ) : (
          <div className="divide-y divide-slate-100">
            {comments.map((comment) => (
              <div key={comment.id} className="p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className="bg-slate-100">#{persianDigits(comment.seq_no)}</Badge>
                  <Badge
                    className={
                      comment.code === "1"
                        ? "bg-rose-100 text-rose-800"
                        : comment.code === "2"
                          ? "bg-amber-100 text-amber-900"
                          : "bg-sky-100 text-sky-800"
                    }
                  >
                    {CODE_LABELS[comment.code] ?? `کد ${comment.code}`}
                  </Badge>
                  <Badge
                    className={
                      comment.status === "closed"
                        ? "bg-emerald-100 text-emerald-800"
                        : comment.status === "answered"
                          ? "bg-sky-100 text-sky-800"
                          : "bg-slate-100 text-slate-600"
                    }
                  >
                    {comment.status === "closed" ? "بسته" : comment.status === "answered" ? "پاسخ داده‌شده" : "باز"}
                  </Badge>
                  {comment.disposition ? (
                    <Badge className="bg-violet-100 text-violet-800">
                      {DISPOSITION_LABELS[comment.disposition] ?? comment.disposition}
                    </Badge>
                  ) : null}
                  <span className="text-[11px] text-slate-400">
                    {comment.author_name ?? "—"} · {jalaliDateTime(comment.created_at)}
                    {comment.page_no ? ` · صفحه ${persianDigits(comment.page_no)}` : ""}
                  </span>
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-800">{comment.text}</p>
                {comment.response_text ? (
                  <div className="mt-2 rounded-lg bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-medium text-slate-500">پاسخ پیمانکار</p>
                    <p className="mt-1 text-xs leading-6 text-slate-700">{comment.response_text}</p>
                    <p className="mt-1 text-[10px] text-slate-400">{jalaliDateTime(comment.responded_at)}</p>
                  </div>
                ) : null}
                <div className="mt-2 flex gap-2">
                  {comment.status !== "closed" ? (
                    <Button variant="secondary" className="px-2 py-1 text-[11px]" onClick={() => void respond(comment)}>
                      ثبت پاسخ
                    </Button>
                  ) : null}
                  {comment.status === "answered" && (hasRole("reviewer") || hasRole("dcc")) ? (
                    closing?.id === comment.id ? (
                      <span className="flex items-center gap-1">
                        <select
                          aria-label="وضعیت بستن نظر"
                          className="h-7 rounded border border-slate-300 bg-white px-1 text-[11px]"
                          value={closing.disposition}
                          onChange={(event) => setClosing({ id: comment.id, disposition: event.target.value })}
                        >
                          {Object.entries(DISPOSITION_LABELS).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <Button
                          variant="ghost"
                          className="px-2 py-1 text-[11px]"
                          onClick={() => void close(comment, closing.disposition)}
                        >
                          ثبت
                        </Button>
                        <Button variant="ghost" className="px-2 py-1 text-[11px]" onClick={() => setClosing(null)}>
                          انصراف
                        </Button>
                      </span>
                    ) : (
                      <Button
                        variant="ghost"
                        className="px-2 py-1 text-[11px]"
                        onClick={() => setClosing({ id: comment.id, disposition: comment.disposition ?? "accepted" })}
                      >
                        بستن نظر
                      </Button>
                    )
                  ) : null}
                  {comment.status !== "closed" ? (
                    <Button
                      variant="ghost"
                      className="px-2 py-1 text-[11px]"
                      onClick={() => api.download(`/revisions/${comment.revision_id}/comments/export`, "comments.csv")}
                    >
                      خروجی
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {hasRole("reviewer") || hasRole("dcc") ? (
        <Card>
          <CardHeader title="ثبت نظر جدید" subtitle="کارشناس رشته یا کنترل مدارک" />
          <div className="space-y-3 p-4">
            <Field label="متن نظر">
              <textarea
                className="input min-h-[80px]"
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="مثلاً: ابعاد در پلان با دیتاشیت هم‌خوان نیست."
              />
            </Field>
            <Field label="کد نظر">
              <Select value={code} onChange={(event) => setCode(event.target.value)}>
                {Object.entries(CODE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
            <Button disabled={busy || text.trim().length < 2} onClick={() => void addComment()}>
              ثبت نظر
            </Button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
