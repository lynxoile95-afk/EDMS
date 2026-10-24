import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  CommentList as CommentListType,
  ProjectMember,
  TransmittalDetail as Detail,
  TransmittalLine,
  TransmittalReferral,
} from "@/lib/types";
import { Button, EmptyState, ErrorBox, Modal, Spinner } from "@/components/ui";
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
  PURPOSE_LABELS,
  REFERRAL_STATUS_LABELS,
  STAGE_LABELS,
  STATUS_LABELS,
  jalali,
  jalaliDateTime,
  persianDigits,
} from "@/lib/format";

export function TransmittalDetail() {
  const { transmittalId } = useParams<{ transmittalId: string }>();
  const { hasRole, user } = useAuth();
  const [data, setData] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [remarks, setRemarks] = useState("");
  const [comments, setComments] = useState<Record<string, CommentListType[]>>({});
  const [records, setRecords] = useState<Record<string, TransmittalLine[]>>({});
  const [activeTab, setActiveTab] = useState("letters");
  // review route: flow history, referral dialog, and the roster the dialog needs
  const [flowOpen, setFlowOpen] = useState(false);
  const [referOpen, setReferOpen] = useState(false);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [referForm, setReferForm] = useState({ discipline_id: "", note: "", due_days: "14" });
  const [referError, setReferError] = useState<string | null>(null);
  const [forwardOpen, setForwardOpen] = useState(false);
  const [forwardForm, setForwardForm] = useState({ to_user_id: "", note: "", due_days: "7" });

  const load = useCallback(async () => {
    if (!transmittalId) return;
    setLoading(true);
    setError(null);
    try {
      const detail = await api.get<Detail>(`/transmittals/${transmittalId}`);
      setData(detail);
      setRemarks(detail.remarks ?? "");
      const revisionIds = Array.from(new Set(detail.lines.map((line) => line.revision_id)));
      const [commentResults, recordResults] = await Promise.all([
        Promise.all(
          revisionIds.map((id) =>
            api
              .get<CommentListType[]>(`/revisions/${id}/comment-lists`)
              .catch(() => [] as CommentListType[]),
          ),
        ),
        Promise.all(
          revisionIds.map((id) =>
            api.get<TransmittalLine[]>(`/revisions/${id}/transmittals`).catch(() => [] as TransmittalLine[]),
          ),
        ),
      ]);
      setComments(Object.fromEntries(revisionIds.map((id, index) => [id, commentResults[index]])));
      setRecords(Object.fromEntries(revisionIds.map((id, index) => [id, recordResults[index]])));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری برگه ارسال ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [transmittalId]);

  useEffect(() => {
    void load();
  }, [load]);

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

  const canManage = hasRole("dcc") || hasRole("project_manager") || hasRole("system_admin");
  const issued = ["issued", "partially_acknowledged", "acknowledged", "closed"].includes(data.status);
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
  const revisionIds = Array.from(new Set(data.lines.map((line) => line.revision_id)));

  // --- review route -------------------------------------------------------
  const stage = data.stage ?? "draft";
  const isDcc = hasRole("dcc");
  const isManagement = hasRole("engineering_manager") || hasRole("project_manager");
  const canForward = isDcc && ["dcc", "draft"].includes(stage);
  const canRefer = isManagement && stage === "management";
  const pendingReferrals = (data.referrals ?? []).filter((item) => item.status === "pending");
  /** A referral only the assignee may close — the API enforces the same rule. */
  const myReferral = pendingReferrals.find((item) => item.to_user_id === user?.id);

  const loadMembers = async () => {
    if (members.length) return;
    try {
      setMembers(await api.get<ProjectMember[]>(`/projects/${data.project_id}/members`));
    } catch {
      setMembers([]);
    }
  };

  const openRefer = async () => {
    setReferError(null);
    await loadMembers();
    const first = members.find((member) => member.role === "reviewer" && member.discipline_id);
    setReferForm((form) => ({ ...form, discipline_id: form.discipline_id || first?.discipline_id || "" }));
    setReferOpen(true);
  };

  const openForward = async () => {
    setReferError(null);
    await loadMembers();
    const manager =
      members.find((member) => member.role === "engineering_manager") ??
      members.find((member) => member.role === "project_manager");
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

  const disciplinesWithEngineer = members.filter(
    (member) => member.role === "reviewer" && member.discipline_id,
  );

  return (
    <div>
      {/* ---------- Transmittal Workspace window ---------- */}
      <Window
        title={
          <span className="ltr">
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
        <div className="mb-1 flex items-stretch border border-[#a8a284] bg-white">
          <div className="flex flex-1 flex-col items-center justify-center gap-1 px-3 py-1">
            <span className="text-[13px] font-bold tracking-tight text-[#1f3864]">Transmittal Workspace</span>
            <span className="flex items-center gap-1.5">
              <StatusLamp tone={issued ? "green" : "yellow"} />
              <span className="text-[10px] text-[#33475b]">
                {STATUS_LABELS[data.status] ?? data.status} ·{" "}
                {data.type === "external" ? "Engineering — [OT]" : "Site — [OS]"}
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
          <div className="w-[250px] border-inline-start border-[#a8a284] text-[10px]">
            {[
              ["Transmittal Identity:", data.id.slice(0, 8).toUpperCase()],
              ["Transmittal No.:", data.number ?? data.preview_number ?? "—"],
              ["Date:", meta.issued_at_jalali ?? (data.issued_at ? jalali(data.issued_at) : "—")],
              ["Registration Time:", data.issued_at ? jalaliDateTime(data.issued_at) : "—"],
              ["Revision Time:", data.due_date ? jalali(data.due_date) : "—"],
            ].map(([label, value]) => (
              <div
                key={label}
                className="flex items-center justify-between gap-2 border-b border-[#e6e1c8] px-2 py-[2px] last:border-b-0"
              >
                <span className="font-bold text-[#20365c]">{label}</span>
                <span className="ltr truncate text-[#11202c]">{value}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="section-bar">Transmittal Information — اطلاعات برگه ارسال</div>
        <FieldGrid>
          <FField label="Transmittal Subject:" full>
            <span className="truncate">{data.lines[0]?.title ?? data.remarks ?? "—"}</span>
          </FField>
          <FField label="Project:">{meta.project_name ?? "—"}</FField>
          <FField label="Contract:" muted>
            —
          </FField>
          <FField label="Received Letter No.:" muted>
            —
          </FField>
          <FField label="Contractor Transmittal No.:" muted>
            —
          </FField>
          <FField label="Issued by:">{meta.issued_by ?? "—"}</FField>
          <FField label="From:">{data.from_org ?? meta.issued_by ?? "—"}</FField>
          <FField label="Coordinator/Originator:">{meta.ddm_rule ?? "—"}</FField>
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

        {/* attachment row of the reference workspace */}
        <div className="mt-1 flex flex-wrap items-center gap-3 border border-[#a8a284] bg-white px-2 py-1">
          <span className="flex items-center gap-1 text-[10px] text-[#1f3864]">
            <IconPaperclip className="h-4 w-4" /> Add Attachment From Parzin
            <span className="text-[9px] text-[#8a97a3]">(فاز ۲)</span>
          </span>
          <span className="flex items-center gap-1 text-[10px] text-[#1f3864]">
            <IconDoc className="h-4 w-4" /> Attachment List ({persianDigits(data.line_count)})
          </span>
          <span className="flex items-center gap-1 text-[10px] text-[#1f3864]">
            <IconArchive className="h-4 w-4" /> Native Files Access
          </span>
          <span className="flex items-center gap-1 text-[10px] text-[#1f3864]">
            <IconEdit className="h-4 w-4" /> Manage Doc. Attachments
          </span>
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
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.id}>
                  <td className="cell-center">{persianDigits(line.line_no)}</td>
                  <td className="whitespace-nowrap">
                    <span className="flex items-center gap-1">
                      <StatusLamp tone={line.status === "issued" ? "green" : "yellow"} />
                      <span className="ltr font-bold text-[#17427a]">{line.doc_code ?? "—"}</span>
                    </span>
                  </td>
                  <td>{line.title ?? "—"}</td>
                  <td className="text-center">{line.discipline_code ?? "—"}</td>
                  <td className="cell-center">{line.rev ?? "—"}</td>
                  <td className="cell-center">{data.purpose}</td>
                  <td className="text-center">{STATUS_LABELS[line.status] ?? line.status}</td>
                  <td className="cell-center">—</td>
                  <td className="text-center">{line.status === "issued" ? "Approved" : "In Review"}</td>
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
                      disabled={(data.referrals ?? []).length === 0}
                      onClick={() => setFlowOpen(true)}
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
                </tr>
              ))}
            </tbody>
          </table>
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
              disabled: (data.referrals ?? []).length === 0,
              onClick: () => setFlowOpen(true),
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
              label: "تأییدیه دریافت",
              icon: <IconMail />,
              disabled: busy || !issued,
              onClick: () => void act(`/transmittals/${data.id}/acknowledge`, { note: null }),
            },
            ...(issued
              ? []
              : [
                  {
                    label: "صدور نهایی",
                    icon: <IconAdd />,
                    groupStart: true,
                    disabled: busy,
                    onClick: () =>
                      void act(
                        `/transmittals/${data.id}/issue`,
                        { remarks: remarks || null, notify: true },
                        "پس از صدور، شماره نهایی تخصیص می‌یابد و محتوای برگه قابل ویرایش نیست. ادامه می‌دهید؟",
                      ),
                  },
                ]),
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

        {!issued ? (
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
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th className="cell-center" style={{ width: 30 }}>Row</th>
                        <th style={{ width: 150 }}>Doc. No.</th>
                        <th>Doc. Title</th>
                        <th style={{ width: 70 }}>Discipline</th>
                        <th style={{ width: 46 }}>Rev</th>
                        <th style={{ width: 46 }}>Pages</th>
                        <th style={{ width: 98 }}>Action</th>
                        <th style={{ width: 84 }}>Status</th>
                        <th style={{ width: 84 }}>Due Date</th>
                        <th className="cell-center" style={{ width: 84 }}>Comments</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.lines.map((line) => (
                        <tr key={line.id}>
                          <td className="cell-center">{persianDigits(line.line_no)}</td>
                          <td className="ltr font-bold text-[#17427a]">{line.doc_code ?? "—"}</td>
                          <td>{line.title ?? "—"}</td>
                          <td className="text-center">{line.discipline_code ?? "—"}</td>
                          <td className="cell-center">{line.rev ?? "—"}</td>
                          <td className="cell-center">{line.page_count ?? "—"}</td>
                          <td className="text-[10px]">{ACTION_LABELS[line.action] ?? line.action}</td>
                          <td className="text-[10px]">{STATUS_LABELS[line.status] ?? line.status}</td>
                          <td className="whitespace-nowrap text-[10px]">{line.due_date ? jalali(line.due_date) : "—"}</td>
                          <td className="cell-center">
                            <Link className="text-[10px] font-bold text-[#1f4e79] underline" to={`/documents/${line.revision_id}/comments`}>
                              Comment List
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ),
            },
            {
              key: "comments",
              label: "Comment List",
              count: allComments.length,
              content: (
                <div>
                  {/* reference toolbar above the comment grid */}
                  <div className="flex flex-wrap items-center gap-3 border-b border-[#a8a284] bg-[#fbfaf2] px-2 py-1">
                    <Link
                      className="flex items-center gap-1 text-[10px] font-bold text-[#1f4e79] hover:underline"
                      to={data.lines[0] ? `/documents/${data.lines[0].revision_id}/comments` : "/review"}
                    >
                      <IconAdd className="h-3.5 w-3.5" /> Add New Comment
                    </Link>
                    <span className="flex items-center gap-1 text-[10px] text-[#33475b]">
                      <IconComment className="h-3.5 w-3.5" /> Show All Comment
                    </span>
                    <button
                      type="button"
                      className="flex items-center gap-1 text-[10px] font-bold text-[#1f4e79] hover:underline disabled:text-[#8a97a3]"
                      disabled={(data.referrals ?? []).length === 0}
                      onClick={() => setFlowOpen(true)}
                    >
                      <IconFlow className="h-3.5 w-3.5" /> Show All Flow History
                      {persianDigits((data.referrals ?? []).length)}
                    </button>
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
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th style={{ width: 30 }}>Row</th>
                        <th>Title</th>
                        <th style={{ width: 130 }}>Doc. No.</th>
                        <th style={{ width: 150 }}>Rev</th>
                        <th className="cell-center" style={{ width: 90 }}>Download</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.lines.map((line) => (
                        <tr key={line.id}>
                          <td className="cell-center">{persianDigits(line.line_no)}</td>
                          <td>{line.title ?? "—"}</td>
                          <td className="ltr">{line.doc_code ?? "—"}</td>
                          <td className="cell-center">{line.rev ?? "—"}</td>
                          <td className="cell-center">
                            <button
                              className="btn btn-sm"
                              onClick={() =>
                                api.download(`/files/revisions/${line.revision_id}`, `${line.doc_code ?? "document"}-Rev${line.rev ?? ""}.pdf`)
                              }
                            >
                              ⤓
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="px-2 py-1 text-[9.5px] text-[#5c6b78]">
                    فایل اصلی (Native File) هر خط از انبار اسناد و با احراز هویت دریافت می‌شود؛ SDC/DWG در فاز ۲.
                  </p>
                </div>
              ),
            },
            {
              key: "revisions",
              label: "Revision Index",
              content: (
                <table className="table-base">
                  <thead>
                    <tr>
                      <th style={{ width: 30 }}>Row</th>
                      <th style={{ width: 150 }}>Doc. No.</th>
                      <th>Doc. Title</th>
                      <th style={{ width: 60 }}>Rev</th>
                      <th style={{ width: 100 }}>Discipline</th>
                      <th style={{ width: 90 }}>Pages</th>
                      <th style={{ width: 100 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.map((line) => (
                      <tr key={line.id}>
                        <td className="cell-center">{persianDigits(line.line_no)}</td>
                        <td className="ltr font-bold">{line.doc_code ?? "—"}</td>
                        <td>{line.title ?? "—"}</td>
                        <td className="cell-center">{line.rev ?? "—"}</td>
                        <td className="text-center">{line.discipline_code ?? "—"}</td>
                        <td className="cell-center">{line.page_count ?? "—"}</td>
                        <td className="text-center">{STATUS_LABELS[line.status] ?? line.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ),
            },
            {
              key: "records",
              label: "Document Records",
              content: (
                <table className="table-base">
                  <thead>
                    <tr>
                      <th style={{ width: 30 }}>Row</th>
                      <th style={{ width: 150 }}>Doc. No.</th>
                      <th>Doc. Title</th>
                      <th style={{ width: 110 }}>Transmittal Type</th>
                      <th style={{ width: 70 }}>Version</th>
                      <th style={{ width: 90 }}>Status</th>
                      <th className="cell-center" style={{ width: 90 }}>View</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.flatMap((line) => {
                      const list = records[line.revision_id] ?? [];
                      if (list.length === 0) {
                        return [
                          <tr key={`${line.id}-none`}>
                            <td className="cell-center">{persianDigits(line.line_no)}</td>
                            <td className="ltr">{line.doc_code ?? "—"}</td>
                            <td>{line.title ?? "—"}</td>
                            <td>—</td>
                            <td className="cell-center">{line.rev ?? "—"}</td>
                            <td className="text-center">No Record</td>
                            <td className="cell-center">—</td>
                          </tr>,
                        ];
                      }
                      return list.map((record, index) => (
                        <tr key={`${line.id}-${record.id}`}>
                          <td className="cell-center">{persianDigits(index + 1)}</td>
                          <td className="ltr">{record.doc_code ?? line.doc_code ?? "—"}</td>
                          <td>{record.title ?? line.title ?? "—"}</td>
                          <td className="text-[10px]">Transmittal</td>
                          <td className="cell-center">{record.rev ?? line.rev ?? "—"}</td>
                          <td className="text-center">{STATUS_LABELS[record.status] ?? record.status}</td>
                          <td className="cell-center">
                            {record.transmittal_id ? (
                              <Link
                                className="text-[10px] font-bold text-[#1f4e79] underline"
                                to={`/transmittals/${record.transmittal_id}`}
                              >
                                مشاهده
                              </Link>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      ));
                    })}
                  </tbody>
                </table>
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
                      Reply Sheet No.: {data.number ? `${data.number}-R` : "—"}
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
                    <FField label="Replied by:">{meta.issued_by ?? "—"}</FField>
                    <FField label="Description:" muted>
                      —
                    </FField>
                    <FField label="Status:">{STATUS_LABELS[data.status] ?? data.status}</FField>
                    <FField label="Related Letter:" muted>
                      —
                    </FField>
                    <FField label="Parzin Number:" muted>
                      —
                    </FField>
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
                        {commentsRows.length === 0 ? (
                          <tr>
                            <td colSpan={14} className="cell-center text-[10px] text-[#5c6b78]">
                              No items have been registered…
                            </td>
                          </tr>
                        ) : (
                          commentsRows.map((row, index) => (
                            <tr key={row.id}>
                              <td className="cell-center">{persianDigits(index + 1)}</td>
                              <td className="cell-center">
                                <input type="checkbox" defaultChecked={row.status === "closed"} aria-label="انتخاب" />
                              </td>
                              <td className="ltr whitespace-nowrap font-bold text-[#17427a]">{row.doc_code ?? "—"}</td>
                              <td className="truncate">{row.doc_title ?? "—"}</td>
                              <td className="cell-center">{row.rev ?? "—"}</td>
                              <td className="text-center">{row.discipline ?? "—"}</td>
                              <td className="whitespace-nowrap text-center text-[10px]">{jalali(row.created_at)}</td>
                              <td className="text-[10px]">{row.author_name ?? "—"}</td>
                              <td className="text-center text-[10px]">
                                {row.disposition ? (DISPOSITION_LABELS[row.disposition] ?? row.disposition) : row.status}
                              </td>
                              <td className="cell-center text-[10px]">{row.status === "closed" ? "OK" : "—"}</td>
                              <td className="max-w-[9rem] truncate text-[10px]">{row.text}</td>
                              <td className="max-w-[9rem] truncate text-[10px]">{row.response_text ?? "—"}</td>
                              <td className="cell-center">
                                <IconButton icon={<IconChain />} label="زنجیره مدارک" disabled />
                              </td>
                              <td className="cell-center">
                                <Link to={`/documents/${row.revision_id}/comments`} aria-label="مشاهده فهرست نظرها">
                                  <IconButton icon={<IconView />} label="مشاهده" />
                                </Link>
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
        title="گردش مدرک — Flow History"
        onClose={() => setFlowOpen(false)}
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
                  <td className="cell-center">{referral.created_at ? jalali(referral.created_at) : "—"}</td>
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
              <option value="">— پیش‌فرض: مدیر مهندسی —</option>
              {members
                .filter((member) => ["engineering_manager", "project_manager"].includes(member.role))
                .map((member) => (
                  <option key={member.user_id} value={member.user_id}>
                    {member.role === "engineering_manager" ? "مدیر مهندسی" : "مدیر پروژه"} —{" "}
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
