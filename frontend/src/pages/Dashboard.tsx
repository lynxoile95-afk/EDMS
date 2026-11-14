import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { Dashboard as DashboardData, ExtractionResult, Notification, Transmittal, TransmittalDetail } from "@/lib/types";
import { Button, Card, EmptyState, ErrorBox, Modal, ProgressBar, Skeleton, Stat, Toast } from "@/components/ui";
import { Ribbon } from "@/components/window";
import {
  IconArchive,
  IconArrow,
  IconClipboard,
  IconChain,
  IconClock,
  IconComment,
  IconDelete,
  IconDoc,
  IconEdit,
  IconFlow,
  IconMail,
  IconPrint,
  IconRefresh,
  IconSearch,
  IconValidation,
  IconView,
} from "@/components/icons";
import { ACTION_LABELS, PURPOSE_LABELS, RULE_LABELS, STAGE_LABELS, STATUS_LABELS, jalali, jalaliDateTime, percent, persianDigits } from "@/lib/format";

/** One row of the inbox: either a received transmittal or a document awaiting review. */
const REFERRAL_ACTION_BY_PURPOSE: Record<string, string> = {
  IFC: "For Construction",
  IFI: "For Information",
};

const REFERRAL_ACTION_LABELS: Record<string, string> = {
  review: "For Follow Up",
  approve: "For Follow Up",
  info: "For Information",
  for_construction: "For Construction",
  return_to_sender: "بازگشت به فرستنده",
};

type InboxOperation = "for_construction" | "info" | "review";

const INBOX_OPERATIONS: { value: InboxOperation; label: string }[] = [
  { value: "for_construction", label: "جهت اجرا" },
  { value: "info", label: "جهت اطلاع" },
  { value: "review", label: "جهت بررسی و اظهار نظر" },
];

const INBOX_PAGE_SIZE = 8;

interface InboxRow {
  key: string;
  kind: "transmittal" | "review";
  code: string | null;
  title: string;
  subject: string;
  action: string;
  sender: string;
  doc_type: string;
  received: string | null;
  due: string | null;
  status: string;
  href: string;
  fresh: boolean;
  entityId?: string;
  transmittalId?: string;
  manualWorkflow: boolean;
  canDelete: boolean;
}

function DashboardSkeleton() {
  return (
    <div className="dashboard-page dashboard-skeleton" aria-label="در حال بارگذاری کارتابل">
      <div className="dashboard-kpi-grid">
        {[1, 2, 3, 4].map((item) => (
          <div className="stat-card" key={item}>
            <Skeleton className="h-12 w-12 rounded-[13px]" />
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="h-7 w-16" />
              <Skeleton className="h-2.5 w-24" />
            </div>
          </div>
        ))}
      </div>
      <Card>
        <div className="card-header">
          <Skeleton className="h-4 w-56" />
          <Skeleton className="h-8 w-24" />
        </div>
        <div className="toolbar">
          <Skeleton className="h-9 w-56" />
          <Skeleton className="h-9 w-36" />
          <Skeleton className="h-9 w-40" />
        </div>
        <div className="p-4">
          {[1, 2, 3, 4, 5].map((item) => <Skeleton className="mb-3 block h-8 w-full" key={item} />)}
        </div>
      </Card>
    </div>
  );
}

export function Dashboard() {
  const { project } = useProject();
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const canDismissReviewRows =
    hasRole("dcc") ||
    hasRole("project_control") ||
    hasRole("pmo") ||
    hasRole("project_manager") ||
    hasRole("system_admin");
  const [data, setData] = useState<DashboardData | null>(null);
  const [transmittals, setTransmittals] = useState<Transmittal[]>([]);
  const [queue, setQueue] = useState<ExtractionResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [checkedRows, setCheckedRows] = useState<Set<string>>(new Set());
  const [flow, setFlow] = useState<TransmittalDetail | null>(null);
  const [flowBusy, setFlowBusy] = useState(false);
  const [issueBusy, setIssueBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [unreadEntities, setUnreadEntities] = useState<Set<string>>(new Set());
  const [filterText, setFilterText] = useState("");
  const [filterKind, setFilterKind] = useState("");
  const [filterAction, setFilterAction] = useState("");
  const [page, setPage] = useState(1);
  const [rowMenu, setRowMenu] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const [dashboard, list, pending, inbox] = await Promise.all([
        api.get<DashboardData>(`/projects/${project.id}/dashboard`),
        // The inbox is per-person (reference «کارتابل وظایف سازمانی»): the API decides
        // what is waiting on *me* — an open referral, an un-acknowledged sheet, or the
        // management/DCC step when I hold that role.
        api.get<{ items: Transmittal[] }>(`/projects/${project.id}/transmittals?mine=true&size=25`),
        api.get<{ items: ExtractionResult[] }>(`/projects/${project.id}/review-queue?review_status=pending&size=25`),
        api.get<{ items: Notification[]; unread: number }>("/notifications?limit=60"),
      ]);
      setData(dashboard);
      setTransmittals(list.items);
      setQueue(pending.items);
      // A row counts as unread when an unread notification points at it (real state,
      // not a client-side guess).
      setUnreadEntities(
        new Set(inbox.items.filter((item) => !item.read_at && item.entity_id).map((item) => item.entity_id as string)),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "بارگذاری کارتابل ناموفق بود.");
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo<InboxRow[]>(() => {
    const reviewRows: InboxRow[] = queue.map((item) => ({
      key: `review-${item.id}`,
      kind: "review",
      code: item.doc_code,
      title: item.title ?? item.doc_code ?? "بدون عنوان",
      subject: "در انتظار اقدام کارشناس کنترل مدارک",
      action: REFERRAL_ACTION_LABELS.review,
      sender: "بارگذاری پیمانکار",
      doc_type: "Drawing / مدرک",
      received: item.created_at,
      due: null,
      status: item.decision,
      href: `/review/${item.id}`,
      fresh: false,
      entityId: item.id,
      manualWorkflow: false,
      // A pending AI extraction is dismissed through the review endpoint; it
      // is not physically deleted like a transmittal draft.
      canDelete: canDismissReviewRows,
    }));

    const transmittalRows: InboxRow[] = transmittals.map((item) => ({
      key: `trn-${item.id}`,
      kind: "transmittal",
      code: item.document_code ?? item.number,
      title: item.document_title ?? item.number ?? "پیش‌نویس",
      subject: `${PURPOSE_LABELS[item.purpose] ?? item.purpose} — ${persianDigits(item.line_count)} مدرک · ${STAGE_LABELS[item.stage ?? "draft"] ?? ""}`,
      action:
        (item.line_action && REFERRAL_ACTION_LABELS[item.line_action]) ||
        REFERRAL_ACTION_BY_PURPOSE[item.purpose] ||
        "For Follow Up",
      sender: item.from_org ?? "—",
      doc_type: item.document_type || (item.type === "external" ? "Transmittal (External)" : "Transmittal"),
      received: item.issued_at ?? item.created_at,
      due: item.due_date,
      status: item.status,
      href: `/transmittals/${item.id}`,
      fresh: false,
      entityId: item.id,
      transmittalId: item.id,
      manualWorkflow: item.manual_workflow,
      // Hard deletion is intentionally limited to drafts; issued sheets remain
      // in the audit trail and use the separate, permission-checked cancel flow.
      canDelete: item.status === "draft",
    }));

    const all = [...transmittalRows, ...reviewRows].map((row) => ({
      ...row,
      fresh: row.entityId ? unreadEntities.has(row.entityId) : false,
    }));
    return all.sort((a, b) => {
      if (!a.received) return 1;
      if (!b.received) return -1;
      return b.received.localeCompare(a.received);
    });
  }, [transmittals, queue, unreadEntities, canDismissReviewRows]);

  const visibleRows = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    return rows.filter((row) => {
      if (filterKind && row.kind !== filterKind) return false;
      if (filterAction && row.action !== filterAction) return false;
      if (!needle) return true;
      return `${row.title} ${row.subject} ${row.sender} ${row.doc_type}`.toLowerCase().includes(needle);
    });
  }, [rows, filterText, filterKind, filterAction]);

  const pageCount = Math.max(1, Math.ceil(visibleRows.length / INBOX_PAGE_SIZE));
  const pageRows = visibleRows.slice((page - 1) * INBOX_PAGE_SIZE, page * INBOX_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [filterText, filterKind, filterAction]);

  useEffect(() => {
    setPage((currentPage) => Math.min(currentPage, pageCount));
  }, [pageCount]);

  const unreadCount = rows.filter((row) => row.fresh).length;

  const current = visibleRows.find((row) => row.key === selected) ?? null;
  const open = (row: InboxRow) => setSelected(row.key);

  const toggleRow = (row: InboxRow) => {
    setSelected(row.key);
    setCheckedRows((previous) => {
      const next = new Set(previous);
      if (next.has(row.key)) next.delete(row.key);
      else next.add(row.key);
      return next;
    });
  };

  const openFlow = async (row: InboxRow | null = current) => {
    if (!row?.transmittalId) return;
    setFlowBusy(true);
    try {
      setFlow(await api.get<TransmittalDetail>(`/transmittals/${row.transmittalId}`));
    } catch (err) {
      setNotice(err instanceof ApiError ? err.detail : "دریافت گردش مدرک ناموفق بود.");
    } finally {
      setFlowBusy(false);
    }
  };

  const acknowledge = async (row: InboxRow | null = current) => {
    if (!row?.transmittalId) return;
    setNotice(null);
    try {
      await api.post(`/transmittals/${row.transmittalId}/acknowledge`, { note: null });
      setNotice("تأییدیه دریافت ثبت شد.");
      await load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.detail : "ثبت تأییدیه ناموفق بود.");
    }
  };

  const issueDraft = async (row: InboxRow | null = current) => {
    if (!row?.transmittalId || !row.manualWorkflow || row.status !== "draft") return;
    if (!window.confirm("پیش‌نویس پس از صدور نهایی شماره رسمی می‌گیرد و صادر می‌شود. ادامه می‌دهید؟")) return;
    setIssueBusy(true);
    setNotice(null);
    try {
      await api.post(`/transmittals/${row.transmittalId}/issue`, { notify: true });
      setNotice("ترانسمیتال با موفقیت صادر شد.");
      await load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.detail : "صدور نهایی ترانسمیتال ناموفق بود.");
    } finally {
      setIssueBusy(false);
    }
  };

  const deleteRow = async (row: InboxRow | null = current) => {
    if (!row) {
      setNotice("ابتدا یک ردیف را انتخاب کنید.");
      return;
    }
    if (!row.canDelete) {
      setNotice("این ردیف قابل حذف نیست؛ فقط پیش‌نویس‌ها و نتایج استخراجِ در انتظار بررسی حذف می‌شوند.");
      return;
    }

    const isReviewRow = row.kind === "review" && Boolean(row.entityId);
    if (!isReviewRow && !row.transmittalId) {
      setNotice("حذف فقط برای ردیف‌های قابل مدیریت کارتابل امکان‌پذیر است.");
      return;
    }
    if (
      !window.confirm(
        isReviewRow
          ? `نتیجه استخراج هوش مصنوعی «${row.title}» از صف بازبینی حذف شود؟`
          : `ردیف «${row.title}» از کارتابل حذف شود؟ این عملیات برای پیش‌نویس قابل بازگشت نیست.`,
      )
    ) {
      return;
    }

    setDeleteBusy(true);
    setNotice(null);
    try {
      if (isReviewRow && row.entityId) {
        // "ignore" records a review decision and removes the row from the
        // pending queue without deleting the uploaded source file or its history.
        await api.post(`/extraction-results/${row.entityId}/review`, {
          action: "ignore",
          notes: "حذف نتیجه استخراج از صف بازبینی توسط کارشناس",
        });
      } else if (row.transmittalId) {
        await api.delete(`/transmittals/${row.transmittalId}`);
      }
      setCheckedRows((previous) => {
        const next = new Set(previous);
        next.delete(row.key);
        return next;
      });
      if (selected === row.key) setSelected(null);
      setNotice("ردیف با موفقیت از کارتابل حذف شد.");
      await load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.detail : "حذف ردیف ناموفق بود.");
    } finally {
      setDeleteBusy(false);
    }
  };

  const deleteSelectedRows = async () => {
    const selectedRows = rows.filter((row) => checkedRows.has(row.key));
    const deletableRows = selectedRows.filter(
      (row) => row.canDelete && (Boolean(row.transmittalId) || (row.kind === "review" && Boolean(row.entityId))),
    );
    if (deletableRows.length === 0) {
      setNotice("از بین ردیف‌های انتخاب‌شده، پیش‌نویس یا نتیجه استخراج قابل حذفی وجود ندارد.");
      return;
    }

    const skipped = selectedRows.length - deletableRows.length;
    const suffix = skipped ? ` ${persianDigits(skipped)} ردیف غیرقابل حذف نادیده گرفته می‌شود.` : "";
    const draftCount = deletableRows.filter((row) => row.kind === "transmittal").length;
    const reviewCount = deletableRows.length - draftCount;
    const selectedDescription = [
      draftCount ? `${persianDigits(draftCount)} پیش‌نویس` : "",
      reviewCount ? `${persianDigits(reviewCount)} نتیجه استخراج` : "",
    ].filter(Boolean).join(" و ");
    if (
      !window.confirm(
        `${selectedDescription} از کارتابل حذف شود؟ نتایج استخراج با وضعیت «نادیده گرفته‌شده» از صف بازبینی خارج می‌شوند.${suffix}`,
      )
    ) {
      return;
    }

    setDeleteBusy(true);
    setNotice(null);
    const results = await Promise.allSettled(
      deletableRows.map((row) => {
        if (row.kind === "review" && row.entityId) {
          return api.post(`/extraction-results/${row.entityId}/review`, {
            action: "ignore",
            notes: "حذف گروهی نتیجه استخراج از صف بازبینی توسط کارشناس",
          });
        }
        return api.delete(`/transmittals/${row.transmittalId}`);
      }),
    );
    const deletedKeys = new Set(
      deletableRows.filter((_, index) => results[index].status === "fulfilled").map((row) => row.key),
    );
    const failed = results.length - deletedKeys.size;
    setCheckedRows((previous) => new Set([...previous].filter((key) => !deletedKeys.has(key))));
    if (selected && deletedKeys.has(selected)) setSelected(null);
    await load();
    setDeleteBusy(false);

    if (failed) {
      setNotice(`${persianDigits(deletedKeys.size)} ردیف حذف شد؛ ${persianDigits(failed)} ردیف حذف نشد.`);
    } else {
      setNotice(`${persianDigits(deletedKeys.size)} ردیف با موفقیت از کارتابل حذف شد.`);
    }
  };

  const chooseOperation = async (row: InboxRow, operation: InboxOperation) => {
    setSelected(row.key);
    setRowMenu(null);
    if (operation === "info" && row.transmittalId) {
      await acknowledge(row);
      return;
    }
    // The detail workspace performs the permission-checked route action. Opening
    // it here keeps the dropdown useful for both transmittals and review results.
    navigate(row.href);
  };

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;
  if (loading) return <DashboardSkeleton />;
  if (error) return <ErrorBox message={error} onRetry={() => void load()} />;
  if (!data) return null;

  const completion = data.documents_total ? data.documents_issued / data.documents_total : 0;

  return (
    <div className="dashboard-page" onClick={() => rowMenu && setRowMenu(null)}>
      {/* ---------- summary strip ---------- */}
      <div className="dashboard-kpi-grid">
        <Stat
          label="مدارک بارگذاری در MDL"
          value={persianDigits(data.documents_total)}
          hint={`${persianDigits(data.revisions_total)} بازنگری`}
          icon={<IconArchive />}
        />
        <Stat
          label="تعداد مدارک ترانسمیتال شده"
          value={persianDigits(data.transmittals_total)}
          hint={`${persianDigits(data.transmittals_open)} برگه باز`}
          tone="good"
          icon={<IconMail />}
        />
        <Stat
          label="پیشرفت صدور مدارک"
          value={percent(completion)}
          hint={`${persianDigits(data.documents_issued)} مدرک صادرشده`}
          tone="good"
          icon={<IconFlow />}
        />
        <Stat
          label="در صف بازبینی"
          value={persianDigits(data.review_queue)}
          hint={`${persianDigits(data.quarantined)} قرنطینه`}
          tone={data.review_queue ? "warn" : "default"}
          icon={<IconClock />}
        />
      </div>

      {/* ---------- inbox ---------- */}
      <Card className="is-main">
        <div className="card-header dashboard-inbox-header">
          <h2 className="dashboard-card-title">
            <IconClipboard />
            <span>
              کارتابل وظایف سازمانی —{" "}
              {data.review_queue > 0 ? `${persianDigits(data.review_queue)} وظیفه در انتظار` : "همه وظایف انجام شده"}
            </span>
          </h2>
          <div className="flex items-center gap-2 text-[10px] text-[#1f3864]">
            <span>
              تعداد کل: <b>{persianDigits(rows.length)}</b>
            </span>
            <span>
              خوانده نشده: <b className="text-[#97282b]">{persianDigits(unreadCount)}</b>
            </span>
            <span>
              خوانده شده: <b>{persianDigits(rows.length - unreadCount)}</b>
            </span>
            <span>
              انتخاب‌شده: <b>{persianDigits(checkedRows.size)}</b>
            </span>
            <Button className="btn-sm" onClick={() => void load()}>
              بازخوانی
            </Button>
          </div>
        </div>
        <div className="toolbar">
          <span className="text-[10px] text-[#1f3864]">فیلترها…</span>
          <input
            className="input w-48"
            placeholder="جستجو در عنوان/فرستنده"
            value={filterText}
            onChange={(event) => setFilterText(event.target.value)}
          />
          <select className="input w-36" value={filterKind} onChange={(event) => setFilterKind(event.target.value)}>
            <option value="">همه انواع مدرک</option>
            <option value="transmittal">Transmittal</option>
            <option value="review">Drawing / مدرک</option>
          </select>
          <select className="input w-40" value={filterAction} onChange={(event) => setFilterAction(event.target.value)}>
            <option value="">همه عملکردهای ارجاع</option>
            <option value="For Construction">For Construction</option>
            <option value="For Information">For Information</option>
            <option value="For Follow Up">For Follow Up</option>
          </select>
          <Button
            className="btn-sm"
            onClick={() => {
              setFilterText("");
              setFilterKind("");
              setFilterAction("");
            }}
          >
            پاک کردن
          </Button>
          <span className="flex-1" />
          <span className="text-[9.5px] text-[#5c6b78]">{persianDigits(visibleRows.length)} ردیف نمایش داده می‌شود</span>
        </div>

        {visibleRows.length === 0 ? (
          <EmptyState
            title="کارتابل خالی است"
            description={`هیچ مدرک یا برگه ارسالی برای «${project.name}» ثبت نشده است.`}
          />
        ) : (
          <div className="dashboard-table-scroll max-h-[52vh] overflow-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th className="cell-center" style={{ width: 38 }}>ردیف</th>
                  <th className="cell-center" style={{ width: 34 }}>انتخاب</th>
                  <th className="cell-center" style={{ width: 34 }}>وضعیت</th>
                  <th style={{ width: 112 }}>تاریخ و ساعت دریافت</th>
                  <th>عنوان مدرک</th>
                  <th style={{ width: 124 }}>عملگر ارجاع</th>
                  <th style={{ width: 122 }}>فرستنده</th>
                  <th style={{ width: 108 }}>نوع مدرک</th>
                  <th style={{ width: 86 }}>مهلت پاسخ</th>
                  <th className="cell-center" style={{ width: 78 }}>عملیات</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((row, index) => (
                  <tr
                    key={row.key}
                    className={clsx(
                      (selected === row.key || checkedRows.has(row.key)) && "is-selected",
                      "cursor-pointer",
                    )}
                    onClick={() => open(row)}
                    onDoubleClick={() => navigate(row.href)}
                  >
                    <td className="cell-center">{persianDigits((page - 1) * INBOX_PAGE_SIZE + index + 1)}</td>
                    <td className="cell-center">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 accent-[#1f4e79]"
                        checked={checkedRows.has(row.key)}
                        aria-label={`انتخاب ردیف ${index + 1}`}
                        title="انتخاب ردیف برای حذف گروهی"
                        onClick={(event) => event.stopPropagation()}
                        onChange={() => toggleRow(row)}
                      />
                    </td>
                    <td className="cell-center">
                      <span className={clsx("inbox-status-badge", row.fresh ? "is-unread" : "is-read")}>
                        <span className="inbox-status-dot" />
                        {row.fresh ? "جدید" : "خوانده‌شده"}
                      </span>
                    </td>
                    <td className="whitespace-nowrap text-[10px] leading-4">
                      {row.received ? (
                        <>
                          <span>{jalali(row.received)}</span>
                          <br />
                          <span className="text-[9.5px] text-[#5c6b78]">
                            {new Date(row.received).toLocaleTimeString("en-GB", {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                              hour12: false,
                            })}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <div className="min-w-0">
                        {row.code ? <span className="ltr block text-[9.5px] text-[#17427a]">{row.code}</span> : null}
                        <Link
                          to={row.href}
                          className="block truncate font-bold text-[#1f3864] underline-offset-2 hover:text-[#1f4e79] hover:underline focus:outline-none focus:ring-1 focus:ring-[#3d789d]"
                          title="باز کردن پنجره مربوط به مدرک"
                          onClick={(event) => event.stopPropagation()}
                        >
                          {row.title}
                        </Link>
                        <p className="line-clamp-1 text-[10px] text-[#3c4b58]">{row.subject}</p>
                      </div>
                    </td>
                    <td className="text-[10px]">{row.action}</td>
                    <td className="text-[10px] leading-4">{row.sender}</td>
                    <td className="text-[10px]">{row.doc_type}</td>
                    <td className="whitespace-nowrap text-[10px]">
                      {row.due ? jalali(row.due) : <span className="text-[#8a5a10]">در اسرع وقت</span>}
                    </td>
                    <td className="cell-center">
                      <div className="relative">
                        <Button
                          className="btn-sm"
                          aria-haspopup="menu"
                          aria-expanded={rowMenu === row.key}
                          onClick={(event) => {
                            event.stopPropagation();
                            setSelected(row.key);
                            setRowMenu(rowMenu === row.key ? null : row.key);
                          }}
                        >
                          عملیات ▾
                        </Button>
                        {rowMenu === row.key ? (
                          <div
                            className="absolute left-0 top-full z-20 w-48 border border-[#4a5a67] bg-white text-right shadow-lg"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <div className="border-b border-[#c3ccd6] bg-[#eef2f6] px-2 py-1 text-[9.5px] font-bold text-[#33475b]">
                              نوع اقدام
                            </div>
                            {INBOX_OPERATIONS.map((operation) => (
                              <button
                                key={operation.value}
                                type="button"
                                className="block w-full px-2 py-1 text-right text-[10.5px] hover:bg-[#e8eff6]"
                                onClick={() => void chooseOperation(row, operation.value)}
                              >
                                {operation.label}
                              </button>
                            ))}
                            <div className="border-t border-[#c3ccd6]" />
                            {[
                              { label: "مشاهده مدرک", run: () => navigate(row.href) },
                              ...(row.manualWorkflow && row.status === "draft"
                                ? [{ label: "صدور نهایی ترانسمیتال", run: () => void issueDraft(row), disabled: issueBusy }]
                                : []),
                              {
                                label: "تأییدیه دریافت",
                                run: () => void acknowledge(row),
                                disabled: !row.transmittalId,
                              },
                              {
                                label: "گردش مدرک",
                                run: () => void openFlow(row),
                                disabled: !row.transmittalId,
                              },
                              {
                                label: "دریافت PDF",
                                run: () =>
                                  row.transmittalId &&
                                  api.download(`/transmittals/${row.transmittalId}/pdf`, `${row.title}.pdf`),
                                disabled: !row.transmittalId,
                              },
                              {
                                label: "حذف ردیف",
                                run: () => void deleteRow(row),
                                disabled: !row.canDelete || deleteBusy,
                              },
                            ].map((action) => (
                              <button
                                key={action.label}
                                type="button"
                                className={clsx(
                                  "block w-full px-2 py-1 text-right text-[10.5px] hover:bg-[#e8eff6] disabled:text-[#9aa8b4]",
                                  action.label === "حذف ردیف" && "text-[#97282b]",
                                )}
                                disabled={action.disabled}
                                onClick={() => {
                                  setRowMenu(null);
                                  action.run();
                                }}
                              >
                                {action.label}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {visibleRows.length > INBOX_PAGE_SIZE ? (
          <div className="table-pagination" aria-label="صفحه‌بندی کارتابل">
            <span className="table-pagination-summary">
              نمایش {persianDigits((page - 1) * INBOX_PAGE_SIZE + 1)} تا {persianDigits(Math.min(page * INBOX_PAGE_SIZE, visibleRows.length))} از {persianDigits(visibleRows.length)} ردیف
            </span>
            <div className="table-pagination-controls">
              <button
                type="button"
                className="table-page-button"
                disabled={page <= 1}
                onClick={() => setPage((currentPage) => Math.max(1, currentPage - 1))}
              >
                قبلی
              </button>
              {Array.from({ length: pageCount }, (_, index) => index + 1).map((pageNumber) => (
                <button
                  key={pageNumber}
                  type="button"
                  className={clsx("table-page-button", pageNumber === page && "is-active")}
                  onClick={() => setPage(pageNumber)}
                >
                  {persianDigits(pageNumber)}
                </button>
              ))}
              <button
                type="button"
                className="table-page-button"
                disabled={page >= pageCount}
                onClick={() => setPage((currentPage) => Math.min(pageCount, currentPage + 1))}
              >
                بعدی
              </button>
            </div>
          </div>
        ) : null}

        {/* ---------- action ribbon (reference bottom bar) ---------- */}
        <Ribbon
          items={[
            {
              label: "مشاهده مدرک",
              icon: <IconView />,
              disabled: !current,
              onClick: () => current && navigate(current.href),
            },
            ...(current?.manualWorkflow && current.status === "draft"
              ? [
                  {
                    label: "صدور نهایی ترانسمیتال",
                    icon: <IconPrint />,
                    disabled: issueBusy,
                    onClick: () => void issueDraft(),
                  },
                ]
              : []),
            {
              label: "تأییدیه دریافت",
              icon: <IconMail />,
              disabled: !current?.transmittalId,
              onClick: () => void acknowledge(),
            },
            {
              label: "گردش مدرک",
              icon: <IconFlow />,
              disabled: !current?.transmittalId || flowBusy,
              onClick: () => void openFlow(),
            },
            {
              label: "دریافت PDF",
              icon: <IconPrint />,
              disabled: !current?.transmittalId,
              onClick: () =>
                current?.transmittalId &&
                api.download(`/transmittals/${current.transmittalId}/pdf`, `${current.title}.pdf`),
            },
            {
              label: "زنجیره مدارک",
              icon: <IconChain />,
              groupStart: true,
              disabled: true,
              title: "فاز ۲ — مدل زنجیره مدارک",
            },
            {
              label: "افزودن پاراف",
              icon: <IconEdit />,
              disabled: true,
              title: "فاز ۲ — پاراف",
            },
            {
              label: "مشاهده پاراف‌ها",
              icon: <IconDoc />,
              disabled: true,
              title: "فاز ۲ — پاراف",
            },
            {
              label: "پاسخ مدرک",
              icon: <IconComment />,
              groupStart: true,
              disabled: !current,
              onClick: () => current && navigate(current.href),
            },
            {
              label: "ارجاع به",
              icon: <IconArrow />,
              disabled: !current?.transmittalId,
              onClick: () => void openFlow(),
            },
            {
              label: checkedRows.size > 1 ? "حذف ردیف‌های انتخاب‌شده" : "حذف ردیف",
              icon: <IconDelete />,
              danger: true,
              disabled:
                deleteBusy ||
                (checkedRows.size > 0
                  ? !rows.some(
                      (row) =>
                        checkedRows.has(row.key) &&
                        row.canDelete &&
                        (Boolean(row.transmittalId) || (row.kind === "review" && Boolean(row.entityId))),
                    )
                  : !current?.canDelete),
              title: "حذف پیش‌نویس‌ها یا بستن نتایج استخراج انتخاب‌شده از کارتابل",
              onClick: () => void (checkedRows.size > 0 ? deleteSelectedRows() : deleteRow()),
            },
            {
              label: "بازخوانی",
              icon: <IconRefresh />,
              disabled: loading || deleteBusy,
              onClick: () => void load(),
            },
            {
              label: "جستجو",
              icon: <IconSearch />,
              disabled: true,
              title: "از نوار فیلتر بالای جدول استفاده کنید",
            },
          ]}
        />
        <div className="dashboard-selection-bar flex items-center justify-between px-2 py-1 text-[9.5px] text-[#5c6b78]">
          <span>{current ? `انتخاب‌شده: ${current.title}` : "یک ردیف را انتخاب کنید"}</span>
          <span className="flex items-center gap-1">
            <IconClock className="h-3 w-3" /> {jalali(new Date())}
          </span>
        </div>
        {notice ? <Toast message={notice} tone="info" /> : null}
      </Card>

      {/* ---------- progress + validation issues ---------- */}
      <div className="grid gap-2 lg:grid-cols-2">
        <Card className="dashboard-status-card">
          <div className="card-header">
            <h2 className="dashboard-card-title">
              <IconFlow />
              <span>وضعیت جاری پروژه</span>
            </h2>
            <span className="text-[10px] text-[#5c6b78]">آخرین به‌روزرسانی: {jalali(new Date())}</span>
          </div>
          <div className="p-2">
            <div className="mb-1 flex items-center justify-between text-[10px] text-[#4a5a67]">
              <span>درصد تکمیل مدارک</span>
              <span>{percent(completion)}</span>
            </div>
            <ProgressBar value={completion} />
            <table className="kv-table mt-2">
              <tbody>
                <tr>
                  <th>برگه‌های ارسال</th>
                  <td>
                    {persianDigits(data.transmittals_total)} ({persianDigits(data.transmittals_open)} باز)
                  </td>
                </tr>
                <tr>
                  <th>مدارک دارای تأخیر</th>
                  <td className={data.overdue_documents ? "font-bold text-[#97282b]" : ""}>
                    {persianDigits(data.overdue_documents)}
                  </td>
                </tr>
                <tr>
                  <th>بارگذاری ۷ روز گذشته</th>
                  <td>{persianDigits(data.uploads_last_7_days)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="dashboard-validation-card">
          <div className="card-header">
            <h2 className="dashboard-card-title">
              <IconValidation />
              <span>پرتکرارترین ایرادهای اعتبارسنجی</span>
            </h2>
            <span className="rules-badge">قواعد R01–R22</span>
          </div>
          <div className="p-2">
            {Object.keys(data.issues_by_rule).length === 0 ? (
              <div className="validation-empty-state">
                <IconValidation />
                <p>ایرادی ثبت نشده است.</p>
              </div>
            ) : (
              <table className="table-base">
                <thead>
                  <tr>
                    <th style={{ width: 50 }}>کد</th>
                    <th>عنوان قاعده</th>
                    <th style={{ width: 50 }} className="cell-center">تعداد</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.issues_by_rule)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 6)
                    .map(([rule, count]) => (
                      <tr key={rule}>
                        <td className="ltr font-bold">{rule}</td>
                        <td>{RULE_LABELS[rule] ?? "قاعده اعتبارسنجی"}</td>
                        <td className="cell-center">{persianDigits(count)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      </div>

      {/* ---------- document flow dialog ---------- */}
      <Modal
        open={flow !== null}
        title={`گردش مدرک — ${flow?.number ?? "برگه ارسال"}`}
        onClose={() => setFlow(null)}
        width="max-w-3xl"
        footer={<Button onClick={() => setFlow(null)}>بستن</Button>}
      >
        {flow ? (
          <div>
            <div className="section-bar">Transmittal Information</div>
            <table className="kv-table mb-2">
              <tbody>
                <tr>
                  <th>شماره برگه</th>
                  <td className="ltr">{flow.number ?? "—"}</td>
                  <th>وضعیت</th>
                  <td>{STATUS_LABELS[flow.status] ?? flow.status}</td>
                </tr>
                <tr>
                  <th>هدف ارسال</th>
                  <td>{PURPOSE_LABELS[flow.purpose] ?? flow.purpose}</td>
                  <th>نوع</th>
                  <td>{flow.type === "external" ? "خارجی" : "داخلی"}</td>
                </tr>
                <tr>
                  <th>از</th>
                  <td>{flow.from_org ?? "—"}</td>
                  <th>به</th>
                  <td>{flow.to_org ?? "—"}</td>
                </tr>
                <tr>
                  <th>تاریخ صدور</th>
                  <td>{flow.issued_at ? jalaliDateTime(flow.issued_at) : "—"}</td>
                  <th>مهلت پاسخ</th>
                  <td>{flow.due_date ? jalali(flow.due_date) : "—"}</td>
                </tr>
              </tbody>
            </table>

            <div className="section-bar">گردش کار گیرندگان</div>
            <table className="table-base">
              <thead>
                <tr>
                  <th style={{ width: 30 }}>#</th>
                  <th>گیرنده</th>
                  <th>عملیات ارجاع</th>
                  <th>اقدام موردنیاز</th>
                  <th>وضعیت</th>
                  <th>تاریخ تأییدیه</th>
                </tr>
              </thead>
              <tbody>
                {flow.recipients.map((recipient, index) => (
                  <tr key={recipient.id}>
                    <td className="cell-center">{persianDigits(index + 1)}</td>
                    <td>
                      <div className="leading-4">
                        <span className="font-bold text-[#1f3864]">{recipient.full_name ?? "—"}</span>
                        <br />
                        <span className="ltr text-[9.5px] text-[#5c6b78]">{recipient.email ?? ""}</span>
                      </div>
                    </td>
                    <td>{ACTION_LABELS[recipient.action] ?? recipient.action}</td>
                    <td>{recipient.action === "review" ? "For Review and Action" : "For Information"}</td>
                    <td className="text-center">
                      <span
                        className="status-dot"
                        style={{ background: recipient.ack_status === "acknowledged" ? "#3f8f45" : "#d9d9d9" }}
                        title={recipient.ack_status}
                      />
                    </td>
                    <td className="whitespace-nowrap text-[10px]">
                      {recipient.acknowledged_at ? jalaliDateTime(recipient.acknowledged_at) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
