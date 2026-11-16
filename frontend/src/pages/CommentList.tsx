import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import DatePicker from "react-multi-date-picker";
import DateObject from "react-date-object";
import gregorian from "react-date-object/calendars/gregorian";
import persian from "react-date-object/calendars/persian";
import persian_fa from "react-date-object/locales/persian_fa";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { getMarkupSession, openMarkupRegistration as markMarkupRegistrationOpened } from "@/lib/markups";
import { useAuth } from "@/lib/auth";
import type { Comment, CommentAttachment, CommentList as CommentListType, DocumentAttachment, MarkupSession } from "@/lib/types";
import { AttachmentPreview } from "@/components/AttachmentPreview";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Modal, ProgressBar, Spinner } from "@/components/ui";
import { FField, FieldGrid } from "@/components/window";
import { DISPOSITION_LABELS, fileSize, jalaliDateTime, persianDigits } from "@/lib/format";

const COMMENT_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;
const COMMENT_ATTACHMENT_EXTENSIONS = new Set(["pdf", "docx", "xlsx", "dwg", "png", "jpg", "zip"]);
const COMMENT_EDITOR_ROLES = ["reviewer", "dcc", "project_control", "pmo", "project_manager"];
const COORDINATOR_ROLES = ["coordinator", "dcc", "project_control", "pmo", "project_manager"];

const CODE_LABELS: Record<string, string> = {
  "1": "کد ۱ — رد / اصلاح اساسی",
  "2": "کد ۲ — بازنگری و ارسال مجدد",
  "3": "کد ۳ — اطلاع / جزئی",
};

const shortening = (value: string, max = 90) => (value.length > max ? `${value.slice(0, max)}…` : value);

interface RevisionDocument {
  doc_code: string | null;
  title: string | null;
}

interface RevisionDetails {
  doc: RevisionDocument | null;
}

interface RegistrationForm {
  disciplineCode: string;
  code: string;
  disposition: string;
  statusChangeDate: string;
  fromPageNo: string;
  toPageNo: string;
  text: string;
  personalRemarks: string;
  coordinatorDate: string;
  judgment: string;
  judgmentComments: string;
  remarks: string;
}

const today = () => {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
};

const LATIN_DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

function emptyForm(): RegistrationForm {
  return {
    disciplineCode: "",
    code: "2",
    disposition: "commented",
    statusChangeDate: today(),
    fromPageNo: "",
    toPageNo: "",
    text: "",
    personalRemarks: "",
    coordinatorDate: today(),
    judgment: "draft",
    judgmentComments: "",
    remarks: "",
  };
}

function isoToJalaliDate(value: string) {
  if (!value) return null;
  return new DateObject({ date: `${value}T12:00:00`, calendar: gregorian }).convert(persian);
}

function jalaliDateToIso(value: DateObject | null) {
  return value ? value.convert(gregorian).format("YYYY-MM-DD") : "";
}

function JalaliDateField({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <DatePicker
      value={isoToJalaliDate(value)}
      calendar={persian}
      locale={persian_fa}
      digits={LATIN_DIGITS}
      format="YYYY/MM/DD"
      inputClass="input w-full text-right font-mono"
      containerClassName="jalali-date-picker"
      calendarPosition="bottom-right"
      portal
      zIndex={100}
      disabled={disabled}
      onChange={(selected) => onChange(jalaliDateToIso(selected as DateObject | null))}
    />
  );
}

function formFromComment(comment: Comment): RegistrationForm {
  return {
    disciplineCode: comment.discipline_code ?? "",
    code: comment.code,
    disposition: comment.disposition ?? "commented",
    statusChangeDate: comment.status_change_date?.slice(0, 10) ?? today(),
    fromPageNo: comment.from_page_no ? String(comment.from_page_no) : comment.page_no ? String(comment.page_no) : "",
    toPageNo: comment.to_page_no ? String(comment.to_page_no) : "",
    text: comment.text,
    personalRemarks: comment.personal_remarks ?? "",
    coordinatorDate: comment.coordinator_date?.slice(0, 10) ?? today(),
    judgment: comment.judgment ?? "draft",
    judgmentComments: comment.judgment_comments ?? "",
    remarks: comment.remarks ?? "",
  };
}

function attachmentPath(attachment: CommentAttachment) {
  // The comments API historically returned a full `/api/v1/...` path while the
  // frontend API wrapper already prepends `/api/v1`. Normalize both shapes so
  // preview and native download never request `/api/v1/api/v1/...`.
  return attachment.download_url.replace(/^\/api\/v1(?=\/)/, "");
}

function toDocumentAttachment(attachment: CommentAttachment): DocumentAttachment {
  return {
    id: attachment.id,
    line_id: attachment.comment_id,
    revision_id: null,
    kind: attachment.mime_type === "application/pdf" ? "pdf" : "native",
    filename: attachment.file_name,
    content_type: attachment.mime_type,
    size_bytes: attachment.file_size,
    uploaded_at: attachment.uploaded_at,
    uploaded_by_id: attachment.uploaded_by_id,
  };
}

function commentFromMarkupSession(session: MarkupSession, existing: Comment | undefined): Comment {
  const outputAttachments: CommentAttachment[] = session.outputs.map((output) => ({
    id: output.attachment_id,
    comment_id: session.id,
    file_name: output.filename,
    file_size: output.size_bytes ?? 0,
    mime_type: output.content_type ?? "application/pdf",
    uploaded_by_id: session.author_id,
    uploaded_by_name: session.author_name,
    uploaded_at: session.submitted_at ?? new Date().toISOString(),
    download_url: output.download_url,
  }));
  if (existing) {
    return {
      ...existing,
      attachments: existing.attachments.length ? existing.attachments : outputAttachments,
      attachment_count: existing.attachments.length || outputAttachments.length,
    };
  }
  const pages = session.annotations.map((annotation) => annotation.page_no);
  const first = session.annotations[0];
  return {
    id: session.id,
    registration_id: session.id,
    revision_id: session.revision_id ?? "",
    comment_list_id: session.id,
    seq_no: 1,
    code: "2",
    category: "PDF markup",
    related_comment_id: null,
    discipline_id: null,
    discipline_code: null,
    discipline_name: null,
    text: `خروجی بازبینی PDF «${session.original_filename}» برای ثبت نظر آماده است.`,
    page_no: pages.length ? Math.min(...pages) : null,
    from_page_no: pages.length ? Math.min(...pages) : null,
    to_page_no: pages.length ? Math.max(...pages) : null,
    bbox: first?.points ?? [],
    status: "open",
    disposition: null,
    status_change_date: null,
    personal_remarks: null,
    coordinator_date: null,
    judgment: "draft",
    judgment_comments: null,
    remarks: null,
    response_text: null,
    responded_at: null,
    author_id: session.author_id,
    author_name: session.author_name,
    created_at: session.submitted_at ?? new Date().toISOString(),
    revision_time: null,
    attachments: outputAttachments,
    attachment_count: outputAttachments.length,
    staged_registration_opened: false,
  };
}

function CommentRegistrationModal({
  open,
  document,
  editing,
  finalizing,
  canEditCoordinator,
  form,
  setForm,
  files,
  onFiles,
  onRemoveFile,
  existingAttachments,
  removedAttachmentIds,
  onRemoveExisting,
  onPreview,
  dragActive,
  setDragActive,
  busy,
  progress,
  error,
  onSave,
  onClose,
}: {
  open: boolean;
  document: RevisionDocument | null;
  editing: Comment | null;
  finalizing: boolean;
  canEditCoordinator: boolean;
  form: RegistrationForm;
  setForm: (updater: (previous: RegistrationForm) => RegistrationForm) => void;
  files: File[];
  onFiles: (incoming: File[]) => void;
  onRemoveFile: (index: number) => void;
  existingAttachments: CommentAttachment[];
  removedAttachmentIds: string[];
  onRemoveExisting: (id: string) => void;
  onPreview: (attachment: CommentAttachment) => void;
  dragActive: boolean;
  setDragActive: (active: boolean) => void;
  busy: boolean;
  progress: number;
  error: string | null;
  onSave: () => void;
  onClose: () => void;
}) {
  const registrationId = editing ? editing.registration_id : "AUTO — پس از ذخیره";
  const registrationTime = editing ? jalaliDateTime(editing.created_at) : "اکنون / پس از Save";
  const revisionTime = editing?.revision_time ? jalaliDateTime(editing.revision_time) : "—";
  const relatedDocument = [document?.doc_code, document?.title].filter(Boolean).join(" — ") || "—";
  const registrationFieldsLocked = Boolean(editing) && !finalizing;

  const set = (key: keyof RegistrationForm, value: string) => {
    setForm((previous) => ({ ...previous, [key]: value }));
  };

  const displayedExisting = existingAttachments.filter((attachment) => !removedAttachmentIds.includes(attachment.id));

  return (
    <Modal
      open={open}
      title={editing && !finalizing ? "Coordinator Comment — نظر هماهنگ‌کننده" : "Comment Registration — ثبت نظر جدید"}
      onClose={onClose}
      width="max-w-5xl"
      footer={
        <>
          <Button variant="primary" disabled={busy || form.text.trim().length < 2} onClick={onSave}>
            {busy ? "در حال ذخیره…" : finalizing ? "Save — ثبت نهایی" : editing ? "Save Coordinator Comment — ذخیره نظر هماهنگ‌کننده" : "Save — ذخیره"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Close — بستن
          </Button>
        </>
      }
    >
      <div className="mb-1 grid gap-1 border border-[#9aa8b4] bg-white p-2 text-[10px] text-[#33475b] sm:grid-cols-3" dir="rtl">
        <div>
          <span className="font-bold">Registration ID:</span>{" "}
          <b dir="ltr" className="font-mono text-[#1f3864]">{registrationId}</b>
        </div>
        <div>
          <span className="font-bold">Registration Time:</span> {registrationTime}
        </div>
        <div>
          <span className="font-bold">Revision Time:</span> {revisionTime}
        </div>
      </div>

      <div className="comment-brand-panel mb-1 px-3 py-2 text-center">
        <img
          className="mx-auto h-10 w-28 object-contain"
          src="/aidana-logo.png"
          alt="AIDANA — سامانه هوشمند مدیریت اسناد مهندسی"
        />
        <div className="text-[11px] font-bold text-[#4a5a67]">Comment Registration — ثبت نظر جدید</div>
      </div>

      {error ? <ErrorBox message={error} /> : null}

      <div className="section-bar mt-1">Comment Information — اطلاعات نظر</div>
      <FieldGrid>
        <FField label="Related Document:">
          <span
            dir="ltr"
            className="block whitespace-normal break-words text-right font-mono text-[10px] leading-5 text-[#536272]"
            title={relatedDocument}
          >
            {relatedDocument}
          </span>
        </FField>
        <FField label="Discipline / رشته:">
          <input
            className="input uppercase"
            value={form.disciplineCode}
            onChange={(event) => set("disciplineCode", event.target.value.toUpperCase())}
            placeholder="مثلاً CIV"
            dir="ltr"
disabled={registrationFieldsLocked}
          />
        </FField>
        <FField label="Code:">
          <select className="input" value={form.code} onChange={(event) => set("code", event.target.value)} disabled={registrationFieldsLocked}>
            {Object.entries(CODE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </FField>
        <FField label="Status:">
          <select className="input" value={form.disposition} onChange={(event) => set("disposition", event.target.value)} disabled={registrationFieldsLocked}>
            <option value="">— انتخاب وضعیت —</option>
            {Object.entries(DISPOSITION_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </FField>
        <FField label="Status Change Date:" full>
          <JalaliDateField value={form.statusChangeDate} onChange={(value) => set("statusChangeDate", value)} disabled={registrationFieldsLocked} />
        </FField>
        <FField label="From Page:">
          <input className="input" type="number" min={1} value={form.fromPageNo} onChange={(event) => set("fromPageNo", event.target.value)} dir="ltr" disabled={registrationFieldsLocked} />
        </FField>
        <FField label="To Page:">
          <input className="input" type="number" min={1} value={form.toPageNo} onChange={(event) => set("toPageNo", event.target.value)} dir="ltr" disabled={registrationFieldsLocked} />
        </FField>
        <FField label="Comment Description:" full>
          <textarea
            className="input min-h-[170px] w-full resize-y disabled:bg-[#e8ebee] disabled:text-[#89939d]"
            value={form.text}
            onChange={(event) => set("text", event.target.value)}
            placeholder="شرح نظر را وارد کنید…"
            readOnly={registrationFieldsLocked}
            required
          />
        </FField>
        <FField label="Personal Remarks:" full>
          <textarea className="input min-h-[45px] w-full disabled:bg-[#e8ebee] disabled:text-[#89939d]" value={form.personalRemarks} onChange={(event) => set("personalRemarks", event.target.value)} placeholder="اختیاری" readOnly={registrationFieldsLocked} />
        </FField>
      </FieldGrid>

      <div className="section-bar mt-2">Attachments / پیوست‌ها</div>
      <div className="border border-[#a8a284] bg-white p-2">
        {editing ? (
          <div className="rounded border border-[#d1d8df] bg-[#f1f4f6] px-3 py-3 text-center text-[10px] text-[#5c6b78]">
            {finalizing
              ? "دو فایل خروجی بازبینی برای ثبت نهایی آماده شده‌اند؛ افزودن یا حذف پیوست در این مرحله مجاز نیست."
              : "پیوست‌های ثبت‌شده پس از ثبت نظر قابل افزودن یا حذف نیستند؛ فقط هماهنگ‌کننده می‌تواند نظر خود را در بخش Coordinator ثبت کند."}
          </div>
        ) : (
          <label
            className={`block cursor-pointer rounded border-2 border-dashed px-3 py-4 text-center text-[11px] transition ${dragActive ? "border-[#2f6ea5] bg-[#eaf3fb]" : "border-[#a9b2bb] bg-[#f8fafc]"}`}
            onDragEnter={(event: DragEvent<HTMLLabelElement>) => { event.preventDefault(); setDragActive(true); }}
            onDragOver={(event: DragEvent<HTMLLabelElement>) => event.preventDefault()}
            onDragLeave={() => setDragActive(false)}
            onDrop={(event: DragEvent<HTMLLabelElement>) => {
              event.preventDefault();
              setDragActive(false);
              onFiles(Array.from(event.dataTransfer.files));
            }}
          >
            <input
              type="file"
              className="sr-only"
              multiple
              accept=".pdf,.docx,.xlsx,.dwg,.png,.jpg,.zip"
              onChange={(event: ChangeEvent<HTMLInputElement>) => onFiles(Array.from(event.target.files ?? []))}
            />
            <span className="text-[#1f4e79]">📎 فایل‌ها را انتخاب کنید یا اینجا بکشید و رها کنید</span>
            <span className="mt-1 block text-[10px] text-[#657585]" dir="ltr">PDF, DOCX, XLSX, DWG, PNG, JPG, ZIP · max 20 MB</span>
          </label>
        )}
        {displayedExisting.length || files.length ? (
          <div className="mt-2 space-y-1">
            {displayedExisting.map((attachment) => (
              <AttachmentRow
                key={attachment.id}
                attachment={attachment}
                onRemove={!editing ? () => onRemoveExisting(attachment.id) : undefined}
                onDownload={() => api.download(attachmentPath(attachment), attachment.file_name)}
                onPreview={() => onPreview(attachment)}
                existing
              />
            ))}
            {files.map((file, index) => (
              <div key={`${file.name}-${file.lastModified}-${index}`} className="flex items-center justify-between gap-2 border border-[#d1d8df] bg-[#f8fafc] px-2 py-1 text-[10px]">
                <span className="min-w-0 truncate" title={file.name}>
                  <span className="text-[#1f4e79]">📎</span> {file.name} <span className="text-[#73808b]">({fileSize(file.size)})</span>
                </span>
                <Button variant="ghost" className="px-1 py-0.5 text-[10px]" onClick={() => onRemoveFile(index)}>حذف</Button>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      {busy ? (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-[#4a5a67]">
          <ProgressBar value={progress} className="flex-1" />
          <span dir="ltr">{Math.round(progress * 100)}%</span>
        </div>
      ) : null}

      <div className="section-bar mt-2">Coordinator — هماهنگ‌کننده</div>
      <FieldGrid>
        <FField label="Date:">
          <JalaliDateField value={form.coordinatorDate} onChange={(value) => set("coordinatorDate", value)} disabled={!canEditCoordinator} />
        </FField>
        <FField label="Judgment:">
          <select className="input" value={form.judgment} onChange={(event) => set("judgment", event.target.value)} disabled={!canEditCoordinator}>
            <option value="draft">Draft — پیش‌نویس</option>
            <option value="OK">OK — تأیید هماهنگ‌کننده</option>
            <option value="accepted">Accepted — پذیرفته</option>
            <option value="rejected">Rejected — رد‌شده</option>
            <option value="commented">Commented — دارای نظر</option>
          </select>
        </FField>
        <FField label="Judgment Comments:" full>
          <textarea className="input min-h-[50px] w-full disabled:bg-[#e8ebee] disabled:text-[#89939d]" value={form.judgmentComments} onChange={(event) => set("judgmentComments", event.target.value)} disabled={!canEditCoordinator} placeholder={canEditCoordinator ? "توضیح قضاوت هماهنگ‌کننده" : "فقط هماهنگ‌کننده می‌تواند این بخش را ویرایش کند"} />
        </FField>
        <FField label="Remarks:" full>
          <textarea className="input min-h-[45px] w-full disabled:bg-[#e8ebee] disabled:text-[#89939d]" value={form.remarks} onChange={(event) => set("remarks", event.target.value)} disabled={!canEditCoordinator} placeholder="یادداشت هماهنگ‌کننده" />
        </FField>
      </FieldGrid>
    </Modal>
  );
}

function AttachmentRow({
  attachment,
  onRemove,
  onDownload,
  onPreview,
  existing = false,
}: {
  attachment: CommentAttachment;
  onRemove?: () => void;
  onDownload: () => void;
  onPreview: () => void;
  existing?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border border-[#d1d8df] bg-[#f8fafc] px-2 py-1 text-[10px]">
      <span className="min-w-0 truncate" title={attachment.file_name}>
        <span className="text-[#1f4e79]">📎</span> {attachment.file_name} <span className="text-[#73808b]">({fileSize(attachment.file_size)} · {attachment.mime_type})</span>
      </span>
      <span className="flex shrink-0 gap-1">
        <Button variant="ghost" className="px-1 py-0.5 text-[10px]" onClick={onPreview}>مشاهده</Button>
        <Button variant="ghost" className="px-1 py-0.5 text-[10px]" onClick={onDownload}>دانلود</Button>
        {existing && onRemove ? <Button variant="ghost" className="px-1 py-0.5 text-[10px] text-rose-700" onClick={onRemove}>حذف</Button> : null}
      </span>
    </div>
  );
}

export function CommentList() {
  const { revisionId } = useParams<{ revisionId: string }>();
  const [search] = useSearchParams();
  const { hasRole } = useAuth();
  const markupSessionId = search.get("markup_session_id");
  const [lists, setLists] = useState<CommentListType[]>([]);
  const [revisionDetails, setRevisionDetails] = useState<RevisionDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [answering, setAnswering] = useState<{ comment: Comment; text: string } | null>(null);
  const [closing, setClosing] = useState<{ id: string; disposition: string } | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Comment | null>(null);
  const [finalizingCommentId, setFinalizingCommentId] = useState<string | null>(null);
  const [form, setForm] = useState<RegistrationForm>(emptyForm);
  const [files, setFiles] = useState<File[]>([]);
  const [removedAttachmentIds, setRemovedAttachmentIds] = useState<string[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [modalError, setModalError] = useState<string | null>(null);
  const [previewAttachment, setPreviewAttachment] = useState<CommentAttachment | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewActionError, setPreviewActionError] = useState<string | null>(null);
  const [previewDownloading, setPreviewDownloading] = useState(false);
  const [previewOpening, setPreviewOpening] = useState(false);
  const [markupPrefillLoading, setMarkupPrefillLoading] = useState(false);
  const [markupPrefillDone, setMarkupPrefillDone] = useState(false);
  const [markupPrefillActive, setMarkupPrefillActive] = useState(false);
  const previewRequestRef = useRef(0);
  const previewUrlRef = useRef<string | null>(null);
  const openedUrlRef = useRef<string | null>(null);

  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    if (openedUrlRef.current) URL.revokeObjectURL(openedUrlRef.current);
  }, []);

  const canRegister = COMMENT_EDITOR_ROLES.some((role) => hasRole(role));
  const canEditCoordinator = COORDINATOR_ROLES.some((role) => hasRole(role));

  const load = useCallback(async () => {
    if (!revisionId) return;
    setLoading(true);
    setError(null);
    try {
      const [nextLists, nextRevision] = await Promise.all([
        api.get<CommentListType[]>(`/revisions/${revisionId}/comment-lists`),
        api.get<RevisionDetails>(`/revisions/${revisionId}`),
      ]);
      setLists(nextLists);
      setRevisionDetails(nextRevision);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری فهرست نظرها ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [revisionId]);

  useEffect(() => { void load(); }, [load]);

  const comments = useMemo(() => lists.flatMap((list) => list.comments), [lists]);
  const markStagedRowOpened = useCallback((sessionId: string) => {
    setLists((current) => current.map((list) => ({
      ...list,
      comments: list.comments.map((comment) => comment.id === sessionId
        ? { ...comment, staged_registration_opened: true }
        : comment),
    })));
  }, []);

  useEffect(() => {
    if (!markupSessionId || !revisionId || !canRegister || markupPrefillDone) return;
    let cancelled = false;
    setMarkupPrefillLoading(true);
    void (async () => {
      try {
        const loaded = await getMarkupSession(markupSessionId);
        if (loaded.revision_id !== revisionId) {
          throw new Error("نشست نشانه‌گذاری به این بازنگری متصل نیست.");
        }
        // The explicit row transition is recorded before the modal opens. This
        // is what makes Delete unavailable even if the operator closes and
        // refreshes the page without saving the form.
        const session = await markMarkupRegistrationOpened(markupSessionId);
        markStagedRowOpened(markupSessionId);
        // Fetch both authenticated outputs before opening the modal. They are
        // displayed as existing staged attachments; they are never re-uploaded
        // from the browser during final registration.
        if (session.outputs.length < 2) {
          throw new Error("هر دو فایل خروجی PDF برای ثبت در Comment لازم است.");
        }
        await Promise.all(
          session.outputs.map((output) => api.blobData(
            `/transmittals/${session.transmittal_id}/attachments/${output.attachment_id}/preview`,
          )),
        );
        if (cancelled) return;
        const stagedComment = commentFromMarkupSession(
          session,
          comments.find((comment) => comment.id === markupSessionId),
        );
        const registration = formFromComment(stagedComment);
        setForm({
          ...registration,
          judgment: registration.judgment === "draft" ? "OK" : registration.judgment,
        });
        setFiles([]);
        setRemovedAttachmentIds([]);
        setEditing(stagedComment);
        setFinalizingCommentId(session.id);
        setModalError(null);
        setMarkupPrefillActive(true);
        setMarkupPrefillDone(true);
        setModalOpen(true);
      } catch (err) {
        if (!cancelled) {
          setActionError(err instanceof ApiError ? err.detail : "پیش‌پر کردن فرم ثبت نظر از خروجی بازبینی ناموفق بود.");
          setMarkupPrefillDone(true);
        }
      } finally {
        if (!cancelled) setMarkupPrefillLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canRegister, markStagedRowOpened, markupPrefillDone, markupSessionId, revisionId]);
  const returnTransmittalId = useMemo(
    () => lists.find((list) => list.transmittal_id)?.transmittal_id ?? null,
    [lists],
  );
  const returnTransmittalPath = returnTransmittalId ? `/transmittals/${returnTransmittalId}` : "/transmittals/new";
  const existingAttachments = editing?.attachments ?? [];
  const previewIsPdf = Boolean(
    previewAttachment &&
      (previewAttachment.mime_type.toLowerCase() === "application/pdf" || previewAttachment.file_name.toLowerCase().endsWith(".pdf")),
  );

  const openNew = () => {
    setEditing(null);
    setFinalizingCommentId(null);
    setForm(emptyForm());
    setFiles([]);
    setRemovedAttachmentIds([]);
    setModalError(null);
    setProgress(0);
    setModalOpen(true);
  };

  const openEdit = (comment: Comment) => {
    setEditing(comment);
    setFinalizingCommentId(null);
    setForm(formFromComment(comment));
    setFiles([]);
    setRemovedAttachmentIds([]);
    setModalError(null);
    setProgress(0);
    setModalOpen(true);
  };

  const openMarkupRegistration = async (comment: Comment) => {
    if (!revisionId || finalizingCommentId === comment.id) return;
    setMarkupPrefillLoading(true);
    setActionError(null);
    try {
      const loaded = await getMarkupSession(comment.id);
      if (loaded.revision_id !== revisionId) {
        throw new Error("نشست نشانه‌گذاری به این بازنگری متصل نیست.");
      }
      const session = await markMarkupRegistrationOpened(comment.id);
      markStagedRowOpened(comment.id);
      if (session.outputs.length < 2) {
        throw new Error("هر دو فایل خروجی PDF برای ثبت در Comment لازم است.");
      }
      await Promise.all(
        session.outputs.map((output) => api.blobData(
          `/transmittals/${session.transmittal_id}/attachments/${output.attachment_id}/preview`,
        )),
      );
      const stagedComment = commentFromMarkupSession(session, comment);
      const registration = formFromComment(stagedComment);
      setEditing(stagedComment);
      setFinalizingCommentId(session.id);
      setForm({
        ...registration,
        judgment: registration.judgment === "draft" ? "OK" : registration.judgment,
      });
      setFiles([]);
      setRemovedAttachmentIds([]);
      setModalError(null);
      setProgress(0);
      setMarkupPrefillActive(true);
      setModalOpen(true);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "بازکردن خروجی‌های بازبینی ناموفق بود.");
    } finally {
      setMarkupPrefillLoading(false);
    }
  };

  const closeModal = () => {
    if (busy) return;
    setModalOpen(false);
    setEditing(null);
    setFinalizingCommentId(null);
    if (markupPrefillActive) {
      // Files created from authenticated output blobs are temporary browser
      // objects; never keep them around after the registration dialog closes.
      setFiles([]);
      setMarkupPrefillActive(false);
    }
  };

  const closeAttachmentPreview = () => {
    previewRequestRef.current += 1;
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
    setPreviewAttachment(null);
    setPreviewUrl(null);
    setPreviewLoading(false);
    setPreviewError(null);
    setPreviewActionError(null);
  };

  const openAttachmentPreview = async (attachment: CommentAttachment) => {
    const requestId = previewRequestRef.current + 1;
    previewRequestRef.current = requestId;
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
    const attachmentUrl = attachmentPath(attachment);
    const separator = attachmentUrl.includes("?") ? "&" : "?";
    const previewPath = `${attachmentUrl}${separator}inline=true`;
    setPreviewAttachment(attachment);
    setPreviewUrl(null);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewActionError(null);
    try {
      const url = await api.blobUrl(previewPath);
      if (previewRequestRef.current !== requestId) {
        URL.revokeObjectURL(url);
        return;
      }
      previewUrlRef.current = url;
      setPreviewUrl(url);
    } catch (err) {
      if (previewRequestRef.current === requestId) {
        setPreviewError(err instanceof ApiError ? err.detail : "نمایش فایل پیوست ناموفق بود.");
      }
    } finally {
      if (previewRequestRef.current === requestId) setPreviewLoading(false);
    }
  };

  const downloadCommentAttachment = async (attachment: CommentAttachment) => {
    setPreviewDownloading(true);
    setPreviewActionError(null);
    try {
      await api.downloadBlob(attachmentPath(attachment), attachment.file_name);
    } catch (err) {
      setPreviewActionError(err instanceof ApiError ? err.detail : "دریافت فایل ناموفق بود.");
    } finally {
      setPreviewDownloading(false);
    }
  };

  const openCommentAttachment = async (attachment: CommentAttachment) => {
    const popup = window.open("about:blank", "_blank");
    if (!popup) {
      setPreviewActionError("بازکردن پنجره جدید ممکن نشد؛ اجازه popup مرورگر را فعال کنید.");
      return;
    }
    popup.opener = null;
    setPreviewOpening(true);
    setPreviewActionError(null);
    try {
      const blob = await api.blobData(`${attachmentPath(attachment)}?inline=true`);
      const objectUrl = URL.createObjectURL(blob);
      if (openedUrlRef.current) URL.revokeObjectURL(openedUrlRef.current);
      openedUrlRef.current = objectUrl;
      popup.location.replace(objectUrl);
    } catch (err) {
      popup.close();
      setPreviewActionError(err instanceof ApiError ? err.detail : "بازکردن فایل ناموفق بود.");
    } finally {
      setPreviewOpening(false);
    }
  };

  const addFiles = (incoming: File[]) => {
    const accepted: File[] = [];
    const errors: string[] = [];
    for (const file of incoming) {
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (!COMMENT_ATTACHMENT_EXTENSIONS.has(extension)) {
        errors.push(`فرمت «${file.name}» مجاز نیست.`);
      } else if (file.size > COMMENT_ATTACHMENT_MAX_BYTES) {
        errors.push(`حجم «${file.name}» بیشتر از ۲۰ مگابایت است.`);
      } else if (files.some((item) => item.name === file.name && item.size === file.size) || accepted.some((item) => item.name === file.name && item.size === file.size)) {
        errors.push(`فایل «${file.name}» تکراری است.`);
      } else {
        accepted.push(file);
      }
    }
    if (errors.length) setModalError(errors.join(" "));
    if (accepted.length) setFiles((previous) => [...previous, ...accepted]);
  };

  const buildFormData = (includeFiles = true) => {
    const data = new FormData();
    data.append("text", form.text.trim());
    data.append("code", form.code);
    if (form.disciplineCode.trim()) data.append("discipline_code", form.disciplineCode.trim());
    if (form.disposition) data.append("disposition", form.disposition);
    if (form.statusChangeDate) data.append("status_change_date", form.statusChangeDate);
    if (form.fromPageNo) data.append("from_page_no", form.fromPageNo);
    if (form.toPageNo) data.append("to_page_no", form.toPageNo);
    if (form.personalRemarks.trim()) data.append("personal_remarks", form.personalRemarks.trim());
    if (form.coordinatorDate) data.append("coordinator_date", form.coordinatorDate);
    data.append("judgment", form.judgment);
    if (form.judgmentComments.trim()) data.append("judgment_comments", form.judgmentComments.trim());
    if (form.remarks.trim()) data.append("remarks", form.remarks.trim());
    if (includeFiles) files.forEach((file) => data.append("files", file, file.name));
    return data;
  };

  const saveRegistration = async () => {
    if (!revisionId || form.text.trim().length < 2) {
      setModalError("شرح نظر اجباری است.");
      return;
    }
    const from = form.fromPageNo ? Number(form.fromPageNo) : null;
    const to = form.toPageNo ? Number(form.toPageNo) : null;
    if ((from !== null && (!Number.isInteger(from) || from < 1)) || (to !== null && (!Number.isInteger(to) || to < 1))) {
      setModalError("شماره صفحه باید عددی مثبت باشد.");
      return;
    }
    if (from !== null && to !== null && to < from) {
      setModalError("صفحه پایان باید بزرگ‌تر یا مساوی صفحه شروع باشد.");
      return;
    }
    setBusy(true);
    setModalError(null);
    setProgress(0);
    try {
      const finalizing = Boolean(finalizingCommentId && editing?.id === finalizingCommentId);
      const path = editing ? `/comments/${editing.id}` : `/revisions/${revisionId}/comments/multipart`;
      const data = buildFormData(!finalizing);
      const saved = editing
        ? await api.patchFormWithProgress<Comment>(path, data, setProgress)
        : await api.postFormWithProgress<Comment>(path, data, setProgress);
      if (!finalizing) {
        for (const attachmentId of removedAttachmentIds) {
          await api.delete(`/comments/${saved.id}/attachments/${attachmentId}`);
        }
      }
      setModalOpen(false);
      setEditing(null);
      setFinalizingCommentId(null);
      setFiles([]);
      setRemovedAttachmentIds([]);
      setMarkupPrefillActive(false);
      await load();
    } catch (err) {
      setModalError(err instanceof ApiError ? err.detail : "ذخیره ثبت نظر ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const respond = async () => {
    if (!answering || answering.text.trim().length < 2) return;
    try {
      await api.post(`/comments/${answering.comment.id}/respond`, { response_text: answering.text.trim() });
      setAnswering(null);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "ثبت پاسخ ناموفق بود.");
    }
  };

  const close = async (comment: Comment, disposition: string) => {
    try {
      await api.post(`/comments/${comment.id}/close`, { disposition });
      setClosing(null);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "بستن نظر ناموفق بود.");
    }
  };

  const deleteMarkupComment = async (comment: Comment) => {
    if (finalizingCommentId === comment.id) return;
    if (!window.confirm("نظر بازبینی و دو فایل پیوست آن حذف شود؟")) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.delete(`/comments/${comment.id}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "حذف نظر ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} onRetry={() => void load()} />;

  return (
    <div className="space-y-3">
      <Card className="is-main">
        <CardHeader
          title="فهرست نظرها (Comment List)"
          subtitle={`${persianDigits(comments.length)} نظر ثبت شده برای این بازنگری`}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {canRegister ? <Button variant="primary" className="px-2 py-1 text-[11px]" onClick={openNew}>Add New Comment — ثبت نظر جدید</Button> : null}
              <Link
                className="btn btn-transmittal-back px-2 py-1 text-[11px]"
                to={returnTransmittalPath}
                title="بازگشت مستقیم به فرم ترانسمیتال"
              >
                بازگشت به ترانسمیتال
              </Link>
            </div>
          }
        />
        {actionError ? <div className="px-4 pt-3"><ErrorBox message={actionError} /></div> : null}
        {markupPrefillLoading ? <div className="px-4 pt-3 text-[11px] text-slate-500">در حال آماده‌سازی خروجی‌های بازبینی برای فرم ثبت نظر…</div> : null}
        {comments.length === 0 ? (
          <EmptyState title="نظری ثبت نشده است" description="کارشناسان می‌توانند نظر خود را روی این مدرک ثبت کنند." action={canRegister ? <Button onClick={openNew}>Add New Comment</Button> : undefined} />
        ) : (
          <div className="divide-y divide-slate-100">
            {comments.map((comment) => (
              <div key={comment.id} className="p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className="bg-slate-100">#{persianDigits(comment.seq_no)}</Badge>
                  <Badge className={comment.code === "1" ? "bg-rose-100 text-rose-800" : comment.code === "2" ? "bg-amber-100 text-amber-900" : "bg-sky-100 text-sky-800"}>{CODE_LABELS[comment.code] ?? `کد ${comment.code}`}</Badge>
                  <Badge className={comment.status === "closed" ? "bg-emerald-100 text-emerald-800" : comment.status === "answered" ? "bg-sky-100 text-sky-800" : "bg-slate-100 text-slate-600"}>{comment.status === "closed" ? "بسته" : comment.status === "answered" ? "پاسخ داده‌شده" : "باز"}</Badge>
                  {comment.disposition ? <Badge className="bg-violet-100 text-violet-800">{DISPOSITION_LABELS[comment.disposition] ?? comment.disposition}</Badge> : null}
                  {comment.attachment_count ? <Badge className="bg-amber-50 text-amber-800">📎 {persianDigits(comment.attachment_count)}</Badge> : null}
                  <span className="text-[11px] text-slate-400">{comment.author_name ?? "—"} · {jalaliDateTime(comment.created_at)}{comment.from_page_no || comment.page_no ? ` · صفحه ${persianDigits(comment.from_page_no ?? comment.page_no ?? "")}${comment.to_page_no ? ` تا ${persianDigits(comment.to_page_no)}` : ""}` : ""}</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-800">{comment.text}</p>
                {comment.attachments.length ? (
                  <div className="mt-2 space-y-1 rounded-lg bg-[#f8fafc] p-2">
                    {comment.attachments.map((attachment) => <AttachmentRow key={attachment.id} attachment={attachment} onDownload={() => api.download(attachmentPath(attachment), attachment.file_name)} onPreview={() => void openAttachmentPreview(attachment)} />)}
                  </div>
                ) : null}
                {comment.response_text ? <div className="mt-2 rounded-lg bg-slate-50 px-3 py-2"><p className="text-[11px] font-medium text-slate-500">پاسخ پیمانکار</p><p className="mt-1 text-xs leading-6 text-slate-700">{comment.response_text}</p><p className="mt-1 text-[10px] text-slate-400">{jalaliDateTime(comment.responded_at)}</p></div> : null}
                <div className="mt-2 flex flex-wrap gap-2">
                  {comment.status !== "closed" ? <Button variant="secondary" className="px-2 py-1 text-[11px]" onClick={() => setAnswering({ comment, text: comment.response_text ?? "" })}>ثبت پاسخ</Button> : null}
                  {canEditCoordinator && comment.status !== "closed" ? <Button variant="ghost" className="px-2 py-1 text-[11px]" onClick={() => openEdit(comment)}>بازکردن/نظر هماهنگ‌کننده</Button> : null}
                  {comment.category === "PDF markup" && canRegister ? (
                    <>
                      <Button
                        variant="secondary"
                        className="px-2 py-1 text-[11px]"
                        disabled={comment.judgment !== "draft" || finalizingCommentId === comment.id || busy}
                        onClick={() => void openMarkupRegistration(comment)}
                      >
                        مشاهده و ثبت نهایی
                      </Button>
                      <Button
                        variant="ghost"
                        className="px-2 py-1 text-[11px] text-rose-700"
                        disabled={comment.judgment !== "draft" || comment.staged_registration_opened || finalizingCommentId === comment.id || busy}
                        onClick={() => void deleteMarkupComment(comment)}
                      >
                        حذف
                      </Button>
                    </>
                  ) : null}
                  {comment.status === "answered" && canRegister ? (closing?.id === comment.id ? <span className="flex items-center gap-1"><select aria-label="وضعیت بستن نظر" className="h-7 rounded border border-slate-300 bg-white px-1 text-[11px]" value={closing.disposition} onChange={(event) => setClosing({ id: comment.id, disposition: event.target.value })}>{Object.entries(DISPOSITION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><Button variant="ghost" className="px-2 py-1 text-[11px]" onClick={() => void close(comment, closing.disposition)}>ثبت</Button><Button variant="ghost" className="px-2 py-1 text-[11px]" onClick={() => setClosing(null)}>انصراف</Button></span> : <Button variant="ghost" className="px-2 py-1 text-[11px]" onClick={() => setClosing({ id: comment.id, disposition: comment.disposition ?? "accepted" })}>بستن نظر</Button>) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {revisionId ? <CommentRegistrationModal
        open={modalOpen}
        document={revisionDetails?.doc ?? null}
        editing={editing}
        finalizing={Boolean(finalizingCommentId && editing?.id === finalizingCommentId)}
        canEditCoordinator={canEditCoordinator}
        form={form}
        setForm={setForm}
        files={files}
        onFiles={addFiles}
        onRemoveFile={(index) => setFiles((previous) => previous.filter((_, fileIndex) => fileIndex !== index))}
        existingAttachments={existingAttachments}
        removedAttachmentIds={removedAttachmentIds}
        onRemoveExisting={(id) => setRemovedAttachmentIds((previous) => [...previous, id])}
        onPreview={(attachment) => void openAttachmentPreview(attachment)}
        dragActive={dragActive}
        setDragActive={setDragActive}
        busy={busy}
        progress={progress}
        error={modalError}
        onSave={() => void saveRegistration()}
        onClose={closeModal}
      /> : null}

      <Modal
        open={previewAttachment !== null}
        title={previewAttachment ? `مشاهده پیوست — ${previewAttachment.file_name}` : "مشاهده پیوست"}
        onClose={closeAttachmentPreview}
        width="max-w-6xl"
        footer={
          <div className="flex items-center justify-between gap-2">
            <span className="text-[9.5px] text-[#5c6b78]">فایل با احراز هویت از انبار اسناد دریافت شده است.</span>
            <div className="flex items-center gap-2">
              {previewAttachment && !previewIsPdf ? (
                <Button
                  variant="primary"
                  className="px-2 py-1 text-[10px]"
                  disabled={previewDownloading}
                  onClick={() => void downloadCommentAttachment(previewAttachment)}
                >
                  {previewDownloading ? "در حال دریافت…" : "دریافت فایل اصلی"}
                </Button>
              ) : null}
              <Button variant="secondary" className="px-2 py-1 text-[10px]" onClick={closeAttachmentPreview}>بستن</Button>
            </div>
          </div>
        }
      >
        {previewActionError ? (
          <div className="mb-2 border border-[#c98a86] bg-[#fdeeee] px-2 py-1.5 text-[10px] text-[#8a2b25]" role="alert">
            {previewActionError}
          </div>
        ) : null}
        {previewAttachment ? (
          <AttachmentPreview
            attachment={toDocumentAttachment(previewAttachment)}
            url={previewUrl}
            loading={previewLoading}
            error={previewError}
            downloading={previewDownloading}
            opening={previewOpening}
            onDownload={() => void downloadCommentAttachment(previewAttachment)}
            onOpen={() => void openCommentAttachment(previewAttachment)}
          />
        ) : null}
      </Modal>

      <Modal open={answering !== null} title="پاسخ به نظر — Reply to Comment" onClose={() => setAnswering(null)} footer={<><Button variant="primary" onClick={() => void respond()} disabled={!answering || answering.text.trim().length < 2}>Save</Button><Button variant="secondary" onClick={() => setAnswering(null)}>Close</Button></>}>
        <div className="section-bar mb-1">Comment Information — اطلاعات نظر</div>
        <FieldGrid>
          <FField label="Related Comment:">{answering ? shortening(answering.comment.text) : "—"}</FField>
          <FField label="Code:">{answering?.comment.code ?? "—"}</FField>
          <FField label="Author:">{answering?.comment.author_name ?? "—"}</FField>
          <FField label="Created:">{answering ? jalaliDateTime(answering.comment.created_at) : "—"}</FField>
          <FField label="Reply / Clarification:" full><textarea className="input min-h-[80px] w-full" value={answering?.text ?? ""} onChange={(event) => setAnswering((previous) => (previous ? { ...previous, text: event.target.value } : previous))} placeholder="پاسخ پیمانکار / توضیح اصلاح" /></FField>
        </FieldGrid>
      </Modal>
    </div>
  );
}
