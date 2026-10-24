import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { Dashboard as DashboardData, ExtractionResult, Notification, Transmittal, TransmittalDetail } from "@/lib/types";
import { Badge, Button, Card, EmptyState, ErrorBox, Modal, ProgressBar, Spinner, Stat } from "@/components/ui";
import { DecisionBadge, ConfidenceBadge } from "@/components/DecisionBadge";
import { Ribbon } from "@/components/window";
import {
  IconArrow,
  IconChain,
  IconClock,
  IconComment,
  IconDoc,
  IconEdit,
  IconFlow,
  IconMail,
  IconPrint,
  IconRefresh,
  IconSearch,
  IconView,
} from "@/components/icons";
import { ACTION_LABELS, PURPOSE_LABELS, RULE_LABELS, STAGE_LABELS, STATUS_LABELS, jalali, jalaliDateTime, percent, persianDigits } from "@/lib/format";

/** One row of the inbox: either a received transmittal or a document awaiting review. */
/** What *I* have to do while the sheet sits in each stage of the review route. */
const STAGE_ACTIONS: Record<string, string> = {
  draft: "تکمیل و صدور برگه ارسال",
  dcc: "کنترل اسناد و ارسال به مدیریت",
  management: "بررسی مدیریت و ارجاع به دیسیپلین",
  discipline: "بررسی کارشناس دیسیپلین",
  closed: "بسته‌شده",
};

interface InboxRow {
  key: string;
  kind: "transmittal" | "review";
  title: string;
  subject: string;
  action: string;
  sender: string;
  doc_type: string;
  received: string | null;
  due: string | null;
  reply: string;
  status: string;
  href: string;
  fresh: boolean;
  entityId?: string;
  transmittalId?: string;
  decision?: string;
  confidence?: number;
}

export function Dashboard() {
  const { project } = useProject();
  const navigate = useNavigate();
  const [data, setData] = useState<DashboardData | null>(null);
  const [transmittals, setTransmittals] = useState<Transmittal[]>([]);
  const [queue, setQueue] = useState<ExtractionResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [flow, setFlow] = useState<TransmittalDetail | null>(null);
  const [flowBusy, setFlowBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [unreadEntities, setUnreadEntities] = useState<Set<string>>(new Set());
  const [filterText, setFilterText] = useState("");
  const [filterKind, setFilterKind] = useState("");
  const [filterAction, setFilterAction] = useState("");
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
      title: item.doc_code ?? "بدون کد",
      subject: item.title ?? "—",
      action: "For Review and Action",
      sender: "بارگذاری پیمانکار",
      doc_type: "Drawing / مدرک",
      received: item.created_at,
      due: null,
      reply: item.review_status === "pending" ? "No Comment" : item.review_status,
      status: item.decision,
      href: `/review/${item.id}`,
      fresh: false,
      entityId: item.id,
      decision: item.decision,
      confidence: item.overall_confidence,
    }));

    const transmittalRows: InboxRow[] = transmittals.map((item) => ({
      key: `trn-${item.id}`,
      kind: "transmittal",
      title: item.number ?? "پیش‌نویس",
      subject: `${PURPOSE_LABELS[item.purpose] ?? item.purpose} — ${persianDigits(item.line_count)} مدرک · ${STAGE_LABELS[item.stage ?? "draft"] ?? ""}`,
      action: STAGE_ACTIONS[item.stage ?? "draft"] ?? "For Follow Up",
      sender: item.from_org ?? "—",
      doc_type: item.type === "external" ? "Transmittal (External)" : "Transmittal",
      received: item.issued_at,
      due: item.due_date,
      reply:
        item.acknowledged_count > 0
          ? `${persianDigits(item.acknowledged_count)} تأییدیه`
          : "بدون پاسخ",
      status: item.status,
      href: `/transmittals/${item.id}`,
      fresh: false,
      entityId: item.id,
      transmittalId: item.id,
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
  }, [transmittals, queue, unreadEntities]);

  const visibleRows = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    return rows.filter((row) => {
      if (filterKind && row.kind !== filterKind) return false;
      if (filterAction && row.action !== filterAction) return false;
      if (!needle) return true;
      return `${row.title} ${row.subject} ${row.sender} ${row.doc_type}`.toLowerCase().includes(needle);
    });
  }, [rows, filterText, filterKind, filterAction]);

  const unreadCount = rows.filter((row) => row.fresh).length;

  const current = visibleRows.find((row) => row.key === selected) ?? null;
  const open = (row: InboxRow) => setSelected(row.key);

  const openFlow = async () => {
    if (!current?.transmittalId) return;
    setFlowBusy(true);
    try {
      setFlow(await api.get<TransmittalDetail>(`/transmittals/${current.transmittalId}`));
    } catch (err) {
      setNotice(err instanceof ApiError ? err.detail : "دریافت گردش مدرک ناموفق بود.");
    } finally {
      setFlowBusy(false);
    }
  };

  const acknowledge = async () => {
    if (!current?.transmittalId) return;
    setNotice(null);
    try {
      await api.post(`/transmittals/${current.transmittalId}/acknowledge`, { note: null });
      setNotice("تأییدیه دریافت ثبت شد.");
      await load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.detail : "ثبت تأییدیه ناموفق بود.");
    }
  };

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;
  if (loading) return <Spinner label="در حال بارگذاری کارتابل…" />;
  if (error) return <ErrorBox message={error} onRetry={() => void load()} />;
  if (!data) return null;

  const completion = data.documents_total ? data.documents_issued / data.documents_total : 0;

  return (
    <div onClick={() => rowMenu && setRowMenu(null)}>
      {/* ---------- summary strip ---------- */}
      <div className="mb-2 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="مدارک ثبت‌شده در MDL" value={persianDigits(data.documents_total)} hint={`${persianDigits(data.revisions_total)} بازنگری`} />
        <Stat label="پیشرفت صدور مدارک" value={percent(completion)} hint={`${persianDigits(data.documents_issued)} مدرک صادرشده`} tone="good" />
        <Stat label="در صف بازبینی DCC" value={persianDigits(data.review_queue)} hint={`${persianDigits(data.quarantined)} قرنطینه`} tone={data.review_queue ? "warn" : "default"} />
        <Stat label="نرخ پذیرش خودکار (۳۰ روز)" value={percent(data.auto_accept_rate)} hint="نسبت مدارک سبز" tone={data.auto_accept_rate > 0.7 ? "good" : "warn"} />
      </div>

      {/* ---------- inbox ---------- */}
      <Card className="is-main">
        <div className="card-header">
          <h2>
            کارتابل وظایف سازمانی —{" "}
            {data.review_queue > 0 ? `${persianDigits(data.review_queue)} وظیفه در انتظار` : "همه وظایف انجام شده"}
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
            <option value="For Follow Up">For Follow Up</option>
            <option value="For Information">For Information</option>
            <option value="For Review and Action">For Review and Action</option>
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
          <div className="max-h-[52vh] overflow-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th className="cell-center" style={{ width: 22 }} />
                  <th style={{ width: 34 }}>ردیف</th>
                  <th style={{ width: 108 }}>تاریخ دریافت</th>
                  <th>عنوان مدرک</th>
                  <th style={{ width: 118 }}>عملکرد ارجاع</th>
                  <th style={{ width: 120 }}>فرستنده</th>
                  <th style={{ width: 104 }}>نوع مدرک</th>
                  <th style={{ width: 82 }}>مهلت پاسخ</th>
                  <th style={{ width: 84 }}>پاسخ</th>
                  <th className="cell-center" style={{ width: 62 }}>عملیات</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, index) => (
                  <tr
                    key={row.key}
                    className={clsx(selected === row.key && "is-selected", "cursor-pointer")}
                    onClick={() => open(row)}
                    onDoubleClick={() => navigate(row.href)}
                  >
                    <td className="cell-center">
                      <div className="flex items-center justify-center gap-1">
                        <span
                          className="status-dot"
                          style={{ background: row.fresh ? "#c0392b" : row.kind === "review" ? "#e3b341" : "#3f8f45" }}
                          title={row.fresh ? "خوانده نشده" : "خوانده شده"}
                        />
                        <span className="text-[10px] text-[#5c6b78]" title="پیوست دارد">
                          ⌸
                        </span>
                      </div>
                    </td>
                    <td className="cell-center">{persianDigits(index + 1)}</td>
                    <td className="whitespace-nowrap text-[10px] leading-4">
                      {row.received ? (
                        <>
                          <span>{jalali(row.received)}</span>
                          <br />
                          <span className="text-[9.5px] text-[#5c6b78]">
                            {new Date(row.received).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <div className="flex items-center gap-1">
                        <span className="ltr font-bold text-[#1f3864]">{row.title}</span>
                        {row.fresh ? <Badge className="bg-[#dff3e2] text-[#1c5c2c]">جدید</Badge> : null}
                        {row.kind === "review" && row.decision ? <DecisionBadge decision={row.decision} /> : null}
                      </div>
                      <p className="line-clamp-1 text-[10px] text-[#3c4b58]">{row.subject}</p>
                    </td>
                    <td className="text-[10px]">{row.action}</td>
                    <td className="text-[10px] leading-4">{row.sender}</td>
                    <td className="text-[10px]">{row.doc_type}</td>
                    <td className="whitespace-nowrap text-[10px]">
                      {row.due ? jalali(row.due) : <span className="text-[#8a5a10]">در اسرع وقت</span>}
                    </td>
                    <td className="text-[10px]">
                      {row.kind === "review" ? (
                        <ConfidenceBadge value={row.confidence ?? 0} />
                      ) : (
                        <Badge
                          className={
                            row.reply.startsWith("بدون")
                              ? "bg-[#fdf1e3] text-[#8a5a10]"
                              : "bg-[#e6f4e8] text-[#1c5c2c]"
                          }
                        >
                          {row.reply}
                        </Badge>
                      )}
                    </td>
                    <td className="cell-center">
                      <div className="relative">
                        <Button
                          className="btn-sm"
                          aria-haspopup="menu"
                          aria-expanded={rowMenu === row.key}
                          onClick={(event) => {
                            event.stopPropagation();
                            setRowMenu(rowMenu === row.key ? null : row.key);
                          }}
                        >
                          عملیات ▾
                        </Button>
                        {rowMenu === row.key ? (
                          <div
                            className="absolute left-0 top-full z-20 w-40 border border-[#4a5a67] bg-white text-right shadow-lg"
                            onClick={(event) => event.stopPropagation()}
                          >
                            {[
                              { label: "مشاهده مدرک", run: () => navigate(row.href) },
                              {
                                label: "تأییدیه دریافت",
                                run: () => {
                                  setSelected(row.key);
                                  void acknowledge();
                                },
                                disabled: !row.transmittalId,
                              },
                              {
                                label: "گردش مدرک",
                                run: () => {
                                  setSelected(row.key);
                                  void openFlow();
                                },
                                disabled: !row.transmittalId,
                              },
                              {
                                label: "دریافت PDF",
                                run: () =>
                                  row.transmittalId &&
                                  api.download(`/transmittals/${row.transmittalId}/pdf`, `${row.title}.pdf`),
                                disabled: !row.transmittalId,
                              },
                            ].map((action) => (
                              <button
                                key={action.label}
                                className="block w-full px-2 py-1 text-right text-[10.5px] hover:bg-[#e8eff6] disabled:text-[#9aa8b4]"
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

        {/* ---------- action ribbon (reference bottom bar) ---------- */}
        <Ribbon
          items={[
            {
              label: "مشاهده مدرک",
              icon: <IconView />,
              disabled: !current,
              onClick: () => current && navigate(current.href),
            },
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
              label: "بازخوانی",
              icon: <IconRefresh />,
              disabled: loading,
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
        <div className="flex items-center justify-between px-2 py-1 text-[9.5px] text-[#5c6b78]">
          <span>{current ? `انتخاب‌شده: ${current.title}` : "یک ردیف را انتخاب کنید"}</span>
          <span className="flex items-center gap-1">
            <IconClock className="h-3 w-3" /> {jalali(new Date())}
          </span>
        </div>
        {notice ? <div className="px-2 pb-1.5 text-[10px] text-[#1f4e79]">{notice}</div> : null}
      </Card>

      {/* ---------- progress + validation issues ---------- */}
      <div className="grid gap-2 lg:grid-cols-2">
        <Card>
          <div className="card-header">
            <h2>وضعیت جاری پروژه</h2>
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

        <Card>
          <div className="card-header">
            <h2>پرتکرارترین ایرادهای اعتبارسنجی</h2>
            <span className="text-[10px] text-[#5c6b78]">قواعد R01–R22</span>
          </div>
          <div className="p-2">
            {Object.keys(data.issues_by_rule).length === 0 ? (
              <p className="text-[10.5px] text-[#5c6b78]">ایرادی ثبت نشده است.</p>
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
