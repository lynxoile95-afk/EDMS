import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { Project } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Modal, Select, Spinner } from "@/components/ui";
import { persianDigits } from "@/lib/format";

interface ProjectParty {
  organization: string;
  role: string;
  responsibility: string;
}

interface ProjectForm {
  code: string;
  name: string;
  client_name: string;
  contractor_name: string;
  contractor_code: string;
  parent_company_code: string;
  project_type: string;
  description: string;
  parties: ProjectParty[];
}

interface ProjectEditForm {
  name: string;
  client_name: string;
  contractor_name: string;
  contractor_code: string;
  parent_company_code: string;
}

const NEW_CLIENT = "__new_client__";

const PROJECT_TYPES = [
  {
    code: "EPC",
    title_en: "Engineering, Procurement and Construction",
    responsibility: "مهندسی + خرید + ساخت",
    feature: "مسئولیت یکپارچه اجرای پروژه",
  },
  {
    code: "EPCF",
    title_en: "Engineering, Procurement, Construction and Finance",
    responsibility: "EPC + تأمین مالی",
    feature: "مسئولیت تأمین مالی نیز به قرارداد اضافه می‌شود",
  },
  {
    code: "EPCM",
    title_en: "Engineering, Procurement and Construction Management",
    responsibility: "مهندسی + تأمین + مدیریت ساخت",
    feature: "مدیریت ساخت به‌جای تعهد مستقیم به اجرای کل ساخت",
  },
  {
    code: "MC",
    title_en: "Management Contract",
    responsibility: "مدیریت پیمانکاران و عملیات اجرایی",
    feature: "تمرکز بر مدیریت اجرا",
  },
  {
    code: "PMC",
    title_en: "Project Management Consultancy",
    responsibility: "مدیریت و کنترل پروژه از طرف کارفرما",
    feature: "نقش مشاور و نماینده کارفرما",
  },
  {
    code: "EP",
    title_en: "Engineering and Procurement",
    responsibility: "مهندسی و خرید",
    feature: "ساخت معمولاً بر عهده سایر پیمانکاران است",
  },
  {
    code: "PC",
    title_en: "Procurement and Construction",
    responsibility: "خرید و ساخت",
    feature: "مهندسی عمدتاً توسط طرف دیگری انجام می‌شود",
  },
  {
    code: "BOT",
    title_en: "Build, Operate and Transfer",
    responsibility: "ساخت و بهره‌برداری و انتقال",
    feature: "مناسب پروژه‌های سرمایه‌گذاری زیرساختی",
  },
  {
    code: "TLA",
    title_en: "Technology License Agreement",
    responsibility: "قرارداد اعطای لیسانس فناوری",
    feature: "اعطای حق استفاده از فناوری و شرایط مجوز",
  },
  {
    code: "LA",
    title_en: "License Agreement",
    responsibility: "قرارداد لیسانس",
    feature: "تعیین حقوق و شرایط بهره‌برداری از فناوری یا محصول",
  },
  {
    code: "TTA",
    title_en: "Technology Transfer Agreement",
    responsibility: "قرارداد انتقال فناوری",
    feature: "انتقال فناوری، مستندات و خدمات مرتبط",
  },
  {
    code: "KTA",
    title_en: "Know-How Transfer Agreement",
    responsibility: "قرارداد انتقال دانش فنی",
    feature: "انتقال دانش فنی، تجربه و روش‌های اجرایی",
  },
  {
    code: "PDP",
    title_en: "Process Design Package",
    responsibility: "بسته طراحی فرآیند",
    feature: "ارائه مبانی و مدارک طراحی فرآیند",
  },
  {
    code: "BEDP",
    title_en: "Basic Engineering Design Package",
    responsibility: "بسته طراحی مهندسی پایه",
    feature: "ارائه بسته طراحی مهندسی پایه",
  },
] as const;

const PARTY_ROLES = [
  {
    value: "client_observer",
    label: "کارفرما / ناظر",
    responsibility: "نظارت عالیه، تأییدات و تحویل‌گیری",
  },
  {
    value: "main_contractor",
    label: "پیمانکار اصلی",
    responsibility: "هماهنگی و اجرای تعهدات اصلی قرارداد",
  },
  {
    value: "vendor",
    label: "شرکت فروشنده / تأمین‌کننده",
    responsibility: "تأمین کالا، تجهیزات یا خدمات تخصصی",
  },
  {
    value: "mc",
    label: "شرکت MC",
    responsibility: "مدیریت پیمانکاران و عملیات اجرایی",
  },
  {
    value: "pmc",
    label: "شرکت PMC / نماینده کارفرما",
    responsibility: "مدیریت و کنترل پروژه از طرف کارفرما",
  },
  {
    value: "consultant",
    label: "مشاور",
    responsibility: "خدمات مهندسی، نظارت یا مشاوره تخصصی",
  },
  {
    value: "construction_contractor",
    label: "پیمانکار ساخت",
    responsibility: "اجرای عملیات ساخت و نصب",
  },
] as const;

const EMPTY_FORM: ProjectForm = {
  code: "",
  name: "",
  client_name: "",
  contractor_name: "",
  contractor_code: "",
  parent_company_code: "",
  project_type: "",
  description: "",
  parties: [],
};

const EMPTY_EDIT_FORM: ProjectEditForm = {
  name: "",
  client_name: "",
  contractor_name: "",
  contractor_code: "",
  parent_company_code: "",
};

const COMPANY_CODE_PATTERN = /^[A-Z]{2}$/;

function deriveCompanyCode(name: string): string {
  const latinLetters = name.match(/[A-Za-z]/g)?.join("") ?? "";
  return latinLetters.slice(0, 2).toUpperCase();
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback;
}

function blankParty(): ProjectParty {
  const role = PARTY_ROLES[0];
  return {
    organization: "",
    role: role.value,
    responsibility: role.responsibility,
  };
}

function projectTypeLabel(item: Project): string {
  const settings = item.settings ?? {};
  const label = settings.project_type_label;
  const code = settings.project_type;
  if (typeof code === "string" && code && typeof label === "string" && label) return `${code} — ${label}`;
  if (typeof code === "string" && code) return code;
  if (typeof label === "string" && label) return label;
  return "—";
}

function partyCount(item: Project): number {
  const parties = item.settings?.project_parties;
  return Array.isArray(parties) ? parties.length : 0;
}

/** The entry point for the project-first workflow: define, select, then enter MDL. */
export function Projects() {
  const { user } = useAuth();
  const { projects, project, loading, select, reload } = useProject();
  const navigate = useNavigate();
  const [form, setForm] = useState<ProjectForm>(EMPTY_FORM);
  const [clientChoice, setClientChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [companyCodeTouched, setCompanyCodeTouched] = useState({ contractor: false, parent: false });
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [editForm, setEditForm] = useState<ProjectEditForm>(EMPTY_EDIT_FORM);
  const [editCodeTouched, setEditCodeTouched] = useState({ contractor: false, parent: false });
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const clientOptions = useMemo(() => {
    const values = new Set(
      projects
        .map((item) => item.client_name?.trim())
        .filter((value): value is string => Boolean(value)),
    );
    return Array.from(values).sort((left, right) => left.localeCompare(right, "fa"));
  }, [projects]);

  const selectedProjectType = PROJECT_TYPES.find((item) => item.code === form.project_type) ?? null;
  const clientSelectValue =
    clientChoice ||
    (form.client_name && clientOptions.includes(form.client_name) ? form.client_name : form.client_name ? NEW_CLIENT : "");

  const update = (field: keyof ProjectForm, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const updateCompanyName = (
    field: "contractor_name" | "client_name",
    codeField: "contractor_code" | "parent_company_code",
    value: string,
  ) => {
    setForm((current) => ({
      ...current,
      [field]: value,
      ...(codeField === "contractor_code" && !companyCodeTouched.contractor
        ? { contractor_code: deriveCompanyCode(value) }
        : {}),
      ...(codeField === "parent_company_code" && !companyCodeTouched.parent
        ? { parent_company_code: deriveCompanyCode(value) }
        : {}),
    }));
  };

  const updateCompanyCode = (field: "contractor_code" | "parent_company_code", value: string) => {
    const normalized = value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 2);
    setForm((current) => ({ ...current, [field]: normalized }));
    setCompanyCodeTouched((current) => ({
      ...current,
      [field === "contractor_code" ? "contractor" : "parent"]: true,
    }));
  };

  const updateParty = (index: number, field: keyof ProjectParty, value: string) => {
    setForm((current) => ({
      ...current,
      parties: current.parties.map((party, partyIndex) =>
        partyIndex === index ? { ...party, [field]: value } : party,
      ),
    }));
  };

  const changePartyRole = (index: number, value: string) => {
    const role = PARTY_ROLES.find((item) => item.value === value) ?? PARTY_ROLES[0];
    setForm((current) => ({
      ...current,
      parties: current.parties.map((party, partyIndex) =>
        partyIndex === index
          ? { ...party, role: role.value, responsibility: role.responsibility }
          : party,
      ),
    }));
  };

  const addParty = () => {
    setForm((current) => ({ ...current, parties: [...current.parties, blankParty()] }));
  };

  const removeParty = (index: number) => {
    setForm((current) => ({
      ...current,
      parties: current.parties.filter((_, partyIndex) => partyIndex !== index),
    }));
  };

  const enter = (item: Project, destination = "/dashboard") => {
    select(item.id);
    navigate(destination);
  };

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    const code = form.code.trim().toUpperCase();
    const name = form.name.trim();
    const clientName = form.client_name.trim();

    if (!/^[A-Z0-9_-]{2,32}$/.test(code)) {
      setError("کد پروژه باید ۲ تا ۳۲ نویسه و فقط شامل حروف انگلیسی، عدد، خط تیره یا زیرخط باشد.");
      return;
    }
    if (name.length < 2) {
      setError("نام پروژه الزامی است.");
      return;
    }
    if (!clientName) {
      setError("نام کارفرما را از فهرست انتخاب کنید یا گزینه «کارفرمای جدید / سایر» را انتخاب و وارد کنید.");
      return;
    }
    if (!COMPANY_CODE_PATTERN.test(form.contractor_code)) {
      setError("کد پیمانکار باید دقیقاً دو حرف انگلیسی A-Z باشد. نام فارسی به‌تنهایی کد تولید نمی‌کند.");
      return;
    }
    if (!COMPANY_CODE_PATTERN.test(form.parent_company_code)) {
      setError("کد شرکت مادر باید دقیقاً دو حرف انگلیسی A-Z باشد. نام فارسی به‌تنهایی کد تولید نمی‌کند.");
      return;
    }
    if (!form.project_type) {
      setError("نوع پروژه / قرارداد را انتخاب کنید.");
      return;
    }

    const parties: ProjectParty[] = [];
    for (const party of form.parties) {
      const organization = party.organization.trim();
      if (!organization) continue;
      if (organization.length < 2) {
        setError("نام شرکت یا سازمان هر طرف پروژه باید حداقل ۲ نویسه باشد.");
        return;
      }
      const role = PARTY_ROLES.find((item) => item.value === party.role) ?? PARTY_ROLES[0];
      parties.push({
        organization,
        role: role.value,
        responsibility: party.responsibility.trim() || role.responsibility,
      });
    }

    setBusy(true);
    try {
      const created = await api.post<Project>("/projects", {
        code,
        name,
        client_name: clientName,
        contractor_name: form.contractor_name.trim() || null,
        contractor_code: form.contractor_code,
        parent_company_code: form.parent_company_code,
        description: form.description.trim() || null,
        settings: {
          project_type: selectedProjectType?.code,
          project_type_title_en: selectedProjectType?.title_en,
          project_type_label: selectedProjectType?.responsibility,
          project_type_feature: selectedProjectType?.feature,
          project_parties: parties,
        },
      });
      await reload();
      select(created.id);
      setForm(EMPTY_FORM);
      setCompanyCodeTouched({ contractor: false, parent: false });
      setClientChoice("");
      setNotice(`پروژه «${created.name}» ساخته شد و اکنون انتخاب شده است.`);
    } catch (err) {
      setError(messageOf(err, "تعریف پروژه ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const canEditProject = (item: Project) =>
    Boolean(user?.is_superuser) ||
    ["dcc", "project_control", "pmo", "project_manager"].includes(item.member_role ?? "");

  const openEdit = (item: Project) => {
    setEditingProject(item);
    setEditError(null);
    setEditCodeTouched({
      contractor: Boolean(item.contractor_code),
      parent: Boolean(item.parent_company_code),
    });
    setEditForm({
      name: item.name,
      client_name: item.client_name ?? "",
      contractor_name: item.contractor_name ?? "",
      contractor_code: item.contractor_code ?? deriveCompanyCode(item.contractor_name ?? ""),
      parent_company_code: item.parent_company_code ?? deriveCompanyCode(item.client_name ?? ""),
    });
  };

  const saveEdit = async () => {
    if (!editingProject) return;
    setEditError(null);
    if (editForm.name.trim().length < 2) {
      setEditError("نام پروژه الزامی است.");
      return;
    }
    if (!COMPANY_CODE_PATTERN.test(editForm.contractor_code)) {
      setEditError("کد پیمانکار باید دقیقاً دو حرف انگلیسی A-Z باشد.");
      return;
    }
    if (!COMPANY_CODE_PATTERN.test(editForm.parent_company_code)) {
      setEditError("کد شرکت مادر باید دقیقاً دو حرف انگلیسی A-Z باشد.");
      return;
    }
    setEditBusy(true);
    try {
      const updated = await api.patch<Project>(`/projects/${editingProject.id}`, {
        name: editForm.name.trim(),
        client_name: editForm.client_name.trim() || null,
        contractor_name: editForm.contractor_name.trim() || null,
        contractor_code: editForm.contractor_code,
        parent_company_code: editForm.parent_company_code,
      });
      await reload();
      select(updated.id);
      setEditingProject(null);
      setNotice(`اطلاعات پروژه «${updated.name}» به‌روزرسانی شد.`);
    } catch (err) {
      setEditError(messageOf(err, "ویرایش پروژه ناموفق بود."));
    } finally {
      setEditBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <Card className="is-main">
        <CardHeader
          title="مدیریت و انتخاب پروژه"
          subtitle="ابتدا پروژه را تعریف و انتخاب کنید؛ سپس MDL همان پروژه را بارگذاری کنید."
        />
        <div className="border-b border-[#c3ccd6] bg-[#fffdf3] px-3 py-2 text-[10.5px] leading-5 text-[#5b4d1a]">
          هر پروژه MDL مستقل خود را دارد. تا زمانی که یک پروژه انتخاب نشده باشد، صفحات MDL، ثبت مدرک و
          ترانسمیتال باز نمی‌شوند و هیچ داده‌ای به پروژه دیگری ارسال نخواهد شد.
        </div>
        {notice ? <div className="px-3 pt-2 text-[10.5px] text-[#1f6b34]">{notice}</div> : null}
        {error ? (
          <div className="p-3">
            <ErrorBox message={error} />
          </div>
        ) : null}
      </Card>

      <div className="grid gap-2">
        {user?.is_superuser ? (
          <Card>
            <CardHeader title="تعریف پروژه جدید" subtitle="پروژه قبل از بارگذاری MDL باید ایجاد شود." />
            <form className="space-y-2 p-3" onSubmit={create}>
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label="شماره قرارداد / کد پروژه *" hint="این مقدار در Contract و شماره ترانسمیتال استفاده می‌شود؛ نمونه: 7001">
                  <Input
                    value={form.code}
                    onChange={(event) => update("code", event.target.value)}
                    placeholder="7001"
                    maxLength={32}
                    dir="ltr"
                    autoComplete="off"
                  />
                </Field>
                <Field label="نام پروژه *">
                  <Input
                    value={form.name}
                    onChange={(event) => update("name", event.target.value)}
                    placeholder="نام پروژه مهندسی"
                    maxLength={255}
                  />
                </Field>
                <Field
                  label="نام کارفرما *"
                  hint={clientOptions.length ? "کارفرماهای ثبت‌شده پروژه‌های قبلی نیز در فهرست هستند." : "برای اولین پروژه، گزینه کارفرمای جدید را انتخاب کنید."}
                >
                  <Select
                    value={clientSelectValue}
                    onChange={(event) => {
                      const value = event.target.value;
                      setClientChoice(value);
                      if (value !== NEW_CLIENT) updateCompanyName("client_name", "parent_company_code", value);
                      else updateCompanyName("client_name", "parent_company_code", "");
                    }}
                  >
                    <option value="">انتخاب کارفرما…</option>
                    {clientOptions.map((client) => (
                      <option key={client} value={client}>
                        {client}
                      </option>
                    ))}
                    <option value={NEW_CLIENT}>کارفرمای جدید / سایر</option>
                  </Select>
                  {clientChoice === NEW_CLIENT ? (
                    <Input
                      className="mt-1"
                      value={form.client_name}
                      onChange={(event) => updateCompanyName("client_name", "parent_company_code", event.target.value)}
                      placeholder="نام شرکت یا سازمان کارفرما"
                      maxLength={255}
                    />
                  ) : null}
                </Field>
                <Field label="نام پیمانکار اصلی">
                  <Input
                    value={form.contractor_name}
                    onChange={(event) => updateCompanyName("contractor_name", "contractor_code", event.target.value)}
                    placeholder="پیمانکار اصلی"
                    maxLength={255}
                  />
                </Field>
                <Field
                  label="کد پیمانکار *"
                  hint="دو حرف اول نام لاتین خودکار است؛ در نام فارسی، کد را دستی با A-Z وارد کنید."
                >
                  <Input
                    value={form.contractor_code}
                    onChange={(event) => updateCompanyCode("contractor_code", event.target.value)}
                    placeholder="IP"
                    maxLength={2}
                    dir="ltr"
                    className="uppercase"
                    autoComplete="off"
                  />
                </Field>
                <Field
                  label="کد شرکت مادر *"
                  hint="معمولاً از دو حرف اول نام لاتین کارفرما تکمیل می‌شود."
                >
                  <Input
                    value={form.parent_company_code}
                    onChange={(event) => updateCompanyCode("parent_company_code", event.target.value)}
                    placeholder="MR"
                    maxLength={2}
                    dir="ltr"
                    className="uppercase"
                    autoComplete="off"
                  />
                </Field>
                <Field label="نوع پروژه / قرارداد *" hint="مسئولیت اصلی پیمانکار را مشخص می‌کند.">
                  <Select
                    value={form.project_type}
                    onChange={(event) => update("project_type", event.target.value)}
                  >
                    <option value="">انتخاب نوع قرارداد…</option>
                    {PROJECT_TYPES.map((item) => (
                      <option key={item.code} value={item.code}>
                        {item.code} — {item.responsibility} ({item.title_en})
                      </option>
                    ))}
                  </Select>
                </Field>
                <div className="self-end text-[9.5px] leading-4 text-[#5c6b78]">
                  {selectedProjectType ? (
                    <>
                      <div>
                        <b className="text-[#1f3864]">عنوان انگلیسی:</b> {selectedProjectType.title_en}
                      </div>
                      <div>
                        <b className="text-[#1f3864]">ویژگی:</b> {selectedProjectType.feature}
                      </div>
                    </>
                  ) : (
                    "پس از انتخاب نوع قرارداد، ویژگی آن در این قسمت نمایش داده می‌شود."
                  )}
                </div>
              </div>

              <div className="border border-[#b9c3cd] bg-[#f7f9fb] p-2">
                <div className="flex flex-wrap items-start justify-between gap-2 border-b border-[#d5dde5] pb-2">
                  <div>
                    <p className="text-[11px] font-bold text-[#1f3864]">طرف‌های قرارداد و ساختار اجرایی</p>
                    <p className="mt-0.5 text-[9.5px] leading-4 text-[#5c6b78]">
                      برای سناریوهایی مثل شرکت فروشنده، شرکت MC و کارفرمای ناظر، هر طرف را جداگانه ثبت کنید.
                    </p>
                  </div>
                  <Button type="button" className="btn-sm" onClick={addParty}>
                    افزودن طرف پروژه
                  </Button>
                </div>
                {form.parties.length === 0 ? (
                  <p className="py-3 text-center text-[10px] text-[#7a8794]">
                    طرف اضافی تعریف نشده است. در صورت نیاز، دکمه «افزودن طرف پروژه» را بزنید.
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    {form.parties.map((party, index) => (
                      <div key={index} className="grid gap-1 border border-[#d5dde5] bg-white p-2 sm:grid-cols-[1.1fr_1fr_1.5fr_auto] sm:items-end">
                        <Field label="شرکت / سازمان">
                          <Input
                            value={party.organization}
                            onChange={(event) => updateParty(index, "organization", event.target.value)}
                            placeholder="مثلاً شرکت فروشنده"
                            maxLength={255}
                          />
                        </Field>
                        <Field label="نقش طرف پروژه">
                          <Select value={party.role} onChange={(event) => changePartyRole(index, event.target.value)}>
                            {PARTY_ROLES.map((role) => (
                              <option key={role.value} value={role.value}>
                                {role.label}
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <Field label="مسئولیت / توضیح">
                          <Input
                            value={party.responsibility}
                            onChange={(event) => updateParty(index, "responsibility", event.target.value)}
                            placeholder="مسئولیت این شرکت در پروژه"
                            maxLength={500}
                          />
                        </Field>
                        <Button type="button" className="btn-sm" variant="danger" onClick={() => removeParty(index)}>
                          حذف
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <Field label="شرح پروژه">
                <textarea
                  className="input min-h-16 resize-y"
                  value={form.description}
                  onChange={(event) => update("description", event.target.value)}
                  placeholder="توضیحات اختیاری"
                  maxLength={2000}
                />
              </Field>
              <div className="flex justify-end">
                <Button type="submit" disabled={busy}>
                  {busy ? "در حال ایجاد…" : "تعریف پروژه و انتخاب آن"}
                </Button>
              </div>
            </form>
          </Card>
        ) : (
          <Card>
            <CardHeader title="دسترسی ایجاد پروژه" />
            <div className="p-3 text-[10.5px] leading-5 text-[#4a5a67]">
              فقط مدیر سیستم می‌تواند پروژه جدید تعریف کند. پس از ایجاد پروژه، مدیر سیستم باید شما را به
              عنوان عضو پروژه و نقش مناسب اضافه کند.
            </div>
          </Card>
        )}

        <Card>
          <CardHeader
            title="پروژه‌های قابل دسترس"
            subtitle={loading ? "در حال دریافت فهرست…" : `${persianDigits(projects.length)} پروژه`}
          />
          {loading ? (
            <Spinner />
          ) : projects.length === 0 ? (
            <EmptyState
              title="هنوز پروژه‌ای تعریف نشده است"
              description={user?.is_superuser ? "از فرم کنار صفحه اولین پروژه را تعریف کنید." : "از مدیر سیستم درخواست دسترسی کنید."}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th style={{ width: 120 }}>کد پروژه</th>
                    <th>نام پروژه</th>
                    <th style={{ width: 150 }}>نوع قرارداد</th>
                    <th style={{ width: 90 }}>طرف‌ها</th>
                    <th style={{ width: 100 }}>تعداد MDL</th>
                    <th style={{ width: 140 }}>نقش شما</th>
                    <th style={{ width: 90 }}>ویرایش</th>
                    <th style={{ width: 150 }}>ورود</th>
                  </tr>
                </thead>
                <tbody>
                  {projects.map((item) => (
                    <tr key={item.id} className={project?.id === item.id ? "is-selected" : undefined}>
                      <td className="ltr font-bold">{item.code}</td>
                      <td>
                        <div className="font-bold text-[#1f3864]">{item.name}</div>
                        <div className="text-[9.5px] text-[#5c6b78]">
                          {item.client_name ?? "بدون کارفرما"}
                        </div>
                      </td>
                      <td>
                        <Badge className="bg-[#eef4fa] text-[#1f3864]">{projectTypeLabel(item)}</Badge>
                      </td>
                      <td className="cell-center">{persianDigits(partyCount(item))}</td>
                      <td className="cell-center">{persianDigits(item.document_count ?? 0)}</td>
                      <td>{item.member_role ?? "—"}</td>
                      <td>
                        {canEditProject(item) ? (
                          <Button className="btn-sm" variant="secondary" onClick={() => openEdit(item)}>
                            ویرایش
                          </Button>
                        ) : "—"}
                      </td>
                      <td>
                        <Button className="btn-sm" onClick={() => enter(item)}>
                          {project?.id === item.id ? "ورود به پروژه" : "انتخاب و ورود"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {project ? (
        <Card>
          <CardHeader title="پروژه انتخاب‌شده" subtitle={`${project.code} — ${project.name}`} />
          <div className="flex flex-wrap items-center justify-between gap-2 p-3">
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10.5px] text-[#4a5a67]">
                اکنون تمام عملیات MDL و ترانسمیتال روی همین پروژه انجام می‌شود.
              </span>
              {projectTypeLabel(project) !== "—" ? (
                <Badge className="bg-[#eef4fa] text-[#1f3864]">{projectTypeLabel(project)}</Badge>
              ) : null}
            </div>
            <div className="flex gap-1">
              {canEditProject(project) ? (
                <Button className="btn-sm" variant="secondary" onClick={() => openEdit(project)}>
                  ویرایش پروژه
                </Button>
              ) : null}
              <Button className="btn-sm" onClick={() => navigate("/mdl")}>
                ورود به MDL
              </Button>
              <Button className="btn-sm" variant="secondary" onClick={() => navigate("/transmittals")}>
                مشاهده ترانسمیتال‌ها
              </Button>
            </div>
          </div>
        </Card>
      ) : null}

      <Modal
        open={Boolean(editingProject)}
        title="ویرایش پروژه و کدهای شرکت"
        onClose={() => setEditingProject(null)}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end gap-1">
            <Button className="btn-sm" variant="secondary" onClick={() => setEditingProject(null)}>
              انصراف
            </Button>
            <Button className="btn-sm" disabled={editBusy} onClick={() => void saveEdit()}>
              {editBusy ? "در حال ذخیره…" : "ذخیره تغییرات"}
            </Button>
          </div>
        }
      >
        {editError ? <ErrorBox message={editError} /> : null}
        <div className="grid gap-2 p-3 sm:grid-cols-2">
          <Field label="نام پروژه *">
            <Input
              value={editForm.name}
              onChange={(event) => setEditForm((current) => ({ ...current, name: event.target.value }))}
              maxLength={255}
            />
          </Field>
          <Field label="نام کارفرما / شرکت مادر">
            <Input
              value={editForm.client_name}
              onChange={(event) => {
                const value = event.target.value;
                setEditForm((current) => ({
                  ...current,
                  client_name: value,
                  ...(editCodeTouched.parent ? {} : { parent_company_code: deriveCompanyCode(value) }),
                }));
              }}
              maxLength={255}
            />
          </Field>
          <Field label="نام پیمانکار اصلی">
            <Input
              value={editForm.contractor_name}
              onChange={(event) => {
                const value = event.target.value;
                setEditForm((current) => ({
                  ...current,
                  contractor_name: value,
                  ...(editCodeTouched.contractor ? {} : { contractor_code: deriveCompanyCode(value) }),
                }));
              }}
              maxLength={255}
            />
          </Field>
          <Field label="کد پیمانکار *" hint="دقیقاً دو حرف انگلیسی A-Z">
            <Input
              value={editForm.contractor_code}
              onChange={(event) => {
                setEditCodeTouched((current) => ({ ...current, contractor: true }));
                setEditForm((current) => ({
                  ...current,
                  contractor_code: event.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 2),
                }));
              }}
              maxLength={2}
              dir="ltr"
              className="uppercase"
            />
          </Field>
          <Field label="کد شرکت مادر *" hint="دقیقاً دو حرف انگلیسی A-Z">
            <Input
              value={editForm.parent_company_code}
              onChange={(event) => {
                setEditCodeTouched((current) => ({ ...current, parent: true }));
                setEditForm((current) => ({
                  ...current,
                  parent_company_code: event.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 2),
                }));
              }}
              maxLength={2}
              dir="ltr"
              className="uppercase"
            />
          </Field>
        </div>
        <div className="border-t border-[#d5dde5] px-3 pb-3 text-[10px] leading-5 text-[#5c6b78]">
          این دو کد فقط روی ترانسمیتال‌های جدید اثر می‌گذارند؛ شماره‌های صادرشده قبلی بدون تغییر باقی می‌مانند.
        </div>
      </Modal>
    </div>
  );
}
