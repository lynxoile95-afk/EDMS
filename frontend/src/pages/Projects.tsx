import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { Project } from "@/lib/types";
import { Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Spinner } from "@/components/ui";
import { persianDigits } from "@/lib/format";

interface ProjectForm {
  code: string;
  name: string;
  client_name: string;
  contractor_name: string;
  description: string;
}

const EMPTY_FORM: ProjectForm = {
  code: "",
  name: "",
  client_name: "",
  contractor_name: "",
  description: "",
};

function messageOf(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback;
}

/** The entry point for the project-first workflow: define, select, then enter MDL. */
export function Projects() {
  const { user } = useAuth();
  const { projects, project, loading, select, reload } = useProject();
  const navigate = useNavigate();
  const [form, setForm] = useState<ProjectForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const update = (field: keyof ProjectForm, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
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
    if (!/^[A-Z0-9_-]{2,32}$/.test(code)) {
      setError("کد پروژه باید ۲ تا ۳۲ نویسه و فقط شامل حروف انگلیسی، عدد، خط تیره یا زیرخط باشد.");
      return;
    }
    if (name.length < 2) {
      setError("نام پروژه الزامی است.");
      return;
    }

    setBusy(true);
    try {
      const created = await api.post<Project>("/projects", {
        code,
        name,
        client_name: form.client_name.trim() || null,
        contractor_name: form.contractor_name.trim() || null,
        description: form.description.trim() || null,
      });
      await reload();
      select(created.id);
      setForm(EMPTY_FORM);
      setNotice(`پروژه «${created.name}» ساخته شد و اکنون انتخاب شده است.`);
    } catch (err) {
      setError(messageOf(err, "تعریف پروژه ناموفق بود."));
    } finally {
      setBusy(false);
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

      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
        {user?.is_superuser ? (
          <Card>
            <CardHeader title="تعریف پروژه جدید" subtitle="پروژه قبل از بارگذاری MDL باید ایجاد شود." />
            <form className="space-y-2 p-3" onSubmit={create}>
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label="کد پروژه *" hint="نمونه: PRJ-001">
                  <Input
                    value={form.code}
                    onChange={(event) => update("code", event.target.value)}
                    placeholder="PRJ-001"
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
                <Field label="نام کارفرما">
                  <Input
                    value={form.client_name}
                    onChange={(event) => update("client_name", event.target.value)}
                    placeholder="کارفرما"
                    maxLength={255}
                  />
                </Field>
                <Field label="نام پیمانکار">
                  <Input
                    value={form.contractor_name}
                    onChange={(event) => update("contractor_name", event.target.value)}
                    placeholder="پیمانکار"
                    maxLength={255}
                  />
                </Field>
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
                    <th style={{ width: 100 }}>تعداد MDL</th>
                    <th style={{ width: 140 }}>نقش شما</th>
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
                      <td className="cell-center">{persianDigits(item.document_count ?? 0)}</td>
                      <td>{item.member_role ?? "—"}</td>
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
            <span className="text-[10.5px] text-[#4a5a67]">
              اکنون تمام عملیات MDL و ترانسمیتال روی همین پروژه انجام می‌شود.
            </span>
            <div className="flex gap-1">
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
    </div>
  );
}
