import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { MarkupEditor } from "@/components/MarkupEditor";
import { ErrorBox, Button, Modal, Spinner } from "@/components/ui";
import { PdfViewer } from "@/components/PdfViewer";
import { Window } from "@/components/window";
import { api, ApiError } from "@/lib/api";
import {
  createMarkupSession,
  downloadMarkupOutput,
  getMarkupSession,
  replyToMarkup,
  saveMarkupAnnotations,
  submitMarkupSession,
  toMarkupAnnotationInputs,
} from "@/lib/markups";
import type { MarkupAnnotation, MarkupReplyStatus, MarkupSession } from "@/lib/types";
import { persianDigits } from "@/lib/format";

const LOCAL_PREFIX = "edms-markup-draft:";

function localKey(sessionId: string) {
  return `${LOCAL_PREFIX}${sessionId}`;
}

function statusLabel(status: MarkupReplyStatus): string {
  return {
    open: "باز",
    closed: "بسته",
    accepted: "پذیرفته‌شده",
    rejected: "ردشده",
    resolved: "حل‌شده",
  }[status];
}

export function MarkupSessionPage() {
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams<{ sessionId: string }>();
  const [search] = useSearchParams();
  const [session, setSession] = useState<MarkupSession | null>(null);
  const [annotations, setAnnotations] = useState<MarkupAnnotation[]>([]);
  const [pdfData, setPdfData] = useState<ArrayBuffer | null>(null);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [replyDrafts, setReplyDrafts] = useState<Record<string, string>>({});
  const [replyStatuses, setReplyStatuses] = useState<Record<string, Extract<MarkupReplyStatus, "accepted" | "rejected" | "resolved">>>({});
  const [savedOutputs, setSavedOutputs] = useState<{ attachment_id: string; kind: "pdf" | "sheet"; filename: string }[]>([]);
  const [commentSheetFilename, setCommentSheetFilename] = useState<string | null>(null);
  const [commentSheetData, setCommentSheetData] = useState<ArrayBuffer | null>(null);
  const [commentSheetLoading, setCommentSheetLoading] = useState(false);
  const [commentSheetError, setCommentSheetError] = useState<string | null>(null);

  const isNew = !routeSessionId || routeSessionId === "new";
  const queryTransmittalId = search.get("transmittal_id") ?? "";
  const queryFileId = search.get("file_id") ?? "";

  const loadSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = isNew
        ? await createMarkupSession(queryTransmittalId, queryFileId)
        : await getMarkupSession(routeSessionId);
      setSession(loaded);
      setAnnotations(loaded.annotations);
      setSavedOutputs(loaded.outputs);
      if (isNew) {
        navigate(`/markups/${loaded.id}`, { replace: true });
      }
      try {
        const local = localStorage.getItem(localKey(loaded.id));
        if (loaded.can_edit && local) {
          const parsed = JSON.parse(local) as { annotations?: MarkupAnnotation[]; version?: number };
          if (Array.isArray(parsed.annotations) && parsed.annotations.length && (parsed.version ?? 0) >= loaded.draft_version) {
            setAnnotations(parsed.annotations);
            setDirty(true);
            setNotice("نسخه محلی ذخیره‌نشده بازیابی شد؛ ذخیره خودکار فعال است و با ثبت نهایی ارسال می‌شود.");
          }
        }
      } catch {
        // A malformed browser backup must never prevent the server session from opening.
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری جلسه بازبینی ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [isNew, navigate, queryFileId, queryTransmittalId, routeSessionId]);

  useEffect(() => {
    if (isNew && (!queryTransmittalId || !queryFileId)) {
      setError("شناسه ترانسمیتال و پیوست PDF برای شروع بازبینی لازم است.");
      setLoading(false);
      return;
    }
    void loadSession();
  }, [isNew, loadSession, queryFileId, queryTransmittalId]);

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    setSourceLoading(true);
    setError(null);
    void api
      .blobData(`/transmittals/${session.transmittal_id}/attachments/${session.file_id}/preview`)
      .then((blob) => blob.arrayBuffer())
      .then((data) => {
        if (!cancelled) setPdfData(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.detail : "دریافت فایل اصلی PDF ناموفق بود.");
      })
      .finally(() => {
        if (!cancelled) setSourceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (!session || !session.can_edit || !dirty) return;
    try {
      localStorage.setItem(localKey(session.id), JSON.stringify({ version: session.draft_version + 1, annotations }));
    } catch {
      setNotice("ذخیره محلی مرورگر در دسترس نیست؛ اتصال را حفظ کنید و ثبت نهایی را بدون بستن صفحه انجام دهید.");
    }
  }, [annotations, dirty, session]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "تغییرات ذخیره‌نشده دارید.";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = useCallback(async () => {
    if (!session || !session.can_edit || !dirty) return true;
    setBusy(true);
    setError(null);
    try {
      const saved = await saveMarkupAnnotations(
        session.id,
        toMarkupAnnotationInputs(annotations, session.annotations),
        session.draft_version,
      );
      setSession(saved);
      setAnnotations(saved.annotations);
      setDirty(false);
      setNotice("پیش‌نویس ذخیره شد.");
      localStorage.removeItem(localKey(session.id));
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "ذخیره پیش‌نویس ناموفق بود؛ دوباره تلاش کنید.");
      return false;
    } finally {
      setBusy(false);
    }
  }, [annotations, dirty, session]);

  useEffect(() => {
    if (!dirty || !session?.can_edit) return;
    const timer = window.setTimeout(() => void save(), 1600);
    return () => window.clearTimeout(timer);
  }, [dirty, save, session?.can_edit]);

  const changeAnnotations = (next: MarkupAnnotation[]) => {
    setAnnotations(next);
    setDirty(true);
    setNotice("تغییرات در حافظه نگه‌داری شد؛ ذخیره خودکار فعال است.");
    if (session?.can_edit) {
      try {
        // Write immediately as well as from the effect below so a sudden tab
        // close cannot race React's next render.
        localStorage.setItem(
          localKey(session.id),
          JSON.stringify({ version: session.draft_version + 1, annotations: next }),
        );
      } catch {
        setNotice("ذخیره محلی مرورگر در دسترس نیست؛ اتصال را حفظ کنید و ثبت نهایی را بدون بستن صفحه انجام دهید.");
      }
    }
  };

  const closeCommentSheet = () => {
    setCommentSheetFilename(null);
    setCommentSheetData(null);
    setCommentSheetLoading(false);
    setCommentSheetError(null);
  };

  const openCommentSheet = async (
    transmittalId: string,
    output: { attachment_id: string; filename: string },
  ) => {
    setCommentSheetFilename(output.filename);
    setCommentSheetData(null);
    setCommentSheetError(null);
    setCommentSheetLoading(true);
    try {
      const blob = await api.blobData(`/transmittals/${transmittalId}/attachments/${output.attachment_id}/preview`);
      setCommentSheetData(await blob.arrayBuffer());
    } catch (err) {
      setCommentSheetError(err instanceof ApiError ? err.detail : "بازکردن Comment Sheet ناموفق بود؛ فایل را دریافت کنید.");
    } finally {
      setCommentSheetLoading(false);
    }
  };

  const submit = async () => {
    if (!session || !session.can_edit) return;
    if (dirty && !(await save())) return;
    if (!window.confirm("پس از ثبت نهایی، جلسه و PDF نشانه‌گذاری‌شده قفل می‌شوند. ادامه می‌دهید؟")) return;
    setBusy(true);
    setError(null);
    try {
      const result = await submitMarkupSession(session.id);
      setSession(result.session);
      setAnnotations(result.session.annotations);
      setSavedOutputs(result.outputs);
      setDirty(false);
      localStorage.removeItem(localKey(session.id));
      setNotice(`ثبت نهایی انجام شد؛ ${persianDigits(result.annotation_count)} نظر در Comment Sheet ثبت شد.`);
      const commentSheet = result.outputs.find((output) => output.kind === "sheet");
      if (commentSheet) await openCommentSheet(result.session.transmittal_id, commentSheet);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "ثبت نهایی و تولید خروجی ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const reply = async (annotation: MarkupAnnotation, status: MarkupReplyStatus = "resolved") => {
    if (!session?.can_reply) return;
    const text = (replyDrafts[annotation.id] ?? "").trim();
    if (!text) return;
    setBusy(true);
    try {
      const updated = await replyToMarkup(session.id, annotation.number, text, status);
      setSession(updated);
      setAnnotations(updated.annotations);
      setReplyDrafts((current) => ({ ...current, [annotation.id]: "" }));
      setNotice(`پاسخ نظر ${persianDigits(annotation.number)} ثبت شد.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "ثبت پاسخ ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    if (dirty && !window.confirm("تغییرات ذخیره‌نشده از بین می‌رود. خارج می‌شوید؟")) return;
    navigate(`/transmittals/${session?.transmittal_id ?? queryTransmittalId}`);
  };

  const downloadOutput = async (attachmentId: string, filename: string) => {
    if (!session) return;
    setBusy(true);
    try {
      await downloadMarkupOutput(session.transmittal_id, attachmentId, filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "دریافت خروجی ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const sortedAnnotations = useMemo(() => [...annotations].sort((a, b) => a.number - b.number), [annotations]);

  if (loading) return <Spinner label="در حال آماده‌سازی جلسه بازبینی…" />;
  if (!session) return <div className="p-3"><ErrorBox message={error ?? "جلسه بازبینی پیدا نشد."} /></div>;

  return (
    <>
      <Window
      title={<span className="ltr" dir="ltr">PDF Review Markup — {session.original_filename}</span>}
      subtitle={session.document_code ? `${session.document_code} · Rev ${session.revision_label ?? "—"}` : undefined}
      onClose={close}
      className="h-[calc(100vh-1rem)]"
      actions={<span className="px-2 text-[10px] text-[#5c6b78]">بازبینی آفلاین / RTL</span>}
    >
      <div className="flex h-[calc(100vh-4.5rem)] min-h-0 flex-col gap-1">
        {error ? <ErrorBox message={error} /> : null}
        {notice ? <div className="border border-[#a8c6a8] bg-[#eef8ee] px-2 py-1 text-[10px] text-[#356d39]" role="status">{notice}</div> : null}
        <div className="flex flex-wrap items-center justify-between gap-2 border border-[#a8a284] bg-[#fbfaf2] px-2 py-1 text-[10px]">
          <div className="flex items-center gap-2">
            <span className="font-bold text-[#20365c]">وضعیت: {session.status === "draft" ? "پیش‌نویس شخصی" : "ثبت نهایی‌شده و قفل"}</span>
            <span className="text-[#5c6b78]">نسخه {persianDigits(session.draft_version)}</span>
            {dirty ? <span className="font-bold text-[#9a3d32]">● تغییر ذخیره‌نشده</span> : <span className="text-[#3f8f45]">● ذخیره‌شده</span>}
          </div>
          <div className="flex items-center gap-1">
            {session.status === "draft" && session.can_edit ? <Button variant="primary" className="btn-sm" disabled={busy} onClick={() => void submit()}>ثبت نهایی و تولید Comment</Button> : null}
            {savedOutputs.map((output) => (
              <Button key={output.attachment_id} className="btn-sm" disabled={busy} onClick={() => void downloadOutput(output.attachment_id, output.filename)}>
                {output.kind === "pdf" ? "دریافت PDF نشانه‌گذاری" : "دریافت Comment Sheet"}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-1 lg:flex-row" dir="ltr">
          <div className="min-h-0 min-w-0 flex-1">
            {sourceLoading || !pdfData ? <Spinner label="در حال دریافت PDF اصلی…" /> : (
              <MarkupEditor
                data={pdfData}
                annotations={annotations}
                onChange={changeAnnotations}
                readOnly={!session.can_edit}
                initialPage={page}
                onPageChange={setPage}
                selectedId={selectedId}
                onSelectedIdChange={setSelectedId}
              />
            )}
          </div>
          <aside className="flex max-h-[40vh] w-full shrink-0 flex-col border border-[#a8a284] bg-white text-[10px] lg:max-h-none lg:w-[22rem]" dir="rtl">
            <div className="flex items-center justify-between border-b border-[#a8a284] bg-[#eef3f8] px-2 py-1 font-bold text-[#1f3864]">
              <span>فهرست نظرات</span><span>{persianDigits(annotations.length)}</span>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {sortedAnnotations.length === 0 ? <p className="p-3 text-center text-[#5c6b78]">هنوز نشانه‌ای ثبت نشده است.</p> : sortedAnnotations.map((annotation) => (
                <div key={annotation.id} className={`border-b border-[#e6e1c8] p-2 ${selectedId === annotation.id ? "bg-[#fffbe4]" : ""}`}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between text-right font-bold text-[#1f4e79] hover:underline"
                    onClick={() => { setPage(annotation.page_no); setSelectedId(annotation.id); }}
                  >
                    <span>{labelText(annotation)}</span><span>صفحه {persianDigits(annotation.page_no)}</span>
                  </button>
                  <p className="mt-1 whitespace-pre-wrap break-words text-[#33475b]">{annotation.text || "نشانه‌گذاری بدون متن"}</p>
                  <div className="mt-1 flex items-center justify-between text-[9px] text-[#7a8794]">
                    <span>{annotation.author_name ?? session.author_name ?? "—"}</span>
                    <span>{statusLabel(annotation.reply_status)}</span>
                  </div>
                  {annotation.reply_text ? <div className="mt-1 border-r-2 border-[#5b9bd5] bg-[#f1f6fb] px-1.5 py-1">{annotation.reply_text}</div> : null}
                  {session.can_reply && session.status === "submitted" ? (
                    <div className="mt-1 flex gap-1">
                      <input
                        className="input min-w-0 flex-1 py-0 text-[9px]"
                        value={replyDrafts[annotation.id] ?? ""}
                        onChange={(event) => setReplyDrafts((current) => ({ ...current, [annotation.id]: event.target.value }))}
                        placeholder="پاسخ مالک…"
                      />
                      <select
                        className="input w-auto py-0 text-[9px]"
                        value={replyStatuses[annotation.id] ?? "resolved"}
                        onChange={(event) => setReplyStatuses((current) => ({ ...current, [annotation.id]: event.target.value as Extract<MarkupReplyStatus, "accepted" | "rejected" | "resolved"> }))}
                        aria-label={`وضعیت پاسخ نظر ${annotation.number}`}
                      >
                        <option value="accepted">Accepted — پذیرفته</option>
                        <option value="rejected">Rejected — رد</option>
                        <option value="resolved">Resolved — حل‌شده</option>
                      </select>
                      <Button className="btn-sm" disabled={busy || !(replyDrafts[annotation.id] ?? "").trim()} onClick={() => void reply(annotation, replyStatuses[annotation.id] ?? "resolved")}>ثبت</Button>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </aside>
        </div>
      </div>
    </Window>
    <Modal
      open={Boolean(commentSheetFilename)}
      title={commentSheetFilename ? `Comment Sheet — ${commentSheetFilename}` : "Comment Sheet"}
      onClose={closeCommentSheet}
      width="max-w-6xl"
      footer={
        <div className="flex items-center justify-between gap-2">
          <span className="text-[9.5px] text-[#5c6b78]">نتیجه ثبت نهایی از انبار اسناد با احراز هویت باز شده است.</span>
          <div className="flex items-center gap-2">
            {savedOutputs.find((output) => output.kind === "sheet") ? (
              <Button
                className="btn-sm"
                disabled={busy}
                onClick={() => {
                  const output = savedOutputs.find((item) => item.kind === "sheet");
                  if (output && session) void downloadOutput(output.attachment_id, output.filename);
                }}
              >
                دریافت Comment Sheet
              </Button>
            ) : null}
            <Button className="btn-sm" onClick={closeCommentSheet}>بستن</Button>
          </div>
        </div>
      }
    >
      {commentSheetLoading ? <Spinner label="در حال بازکردن Comment Sheet…" /> : null}
      {commentSheetError ? <ErrorBox message={commentSheetError} /> : null}
      {commentSheetData ? (
        <div className="h-[62vh] min-h-[30rem] border border-[#a8a284] bg-[#e9edf0]">
          <PdfViewer data={commentSheetData} />
        </div>
      ) : null}
      {!commentSheetLoading && !commentSheetError && !commentSheetData ? (
        <p className="text-[10.5px] text-[#5c6b78]">فایل Comment Sheet برای نمایش آماده نیست.</p>
      ) : null}
    </Modal>
    </>
  );
}

function labelText(annotation: MarkupAnnotation): string {
  return `${persianDigits(annotation.number)} · ${annotation.kind}`;
}
