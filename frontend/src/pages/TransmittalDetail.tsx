import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  CommentList as CommentListType,
  DocumentRecord,
  ProjectMember,
  ReplySheet,
  ReplySheetRow,
  TransmittalDetail as Detail,
  TransmittalLine,
  TransmittalReferral,
} from "@/lib/types";
import { Button, EmptyState, ErrorBox, Modal, Spinner } from "@/components/ui";
import { AttachmentPreview } from "@/components/AttachmentPreview";
import { FieldGrid, FField, IconButton, Ribbon, StatusLamp, Window } from "@/components/window";
import {
  IconAdd,
  IconArchive,
  IconArrow,
  IconChain,
  IconClock,
  IconComment,
  IconDelete,
  IconDoc,
  IconEdit,
  IconFlow,
  IconLock,
  IconMail,
  IconPaperclip,
  IconPrint,
  IconRefresh,
  IconView,
} from "@/components/icons";
import { Tabs } from "@/components/Tabs";
import {
  ACTION_LABELS,
  DISPOSITION_LABELS,
  DOC_STATUS_OPTIONS,
  PURPOSE_LABELS,
  REFERRAL_STATUS_LABELS,
  STAGE_LABELS,
  STATUS_LABELS,
  TRANSMITTAL_TYPE_LABELS,
  docStatusOption,
  fileSize,
  jalali,
  jalaliDateTime,
  jalaliTime,
  persianDigits,
} from "@/lib/format";

type PersonnelReferralAction = "review" | "approve" | "info" | "for_construction" | "return_to_sender";

const PERSONNEL_REFERRAL_ACTIONS: Array<{ value: PersonnelReferralAction; label: string }> = [
  { value: "review", label: "For Review and Action — بررسی و اقدام" },
  { value: "approve", label: "For Follow Up — پیگیری" },
  { value: "info", label: "For Information — جهت اطلاع" },
  { value: "for_construction", label: "For Construction — جهت اجرا" },
  { value: "return_to_sender", label: "بازگشت به فرستنده" },
];

type RelatedAttachmentType = "letter" | "correspondence" | "meeting_minutes" | "image";

const RELATED_ATTACHMENT_TYPES: Array<{ value: RelatedAttachmentType; label: string }> = [
  { value: "letter", label: "نامه" },
  { value: "correspondence", label: "مکاتبه" },
  { value: "meeting_minutes", label: "صورتجلسه" },
  { value: "image", label: "تصویر" },
];

function relatedAttachmentLabel(value: string | null | undefined): string {
  return RELATED_ATTACHMENT_TYPES.find((item) => item.value === value)?.label ?? value ?? "پیوست مرتبط";
}

interface PersonnelReferralDraft {
  action: PersonnelReferralAction;
  note: string;
}

export function TransmittalDetail() {
  const { transmittalId } = useParams<{ transmittalId: string }>();
  const navigate = useNavigate();
  const { hasRole, user } = useAuth();
  const [data, setData] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [remarks, setRemarks] = useState("");
  const [comments, setComments] = useState<Record<string, CommentListType[]>>({});
  const [replySheetRows, setReplySheetRows] = useState<ReplySheetRow[]>([]);
  const [replySheetRecord, setReplySheetRecord] = useState<ReplySheet | null>(null);
  const [documentRecords, setDocumentRecords] = useState<DocumentRecord[]>([]);
  const [activeTab, setActiveTab] = useState("letters");
  // review route: flow history, referral dialog, and the roster the dialog needs
  const [flowOpen, setFlowOpen] = useState(false);
  const [referOpen, setReferOpen] = useState(false);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [referForm, setReferForm] = useState({ discipline_id: "", note: "", due_days: "14" });
  const [referError, setReferError] = useState<string | null>(null);
  const [forwardOpen, setForwardOpen] = useState(false);
  const [forwardForm, setForwardForm] = useState({ to_user_id: "", note: "", due_days: "7" });
  const [personnelReferOpen, setPersonnelReferOpen] = useState(false);
  const [personnelQuery, setPersonnelQuery] = useState("");
  const [personnelIds, setPersonnelIds] = useState<string[]>([]);
  const [personnelDrafts, setPersonnelDrafts] = useState<Record<string, PersonnelReferralDraft>>({});
  const [personnelReferError, setPersonnelReferError] = useState<string | null>(null);
  const [personnelReferBusy, setPersonnelReferBusy] = useState(false);
  const [flowLine, setFlowLine] = useState<TransmittalLine | null>(null);
  const [attachmentLineId, setAttachmentLineId] = useState<string | null>(null);
  const [attachmentKind, setAttachmentKind] = useState<"pdf" | "native">("pdf");
  const [relatedAttachmentType, setRelatedAttachmentType] = useState<RelatedAttachmentType>("letter");
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [editLine, setEditLine] = useState<TransmittalLine | null>(null);
  const [editForm, setEditForm] = useState({ title: "", status: "", reason: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [editAttachment, setEditAttachment] = useState<Detail["attachments"][number] | null>(null);
  const [attachmentEditForm, setAttachmentEditForm] = useState({ filename: "", kind: "pdf" as "pdf" | "native" });
  const [attachmentEditFile, setAttachmentEditFile] = useState<File | null>(null);
  const [attachmentEditError, setAttachmentEditError] = useState<string | null>(null);
  const [accessAttachment, setAccessAttachment] = useState<Detail["attachments"][number] | null>(null);
  const [accessMembers, setAccessMembers] = useState<ProjectMember[]>([]);
  const [accessUserIds, setAccessUserIds] = useState<string[]>([]);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [accessBusy, setAccessBusy] = useState(false);
  const [previewAttachment, setPreviewAttachment] = useState<Detail["attachments"][number] | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequestRef = useRef(0);
  const previewUrlRef = useRef<string | null>(null);
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const nativeInputRef = useRef<HTMLInputElement>(null);
  const relatedInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!transmittalId) return;
    setLoading(true);
    setError(null);
    try {
      const detail = await api.get<Detail>(`/transmittals/${transmittalId}`);
      setData(detail);
      setRemarks(detail.remarks ?? "");
      const revisionIds = Array.from(new Set(detail.lines.map((line) => line.revision_id)));
      const mdlItemIds = Array.from(
        new Set(detail.lines.map((line) => line.mdl_item_id).filter((id): id is string => Boolean(id))),
      );
      const [commentResults, recordResults, sheetRows, sheetRecord] = await Promise.all([
        Promise.all(
          revisionIds.map((id) =>
            api
              .get<CommentListType[]>(`/revisions/${id}/comment-lists`)
              .catch(() => [] as CommentListType[]),
          ),
        ),
        Promise.all(
          mdlItemIds.map((id) =>
            api.get<DocumentRecord[]>(`/mdl-items/${id}/document-records`).catch(() => [] as DocumentRecord[]),
          ),
        ),
        api.get<ReplySheetRow[]>(`/transmittals/${transmittalId}/reply-sheet`).catch(() => [] as ReplySheetRow[]),
        api.get<ReplySheet | null>(`/transmittals/${transmittalId}/reply-sheet/record`).catch(() => null),
      ]);
      setComments(Object.fromEntries(revisionIds.map((id, index) => [id, commentResults[index]])));
      setDocumentRecords(recordResults.flat());
      setReplySheetRows(sheetRows);
      setReplySheetRecord(sheetRecord ?? detail.reply_sheet ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری برگه ارسال ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [transmittalId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    [],
  );

  const act = async (path: string, body: unknown, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.post(path, body);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "عملیات ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spinner label="در حال بارگذاری برگه ارسال…" />;
  if (error) return <ErrorBox message={error} onRetry={() => void load()} />;
  if (!data) return null;

  const canManage =
    hasRole("dcc") || hasRole("project_control") || hasRole("pmo") || hasRole("project_manager") || hasRole("system_admin");
  const issued = ["issued", "final", "partially_acknowledged", "acknowledged", "closed"].includes(data.status);
  const isFinal = data.is_final || data.status === "final";
  const attachmentsLocked = issued || data.status === "cancelled";
  const isManualOwner = data.manual_workflow && data.created_by_id === user?.id;
  const isProjectManager = hasRole("project_manager");
  const isAfc = data.purpose === "AFC" || data.lines.some((line) => line.poi === "AFC");
  const canCoordinator = canManage || hasRole("coordinator");
  const canNoComment = canCoordinator && !isFinal && data.status !== "cancelled";
  const canFinalizeAfc = isAfc && isProjectManager && !isFinal && data.status !== "cancelled";
  const canFinalIssue = canManage || isManualOwner;
  // Any authenticated project member who can view this sheet may refer it;
  // the backend still enforces project membership and access-circle ACLs.
  const canReferPeople = Boolean(user);
  const allComments = Object.values(comments).flat().flatMap((list) => list.comments);
  const meta = data.meta as Record<string, string | undefined>;
  const commentsRows = data.lines.flatMap((line) =>
    (comments[line.revision_id] ?? []).flatMap((list) =>
      list.comments.map((comment) => ({
        ...comment,
        doc_code: line.doc_code,
        doc_title: line.title,
        rev: line.rev,
        discipline: line.discipline_code,
      })),
    ),
  );
  const displayedReplyRows: ReplySheetRow[] = replySheetRows.length
    ? replySheetRows
    : commentsRows.map((row) => ({
        id: row.id,
        reply_sheet_id: null,
        transmittal_line_id: null,
        revision_id: row.revision_id,
        comment_id: row.id,
        line_no: 0,
        doc_code: row.doc_code,
        title: row.doc_title,
        rev: row.rev,
        discipline: row.discipline,
        comment_code: row.code,
        comment_text: row.text,
        author_name: row.author_name,
        response_text: row.response_text,
        disposition: row.disposition,
        status: row.status,
        judgment: row.judgment,
        owner_comments: null,
        provider_clarification: null,
        assign_user_id: null,
        assign_user_name: null,
        doc_status: null,
        doc_percent: null,
        created_at: row.created_at,
        number: null,
        replied_by_id: null,
        replied_by_name: null,
        sheet_created_at: null,
        sheet_status: null,
        flow_history_count: 0,
        view_url: null,
      }));
  const revisionIds = Array.from(new Set(data.lines.map((line) => line.revision_id)));
  const nativeAttachments = (data.attachments ?? []).filter((attachment) => attachment.kind === "native");
  const relatedAttachments = (data.attachments ?? []).filter((attachment) => attachment.kind === "related");

  // --- review route -------------------------------------------------------
  const stage = data.stage ?? "draft";
  const isDcc = hasRole("dcc") || hasRole("project_control") || hasRole("pmo");
  const isManagement =
    hasRole("engineering_manager") || hasRole("project_control") || hasRole("pmo") || hasRole("project_manager");
  const canForward = isDcc && ["dcc", "draft"].includes(stage);
  const canRefer = isManagement && stage === "management";
  const pendingReferrals = (data.referrals ?? []).filter((item) => item.status === "pending");
  /** A referral only the assignee may close — the API enforces the same rule. */
  const myReferral = pendingReferrals.find((item) => item.to_user_id === user?.id);

  const loadMembers = async (): Promise<ProjectMember[]> => {
    if (members.length) return members;
    try {
      const roster = await api.get<ProjectMember[]>(`/projects/${data.project_id}/members`);
      setMembers(roster);
      return roster;
    } catch {
      setMembers([]);
      return [];
    }
  };

  const openRefer = async () => {
    setReferError(null);
    const roster = await loadMembers();
    const first = roster.find((member) => member.role === "reviewer" && member.discipline_id);
    setReferForm((form) => ({ ...form, discipline_id: form.discipline_id || first?.discipline_id || "" }));
    setReferOpen(true);
  };

  const openForward = async () => {
    setReferError(null);
    const roster = await loadMembers();
    const manager =
      roster.find((member) => member.role === "engineering_manager") ??
      roster.find((member) => member.role === "project_control") ??
      roster.find((member) => member.role === "pmo") ??
      roster.find((member) => member.role === "project_manager");
    setForwardForm((form) => ({ ...form, to_user_id: form.to_user_id || manager?.user_id || "" }));
    setForwardOpen(true);
  };

  const submitRefer = async () => {
    setReferError(null);
    if (!referForm.discipline_id) {
      setReferError("دیسیپلین مقصد را انتخاب کنید.");
      return;
    }
    setBusy(true);
    try {
      await api.post(`/transmittals/${data.id}/refer`, {
        discipline_id: referForm.discipline_id,
        action: "review",
        note: referForm.note || null,
        due_days: Number(referForm.due_days) || 14,
      });
      setReferOpen(false);
      setReferForm({ discipline_id: "", note: "", due_days: "14" });
      await load();
    } catch (err) {
      setReferError(err instanceof ApiError ? err.detail : "ارجاع ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const submitForward = async () => {
    setReferError(null);
    setBusy(true);
    try {
      await api.post(`/transmittals/${data.id}/forward`, {
        to_user_id: forwardForm.to_user_id || null,
        note: forwardForm.note || null,
        due_days: Number(forwardForm.due_days) || 7,
      });
      setForwardOpen(false);
      setForwardForm({ to_user_id: "", note: "", due_days: "7" });
      await load();
    } catch (err) {
      setReferError(err instanceof ApiError ? err.detail : "ارجاع به مدیریت ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const openPersonnelRefer = async () => {
    setPersonnelReferError(null);
    setPersonnelQuery("");
    setPersonnelIds([]);
    setPersonnelDrafts({});
    try {
      const roster = await loadMembers();
      setMembers(roster.filter((member) => member.is_active));
      setPersonnelReferOpen(true);
    } catch (err) {
      setPersonnelReferError(err instanceof ApiError ? err.detail : "دریافت فهرست پرسنل ناموفق بود.");
    }
  };

  const togglePersonnel = (userId: string) => {
    if (personnelIds.includes(userId)) {
      setPersonnelIds((current) => current.filter((id) => id !== userId));
      setPersonnelDrafts((drafts) => {
        const next = { ...drafts };
        delete next[userId];
        return next;
      });
      return;
    }
    setPersonnelIds((current) => [...current, userId]);
    setPersonnelDrafts((drafts) => ({
      ...drafts,
      [userId]: { action: "review", note: "" },
    }));
  };

  const updatePersonnelDraft = (userId: string, patch: Partial<PersonnelReferralDraft>) => {
    setPersonnelDrafts((drafts) => ({
      ...drafts,
      [userId]: { ...(drafts[userId] ?? { action: "review", note: "" }), ...patch },
    }));
  };

  const submitPersonnelRefer = async () => {
    if (!personnelIds.length) {
      setPersonnelReferError("حداقل یک نفر از پرسنل پروژه را انتخاب کنید.");
      return;
    }
    setPersonnelReferBusy(true);
    setPersonnelReferError(null);
    try {
      await api.post(`/transmittals/${data.id}/refer-people`, {
        // Keep user_ids for older API instances while the current backend uses
        // people[] for each person's independent action and note.
        user_ids: personnelIds,
        people: personnelIds.map((userId) => ({
          user_id: userId,
          action: personnelDrafts[userId]?.action ?? "review",
          note: personnelDrafts[userId]?.note.trim() || null,
        })),
        due_days: 14,
      });
      setPersonnelReferOpen(false);
      setPersonnelIds([]);
      setPersonnelDrafts({});
      await load();
    } catch (err) {
      setPersonnelReferError(err instanceof ApiError ? err.detail : "ارجاع مدرک ناموفق بود.");
    } finally {
      setPersonnelReferBusy(false);
    }
  };

  const completeMyReferral = async (referral: TransmittalReferral) => {
    const note = window.prompt("نتیجه بررسی خود را وارد کنید (اختیاری):") ?? undefined;
    setBusy(true);
    setActionError(null);
    try {
      await api.post(`/transmittals/${data.id}/referrals/${referral.id}/complete`, {
        note: note || null,
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "ثبت پاسخ ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const canEditDocuments = hasRole("dcc") || hasRole("project_control") || hasRole("pmo") || hasRole("system_admin");
  // Issued/green sheets and cancelled sheets are immutable. This is mirrored by
  // the backend guard; the UI also removes upload/edit/delete controls to avoid
  // misleading clicks.
  const canManageAttachments = !attachmentsLocked && (canManage || isManualOwner);
  const isTransmittalIssuer = Boolean(
    user && (user.is_superuser || [data.created_by_id, data.sender_id, data.issued_by_id].includes(user.id)),
  );
  // Native files are owned by the issuer. Do not require a DCC role here:
  // legitimate issuers can be submitters or senders without document-control privileges.
  const canManageNative = !attachmentsLocked && isTransmittalIssuer;
  // Letters are append-only supporting records: the issuer or document-control
  // role may add a later letter/minute/photo even after the transmittal is issued.
  const canManageRelatedAttachments = data.status !== "cancelled" && (canManage || isTransmittalIssuer);

  const openFlowForLine = (line?: TransmittalLine) => {
    setFlowLine(line ?? null);
    setFlowOpen(true);
  };

  const openLineEdit = (line: TransmittalLine) => {
    setEditError(null);
    setEditLine(line);
    setEditForm({ title: line.title ?? "", status: line.doc_status ?? "", reason: "" });
  };

  const saveLineEdit = async () => {
    if (!editLine?.mdl_item_id) {
      setEditError("شناسه مدرک برای ویرایش در دسترس نیست.");
      return;
    }
    setBusy(true);
    setEditError(null);
    try {
      await api.patch(`/projects/${data.project_id}/mdl/items/${editLine.mdl_item_id}`, {
        title: editForm.title.trim() || null,
        status: editForm.status || null,
        reason: editForm.reason.trim() || "ویرایش از فضای کاری برگه ارسال",
      });
      setEditLine(null);
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.detail : "ذخیره ویرایش مدرک ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const chooseAttachment = (line: TransmittalLine, kind: "pdf" | "native") => {
    setAttachmentError(null);
    setAttachmentLineId(line.id);
    setAttachmentKind(kind);
    if (kind === "pdf") pdfInputRef.current?.click();
    else nativeInputRef.current?.click();
  };

  const uploadAttachment = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const lineId = attachmentLineId;
    const kind = attachmentKind;
    event.target.value = "";
    if (!file || !lineId) return;
    setBusy(true);
    setAttachmentError(null);
    try {
      const form = new FormData();
      form.append("line_id", lineId);
      form.append("kind", kind);
      form.append("file", file);
      await api.postForm(`/transmittals/${data.id}/attachments`, form);
      await load();
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.detail : "بارگذاری پیوست ناموفق بود.");
    } finally {
      setBusy(false);
      setAttachmentLineId(null);
    }
  };

  const chooseRelatedAttachment = () => {
    setAttachmentError(null);
    relatedInputRef.current?.click();
  };

  const uploadRelatedAttachment = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setAttachmentError(null);
    try {
      const form = new FormData();
      form.append("related_type", relatedAttachmentType);
      form.append("file", file);
      await api.postForm(`/transmittals/${data.id}/related-attachments`, form);
      await load();
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.detail : "بارگذاری فایل مرتبط ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const downloadAttachment = async (attachment: Detail["attachments"][number]) => {
    try {
      await api.download(
        `/transmittals/${data.id}/attachments/${attachment.id}/download`,
        attachment.filename,
      );
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.detail : "دریافت پیوست ناموفق بود.");
    }
  };

  const viewDocumentRecord = async (record: DocumentRecord) => {
    if (!record.has_file) {
      if (record.transmittal_id) navigate(`/transmittals/${record.transmittal_id}`);
      else setActionError("فایل این بازنگری برای مشاهده در انبار اسناد موجود نیست.");
      return;
    }
    try {
      await api.download(
        `/files/revisions/${record.revision_id}`,
        `${record.doc_code ?? "document"}-Rev${record.version}.pdf`,
      );
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "مشاهده بازنگری ناموفق بود.");
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
  };

  const openAttachmentPreview = async (attachment: Detail["attachments"][number]) => {
    const requestId = previewRequestRef.current + 1;
    previewRequestRef.current = requestId;
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
    setPreviewAttachment(attachment);
    setPreviewUrl(null);
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const url = await api.blobUrl(
        `/transmittals/${data.id}/attachments/${attachment.id}/preview`,
      );
      if (previewRequestRef.current !== requestId) {
        URL.revokeObjectURL(url);
        return;
      }
      previewUrlRef.current = url;
      setPreviewUrl(url);
    } catch (err) {
      if (previewRequestRef.current === requestId) {
        setPreviewError(err instanceof ApiError ? err.detail : "نمایش پیوست ناموفق بود.");
      }
    } finally {
      if (previewRequestRef.current === requestId) setPreviewLoading(false);
    }
  };

  const deleteAttachment = async (attachment: Detail["attachments"][number]) => {
    if (!window.confirm(`پیوست «${attachment.filename}» حذف شود؟`)) return;
    setBusy(true);
    setAttachmentError(null);
    try {
      await api.delete(`/transmittals/${data.id}/attachments/${attachment.id}`);
      await load();
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.detail : "حذف پیوست ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const openAttachmentEdit = (attachment: Detail["attachments"][number]) => {
    if (attachment.kind === "related") return;
    setAttachmentEditError(null);
    setEditAttachment(attachment);
    setAttachmentEditForm({ filename: attachment.filename, kind: attachment.kind });
    setAttachmentEditFile(null);
  };

  const saveAttachmentEdit = async () => {
    if (!editAttachment) return;
    setBusy(true);
    setAttachmentEditError(null);
    try {
      const form = new FormData();
      form.append("filename", attachmentEditForm.filename.trim());
      form.append("kind", attachmentEditForm.kind);
      if (attachmentEditFile) form.append("file", attachmentEditFile);
      await api.patchForm(
        `/transmittals/${data.id}/attachments/${editAttachment.id}`,
        form,
      );
      setEditAttachment(null);
      await load();
    } catch (err) {
      setAttachmentEditError(err instanceof ApiError ? err.detail : "ویرایش پیوست ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const canViewAttachment = (attachment: Detail["attachments"][number]) => {
    if (attachment.kind !== "native" || !attachment.access_restricted) return true;
    const currentUserId = user?.id;
    return Boolean(
      currentUserId &&
        (user?.is_superuser ||
          attachment.access_user_ids?.includes(currentUserId) ||
          [data.created_by_id, data.sender_id, data.issued_by_id].includes(currentUserId)),
    );
  };

  const openAttachmentAccess = async (attachment: Detail["attachments"][number]) => {
    setAccessError(null);
    setAccessAttachment(attachment);
    setAccessUserIds(attachment.access_user_ids ?? (attachment.uploaded_by_id ? [attachment.uploaded_by_id] : []));
    const roster = await loadMembers();
    setAccessMembers(roster.filter((member) => member.is_active));
  };

  const saveAttachmentAccess = async () => {
    if (!accessAttachment) return;
    setAccessBusy(true);
    setAccessError(null);
    try {
      await api.patch(`/transmittals/${data.id}/attachments/${accessAttachment.id}/access`, {
        user_ids: accessUserIds,
      });
      setAccessAttachment(null);
      await load();
    } catch (err) {
      setAccessError(err instanceof ApiError ? err.detail : "ذخیره سطح دسترسی فایل ناموفق بود.");
    } finally {
      setAccessBusy(false);
    }
  };

  const toggleAttachmentAccess = (userId: string) => {
    setAccessUserIds((current) =>
      current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId],
    );
  };

  const disciplinesWithEngineer = members.filter(
    (member) => member.role === "reviewer" && member.discipline_id,
  );
  const personnelOptions = Array.from(
    new Map(
      members
        .filter((member) => member.is_active)
        .map((member) => [member.user_id, member]),
    ).values(),
  );
  const filteredPersonnel = personnelOptions.filter((member) => {
    const needle = personnelQuery.trim().toLowerCase();
    if (!needle) return true;
    return [member.full_name, member.email, member.discipline_name ?? "", member.discipline_code ?? ""]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
  const selectedPersonnel = personnelOptions.filter((member) => personnelIds.includes(member.user_id));

  return (
    <div>
      {/* ---------- Transmittal Workspace window ---------- */}
      <Window
        title={
          <span className="ltr" dir="ltr">
            Transmittal Workspace — {data.number ?? data.preview_number ?? "Draft"}
          </span>
        }
        onClose={() => window.history.back()}
        actions={
          <Link className="wt-btn" to="/transmittals" title="فهرست برگه‌های ارسال">
            ▤
          </Link>
        }
      >
        {/* identity strip: the reference prints these five values at the window's top */}
        <div className="workspace-identity mb-1">
          <div className="workspace-identity-main order-2 flex flex-1 flex-col items-center justify-center gap-1 px-3 py-1">
            <span className="text-[13px] font-bold tracking-tight text-[#1f3864]">Transmittal Workspace</span>
            <span className="flex items-center gap-1.5">
              <StatusLamp tone={issued ? "green" : "yellow"} />
              <span className="text-[10px] text-[#33475b]">
                {STATUS_LABELS[data.status] ?? data.status} ·{" "}
                {TRANSMITTAL_TYPE_LABELS[data.type] ?? data.type}
              </span>
              {/* review route: where the sheet is right now */}
              <span
                className="rounded-sm border border-[#a8a284] bg-[#f3f0df] px-1.5 py-[1px] text-[9.5px] font-bold text-[#20365c]"
                title="مرحله جاری گردش برگه ارسال"
              >
                مرحله: {data.stage_label ?? STAGE_LABELS[stage] ?? stage}
              </span>
            </span>
          </div>
          <div className="workspace-identity-meta order-1 text-[10px]">
            {[
              ["Transmittal Identity:", data.id.slice(0, 8).toUpperCase()],
              ["Transmittal No.:", data.number ?? data.preview_number ?? "—"],
              ["Date:", data.issue_date ? jalali(data.issue_date) : meta.issued_at_jalali ?? (data.issued_at ? jalali(data.issued_at) : "—")],
              ["Registration Time:", data.issued_at ? jalaliDateTime(data.issued_at) : "—"],
              ["Revision Time:", data.due_date ? jalali(data.due_date) : "—"],
            ].map(([label, value]) => (
              <div
                key={label}
                className="workspace-identity-meta-row grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-b border-[#e6e1c8] px-2 py-[2px] last:border-b-0"
              >
                <span className="text-right font-bold text-[#20365c]">{label}</span>
                <span
                  className="ltr break-words text-[#11202c]"
                  dir="ltr"
                  style={{ textAlign: label === "Transmittal No.:" ? "right" : "left" }}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {meta.numbering_warning ? (
          <div className="mb-1 border border-[#c98a86] bg-[#fdeeee] px-2 py-1.5 text-[10px] leading-5 text-[#8a2b25]">
            ⚠ {meta.numbering_warning}
          </div>
        ) : null}

        <div className="section-bar">Transmittal Information — اطلاعات برگه ارسال</div>
        <FieldGrid className="workspace-info-grid">
          <FField label="Transmittal Subject:" full>
            {data.lines.length > 0 ? (
              <span className="block w-full">
                {data.lines.map((line) => (
                  <span key={line.id} className="block break-words">
                    <span className="ltr font-bold text-[#17427a]" dir="ltr">{line.doc_code ?? "—"}</span>
                    <span> — {line.title ?? "بدون عنوان"}</span>
                  </span>
                ))}
              </span>
            ) : (
              <span>{data.remarks ?? "—"}</span>
            )}
          </FField>
          <FField label="Project:">{meta.project_name ?? "—"}</FField>
          <FField label="Contract:">
            <span className="ltr break-words" dir="ltr">{meta.contract_number ?? "—"}</span>
          </FField>
          <FField label="Received Letter No.:" muted>
            —
          </FField>
          <FField label="Issued by:">{meta.issued_by ?? "—"}</FField>
          <FField label="Contractor/Originator:">{data.from_org ?? meta.issued_by ?? "—"}</FField>
          <FField label="Coordinator/Originator:">
            {data.final_approver_name ?? data.issued_by_name ?? meta.issued_by ?? "—"}
          </FField>
          <FField label="Status:">{STATUS_LABELS[data.status] ?? data.status}</FField>
          <FField label="مرحله گردش:">{data.stage_label ?? STAGE_LABELS[stage] ?? stage}</FField>
          <FField label="ارجاع‌های باز:">
            {persianDigits(pendingReferrals.length)}
            {myReferral ? " — یکی از آن‌ها در کارتابل شماست" : ""}
          </FField>
          <FField label="P.O.I:">{PURPOSE_LABELS[data.purpose] ?? data.purpose}</FField>
          <FField label="Due Date:">{data.due_date ? jalali(data.due_date) : "در اسرع وقت"}</FField>
          <FField label="Recipients:">
            {persianDigits(data.acknowledged_count)} از {persianDigits(data.recipient_count)} تأیید شده
          </FField>
          <FField label="Remarks:" full muted={!data.remarks}>
            {data.remarks || undefined}
          </FField>
        </FieldGrid>

        {/* Real attachment controls; the hidden inputs keep the classic compact toolbar. */}
        <div className="mt-1 flex flex-wrap items-center gap-2 border border-[#a8a284] bg-white px-2 py-1">
          <span className="flex items-center gap-1 text-[10px] font-bold text-[#1f3864]">
            <IconPaperclip className="h-4 w-4" /> پیوست‌های مدرک / Document Attachments
          </span>
          <span className="text-[9.5px] text-[#5c6b78]">
            {persianDigits((data.attachments ?? []).length)} فایل برای {persianDigits(data.line_count)} مدرک
          </span>
          {attachmentsLocked ? (
            <span className="border border-[#9bc3a0] bg-[#eef8ef] px-1.5 py-0.5 text-[9.5px] font-bold text-[#286437]">
              این برگه نهایی یا لغو شده است؛ پیوست‌ها قفل شده‌اند.
            </span>
          ) : null}
          <span className="flex-1" />
          <button
            type="button"
            className="flex items-center gap-1 border border-[#8fa2b4] bg-[#eef3f8] px-1.5 py-0.5 text-[10px] font-bold text-[#1f3864] disabled:opacity-50"
            disabled={!canManageAttachments || busy || data.lines.length === 0}
            onClick={() => data.lines[0] && chooseAttachment(data.lines[0], "pdf")}
            title="ابتدا مدرک را از فهرست پیوست‌ها انتخاب کنید؛ این دکمه برای مدرک اول است"
          >
            <IconPaperclip className="h-3.5 w-3.5" /> PDF
          </button>
          <button
            type="button"
            className="flex items-center gap-1 border border-[#8fa2b4] bg-[#eef3f8] px-1.5 py-0.5 text-[10px] font-bold text-[#1f3864] disabled:opacity-50"
            disabled={!canManageNative || busy || data.lines.length === 0}
            onClick={() => data.lines[0] && chooseAttachment(data.lines[0], "native")}
            title="ابتدا مدرک را از فهرست پیوست‌ها انتخاب کنید؛ این دکمه برای مدرک اول است"
          >
            <IconArchive className="h-3.5 w-3.5" /> Native
          </button>
          <span className="text-[9px] text-[#8a97a3]">از دکمه‌های هر ردیف برای انتخاب مدرک دقیق استفاده کنید.</span>
          <input ref={pdfInputRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(event) => void uploadAttachment(event)} />
          <input
            ref={nativeInputRef}
            type="file"
            accept=".doc,.docx,.xls,.xlsx,.xlsm,.dwg,.dxf,.zip,.rar,.7z,.png,.jpg,.jpeg,.psd,.tif,.tiff,.ppt,.pptx,.csv,.txt,.ifc,.rvt,.skp,.step,.stp,.igs,.iges,.sldprt,.sldasm"
            className="hidden"
            onChange={(event) => void uploadAttachment(event)}
          />
        </div>

        {/* documents grid — the columns printed by the reference system */}
        <div className="mt-1 max-h-[38vh] overflow-auto border border-[#a8a284]">
          <table className="table-base">
            <thead>
              <tr>
                <th style={{ width: 30 }}>Row</th>
                <th style={{ width: 130 }}>Doc. No.</th>
                <th>Doc. Title</th>
                <th style={{ width: 62 }}>Discipline</th>
                <th style={{ width: 46 }}>Version</th>
                <th style={{ width: 48 }}>P.O.I</th>
                <th style={{ width: 88 }}>Doc. Status</th>
                <th style={{ width: 56 }}>Doc. %</th>
                <th style={{ width: 78 }}>MDL Status</th>
                <th style={{ width: 84 }}>Confidentiality</th>
                <th style={{ width: 74 }} className="cell-center">Related Forms</th>
                <th style={{ width: 46 }} className="cell-center">Chain</th>
                <th style={{ width: 74 }} className="cell-center">Assign To</th>
                <th style={{ width: 62 }} className="cell-center">Flow History</th>
                <th style={{ width: 48 }} className="cell-center">View</th>
                <th style={{ width: 48 }} className="cell-center">Edit</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.id}>
                  <td className="cell-center">{persianDigits(line.line_no)}</td>
                  <td className="break-words">
                    <span className="flex min-w-0 items-start gap-1">
                      <StatusLamp tone={line.status === "issued" ? "green" : "yellow"} />
                      <span className="ltr break-words font-bold text-[#17427a]" dir="ltr">{line.doc_code ?? "—"}</span>
                    </span>
                  </td>
                  <td>{line.title ?? "—"}</td>
                  <td className="text-center">{line.discipline_code ?? "—"}</td>
                  <td className="cell-center">{line.rev ?? "—"}</td>
                  <td className="cell-center">{data.purpose}</td>
                  <td className="text-center" title={docStatusOption(line.doc_status)?.description ?? ""}>
                    {docStatusOption(line.doc_status) ? (
                      <span className="block">
                        <span className="font-bold">{docStatusOption(line.doc_status)?.label}</span>
                        {docStatusOption(line.doc_status)?.description ? (
                          <span className="mt-0.5 block text-[8px] leading-3 text-[#5c6b78]">
                            {docStatusOption(line.doc_status)?.description}
                          </span>
                        ) : null}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="cell-center">—</td>
                  <td className="text-center">{STATUS_LABELS[line.status] ?? line.status}</td>
                  <td className="text-center">Normal</td>
                  <td className="cell-center">
                    <Link to={`/documents/${line.revision_id}/comments`} aria-label="Comment List">
                      <IconButton icon={<IconComment />} label="Comment List" />
                    </Link>
                  </td>
                  <td className="cell-center">
                    <IconButton icon={<IconChain />} label="زنجیره مدارک" disabled />
                  </td>
                  <td className="cell-center">
                    <IconButton icon={<IconArrow />} label="ارجاع" disabled />
                  </td>
                  <td className="cell-center">
                    <IconButton
                      icon={<IconFlow />}
                      label="گردش مدرک"
                      disabled={busy}
                      onClick={() => openFlowForLine(line)}
                    />
                  </td>
                  <td className="cell-center">
                    <IconButton
                      icon={<IconView />}
                      label="مشاهده مدرک"
                      onClick={() =>
                        api.download(
                          `/files/revisions/${line.revision_id}`,
                          `${line.doc_code ?? "document"}-Rev${line.rev ?? ""}.pdf`,
                        )
                      }
                    />
                  </td>
                  <td className="cell-center">
                    <IconButton
                      icon={<IconEdit />}
                      label="ویرایش مدرک و وضعیت"
                      disabled={!canEditDocuments || !line.mdl_item_id || busy}
                      onClick={() => openLineEdit(line)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-1 border border-[#a8a284] bg-white">
          <div className="flex items-center justify-between border-b border-[#a8a284] bg-[#eef3f8] px-2 py-1">
            <span className="flex items-center gap-1 text-[10.5px] font-bold text-[#1f3864]">
              <IconDoc className="h-3.5 w-3.5" /> Attachment List — فهرست و مدیریت پیوست‌ها
            </span>
            <span className="text-[9px] text-[#5c6b78]">PDF و فایل Native با احراز هویت دریافت می‌شوند.</span>
          </div>
          <div className="max-h-[25vh] overflow-auto">
            {data.lines.map((line) => {
              const lineAttachments = (data.attachments ?? []).filter((attachment) => attachment.line_id === line.id);
              return (
                <div key={line.id} className="flex flex-wrap items-center gap-2 border-b border-[#e6e1c8] px-2 py-1 last:border-b-0">
                  <div className="min-w-[15rem] flex-1">
                    <span className="ltr font-bold text-[#17427a]">{line.doc_code ?? "—"}</span>
                    <span className="mx-1 text-[#8a97a3]">—</span>
                    <span className="text-[10px]">{line.title ?? "بدون عنوان"}</span>
                    <span className="mx-1 text-[9px] text-[#5c6b78]">({persianDigits(lineAttachments.length)} پیوست)</span>
                  </div>
                  <button
                    type="button"
                    className="flex items-center gap-1 border border-[#8fa2b4] bg-[#f7f8f2] px-1.5 py-0.5 text-[9.5px] font-bold text-[#1f3864] disabled:opacity-50"
                    disabled={!canManageAttachments || busy}
                    onClick={() => chooseAttachment(line, "pdf")}
                  >
                    <IconPaperclip className="h-3 w-3" /> افزودن PDF
                  </button>
                  <button
                    type="button"
                    className="flex items-center gap-1 border border-[#8fa2b4] bg-[#f7f8f2] px-1.5 py-0.5 text-[9.5px] font-bold text-[#1f3864] disabled:opacity-50"
                    disabled={!canManageNative || busy}
                    onClick={() => chooseAttachment(line, "native")}
                  >
                    <IconArchive className="h-3 w-3" /> افزودن Native
                  </button>
                  {lineAttachments.length === 0 ? (
                    <span className="text-[9.5px] text-[#8a97a3]">پیوستی ثبت نشده است.</span>
                  ) : (
                    <div className="flex min-w-full flex-wrap items-center gap-1 border-t border-dashed border-[#d4ceb0] pt-1">
                      {lineAttachments.map((attachment) => {
                        const accessible = canViewAttachment(attachment);
                        return (
                          <div key={attachment.id} className="flex items-center gap-1 border border-[#b9c5cf] bg-[#fbfaf2] px-1 py-0.5 text-[9.5px]">
                            <span className="rounded-sm border border-[#a8a284] bg-white px-1 font-bold text-[#33475b]">{attachment.kind.toUpperCase()}</span>
                            <span className="max-w-[18rem] truncate" title={attachment.filename}>{attachment.filename}</span>
                            <span className="text-[#7a8794]">{fileSize(attachment.size_bytes)}</span>
                            <IconButton icon={accessible ? <IconView /> : <IconLock />} label={accessible ? "مشاهده پیوست" : "دسترسی مشاهده ندارید"} disabled={!accessible || busy} onClick={() => void openAttachmentPreview(attachment)} />
                            <IconButton icon={<IconArchive />} label={accessible ? "دانلود پیوست" : "دسترسی دانلود ندارید"} disabled={!accessible || busy} onClick={() => void downloadAttachment(attachment)} />
                            {canManageAttachments && (attachment.kind !== "native" || canManageNative) ? (
                              <IconButton icon={<IconEdit />} label="ویرایش پیوست" onClick={() => openAttachmentEdit(attachment)} />
                            ) : null}
                            {canManageAttachments && (attachment.kind !== "native" || canManageNative) ? (
                              <IconButton icon={<IconDelete />} label="حذف پیوست" disabled={busy} onClick={() => void deleteAttachment(attachment)} />
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {attachmentError ? <div className="border-t border-[#c98a86] bg-[#fdeeee] px-2 py-1 text-[10px] text-[#8a2b25]">{attachmentError}</div> : null}
        </div>

        {/* the window's ribbon: only actions that really exist are enabled */}
        <Ribbon
          items={[
            { label: "بازخوانی", icon: <IconRefresh />, onClick: () => void load(), disabled: busy },
            {
              label: "وضعیت گردش کارها",
              icon: <IconFlow />,
              onClick: () => setActiveTab("comments"),
            },
            { label: "WorkSpace", icon: <IconDoc />, onClick: () => setActiveTab("letters") },
            { label: "گردش مدارک", icon: <IconChain />, groupStart: true, disabled: true, title: "فاز ۲" },
            { label: "لیست پاراف", icon: <IconEdit />, disabled: true, title: "فاز ۲" },
            // --- the review route: only the role that owns the step sees the button
            ...(canForward
              ? [
                  {
                    label: "ارسال به مدیریت",
                    icon: <IconArrow />,
                    groupStart: true,
                    disabled: busy,
                    title: "گردش مدرک — کنترل اسناد ← مدیر مهندسی/مدیر پروژه",
                    onClick: () => void openForward(),
                  },
                ]
              : []),
            ...(canRefer
              ? [
                  {
                    label: "ارجاع به دیسیپلین",
                    icon: <IconFlow />,
                    groupStart: !canForward,
                    disabled: busy,
                    title: "گردش مدرک — مدیر مهندسی ← کارشناس دیسیپلین",
                    onClick: () => void openRefer(),
                  },
                ]
              : []),
            ...(myReferral
              ? [
                  {
                    label: "پایان بررسی",
                    icon: <IconChain />,
                    disabled: busy,
                    title: `ارجاع باز برای شما تا ${myReferral.due_date ? jalali(myReferral.due_date) : "—"}`,
                    onClick: () => void completeMyReferral(myReferral),
                  },
                ]
              : []),
            {
              label: "گردش مدرک",
              icon: <IconFlow />,
              disabled: busy,
              onClick: () => openFlowForLine(),
            },
            {
              label: "یادآوری",
              icon: <IconClock />,
              groupStart: true,
              disabled: busy || !issued,
              onClick: () => void act(`/transmittals/${data.id}/remind`, {}),
            },
            {
              label: "مشاهده مدرک",
              icon: <IconPrint />,
              onClick: () => api.download(`/transmittals/${data.id}/pdf`, `${data.number ?? "transmittal"}.pdf`),
            },
            {
              label: "دریافت Reply Sheet",
              icon: <IconComment />,
              disabled: busy,
              onClick: () => api.download(`/transmittals/${data.id}/reply-sheet.pdf`, `${replySheetRecord?.number ?? data.number ?? "reply-sheet"}-reply-sheet.pdf`),
            },
            {
              label: "تأییدیه دریافت",
              icon: <IconMail />,
              disabled: busy || !issued,
              onClick: () => void act(`/transmittals/${data.id}/acknowledge`, { note: null }),
            },
            ...(canNoComment
              ? [
                  {
                    label: "ثبت No Comment",
                    icon: <IconComment />,
                    groupStart: true,
                    disabled: busy,
                    title: "تکمیل Reply Sheet با Judgment = OK و Doc % = 100",
                    onClick: () =>
                      void act(
                        `/transmittals/${data.id}/reply-sheet/no-comment`,
                        { remarks: remarks || null },
                        "برای همه مدارک این ترانسمیتال No Comment و Judgment = OK ثبت شود؟",
                      ),
                  },
                ]
              : []),
            ...(canFinalizeAfc
              ? [
                  {
                    label: "No Comment & Finalize",
                    icon: <IconLock />,
                    groupStart: true,
                    disabled: busy,
                    title: "فقط Project Manager برای P.O.I = AFC مجاز است",
                    onClick: () =>
                      void act(
                        `/transmittals/${data.id}/reply-sheet/no-comment-finalize`,
                        { remarks: remarks || null },
                        "نسخه AFC با No Comment نهایی و غیرقابل ویرایش می‌شود. ادامه می‌دهید؟",
                      ),
                  },
                ]
              : []),
            ...(attachmentsLocked
              ? []
              : [
                  {
                    label: "صدور نهایی",
                    icon: <IconAdd />,
                    groupStart: true,
                    disabled: busy || !canFinalIssue || isAfc,
                    title: isAfc ? "برای AFC فقط No Comment & Finalize توسط Project Manager مجاز است" : canFinalIssue ? "صدور نهایی ترانسمیتال" : "فقط ایجادکننده یا مسئول کنترل مجاز است",
                    onClick: () =>
                      void act(
                        `/transmittals/${data.id}/issue`,
                        { remarks: remarks || null, notify: true },
                        "پس از صدور، شماره نهایی تخصیص می‌یابد و محتوای برگه قابل ویرایش نیست. ادامه می‌دهید؟",
                      ),
                  },
                ]),
            ...(data.os_available && isProjectManager
              ? [
                  {
                    label: "ایجاد OS از AFC",
                    icon: <IconArchive />,
                    groupStart: true,
                    disabled: busy,
                    title: "ایجاد پیش‌نویس OS با Revision B0",
                    onClick: () => void act(`/transmittals/${data.id}/reply-sheet/create-os`, {}),
                  },
                ]
              : []),
            ...(issued && canReferPeople
              ? [
                  {
                    label: "ارجاع به پرسنل",
                    icon: <IconArrow />,
                    groupStart: true,
                    disabled: busy,
                    title: "ارجاع مدرک به یک یا چند عضو فعال پروژه",
                    onClick: () => void openPersonnelRefer(),
                  },
                ]
              : []),
            {
              label: "ابطال",
              icon: <IconDelete />,
              danger: true,
              disabled: busy,
              onClick: () => {
                const reason = window.prompt("دلیل ابطال را وارد کنید:");
                if (reason) void act(`/transmittals/${data.id}/cancel`, { reason });
              },
            },
          ]}
        />

        {actionError ? <ErrorBox message={actionError} /> : null}

        {!attachmentsLocked ? (
          <div className="mt-1 flex items-center gap-2 border border-[#a8a284] bg-white px-2 py-1">
            <span className="text-[10px] font-bold text-[#20365c]">توضیحات صدور (Remarks):</span>
            <input
              className="input flex-1"
              placeholder="پیش از صدور می‌توانید توضیحات برگه را وارد کنید"
              value={remarks}
              onChange={(event) => setRemarks(event.target.value)}
            />
            <span className="text-[9px] text-[#5c6b78]">{canManage ? "دسترسی مدیریت: فعال" : "فقط مشاهده"}</span>
          </div>
        ) : null}

        {/* ---------- tabbed workspace ---------- */}
        <div className="mt-1 border border-[#a8a284] bg-white">
          <Tabs
          tabs={[
            {
              key: "letters",
              label: "Letters",
              count: data.lines.length,
              content: (
                <div className="max-h-[46vh] overflow-auto">
                  <div className="border-t border-[#a8a284] bg-[#fbfaf2]">
                    <div className="flex flex-wrap items-center gap-2 border-b border-[#d4ceb0] px-2 py-1">
                      <span className="flex items-center gap-1 text-[10.5px] font-bold text-[#1f3864]">
                        <IconPaperclip className="h-3.5 w-3.5" /> سوابق مرتبط — Related Records: نامه، مکاتبه، صورتجلسه و تصویر
                      </span>
                      <span className="text-[9px] text-[#5c6b78]">
                        {persianDigits(relatedAttachments.length)} فایل مرتبط با این ترانسمیتال
                      </span>
                      <span className="flex-1" />
                      <select
                        className="input h-6 w-auto min-w-[7.5rem] py-0 text-[9.5px]"
                        value={relatedAttachmentType}
                        onChange={(event) => setRelatedAttachmentType(event.target.value as RelatedAttachmentType)}
                        disabled={!canManageRelatedAttachments || busy}
                        aria-label="نوع فایل مرتبط"
                      >
                        {RELATED_ATTACHMENT_TYPES.map((item) => (
                          <option key={item.value} value={item.value}>{item.label}</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="flex items-center gap-1 border border-[#1f4e79] bg-[#eef3f8] px-2 py-1 text-[9.5px] font-bold text-[#1f3864] hover:bg-[#e0ebf5] disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={!canManageRelatedAttachments || busy}
                        onClick={chooseRelatedAttachment}
                        title="افزودن نامه، مکاتبه، صورتجلسه یا تصویر مرتبط با این ترانسمیتال"
                      >
                        <IconAdd className="h-3.5 w-3.5" /> افزودن فایل مرتبط
                      </button>
                      <input
                        ref={relatedInputRef}
                        type="file"
                        accept=".pdf,.doc,.docx,.xls,.xlsx,.xlsm,.ppt,.pptx,.txt,.csv,.zip,.rar,.7z,.png,.jpg,.jpeg,.gif,.bmp,.tif,.tiff,.webp"
                        className="hidden"
                        onChange={(event) => void uploadRelatedAttachment(event)}
                      />
                    </div>
                    <div className="max-h-[22vh] overflow-auto">
                      <table className="table-base">
                        <thead>
                          <tr>
                            <th className="cell-center" style={{ width: 34 }}>Row</th>
                            <th style={{ width: 96 }}>نوع</th>
                            <th>نام فایل</th>
                            <th style={{ width: 92 }}>تاریخ ثبت</th>
                            <th style={{ width: 120 }}>ثبت‌کننده</th>
                            <th className="cell-center" style={{ width: 48 }}>View</th>
                            <th className="cell-center" style={{ width: 64 }}>Download</th>
                            <th className="cell-center" style={{ width: 48 }}>Delete</th>
                          </tr>
                        </thead>
                        <tbody>
                          {relatedAttachments.length === 0 ? (
                            <tr>
                              <td colSpan={8} className="cell-center py-3 text-[9.5px] text-[#5c6b78]">
                                هنوز نامه، مکاتبه، صورتجلسه یا تصویر مرتبطی ثبت نشده است.
                              </td>
                            </tr>
                          ) : (
                            relatedAttachments.map((attachment, index) => (
                              <tr key={attachment.id}>
                                <td className="cell-center">{persianDigits(index + 1)}</td>
                                <td className="font-bold">{relatedAttachmentLabel(attachment.related_type)}</td>
                                <td>
                                  <span className="ltr block truncate font-bold" title={attachment.filename}>{attachment.filename}</span>
                                  <span className="text-[9px] text-[#5c6b78]">{fileSize(attachment.size_bytes)} · {attachment.content_type}</span>
                                </td>
                                <td className="cell-center whitespace-nowrap text-[9.5px]">{jalali(attachment.uploaded_at)}</td>
                                <td className="truncate text-[9.5px]">{attachment.uploaded_by_name ?? "—"}</td>
                                <td className="cell-center">
                                  <IconButton icon={<IconView />} label="مشاهده فایل مرتبط" disabled={busy} onClick={() => void openAttachmentPreview(attachment)} />
                                </td>
                                <td className="cell-center">
                                  <IconButton icon={<IconArchive />} label="دانلود فایل مرتبط" disabled={busy} onClick={() => void downloadAttachment(attachment)} />
                                </td>
                                <td className="cell-center">
                                  {canManageAttachments ? (
                                    <IconButton icon={<IconDelete />} label="حذف فایل مرتبط" disabled={busy} onClick={() => void deleteAttachment(attachment)} />
                                  ) : <span>—</span>}
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              ),
            },
            {
              key: "comments",
              label: "Comment List",
              count: allComments.length,
              countClassName: allComments.length > 0 ? "font-extrabold text-[#c0392b]" : undefined,
              content: (
                <div>
                  {/* reference toolbar above the comment grid */}
                  <div className="flex flex-wrap items-center gap-3 border-b border-[#a8a284] bg-[#fbfaf2] px-2 py-1">
                    {data.lines[0] ? (
                      <Link
                        className="flex items-center gap-1 text-[10px] font-bold text-[#1f4e79] hover:underline"
                        to={`/documents/${data.lines[0].revision_id}/comments`}
                        aria-label="Show All Comment"
                      >
                        <IconComment className="h-3.5 w-3.5" /> Show All Comment
                      </Link>
                    ) : (
                      <span className="flex items-center gap-1 text-[10px] text-[#8a97a3]">
                        <IconComment className="h-3.5 w-3.5" /> Show All Comment
                      </span>
                    )}
                    <span className="flex-1" />
                    <span className="text-[9.5px] text-[#5c6b78]">
                      Comment List: {persianDigits(allComments.length)} مورد
                    </span>
                  </div>
                  <div className="max-h-[46vh] overflow-auto">
                  {allComments.length === 0 ? (
                    <EmptyState title="No items have been registered…" description="برای این بازنگری‌ها نظری ثبت نشده است." />
                  ) : (
                    <table className="table-base">
                      <thead>
                        <tr>
                          <th style={{ width: 30 }}>Row</th>
                          <th style={{ width: 130 }}>Doc. No.</th>
                          <th style={{ width: 40 }}>Code</th>
                          <th>Comment</th>
                          <th style={{ width: 52 }}>Status</th>
                          <th style={{ width: 110 }}>Author</th>
                          <th style={{ width: 96 }}>Created</th>
                          <th style={{ width: 60 }} className="cell-center">Judgment</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.lines.map((line) => {
                          const list = (comments[line.revision_id] ?? []).flatMap((entry) => entry.comments);
                          if (list.length === 0) {
                            return (
                              <tr key={line.id}>
                                <td className="cell-center">{persianDigits(line.line_no)}</td>
                                <td className="ltr">{line.doc_code ?? "—"}</td>
                                <td className="cell-center">—</td>
                                <td>NO COMMENT</td>
                                <td className="cell-center">—</td>
                                <td>—</td>
                                <td>—</td>
                                <td className="cell-center">OK</td>
                              </tr>
                            );
                          }
                          return list.map((comment, index) => (
                            <tr key={comment.id}>
                              <td className="cell-center">{persianDigits(index + 1)}</td>
                              <td className="ltr">{line.doc_code ?? "—"}</td>
                              <td className="cell-center">{comment.code}</td>
                              <td className="max-w-[26rem]">{comment.text}</td>
                              <td className="text-center">{comment.status}</td>
                              <td>{comment.author_name ?? "—"}</td>
                              <td className="whitespace-nowrap text-[10px]">{jalali(comment.created_at)}</td>
                              <td className="cell-center">{comment.status === "closed" ? "OK" : "—"}</td>
                            </tr>
                          ));
                        })}
                      </tbody>
                    </table>
                  )}
                  </div>
                </div>
              ),
            },
            {
              key: "native",
              label: "Native Files",
              content: (
                <div>
                  <div className="flex flex-wrap items-center gap-2 border-b border-[#a8a284] bg-[#fbfaf2] px-2 py-1">
                    <span className="text-[9.5px] text-[#5c6b78]">
                      فایل اصلی طراحی و تولید مدرک؛ فقط صادرکننده می‌تواند آن را بارگذاری کند.
                    </span>
                    <span className="flex-1" />
                    <button
                      type="button"
                      className="flex items-center gap-1 border border-[#1a5327] bg-gradient-to-b from-[#6fba72] to-[#2f7838] px-2 py-1 text-[10px] font-bold text-white shadow-sm hover:from-[#82c883] hover:to-[#3f8f45] disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={!canManageNative || busy || data.lines.length === 0}
                      onClick={() => data.lines[0] && chooseAttachment(data.lines[0], "native")}
                      title="افزودن فایل Native برای اولین مدرک این ترانسمیتال"
                    >
                      <IconAdd className="h-4 w-4 rounded-full bg-[#2b6e34] p-0.5 text-white" /> Add Native File
                    </button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="table-base">
                      <thead>
                        <tr>
                          <th style={{ width: 42 }}>Row</th>
                          <th>Title</th>
                          <th style={{ width: 92 }}>Publish Date</th>
                          <th style={{ width: 110 }}>Publisher</th>
                          <th>Details</th>
                          <th className="cell-center" style={{ width: 48 }}>Edit</th>
                          <th className="cell-center" style={{ width: 48 }}>View</th>
                          <th className="cell-center" style={{ width: 62 }}>Download</th>
                          <th className="cell-center" style={{ width: 72 }}>Set Access</th>
                        </tr>
                      </thead>
                      <tbody>
                        {nativeAttachments.length === 0 ? (
                          <tr>
                            <td colSpan={9} className="cell-center py-4 text-[10px] text-[#5c6b78]">
                              فایل Native ثبت نشده است.
                            </td>
                          </tr>
                        ) : (
                          nativeAttachments.map((attachment, index) => {
                            const line = data.lines.find((item) => item.id === attachment.line_id);
                            const accessible = canViewAttachment(attachment);
                            return (
                              <tr key={attachment.id}>
                                <td className="cell-center">{persianDigits(index + 1)}</td>
                                <td>
                                  <div className="font-bold">{line?.title ?? attachment.filename}</div>
                                  <div className="ltr text-[9px] text-[#5c6b78]">{line?.doc_code ?? "—"}</div>
                                </td>
                                <td className="whitespace-nowrap text-[10px]">{jalali(attachment.uploaded_at)}</td>
                                <td className="text-[10px]">{attachment.uploaded_by_name ?? "—"}</td>
                                <td>
                                  <div className="ltr truncate font-mono text-[10px]" title={attachment.filename}>{attachment.filename}</div>
                                  <div className="text-[9px] text-[#5c6b78]">{fileSize(attachment.size_bytes)} · {attachment.content_type}</div>
                                  {!accessible ? <span className="mt-0.5 inline-flex items-center gap-1 text-[9px] font-bold text-[#9a3d32]"><IconLock className="h-3 w-3" /> دسترسی محدود</span> : null}
                                </td>
                                <td className="cell-center">
                                  {canManageNative ? <IconButton icon={<IconEdit />} label="ویرایش فایل Native" disabled={busy} onClick={() => openAttachmentEdit(attachment)} /> : <span>—</span>}
                                </td>
                                <td className="cell-center">
                                  <IconButton icon={<IconView />} label={accessible ? "مشاهده فایل Native" : "دسترسی مشاهده ندارید"} disabled={!accessible || busy} onClick={() => void openAttachmentPreview(attachment)} />
                                </td>
                                <td className="cell-center">
                                  <IconButton icon={<IconArchive />} label={accessible ? "دانلود فایل Native" : "دسترسی دانلود ندارید"} disabled={!accessible || busy} onClick={() => void downloadAttachment(attachment)} />
                                </td>
                                <td className="cell-center">
                                  {canManageNative ? <IconButton icon={<IconLock />} label="تعیین سطح دسترسی" disabled={busy} onClick={() => void openAttachmentAccess(attachment)} /> : <span>—</span>}
                                </td>
                              </tr>
                            );
                          })
                        )}
                      </tbody>
                    </table>
                  </div>
                  <p className="px-2 py-1 text-[9.5px] leading-5 text-[#5c6b78]">
                    فایل‌های Native شامل Word، Excel، DWG، ZIP، RAR، PNG، PSD و سایر فایل‌های اصلی هستند. مشاهده و دانلود فقط برای کاربران تعیین‌شده و با احراز هویت انجام می‌شود.
                  </p>
                </div>
              ),
            },
            {
              key: "records",
              label: "Document Records",
              content: (
                <div className="overflow-x-auto">
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th style={{ width: 34 }}>Row</th>
                        <th style={{ width: 150 }}>Doc. No.</th>
                        <th>Doc. Title</th>
                        <th style={{ width: 112 }}>Transmittal Type</th>
                        <th style={{ width: 78 }}>Discipline</th>
                        <th style={{ width: 66 }}>Version</th>
                        <th style={{ width: 92 }}>Status</th>
                        <th className="cell-center" style={{ width: 58 }}>Chain</th>
                        <th style={{ width: 150 }}>Assign To</th>
                        <th className="cell-center" style={{ width: 78 }}>Flow History</th>
                        <th className="cell-center" style={{ width: 52 }}>View</th>
                        <th className="cell-center" style={{ width: 64 }}>View W.S.</th>
                      </tr>
                    </thead>
                    <tbody>
                      {documentRecords.length === 0 ? (
                        <tr>
                          <td colSpan={12} className="cell-center py-4 text-[10px] text-[#5c6b78]">
                            سابقه‌ای برای این Doc. No. ثبت نشده است.
                          </td>
                        </tr>
                      ) : (
                        documentRecords.map((record, index) => (
                          <tr
                            key={record.id}
                            className={record.is_latest ? "bg-[#f4fbf4]" : "bg-[#f1f2f3] text-[#68737d]"}
                          >
                            <td className="cell-center">{persianDigits(index + 1)}</td>
                            <td className="ltr font-bold">{record.doc_code ?? "—"}</td>
                            <td title={record.title ?? ""}>
                              <span className={record.is_latest ? "font-bold text-[#1f3864]" : "font-medium"}>
                                {record.title ?? "—"}
                              </span>
                              {record.is_latest ? (
                                <span className="mr-1 inline-flex rounded-sm border border-[#79a67d] bg-[#eaf7eb] px-1 text-[8.5px] font-bold text-[#2f7139]">
                                  Latest
                                </span>
                              ) : null}
                            </td>
                            <td className="text-[10px]">
                              {TRANSMITTAL_TYPE_LABELS[record.transmittal_type ?? ""] ?? record.transmittal_type ?? "—"}
                            </td>
                            <td className="text-center">{record.discipline ?? "—"}</td>
                            <td className="cell-center ltr font-bold">{record.version}</td>
                            <td className="cell-center">
                              {record.is_latest ? (
                                <span className="font-bold text-[#2f7139]">
                                  Latest · {STATUS_LABELS[record.status] ?? record.status}
                                </span>
                              ) : (
                                <span className="font-bold text-[#737b82]">Void</span>
                              )}
                            </td>
                            <td className="cell-center">
                              <IconButton
                                icon={<IconChain />}
                                label={`زنجیره ${persianDigits(record.chain_count)} بازنگری`}
                                onClick={() => setActiveTab("records")}
                              />
                            </td>
                            <td className="max-w-[9rem] truncate text-[9.5px]" title={record.assigned_to ?? ""}>
                              {record.assigned_to ?? "—"}
                            </td>
                            <td className="cell-center">
                              {record.transmittal_id ? (
                                <IconButton
                                  icon={<IconFlow />}
                                  label={`Flow History (${persianDigits(record.flow_history_count)})`}
                                  onClick={() => navigate(`/transmittals/${record.transmittal_id}`)}
                                />
                              ) : (
                                <span>—</span>
                              )}
                            </td>
                            <td className="cell-center">
                              <IconButton
                                icon={<IconView />}
                                label="مشاهده بازنگری"
                                disabled={!record.has_file && !record.transmittal_id}
                                onClick={() => void viewDocumentRecord(record)}
                              />
                            </td>
                            <td className="cell-center">
                              {record.transmittal_id ? (
                                <IconButton
                                  icon={<IconDoc />}
                                  label="مشاهده فضای کاری"
                                  onClick={() => navigate(`/transmittals/${record.transmittal_id}`)}
                                />
                              ) : (
                                <span>—</span>
                              )}
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              ),
            },
            {
              key: "reply",
              label: "Reply Sheet",
              count: data.recipient_count,
              content: (
                <div className="p-[3px]">
                  <div className="mb-1 flex items-center justify-between border border-[#a8a284] bg-white px-2 py-1">
                    <span className="text-[11px] font-bold text-[#1f3864]">Reply Sheet — برگه پاسخ</span>
                    <span className="ltr text-[10px] text-[#33475b]">
                      Reply Sheet No.: {replySheetRecord?.number ?? (data.number ? `${data.number}-R01` : "—")}
                    </span>
                  </div>
                  <FieldGrid>
                    <FField label="Subject:" full>
                      {data.lines[0]?.title ?? "—"}
                    </FField>
                    <FField label="Transmittal:">
                      {data.number ? (
                        <span className="ltr font-bold text-[#17427a]">{data.number}</span>
                      ) : (
                        "—"
                      )}
                    </FField>
                    <FField label="Replied by:">{replySheetRecord?.replied_by_name ?? meta.issued_by ?? "—"}</FField>
                    <FField label="Created:">{replySheetRecord?.created_at ? jalaliDateTime(replySheetRecord.created_at) : "—"}</FField>
                    <FField label="Status:">{replySheetRecord ? (STATUS_LABELS[replySheetRecord.status] ?? replySheetRecord.status) : "—"}</FField>
                    <FField label="Assign:">{replySheetRecord?.assigned_to_name ?? "—"}</FField>
                    <FField label="Flow History:">
                      {persianDigits(replySheetRecord?.flow_history.length ?? 0)}
                    </FField>
                    <FField label="Doc Status:">{replySheetRecord?.doc_status ?? "—"}</FField>
                    <FField label="Doc %:">{replySheetRecord ? `${persianDigits(replySheetRecord.doc_percent)}٪` : "—"}</FField>
                    <FField label="Acknowledgements:">
                      {persianDigits(data.acknowledged_count)} از {persianDigits(data.recipient_count)}
                    </FField>
                    <FField label="Issued:" full>
                      {data.issued_at ? jalaliDateTime(data.issued_at) : "—"}
                    </FField>
                  </FieldGrid>

                  <div className="mt-1 max-h-[30vh] overflow-auto border border-[#a8a284]">
                    <table className="table-base">
                      <thead>
                        <tr>
                          <th style={{ width: 30 }}>Row</th>
                          <th style={{ width: 24 }} className="cell-center">✓</th>
                          <th style={{ width: 120 }}>Doc. No.</th>
                          <th>Doc. Title</th>
                          <th style={{ width: 44 }}>Ver.</th>
                          <th style={{ width: 70 }}>Discipline</th>
                          <th style={{ width: 80 }}>Comment Date</th>
                          <th style={{ width: 100 }}>Create By</th>
                          <th style={{ width: 78 }}>Status</th>
                          <th style={{ width: 62 }} className="cell-center">Judgment</th>
                          <th style={{ width: 110 }}>Owner's Comments</th>
                          <th style={{ width: 110 }}>Provider Clarification / Reply</th>
                          <th style={{ width: 62 }} className="cell-center">Chain</th>
                          <th style={{ width: 52 }} className="cell-center">View</th>
                        </tr>
                      </thead>
                      <tbody>
                        {displayedReplyRows.length === 0 ? (
                          <tr>
                            <td colSpan={14} className="cell-center text-[10px] text-[#5c6b78]">
                              No items have been registered…
                            </td>
                          </tr>
                        ) : (
                          displayedReplyRows.map((row, index) => (
                            <tr key={row.id ?? `${row.revision_id ?? "row"}-${index}`}>
                              <td className="cell-center">{persianDigits(index + 1)}</td>
                              <td className="cell-center">
                                <input type="checkbox" defaultChecked={row.status === "closed" || row.status === "No Comment"} aria-label="انتخاب" />
                              </td>
                              <td className="ltr whitespace-nowrap font-bold text-[#17427a]">{row.doc_code ?? "—"}</td>
                              <td className="truncate">{row.title ?? "—"}</td>
                              <td className="cell-center">{row.rev ?? "—"}</td>
                              <td className="text-center">{row.discipline ?? "—"}</td>
                              <td className="whitespace-nowrap text-center text-[10px]">{row.created_at ? jalali(row.created_at) : "—"}</td>
                              <td className="text-[10px]">{row.author_name ?? row.replied_by_name ?? "—"}</td>
                              <td className="text-center text-[10px]">
                                {row.disposition ? (DISPOSITION_LABELS[row.disposition] ?? row.disposition) : row.status}
                              </td>
                              <td className="cell-center text-[10px]">{row.judgment ?? "—"}</td>
                              <td className="max-w-[9rem] truncate text-[10px]">{row.owner_comments ?? row.comment_text ?? "—"}</td>
                              <td className="max-w-[9rem] truncate text-[10px]">{row.provider_clarification ?? row.response_text ?? "—"}</td>
                              <td className="cell-center">
                                <IconButton icon={<IconChain />} label="زنجیره مدارک" disabled />
                              </td>
                              <td className="cell-center">
                                {row.revision_id ? (
                                  <Link to={`/documents/${row.revision_id}/comments`} aria-label="مشاهده فهرست نظرها">
                                    <IconButton icon={<IconView />} label="مشاهده" />
                                  </Link>
                                ) : "—"}
                              </td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>

                  {/* The four creation rules printed under the reference Reply Sheet. */}
                  <div className="mt-1 border border-[#a8a284] bg-[#fffdf3] px-3 py-2 text-[10px] leading-5 text-[#3b4b57]">
                    <p className="mb-1 font-bold text-[#1f3864]">قوانین ایجاد فریند:</p>
                    <p>۱. براساس پروژه، ایجاد اولین فرایند در صورت وجود حداقل یک کامنت به ازای هر سند موجود در ارسال، امکان‌پذیر می‌باشد.</p>
                    <p>۲. براساس پروژه، ایجاد فرایند در صورتی امکان‌پذیر است که در فرایند قبلی تمام کامنت‌ها تعیین تکلیف شده باشند و هیچ سندی از اسناد ارسال شده وضعیت «Under Review» به‌لحاظ تأیید یا Return نداشته باشد.</p>
                    <p>۳. براساس پروژه، ایجاد فرایند در صورتی امکان‌پذیر است که تمام کامنت‌ها از یک نوع باشند (Accept یا Ignore).</p>
                    <p>۴. در صورتی که نظر Coordinator برای یک کامنت انتخاب شده باشد، کامنت مذکور در فرایند جاری به‌صورت اتوماتیک حذف می‌شود.</p>
                  </div>
                </div>
              ),
            },
            {
              key: "delivery",
              label: "Delivery & archive of copies",
              content: (
                <div className="p-2">
                  <table className="kv-table">
                    <tbody>
                      <tr>
                        <th>نسخه بایگانی</th>
                        <td className="ltr">{data.number ? `${data.number}.pdf` : "پس از صدور ساخته می‌شود"}</td>
                        <th>تاریخ بایگانی</th>
                        <td>{data.issued_at ? jalaliDateTime(data.issued_at) : "—"}</td>
                      </tr>
                      <tr>
                        <th>مسیر ذخیره‌سازی</th>
                        <td className="ltr" colSpan={3}>
                          {data.number ? `transmittals/${data.number}/${data.id}.pdf` : "—"}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    <Button className="btn-sm" onClick={() => api.download(`/transmittals/${data.id}/pdf`, `${data.number ?? "transmittal"}.pdf`)}>
                      دریافت نسخه بایگانی
                    </Button>
                    <Button className="btn-sm" onClick={() => window.print()}>
                      چاپ
                    </Button>
                  </div>
                  <p className="mt-1.5 text-[9.5px] leading-5 text-[#5c6b78]">
                    نسخه بایگانی همان PDF امضاشده سیستم است که هنگام صدور تولید و در MinIO نگه‌داری می‌شود؛
                    در محیط توسعه بدون MinIO، از ReportLab استفاده می‌شود.
                  </p>
                </div>
              ),
            },
            ]}
            active={activeTab}
            onActiveChange={setActiveTab}
          />
        </div>
      </Window>


      {/* ---------- گردش مدرک — the full referral history ---------- */}
      <Modal
        open={flowOpen}
        title={`گردش مدرک — ${flowLine?.doc_code ?? data.number ?? "برگه ارسال"} — Flow History`}
        onClose={() => { setFlowOpen(false); setFlowLine(null); }}
        width="max-w-4xl"
        footer={
          <div className="flex items-center justify-between">
            <span className="text-[9.5px] text-[#5c6b78]">
              مرحله جاری: {data.stage_label ?? STAGE_LABELS[stage] ?? stage}
            </span>
            <Button className="btn-sm" onClick={() => setFlowOpen(false)}>
              بستن
            </Button>
          </div>
        }
      >
        <div className="mb-1 border border-[#a8a284] bg-[#fffdf3] px-2 py-1 text-[10px] leading-5 text-[#3b4b57]">
          <span className="font-bold text-[#1f3864]">مدرک منتخب:</span>{" "}
          <span className="ltr font-bold">{flowLine?.doc_code ?? "—"}</span>{" "}
          <span>{flowLine?.title ?? "گردش کامل برگه ارسال"}</span>
          <span className="mx-1 text-[#7a8794]">·</span>
          <span>تاریخچه ارجاع‌های همین برگه ارسال در زیر نمایش داده می‌شود.</span>
        </div>
        <table className="table-base">
          <thead>
            <tr>
              <th style={{ width: 34 }}>ردیف</th>
              <th style={{ width: 84 }}>وضعیت</th>
              <th style={{ width: 138 }}>فرستنده</th>
              <th style={{ width: 138 }}>گیرنده</th>
              <th style={{ width: 74 }}>دیسیپلین</th>
              <th style={{ width: 96 }}>عملیات ارجاع</th>
              <th>دستور ارجاع</th>
              <th style={{ width: 84 }}>تاریخ دریافت</th>
              <th style={{ width: 84 }}>تاریخ پاسخ</th>
            </tr>
          </thead>
          <tbody>
            {(data.referrals ?? []).length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-[10px] text-[#5c6b78]">
                  هنوز ارجاعی ثبت نشده است.
                </td>
              </tr>
            ) : (
              (data.referrals ?? []).map((referral, index) => (
                <tr key={referral.id}>
                  <td className="cell-center">{persianDigits(index + 1)}</td>
                  <td className="text-center">
                    <span className="flex items-center justify-center gap-1">
                      <StatusLamp tone={referral.status === "pending" ? "yellow" : "green"} />
                      {REFERRAL_STATUS_LABELS[referral.status] ?? referral.status}
                    </span>
                  </td>
                  <td className="truncate">{referral.from_user_name ?? "—"}</td>
                  <td className="truncate">{referral.to_user_name ?? "—"}</td>
                  <td className="text-center">{referral.discipline_code ?? "—"}</td>
                  <td className="text-center">{ACTION_LABELS[referral.action] ?? referral.action}</td>
                  <td className="truncate" title={referral.note ?? ""}>
                    {referral.note || "—"}
                  </td>
                  <td className="cell-center whitespace-nowrap">
                    {referral.created_at ? (
                      <span className="inline-flex flex-col items-center leading-4">
                        <span>{jalali(referral.created_at)}</span>
                        <span className="text-[9px] text-[#5c6b78]" dir="ltr">{jalaliTime(referral.created_at)}</span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="cell-center">
                    {referral.responded_at ? jalali(referral.responded_at) : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <p className="mt-1 border border-[#a8a284] bg-[#fffdf3] px-2 py-1 text-[9.5px] leading-5 text-[#3b4b57]">
          مسیر گردش: پیمانکار ← کارشناس کنترل اسناد مهندسی ← مدیر مهندسی/مدیر پروژه ←
          کارشناس دیسیپلین ← مدیر مهندسی. هر ارجاع تا زمانی که گیرنده پاسخ نداده «در انتظار
          پاسخ» است و برگه از کارتابل او خارج نمی‌شود.
        </p>
      </Modal>

      {/* ---------- سطح دسترسی فایل Native ---------- */}
      <Modal
        open={Boolean(accessAttachment)}
        title={accessAttachment ? `Set Access — ${accessAttachment.filename}` : "Set Access"}
        onClose={() => {
          if (!accessBusy) setAccessAttachment(null);
        }}
        width="max-w-2xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button className="btn-sm" disabled={accessBusy} onClick={() => setAccessAttachment(null)}>
              انصراف
            </Button>
            <Button className="btn-sm btn-primary" disabled={accessBusy || !accessAttachment} onClick={() => void saveAttachmentAccess()}>
              {accessBusy ? "در حال ذخیره…" : "ذخیره سطح دسترسی"}
            </Button>
          </div>
        }
      >
        {accessError ? <ErrorBox message={accessError} /> : null}
        <div className="mb-2 border border-[#a8a284] bg-[#fffdf3] px-2 py-1.5 text-[10px] leading-5 text-[#5c4b18]">
          فقط کاربران انتخاب‌شده می‌توانند فایل Native را مشاهده یا دانلود کنند. صادرکننده و بارگذار فایل همیشه دسترسی خود را حفظ می‌کنند.
        </div>
        <div className="max-h-72 overflow-auto border border-[#c3ccd6] bg-white">
          {accessMembers.length === 0 ? (
            <p className="px-2 py-4 text-center text-[10px] text-[#5c6b78]">عضو فعالی برای تعیین دسترسی پیدا نشد.</p>
          ) : (
            accessMembers.map((member) => (
              <label key={member.user_id} className="flex cursor-pointer items-center gap-2 border-b border-[#e6e1c8] px-2 py-1.5 text-[10px] last:border-b-0 hover:bg-[#f7f8f2]">
                <input
                  type="checkbox"
                  checked={accessUserIds.includes(member.user_id)}
                  onChange={() => toggleAttachmentAccess(member.user_id)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block font-bold text-[#20365c]">{member.full_name}</span>
                  <span className="block truncate text-[9px] text-[#5c6b78]">{member.role} · {member.email}</span>
                </span>
                {accessUserIds.includes(member.user_id) ? <span className="text-[9px] font-bold text-[#3f8f45]">مجاز</span> : null}
              </label>
            ))
          )}
        </div>
      </Modal>

      {/* ---------- مشاهده واقعی PDF و فایل Native ---------- */}
      <Modal
        open={Boolean(previewAttachment)}
        title={previewAttachment ? `مشاهده پیوست — ${previewAttachment.filename}` : "مشاهده پیوست"}
        onClose={closeAttachmentPreview}
        width="max-w-6xl"
        footer={
          <div className="flex items-center justify-between gap-2">
            <span className="text-[9.5px] text-[#5c6b78]">
              فایل از انبار اسناد و با احراز هویت دریافت شده است.
            </span>
            <div className="flex items-center gap-2">
              {previewAttachment ? (
                <Button className="btn-sm" onClick={() => void downloadAttachment(previewAttachment)}>
                  دریافت فایل
                </Button>
              ) : null}
              <Button className="btn-sm" onClick={closeAttachmentPreview}>
                بستن
              </Button>
            </div>
          </div>
        }
      >
        {previewAttachment ? (
          <AttachmentPreview
            attachment={previewAttachment}
            url={previewUrl}
            loading={previewLoading}
            error={previewError}
            onDownload={() => void downloadAttachment(previewAttachment)}
          />
        ) : null}
      </Modal>

      {/* ---------- ویرایش/جایگزینی واقعی پیوست ---------- */}
      <Modal
        open={Boolean(editAttachment)}
        title="ویرایش پیوست — Attachment Edit"
        onClose={() => setEditAttachment(null)}
        width="max-w-xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button className="btn-sm" onClick={() => setEditAttachment(null)}>
              انصراف
            </Button>
            <Button className="btn-sm btn-primary" disabled={busy || !canManageAttachments} onClick={() => void saveAttachmentEdit()}>
              ذخیره پیوست
            </Button>
          </div>
        }
      >
        {attachmentEditError ? <ErrorBox message={attachmentEditError} /> : null}
        {editAttachment ? (
          <FieldGrid>
            <FField label="نام فایل:" full>
              <input
                className="input ltr"
                value={attachmentEditForm.filename}
                onChange={(event) => setAttachmentEditForm({ ...attachmentEditForm, filename: event.target.value })}
              />
            </FField>
            <FField label="نوع پیوست:">
              <select
                className="input"
                value={attachmentEditForm.kind}
                onChange={(event) => setAttachmentEditForm({ ...attachmentEditForm, kind: event.target.value as "pdf" | "native" })}
              >
                <option value="pdf">PDF</option>
                <option value="native">Native</option>
              </select>
            </FField>
            <FField label="جایگزینی فایل:" full>
              <input
                className="input"
                type="file"
                accept={attachmentEditForm.kind === "pdf" ? "application/pdf,.pdf" : undefined}
                onChange={(event) => setAttachmentEditFile(event.target.files?.[0] ?? null)}
              />
              <span className="mt-0.5 block text-[9.5px] leading-5 text-[#5c6b78]">
                در صورت انتخاب فایل، bytes قبلی در انبار ذخیره‌سازی با نسخه جدید جایگزین می‌شود؛ بدون فایل فقط نام پیوست قابل تغییر است و تغییر نوع نیازمند فایل معتبر جدید است.
              </span>
            </FField>
          </FieldGrid>
        ) : null}
      </Modal>

      {/* ---------- ویرایش واقعی ردیف MDL از فضای کاری مدرک ---------- */}
      <Modal
        open={Boolean(editLine)}
        title="ویرایش مدرک — Document Edit"
        onClose={() => setEditLine(null)}
        width="max-w-2xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button className="btn-sm" onClick={() => setEditLine(null)}>
              انصراف
            </Button>
            <Button className="btn-sm btn-primary" disabled={busy || !canEditDocuments} onClick={() => void saveLineEdit()}>
              ذخیره و بازخوانی
            </Button>
          </div>
        }
      >
        {editError ? <ErrorBox message={editError} /> : null}
        {editLine ? (
          <FieldGrid>
            <FField label="Doc. No.:">
              <span className="ltr font-bold text-[#17427a]">{editLine.doc_code ?? "—"}</span>
            </FField>
            <FField label="Revision:">
              <span className="ltr">{editLine.rev ?? "—"}</span>
            </FField>
            <FField label="Doc. Title:" full>
              <input
                className="input"
                value={editForm.title}
                onChange={(event) => setEditForm({ ...editForm, title: event.target.value })}
              />
            </FField>
            <FField label="Doc. Status:" full>
              <select
                className="input"
                value={editForm.status}
                onChange={(event) => setEditForm({ ...editForm, status: event.target.value })}
              >
                <option value="">— بدون وضعیت —</option>
                {editForm.status && !DOC_STATUS_OPTIONS.some((option) => option.value === editForm.status) ? (
                  <option value={editForm.status}>{editForm.status}</option>
                ) : null}
                {DOC_STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value} title={option.description}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span className="mt-0.5 block text-[9.5px] leading-5 text-[#5c6b78]">
                Approved: تأیید کامل · Commented: اصلاح و ارسال مجدد · Approved As Noted: تأیید مشروط · Reject: عودت به دلیل ایراد عمده
              </span>
            </FField>
            <FField label="دلیل ویرایش:" full>
              <input
                className="input"
                value={editForm.reason}
                placeholder="برای ثبت در تاریخچه و ممیزی"
                onChange={(event) => setEditForm({ ...editForm, reason: event.target.value })}
              />
            </FField>
          </FieldGrid>
        ) : null}
      </Modal>

      {/* ---------- ارجاع به دیسیپلین (مدیر مهندسی) ---------- */}
      <Modal
        open={referOpen}
        title="ارجاع مدرک — Refer To Discipline"
        onClose={() => setReferOpen(false)}
        width="max-w-2xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button className="btn-sm" onClick={() => setReferOpen(false)}>
              انصراف
            </Button>
            <Button className="btn-sm btn-primary" disabled={busy} onClick={() => void submitRefer()}>
              ارجاع
            </Button>
          </div>
        }
      >
        {referError ? <ErrorBox message={referError} /> : null}
        <FieldGrid>
          <FField label="برگه ارسال:">
            <span className="ltr">{data.number ?? data.preview_number ?? "Draft"}</span>
          </FField>
          <FField label="گیرنده:">
            <select
              className="input"
              value={referForm.discipline_id}
              onChange={(event) => setReferForm({ ...referForm, discipline_id: event.target.value })}
            >
              <option value="">— انتخاب دیسیپلین —</option>
              {disciplinesWithEngineer.map((member) => (
                <option key={member.discipline_id ?? member.id} value={member.discipline_id ?? ""}>
                  {member.discipline_name ?? member.discipline_code} — {member.full_name}
                </option>
              ))}
            </select>
          </FField>
          <FField label="دستور ارجاع:" full>
            <input
              className="input"
              value={referForm.note}
              placeholder="مثلاً: بررسی و اعلام نظر در خصوص نقشه‌های پیوست"
              onChange={(event) => setReferForm({ ...referForm, note: event.target.value })}
            />
          </FField>
          <FField label="مهلت پاسخ (روز):">
            <input
              className="input ltr"
              type="number"
              min={1}
              max={120}
              value={referForm.due_days}
              onChange={(event) => setReferForm({ ...referForm, due_days: event.target.value })}
            />
          </FField>
          <FField label="عملیات ارجاع:">بررسی و اعلام نظر</FField>
        </FieldGrid>
        <p className="mt-1 text-[9.5px] leading-5 text-[#5c6b78]">
          هر دیسیپلین یک کارشناس دارد: برق، سیویل، معماری و تاسیسات. اگر برای دیسیپلین
          انتخاب‌شده کارشناسی تعریف نشده باشد، سامانه ارجاع را نمی‌پذیرد.
        </p>
      </Modal>

      {/* ---------- ارجاع پس از صدور به چند عضو فعال پروژه ---------- */}
      <Modal
        open={personnelReferOpen}
        title="ارجاع مدرک به پرسنل پروژه"
        onClose={() => setPersonnelReferOpen(false)}
        width="max-w-2xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button className="btn-sm" onClick={() => setPersonnelReferOpen(false)}>
              انصراف
            </Button>
            <Button
              className="btn-sm btn-primary"
              disabled={personnelReferBusy || personnelIds.length === 0}
              onClick={() => void submitPersonnelRefer()}
            >
              {personnelReferBusy ? "در حال ثبت…" : "ثبت ارجاع"}
            </Button>
          </div>
        }
      >
        {personnelReferError ? <ErrorBox message={personnelReferError} /> : null}
        <div className="mb-2 border border-[#a8a284] bg-[#fffdf3] px-2 py-1.5 text-[10px] leading-5 text-[#5c4b18]">
          این عملیات فقط پس از صدور نهایی انجام می‌شود. یک یا چند عضو فعال همین پروژه را انتخاب کنید؛ هر نفر ارجاع مستقل و اعلان جداگانه دریافت می‌کند.
        </div>
        <FieldGrid>
          <FField label="برگه ارسال:">
            <span className="ltr">{data.number ?? data.preview_number ?? "Draft"}</span>
          </FField>
          <FField label="افراد انتخاب‌شده:">
            <span>{persianDigits(personnelIds.length)} نفر</span>
          </FField>
          <FField label="جست‌وجوی پرسنل:" full>
            <input
              className="input"
              value={personnelQuery}
              placeholder="نام، رایانامه یا دیسیپلین…"
              onChange={(event) => setPersonnelQuery(event.target.value)}
            />
          </FField>
          <FField label="انتخاب پرسنل و تنظیم ارجاع:" full>
            <div className="grid grid-cols-2 gap-1.5" aria-label="انتخاب اعضا و تنظیم ارجاع">
              <section className="min-w-0 border border-[#c3ccd6] bg-white">
                <div className="border-b border-[#c3ccd6] bg-[#eef3f8] px-2 py-1 text-[10px] font-bold text-[#1f3864]">
                  اعضای فعال پروژه ({persianDigits(personnelOptions.length)})
                </div>
                <div className="max-h-72 overflow-auto">
                  {filteredPersonnel.length === 0 ? (
                    <p className="px-2 py-4 text-center text-[10px] text-[#5c6b78]">عضو فعالی با این جست‌وجو پیدا نشد.</p>
                  ) : (
                    filteredPersonnel.map((member) => (
                      <label
                        key={member.user_id}
                        className="flex cursor-pointer items-center gap-2 border-b border-[#e6e1c8] px-2 py-1.5 text-[10px] last:border-b-0 hover:bg-[#f7f8f2]"
                      >
                        <input
                          type="checkbox"
                          checked={personnelIds.includes(member.user_id)}
                          onChange={() => togglePersonnel(member.user_id)}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block font-bold text-[#20365c]">{member.full_name}</span>
                          <span className="block truncate text-[9px] text-[#5c6b78]">{member.discipline_name ?? member.discipline_code ?? "عضو پروژه"} · {member.email}</span>
                        </span>
                        {personnelIds.includes(member.user_id) ? <span className="text-[9px] font-bold text-[#3f8f45]">انتخاب‌شده</span> : null}
                      </label>
                    ))
                  )}
                </div>
              </section>

              <section className="min-w-0 border border-[#c3ccd6] bg-white">
                <div className="border-b border-[#c3ccd6] bg-[#eef3f8] px-2 py-1 text-[10px] font-bold text-[#1f3864]">
                  افراد انتخاب‌شده ({persianDigits(personnelIds.length)})
                </div>
                <div className="max-h-72 overflow-auto">
                  {selectedPersonnel.length === 0 ? (
                    <p className="px-2 py-4 text-center text-[10px] text-[#5c6b78]">
                      ابتدا یک یا چند نفر را از فهرست اعضای فعال پروژه انتخاب کنید.
                    </p>
                  ) : (
                    selectedPersonnel.map((member) => {
                      const draft = personnelDrafts[member.user_id] ?? { action: "review" as PersonnelReferralAction, note: "" };
                      return (
                        <div key={member.user_id} className="border-b border-[#e6e1c8] px-2 py-1.5 last:border-b-0">
                          <div className="mb-1 flex items-center gap-2">
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[10px] font-bold text-[#20365c]">{member.full_name}</span>
                              <span className="block truncate text-[9px] text-[#5c6b78]">{member.discipline_name ?? member.discipline_code ?? "عضو پروژه"} · {member.email}</span>
                            </span>
                            <button
                              type="button"
                              className="shrink-0 text-[9px] font-bold text-[#97282b] underline"
                              onClick={() => togglePersonnel(member.user_id)}
                            >
                              حذف
                            </button>
                          </div>
                          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-1.5">
                            <label className="block">
                              <span className="mb-0.5 block text-[9px] font-bold text-[#5c6b78]">عملیات ارجاع</span>
                              <select
                                className="input"
                                value={draft.action}
                                onChange={(event) => updatePersonnelDraft(member.user_id, { action: event.target.value as PersonnelReferralAction })}
                              >
                                {PERSONNEL_REFERRAL_ACTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                              </select>
                            </label>
                            <label className="block">
                              <span className="mb-0.5 block text-[9px] font-bold text-[#5c6b78]">هامش کوتاه برای این شخص</span>
                              <input
                                className="input"
                                maxLength={500}
                                value={draft.note}
                                placeholder="مثلاً: بررسی و اعلام نظر تا پایان هفته"
                                onChange={(event) => updatePersonnelDraft(member.user_id, { note: event.target.value })}
                              />
                            </label>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </section>
            </div>
          </FField>
        </FieldGrid>
      </Modal>

      {/* ---------- ارسال به مدیریت (کارشناس کنترل اسناد) ---------- */}
      <Modal
        open={forwardOpen}
        title="ارجاع به مدیر مهندسی — Forward To Management"
        onClose={() => setForwardOpen(false)}
        width="max-w-2xl"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button className="btn-sm" onClick={() => setForwardOpen(false)}>
              انصراف
            </Button>
            <Button className="btn-sm btn-primary" disabled={busy} onClick={() => void submitForward()}>
              ارسال
            </Button>
          </div>
        }
      >
        {referError ? <ErrorBox message={referError} /> : null}
        <FieldGrid>
          <FField label="برگه ارسال:">
            <span className="ltr">{data.number ?? data.preview_number ?? "Draft"}</span>
          </FField>
          <FField label="مدیر مقصد:">
            <select
              className="input"
              value={forwardForm.to_user_id}
              onChange={(event) => setForwardForm({ ...forwardForm, to_user_id: event.target.value })}
            >
              <option value="">— پیش‌فرض: مدیر مهندسی / PMO —</option>
              {members
                .filter((member) => ["engineering_manager", "project_control", "pmo", "project_manager"].includes(member.role))
                .map((member) => (
                  <option key={member.user_id} value={member.user_id}>
                    {member.role === "engineering_manager" ? "مدیر مهندسی" : member.role === "project_control" ? "کنترل پروژه" : member.role === "pmo" ? "واحد PMO" : "مدیر پروژه"} —{" "}
                    {member.full_name}
                  </option>
                ))}
            </select>
          </FField>
          <FField label="توضیح کنترل:" full>
            <input
              className="input"
              value={forwardForm.note}
              placeholder="مثلاً: مدارک کنترل و پیوست شد؛ جهت بررسی و ارجاع"
              onChange={(event) => setForwardForm({ ...forwardForm, note: event.target.value })}
            />
          </FField>
          <FField label="مهلت (روز):">
            <input
              className="input ltr"
              type="number"
              min={1}
              max={90}
              value={forwardForm.due_days}
              onChange={(event) => setForwardForm({ ...forwardForm, due_days: event.target.value })}
            />
          </FField>
        </FieldGrid>
      </Modal>

      <p className="text-center text-[9.5px] text-[#5c6b78]">
        {revisionIds.length} بازنگری در این برگه · {persianDigits(allComments.length)} نظر ثبت‌شده
      </p>
      <input type="hidden" value={remarks} readOnly />
    </div>
  );
}
