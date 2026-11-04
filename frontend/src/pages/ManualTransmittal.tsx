import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { MDLItem, ProjectMember, TransmittalDetail } from "@/lib/types";
import { Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Select, Spinner } from "@/components/ui";
import { PURPOSE_LABELS, persianDigits } from "@/lib/format";

interface ManualLine {
  revision: string;
  page_count: string;
  note: string;
}

interface ProjectParty {
  organization?: string;
  role?: string;
  responsibility?: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.detail : "ثبت ترانسمیتال دستی ناموفق بود.";
}

export function ManualTransmittalPage() {
  const { project, isDcc } = useProject();
  const navigate = useNavigate();
  const [items, setItems] = useState<MDLItem[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [selected, setSelected] = useState<Record<string, ManualLine>>({});
  const [purpose, setPurpose] = useState("IFR");
  const [type, setType] = useState("internal");
  const [fromOrg, setFromOrg] = useState("");
  const [toOrg, setToOrg] = useState("");
  const [remarks, setRemarks] = useState("");
  const [recipientIds, setRecipientIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const toggleRecipient = (userId: string) => {
    setRecipientIds((current) =>
      current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId],
    );
  };

  const submit = async () => {
    if (!project) return;
    setError(null);
    if (selectedItems.length === 0) {
      setError("حداقل یک مدرک از MDL را انتخاب کنید.");
      return;
    }
    if (recipientIds.length === 0) {
      setError("حداقل یک گیرنده از اعضای پروژه انتخاب کنید.");
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
        })),
        purpose,
        type,
        from_org: fromOrg.trim() || null,
        to_org: toOrg.trim() || null,
        remarks: remarks.trim() || null,
        extra_recipient_ids: recipientIds,
      });
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
          در این مسیر، اطلاعات مدرک از MDL و مقدار بازنگری از کاربر گرفته می‌شود. برای هر ردیف در صورت نبودن
          بازنگری قبلی، یک بازنگری دستی ثبت می‌شود و سپس پیش‌نویس ترانسمیتال ساخته خواهد شد.
        </div>
        {!isDcc ? (
          <div className="px-3 pt-2 text-[10.5px] text-[#8a5a12]">
            ثبت نهایی توسط کنترل مدارک یا مدیر پروژه انجام می‌شود؛ دسترسی فعلی شما ممکن است فقط مشاهده باشد.
          </div>
        ) : null}
      </Card>

      {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}

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
                <Select value={type} onChange={(event) => setType(event.target.value)}>
                  <option value="internal">داخلی — Internal</option>
                  <option value="external">خارجی — External</option>
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

          <Card>
            <CardHeader title="گیرندگان پروژه" subtitle="برای تشکیل کارتابل و صدور ترانسمیتال حداقل یک گیرنده انتخاب کنید." />
            <div className="divide-y divide-slate-100">
              {members.length === 0 ? (
                <p className="p-3 text-xs text-slate-500">عضو فعالی در این پروژه تعریف نشده است.</p>
              ) : (
                members.map((member) => (
                  <label key={member.user_id} className="flex cursor-pointer items-start gap-2 px-3 py-2 hover:bg-slate-50">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={recipientIds.includes(member.user_id)}
                      onChange={() => toggleRecipient(member.user_id)}
                    />
                    <span className="min-w-0">
                      <span className="block text-xs font-bold text-[#1f3864]">{member.full_name}</span>
                      <span className="block ltr text-[10px] text-slate-500">{member.email}</span>
                      <span className="text-[10px] text-slate-500">{member.role}{member.discipline_code ? ` · ${member.discipline_code}` : ""}</span>
                    </span>
                  </label>
                ))
              )}
            </div>
          </Card>

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
            {busy ? "در حال ایجاد ترانسمیتال…" : "ایجاد ترانسمیتال جدید"}
          </Button>
        </div>
      </div>
    </div>
  );
}
