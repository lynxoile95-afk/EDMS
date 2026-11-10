import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { MDLItem, ProjectMember, TransmittalDetail } from "@/lib/types";
import { Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Select, Spinner } from "@/components/ui";
import { PURPOSE_LABELS, jalali, persianDigits } from "@/lib/format";

interface ManualLine {
  revision: string;
  page_count: string;
  note: string;
  doc_status: string;
}

interface ProjectParty {
  organization?: string;
  role?: string;
  responsibility?: string;
}

const DOC_STATUS_OPTIONS = ["Commented", "No Comment", "Approve As Note"] as const;

type ManualTransmittalType = "OT" | "OS";

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.detail : "ثبت ترانسمیتال دستی ناموفق بود.";
}

export function ManualTransmittalPage() {
  const { project, isDcc } = useProject();
  const navigate = useNavigate();
  const [items, setItems] = useState<MDLItem[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [selected, setSelected] = useState<Record<string, ManualLine>>({});
  const [attachments, setAttachments] = useState<Record<string, File[]>>({});
  const [purpose, setPurpose] = useState("IFR");
  const [type, setType] = useState<ManualTransmittalType>("OT");
  const [issueDate, setIssueDate] = useState(() => jalali(new Date()));
  const [finalApproverId, setFinalApproverId] = useState("");
  const [fromOrg, setFromOrg] = useState("");
  const [toOrg, setToOrg] = useState("");
  const [remarks, setRemarks] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const [mdl, roster] = await Promise.all([
        api.get<{ items: MDLItem[]; total: number }>(`/projects/${project.id}/mdl/items?size=200`),
        api.get<ProjectMember[]>(`/projects/${project.id}/members`),
      ]);
      setItems(mdl.items);
      setMembers(roster.filter((member) => member.is_active));
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void load();
  }, [load]);

  const organizationOptions = useMemo(() => {
    if (!project) return [];
    const parties = Array.isArray(project.settings.project_parties)
      ? (project.settings.project_parties as ProjectParty[])
          .map((party) => party.organization?.trim())
          .filter((value): value is string => Boolean(value))
      : [];
    return Array.from(
      new Set([project.contractor_name?.trim(), project.client_name?.trim(), ...parties].filter(Boolean) as string[]),
    );
  }, [project]);

  useEffect(() => {
    if (!project) return;
    if (!fromOrg) setFromOrg(project.contractor_name ?? organizationOptions[0] ?? "");
    if (!toOrg) setToOrg(project.client_name ?? organizationOptions[1] ?? organizationOptions[0] ?? "");
  }, [project, organizationOptions, fromOrg, toOrg]);

  const visibleItems = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return items;
    return items.filter(
      (item) =>
        item.doc_code.toLowerCase().includes(term) ||
        item.title.toLowerCase().includes(term) ||
        (item.discipline_code ?? "").toLowerCase().includes(term),
    );
  }, [items, query]);

  const selectedItems = items.filter((item) => Boolean(selected[item.id]));
  const contractorCode = project?.contractor_code?.trim().toUpperCase() ?? "";
  const parentCompanyCode = project?.parent_company_code?.trim().toUpperCase() ?? "";
  const projectCodesReady = /^[A-Z]{2}$/.test(contractorCode) && /^[A-Z]{2}$/.test(parentCompanyCode);
  const numberingWarning = project && !projectCodesReady
    ? "کد پیمانکار و کد شرکت مادر این پروژه تکمیل نشده است؛ شماره با قالب قدیمی صادر می‌شود. برای فعال شدن قالب جدید، هر دو کد را دقیقاً با دو حرف انگلیسی A-Z ثبت کنید."
    : null;
  const approverOptions = useMemo(() => {
    const unique = new Map<string, ProjectMember>();
    members.forEach((member) => {
      if (!unique.has(member.user_id)) unique.set(member.user_id, member);
    });
    return Array.from(unique.values());
  }, [members]);

  const toggleItem = (item: MDLItem) => {
    setSelected((current) => {
      if (current[item.id]) {
        const next = { ...current };
        delete next[item.id];
        return next;
      }
      return {
        ...current,
        [item.id]: {
          revision: item.current_rev ?? "A",
          page_count: String(item.page_count ?? 1),
          note: "",
          doc_status: "No Comment",
        },
      };
    });
  };

  const updateLine = (itemId: string, field: keyof ManualLine, value: string) => {
    setSelected((current) => ({
      ...current,
      [itemId]: { ...current[itemId], [field]: value },
    }));
  };

  const updateAttachments = (itemId: string, files: FileList | null) => {
    setAttachments((current) => ({
      ...current,
      [itemId]: files ? Array.from(files) : [],
    }));
  };

  const submit = async () => {
    if (!project) return;
    setError(null);
    setDraftId(null);
    if (selectedItems.length === 0) {
      setError("حداقل یک مدرک از MDL را انتخاب کنید.");
      return;
    }
    if (!finalApproverId) {
      setError("تأیید کننده نهایی ترانسمیتال را انتخاب کنید.");
      return;
    }
    for (const item of selectedItems) {
      const line = selected[item.id];
      if (!line.revision.trim()) {
        setError(`بازنگری مدرک «${item.doc_code}» را وارد کنید.`);
        return;
      }
    }

    setBusy(true);
    try {
      const created = await api.post<TransmittalDetail>(`/projects/${project.id}/transmittals/manual`, {
        rows: selectedItems.map((item) => ({
          mdl_item_id: item.id,
          revision: selected[item.id].revision.trim(),
          page_count: Math.max(1, Number(selected[item.id].page_count) || 1),
          note: selected[item.id].note.trim() || null,
          doc_status: selected[item.id].doc_status,
        })),
        purpose,
        type,
        issue_date: issueDate,
        final_approver_id: finalApproverId,
        from_org: fromOrg.trim() || null,
        to_org: toOrg.trim() || null,
        remarks: remarks.trim() || null,
      });
      setDraftId(created.id);

      for (const item of selectedItems) {
        const line = created.lines.find((candidate) => candidate.mdl_item_id === item.id);
        const files = attachments[item.id] ?? [];
        if (files.length === 0) continue;
        if (!line) throw new Error(`ردیف مدرک «${item.doc_code}» در پیش‌نویس پیدا نشد.`);
        for (const file of files) {
          const form = new FormData();
          form.append("line_id", line.id);
          form.append("kind", "pdf");
          form.append("file", file);
          await api.postForm(`/transmittals/${created.id}/attachments`, form);
        }
      }
      navigate(`/transmittals/${created.id}`);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  };

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" description="ابتدا پروژه را تعریف و انتخاب کنید." />;

  return (
    <div className="space-y-3">
      <Card className="is-main">
        <CardHeader
          title="ثبت ترانسمیتال دستی"
          subtitle={`پروژه: ${project.code} — ${project.name} · بدون استخراج هوش مصنوعی`}
          actions={
            <Button variant="secondary" onClick={() => navigate("/upload")}>
              بازگشت به بارگذاری هوشمند
            </Button>
          }
        />
        <div className="border-b border-[#c3ccd6] bg-[#fffdf3] px-3 py-2 text-[10.5px] leading-5 text-[#5b4d1a]">
          در این مسیر، اطلاعات مدرک از MDL و مقدار بازنگری از کاربر گرفته می‌شود. با کلیک روی «ترانسمیتال جدید»،
          ابتدا یک پیش‌نویس موقت در کارتابل ایجادکننده ساخته می‌شود.
        </div>
        {!isDcc ? (
          <div className="px-3 pt-2 text-[10.5px] text-[#8a5a12]">
            ثبت نهایی پس از بررسی پیش‌نویس در کارتابل انجام می‌شود؛ دسترسی فعلی شما ممکن است فقط مشاهده باشد.
          </div>
        ) : null}
        {numberingWarning ? (
          <div className="mx-3 mb-2 border border-[#c98a86] bg-[#fdeeee] px-2 py-1.5 text-[10.5px] leading-5 text-[#8a2b25]">
            ⚠ {numberingWarning} برای تکمیل آن به صفحه مدیریت پروژه بروید.
          </div>
        ) : null}
      </Card>

      {error ? (
        <ErrorBox message={error} />
      ) : null}
      {draftId ? (
        <div className="border border-[#c3ccd6] bg-[#eef4fa] px-3 py-2 text-[10.5px] text-[#1f3864]">
          پیش‌نویس ایجاد شد. <button className="font-bold underline" onClick={() => navigate(`/transmittals/${draftId}`)}>مشاهده پیش‌نویس</button>
        </div>
      ) : null}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)]">
        <Card>
          <CardHeader
            title="انتخاب مدارک از MDL"
            subtitle={`${persianDigits(selectedItems.length)} مدرک انتخاب شده از ${persianDigits(items.length)} مدرک`}
            actions={
              <Input
                className="w-56"
                placeholder="جست‌وجوی کد یا عنوان…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            }
          />
          {loading ? (
            <Spinner label="در حال دریافت MDL…" />
          ) : items.length === 0 ? (
            <EmptyState
              title="MDL این پروژه خالی است"
              description="ابتدا از صفحه MDL ردیف دستی ثبت کنید یا فایل Excel چهارستونه را بارگذاری کنید."
              action={<Button onClick={() => navigate("/mdl")}>رفتن به MDL</Button>}
            />
          ) : (
            <div className="max-h-[620px] overflow-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th className="w-8" />
                    <th>کد مدرک</th>
                    <th>عنوان</th>
                    <th>دیسیپلین</th>
                    <th>بازنگری جاری</th>
                    <th>بازنگری ترانسمیتال</th>
                    <th>صفحه</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleItems.map((item) => {
                    const line = selected[item.id];
                    return (
                      <tr key={item.id} className={line ? "is-selected" : undefined}>
                        <td>
                          <input
                            type="checkbox"
                            checked={Boolean(line)}
                            onChange={() => toggleItem(item)}
                            aria-label={`انتخاب ${item.doc_code}`}
                          />
                        </td>
                        <td className="ltr whitespace-nowrap font-mono text-xs">{item.doc_code}</td>
                        <td className="max-w-[18rem] truncate text-xs" title={item.title}>{item.title}</td>
                        <td className="text-xs">{item.discipline_code ?? "—"}</td>
                        <td className="text-center text-xs">{item.current_rev ?? "—"}</td>
                        <td>
                          <Input
                            className="w-20 ltr text-center"
                            value={line?.revision ?? ""}
                            disabled={!line}
                            onChange={(event) => updateLine(item.id, "revision", event.target.value)}
                            placeholder="A"
                          />
                        </td>
                        <td>
                          <Input
                            className="w-16 ltr text-center"
                            type="number"
                            min={1}
                            disabled={!line}
                            value={line?.page_count ?? "1"}
                            onChange={(event) => updateLine(item.id, "page_count", event.target.value)}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {visibleItems.length === 0 ? (
                <p className="p-4 text-center text-xs text-slate-500">مدرکی با این جست‌وجو پیدا نشد.</p>
              ) : null}
            </div>
          )}
        </Card>

        <div className="space-y-3">
          <Card>
            <CardHeader title="مشخصات ترانسمیتال" />
            <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-1">
              <Field label="هدف ارسال (P.O.I)">
                <Select value={purpose} onChange={(event) => setPurpose(event.target.value)}>
                  {Object.entries(PURPOSE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </Select>
              </Field>
              <Field label="نوع برگه ارسال">
                <Select value={type} onChange={(event) => setType(event.target.value as ManualTransmittalType)}>
                  <option value="OT">ترانسمیتال مهندسی - OT</option>
                  <option value="OS">ترانسمیتال نهایی - OS</option>
                </Select>
              </Field>
              <Field label="تاریخ صدور ترانسمیتال">
                <Input
                  className="ltr text-center"
                  type="text"
                  inputMode="numeric"
                  dir="ltr"
                  placeholder="۱۴۰۵/۰۸/۰۲"
                  value={issueDate}
                  onChange={(event) => setIssueDate(event.target.value)}
                />
              </Field>
              <Field label="تأیید کننده نهایی">
                <Select value={finalApproverId} onChange={(event) => setFinalApproverId(event.target.value)}>
                  <option value="">انتخاب شخص…</option>
                  {approverOptions.map((member) => (
                    <option key={member.user_id} value={member.user_id}>
                      {member.full_name}{member.discipline_name ? ` — ${member.discipline_name}` : ""}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="فرستنده / پیمانکار">
                <Select value={fromOrg} onChange={(event) => setFromOrg(event.target.value)}>
                  <option value="">انتخاب سازمان…</option>
                  {organizationOptions.map((organization) => <option key={`from-${organization}`} value={organization}>{organization}</option>)}
                </Select>
              </Field>
              <Field label="گیرنده / کارفرما یا طرف قرارداد">
                <Select value={toOrg} onChange={(event) => setToOrg(event.target.value)}>
                  <option value="">انتخاب سازمان…</option>
                  {organizationOptions.map((organization) => <option key={`to-${organization}`} value={organization}>{organization}</option>)}
                </Select>
              </Field>
              <Field label="توضیحات">
                <textarea
                  className="input min-h-[72px]"
                  value={remarks}
                  onChange={(event) => setRemarks(event.target.value)}
                  placeholder="توضیحات ترانسمیتال دستی"
                />
              </Field>
            </div>
          </Card>

          {selectedItems.length > 0 ? (
            <Card>
              <CardHeader title="Doc.Status" subtitle="وضعیت هر مدرک انتخاب‌شده را مشخص کنید." />
              <div className="space-y-2 p-3">
                {selectedItems.map((item) => (
                  <Field key={item.id} label={`${item.doc_code} — Doc.Status`}>
                    <Select
                      value={selected[item.id].doc_status}
                      onChange={(event) => updateLine(item.id, "doc_status", event.target.value)}
                    >
                      {DOC_STATUS_OPTIONS.map((status) => <option key={status} value={status}>{status}</option>)}
                    </Select>
                  </Field>
                ))}
              </div>
            </Card>
          ) : null}

          {selectedItems.length > 0 ? (
            <Card>
              <CardHeader title="بارگذاری فایل پیوست" subtitle="فقط فایل PDF پذیرفته می‌شود." />
              <div className="space-y-2 p-3">
                {selectedItems.map((item) => (
                  <Field key={item.id} label={`${item.doc_code} — فایل پیوست PDF`}>
                    <Input
                      type="file"
                      accept="application/pdf,.pdf"
                      multiple
                      onChange={(event) => updateAttachments(item.id, event.target.files)}
                    />
                    <span className="mt-0.5 block text-[9.5px] text-[#7a8794]">
                      {(attachments[item.id] ?? []).length
                        ? `${persianDigits((attachments[item.id] ?? []).length)} فایل انتخاب شده است.`
                        : "انتخاب فایل اختیاری است."}
                    </span>
                  </Field>
                ))}
              </div>
            </Card>
          ) : null}

          {selectedItems.length > 0 ? (
            <Card>
              <CardHeader title="یادداشت ردیف‌های انتخاب‌شده" />
              <div className="space-y-2 p-3">
                {selectedItems.map((item) => (
                  <Field key={item.id} label={`${item.doc_code} — یادداشت`}>
                    <Input
                      value={selected[item.id].note}
                      onChange={(event) => updateLine(item.id, "note", event.target.value)}
                      placeholder="اختیاری"
                    />
                  </Field>
                ))}
              </div>
            </Card>
          ) : null}

          <Button className="w-full" disabled={busy || loading || selectedItems.length === 0} onClick={() => void submit()}>
            {busy ? "در حال ایجاد پیش‌نویس…" : "ترانسمیتال جدید"}
          </Button>
        </div>
      </div>
    </div>
  );
}
