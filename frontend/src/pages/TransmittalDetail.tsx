import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { CommentList as CommentListType, TransmittalDetail as Detail, TransmittalLine } from "@/lib/types";
import { Badge, Button, Card, EmptyState, ErrorBox, Field, Spinner, SectionBar } from "@/components/ui";
import { Tabs } from "@/components/Tabs";
import { ACTION_LABELS, PURPOSE_LABELS, STATUS_LABELS, jalali, jalaliDateTime, persianDigits } from "@/lib/format";

const STATUS_DOT: Record<string, string> = {
  draft: "#c9c9c9",
  pending_approval: "#e3b341",
  issued: "#3f8f45",
  partially_acknowledged: "#4f83c8",
  acknowledged: "#2f7d3a",
  closed: "#7a8794",
  cancelled: "#b3403a",
};

export function TransmittalDetail() {
  const { transmittalId } = useParams<{ transmittalId: string }>();
  const { hasRole } = useAuth();
  const [data, setData] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [remarks, setRemarks] = useState("");
  const [comments, setComments] = useState<Record<string, CommentListType[]>>({});
  const [records, setRecords] = useState<Record<string, TransmittalLine[]>>({});

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
  const revisionIds = Array.from(new Set(data.lines.map((line) => line.revision_id)));

  return (
    <div>
      {/* ---------- workspace header ---------- */}
      <Card>
        <div className="card-header">
          <h2 className="flex flex-wrap items-center gap-1.5">
            <span className="ltr">{data.number ?? "پیش‌نویس"}</span>
            <Badge className="bg-[#e7eef6] text-[#1f3864]">{STATUS_LABELS[data.status] ?? data.status}</Badge>
            <Badge className="bg-[#eef2f6]">{PURPOSE_LABELS[data.purpose] ?? data.purpose}</Badge>
            <Badge className="bg-[#eef2f6]">{data.type === "external" ? "خارجی" : "داخلی"}</Badge>
            {!data.number && data.preview_number ? (
              <span className="text-[9.5px] font-normal text-[#7a8794]">شماره پیشنهادی: {data.preview_number}</span>
            ) : null}
          </h2>
          <div className="flex items-center gap-1">
            <Button className="btn-sm" onClick={() => api.download(`/transmittals/${data.id}/pdf`, `${data.number ?? "transmittal"}.pdf`)}>
              دریافت PDF
            </Button>
            <Link className="btn btn-sm" to="/transmittals">
              فهرست برگه‌ها
            </Link>
          </div>
        </div>

        <div className="p-1.5">
          <div className="flex items-center justify-between border border-[#9aa8b4] bg-gradient-to-b from-[#fbfcfd] to-[#e8eef4] px-2 py-1">
            <span className="flex items-center gap-1.5 text-[11px] font-bold text-[#17427a]">
              <span className="status-dot" style={{ background: STATUS_DOT[data.status] ?? "#999" }} />
              Transmittal Workspace — Engineering Document Management
            </span>
            <span className="text-[9.5px] text-[#5c6b78]">
              ثبت: {data.issued_at ? jalaliDateTime(data.issued_at) : "—"} · مهلت: {data.due_date ? jalali(data.due_date) : "—"}
            </span>
          </div>

          <SectionBar>Transmittal Information — اطلاعات برگه ارسال</SectionBar>
          <table className="kv-table">
            <tbody>
              <tr>
                <th>شماره برگه</th>
                <td className="ltr font-bold">{data.number ?? "—"}</td>
                <th>وضعیت</th>
                <td>{STATUS_LABELS[data.status] ?? data.status}</td>
                <th>هدف</th>
                <td>{PURPOSE_LABELS[data.purpose] ?? data.purpose}</td>
              </tr>
              <tr>
                <th>فرستنده</th>
                <td>{data.from_org ?? "—"}</td>
                <th>گیرنده</th>
                <td>{data.to_org ?? "—"}</td>
                <th>تعداد مدارک</th>
                <td>{persianDigits(data.line_count)}</td>
              </tr>
              <tr>
                <th>تأییدیه‌ها</th>
                <td>
                  {persianDigits(data.acknowledged_count)} از {persianDigits(data.recipient_count)}
                </td>
                <th>تاریخ صدور</th>
                <td>{data.issued_at ? jalaliDateTime(data.issued_at) : "—"}</td>
                <th>مهلت پاسخ</th>
                <td>{data.due_date ? jalali(data.due_date) : "—"}</td>
              </tr>
            </tbody>
          </table>

          {actionError ? (
            <div className="mt-1">
              <ErrorBox message={actionError} />
            </div>
          ) : null}

          <div className="toolbar mt-1.5 justify-between">
            <div className="flex flex-wrap items-center gap-1">
              {!issued ? (
                <>
                  <input
                    className="input w-64"
                    placeholder="توضیحات برگه ارسال"
                    value={remarks}
                    onChange={(event) => setRemarks(event.target.value)}
                  />
                  <Button
                    variant="primary"
                    className="btn-sm"
                    disabled={busy}
                    onClick={() =>
                      act(
                        `/transmittals/${data.id}/issue`,
                        { remarks: remarks || null, notify: true },
                        "پس از صدور، شماره نهایی تخصیص می‌یابد و محتوای برگه قابل ویرایش نیست. ادامه می‌دهید؟",
                      )
                    }
                  >
                    صدور نهایی و ارسال
                  </Button>
                  <Button
                    variant="danger"
                    className="btn-sm"
                    disabled={busy}
                    onClick={() => {
                      const reason = window.prompt("دلیل ابطال را وارد کنید:");
                      if (reason) void act(`/transmittals/${data.id}/cancel`, { reason });
                    }}
                  >
                    ابطال
                  </Button>
                </>
              ) : (
                <>
                  <Button className="btn-sm" disabled={busy} onClick={() => act(`/transmittals/${data.id}/remind`, {})}>
                    ارسال یادآور
                  </Button>
                  <Button className="btn-sm" disabled={busy} onClick={() => act(`/transmittals/${data.id}/acknowledge`, { note: null })}>
                    ثبت تأییدیه دریافت
                  </Button>
                  <Button className="btn-sm" onClick={() => api.download(`/transmittals/${data.id}/pdf`, `${data.number}.pdf`)}>
                    چاپ برگه ارسال
                  </Button>
                </>
              )}
            </div>
            <span className="text-[9.5px] text-[#5c6b78]">
              {canManage ? "دسترسی مدیریت: فعال" : "فقط مشاهده"}
            </span>
          </div>
        </div>
      </Card>

      {/* ---------- tabbed workspace ---------- */}
      <Card>
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
                <div>
                  <table className="kv-table mb-1.5">
                    <tbody>
                      <tr>
                        <th>شماره پاسخ</th>
                        <td className="ltr">{data.number ? `${data.number}-R` : "—"}</td>
                        <th>وضعیت</th>
                        <td>{STATUS_LABELS[data.status] ?? data.status}</td>
                      </tr>
                      <tr>
                        <th>تأییدشده</th>
                        <td>
                          {persianDigits(data.acknowledged_count)} از {persianDigits(data.recipient_count)}
                        </td>
                        <th>تاریخ</th>
                        <td>{data.issued_at ? jalaliDateTime(data.issued_at) : "—"}</td>
                      </tr>
                    </tbody>
                  </table>
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th style={{ width: 30 }}>Row</th>
                        <th>گیرنده</th>
                        <th style={{ width: 120 }}>اقدام</th>
                        <th style={{ width: 90 }}>وضعیت</th>
                        <th style={{ width: 130 }}>تاریخ تأییدیه</th>
                        <th className="cell-center" style={{ width: 90 }}>Judgment</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.recipients.map((recipient, index) => (
                        <tr key={recipient.id}>
                          <td className="cell-center">{persianDigits(index + 1)}</td>
                          <td>
                            {recipient.full_name ?? "—"}
                            <span className="ltr mr-1 text-[9.5px] text-[#5c6b78]">{recipient.email ?? ""}</span>
                          </td>
                          <td className="text-[10px]">{ACTION_LABELS[recipient.action] ?? recipient.action}</td>
                          <td className="text-center">
                            <span
                              className="status-dot"
                              style={{ background: recipient.ack_status === "acknowledged" ? "#3f8f45" : "#d9d9d9" }}
                            />
                          </td>
                          <td className="whitespace-nowrap text-[10px]">
                            {recipient.acknowledged_at ? jalaliDateTime(recipient.acknowledged_at) : "—"}
                          </td>
                          <td className="cell-center">{recipient.ack_status === "acknowledged" ? "OK" : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
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
        />
      </Card>

      {data.remarks ? (
        <Card>
          <div className="card-header">
            <h2>توضیحات برگه</h2>
          </div>
          <div className="p-2">
            <Field label="Remarks">
              <p className="text-[10.5px] leading-6 text-[#23303a]">{data.remarks}</p>
            </Field>
          </div>
        </Card>
      ) : null}

      <div className="strip-green mb-2" />
      <p className="text-center text-[9.5px] text-[#5c6b78]">
        {revisionIds.length} بازنگری در این برگه · {persianDigits(allComments.length)} نظر ثبت‌شده
      </p>
      <input type="hidden" value={remarks} readOnly />
    </div>
  );
}
