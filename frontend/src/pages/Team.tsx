import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { AccessCircle, ProjectMember, Role, SystemUser, Lookup } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Modal, Select, Spinner } from "@/components/ui";
import { persianDigits } from "@/lib/format";

/** The roles the review route needs, in route order. */
const ROUTE_ROLES = [
  { role: "submitter", step: "۱", label: "پیمانکار", job: "برگه ارسال را می‌سازد و برای کنترل اسناد می‌فرستد" },
  { role: "dcc", step: "۲", label: "کارشناس کنترل اسناد مهندسی", job: "محتوا را کنترل می‌کند و به مدیر می‌فرستد" },
  { role: "project_control", step: "۳", label: "کنترل پروژه", job: "وضعیت، برنامه و هماهنگی ارجاع‌ها را کنترل می‌کند" },
  { role: "pmo", step: "۳", label: "واحد PMO", job: "هماهنگی پرتفوی و پیگیری گردش مدارک" },
  { role: "engineering_manager", step: "۳", label: "مدیر مهندسی", job: "برگه را به دیسیپلین مربوطه ارجاع می‌دهد" },
  { role: "project_manager", step: "۳", label: "مدیر پروژه", job: "در مرحله مدیریت هم‌کار می‌کند و ارجاع می‌دهد" },
  { role: "reviewer", step: "۴", label: "کارشناس دیسیپلین", job: "برگه ارجاع‌شده به دیسیپلین خودش را بررسی می‌کند" },
  { role: "system_admin", step: "—", label: "مدیر سیستم", job: "مدیریت حساب‌ها، پروژه‌ها و سیاست دسترسی" },
];

const ROLE_LABELS: Record<string, string> = {
  submitter: "پیمانکار",
  dcc: "کنترل اسناد",
  project_control: "کنترل پروژه",
  pmo: "واحد PMO",
  engineering_manager: "مدیر مهندسی",
  project_manager: "مدیر پروژه",
  reviewer: "کارشناس دیسیپلین",
  system_admin: "مدیر سیستم",
};

const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: "submitter", label: "پیمانکار" },
  { value: "dcc", label: "کنترل اسناد (DCC)" },
  { value: "project_control", label: "کنترل پروژه" },
  { value: "pmo", label: "واحد PMO" },
  { value: "engineering_manager", label: "مدیر مهندسی" },
  { value: "project_manager", label: "مدیر پروژه" },
  { value: "reviewer", label: "کارشناس دیسیپلین" },
  { value: "system_admin", label: "مدیر سیستم" },
];

interface NewUserForm {
  full_name: string;
  email: string;
  national_id: string;
  mobile: string;
  password: string;
  is_superuser: boolean;
}

interface NewCircleForm {
  code: string;
  name: string;
  description: string;
}

interface NewMemberForm {
  user_id: string;
  role: Role;
  discipline_id: string;
  circle_id: string;
  can_access_external: boolean;
}

const EMPTY_USER: NewUserForm = {
  full_name: "",
  email: "",
  national_id: "",
  mobile: "",
  password: "",
  is_superuser: false,
};

const EMPTY_CIRCLE: NewCircleForm = { code: "", name: "", description: "" };
const EMPTY_MEMBER: NewMemberForm = {
  user_id: "",
  role: "reviewer",
  discipline_id: "",
  circle_id: "",
  can_access_external: false,
};

function messageOf(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback;
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/**
 * Classic EDMS roster plus the real project access boundary administration.
 * Roles answer «what may this person do in the route?»; circles answer «with
 * whom may this person collaborate?». Keeping those tables separate prevents
 * a reviewer role from accidentally granting project-wide visibility.
 */
export function Team() {
  const { project } = useProject();
  const { user } = useAuth();
  const canManageCircles = Boolean(
    user?.is_superuser ||
      ["dcc", "project_control", "pmo", "project_manager"].includes(project?.member_role ?? ""),
  );
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [circles, setCircles] = useState<AccessCircle[]>([]);
  const [users, setUsers] = useState<SystemUser[]>([]);
  const [disciplines, setDisciplines] = useState<Lookup[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [circleModalOpen, setCircleModalOpen] = useState(false);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [newUser, setNewUser] = useState<NewUserForm>(EMPTY_USER);
  const [newCircle, setNewCircle] = useState<NewCircleForm>(EMPTY_CIRCLE);
  const [newMember, setNewMember] = useState<NewMemberForm>(EMPTY_MEMBER);

  const load = async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const [memberRows, circleRows, disciplineRows] = await Promise.all([
        api.get<ProjectMember[]>(`/projects/${project.id}/members`),
        api.get<AccessCircle[]>(`/projects/${project.id}/circles`),
        api.get<Lookup[]>(`/projects/${project.id}/disciplines`),
      ]);
      setMembers(memberRows);
      setCircles(circleRows);
      setDisciplines(disciplineRows);
      if (user?.is_superuser) {
        setUsers(await api.get<SystemUser[]>("/admin/users"));
      } else {
        setUsers([]);
      }
    } catch (err) {
      setError(messageOf(err, "بارگذاری کاربران و حلقه‌های دسترسی ناموفق بود."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [project?.id, user?.is_superuser]);

  const usersForPicker = useMemo(() => {
    if (user?.is_superuser && users.length) return users.filter((item) => item.is_active);
    const byId = new Map<string, SystemUser>();
    members.forEach((item) => {
      byId.set(item.user_id, {
        id: item.user_id,
        email: item.email,
        full_name: item.full_name,
        national_id: item.national_id,
        mobile: item.mobile,
        is_active: item.is_active,
        is_superuser: false,
        locale: "fa",
        auth_provider: "local",
        last_login_at: null,
        memberships: [],
      });
    });
    return Array.from(byId.values());
  }, [members, user?.is_superuser, users]);

  const selectedCircle = circles.find((circle) => circle.id === newMember.circle_id);
  const selectedUserAlreadyInProject = members.some(
    (item) => item.user_id === newMember.user_id && item.role === newMember.role,
  );

  const createUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await api.post<SystemUser>("/admin/users", {
        ...newUser,
        full_name: newUser.full_name.trim(),
        email: newUser.email.trim(),
        national_id: newUser.national_id.trim(),
        mobile: newUser.mobile.trim(),
      });
      setUsers((current) => [...current, created].sort((left, right) => left.full_name.localeCompare(right.full_name, "fa")));
      setNewMember((current) => ({ ...current, user_id: created.id }));
      setNewUser(EMPTY_USER);
      setUserModalOpen(false);
      setMemberModalOpen(true);
      setNotice(`حساب «${created.full_name}» ساخته شد؛ اکنون نقش و حلقه را تعیین کنید.`);
    } catch (err) {
      setError(messageOf(err, "ایجاد حساب کاربر ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const createCircle = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!project) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.post<AccessCircle>(`/projects/${project.id}/circles`, {
        code: newCircle.code.trim().toUpperCase(),
        name: newCircle.name.trim(),
        description: newCircle.description.trim() || null,
      });
      setNewCircle(EMPTY_CIRCLE);
      setCircleModalOpen(false);
      await load();
      setNotice("حلقه دسترسی ساخته شد. اکنون افراد پروژه را داخل آن قرار دهید.");
    } catch (err) {
      setError(messageOf(err, "ایجاد حلقه ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const addMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!project || !newMember.user_id || !newMember.circle_id) {
      setError("کاربر و حلقه دسترسی را انتخاب کنید.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (!selectedUserAlreadyInProject) {
        await api.post<ProjectMember>(`/projects/${project.id}/members`, {
          user_id: newMember.user_id,
          role: newMember.role,
          discipline_id: newMember.discipline_id || null,
        });
      }
      await api.post<AccessCircle>(`/projects/${project.id}/circles/${newMember.circle_id}/members`, {
        user_id: newMember.user_id,
        can_access_external: newMember.can_access_external,
      });
      setMemberModalOpen(false);
      setNewMember({ ...EMPTY_MEMBER, circle_id: newMember.circle_id });
      await load();
      setNotice("فرد با نقش پروژه و عضویت حلقه ثبت شد.");
    } catch (err) {
      setError(messageOf(err, "افزودن فرد به پروژه یا حلقه ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const toggleExternal = async (circle: AccessCircle, memberId: string, checked: boolean) => {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      await api.patch<AccessCircle>(
        `/projects/${project.id}/circles/${circle.id}/members/${memberId}`,
        { can_access_external: checked },
      );
      await load();
    } catch (err) {
      setError(messageOf(err, "تغییر دسترسی خارجی ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const removeCircleMember = async (circle: AccessCircle, memberId: string) => {
    if (!project || !window.confirm("این فرد از حلقه حذف شود؟ عضویت او در پروژه حذف نمی‌شود.")) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/projects/${project.id}/circles/${circle.id}/members/${memberId}`);
      await load();
    } catch (err) {
      setError(messageOf(err, "حذف فرد از حلقه ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;

  return (
    <div className="space-y-4">
      <Card className="is-main">
        <CardHeader
          title="کاربران، نقش‌ها و حلقه‌های دسترسی"
          subtitle={`${persianDigits(members.length)} عضویت در پروژه — نقش گردش و مرز دسترسی جداگانه کنترل می‌شوند`}
          actions={
            <div className="flex flex-wrap gap-1">
              {user?.is_superuser ? (
                <Button className="btn-sm" variant="primary" onClick={() => setUserModalOpen(true)}>
                  تعریف کاربر جدید
                </Button>
              ) : null}
              {canManageCircles ? (
                <>
                  <Button className="btn-sm" onClick={() => setCircleModalOpen(true)}>
                    ایجاد حلقه دسترسی
                  </Button>
                  <Button className="btn-sm" onClick={() => setMemberModalOpen(true)} disabled={!circles.length}>
                    افزودن فرد به حلقه
                  </Button>
                </>
              ) : null}
            </div>
          }
        />
        <div className="border-b border-[#c3ccd6] bg-[#fffdf3] px-3 py-2 text-[10.5px] leading-5 text-[#5b4d1a]">
          <b>قاعده دسترسی:</b> افراد هر حلقه فقط ترانسمیتال‌ها و ارجاع‌های همان حلقه را می‌بینند و می‌توانند برای
          یکدیگر ارجاع ثبت کنند. در هر حلقه فقط یک نفر می‌تواند گزینه «دسترسی خارج از حلقه» داشته باشد؛ این
          گزینه برای DCC، کنترل پروژه یا PMO که باید با حلقه‌های دیگر کار کند فعال می‌شود.
        </div>
        {notice ? <div className="px-3 pt-2 text-[10.5px] text-[#1f6b34]">{notice}</div> : null}
        {error ? (
          <div className="p-3">
            <ErrorBox message={error} />
          </div>
        ) : null}
      </Card>

      {loading ? <Spinner label="در حال دریافت کاربران و حلقه‌های دسترسی…" /> : null}

      <Card>
        <CardHeader
          title="کاربران و نقش‌های مرحلهٔ گردش"
          subtitle="کد ملی و شماره موبایل برای هر حساب نگهداری می‌شود؛ نقش، سطح عملیاتی فرد در این پروژه است."
        />
        {!loading && members.length === 0 ? (
          <EmptyState
            title="عضوی برای این پروژه تعریف نشده است"
            description={user?.is_superuser ? "یک کاربر بسازید و سپس نقش و حلقه او را تعیین کنید." : "از مدیر سیستم درخواست عضویت کنید."}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th style={{ width: 190 }}>نقش</th>
                  <th>کاربر</th>
                  <th style={{ width: 170 }}>کد ملی</th>
                  <th style={{ width: 130 }}>موبایل</th>
                  <th style={{ width: 190 }} className="ltr">ورود</th>
                  <th style={{ width: 110 }}>دیسیپلین</th>
                  <th>مسئولیت در گردش</th>
                </tr>
              </thead>
              <tbody>
                {members.map((member) => {
                  const spec = ROUTE_ROLES.find((item) => item.role === member.role);
                  return (
                    <tr key={member.id}>
                      <td>{roleLabel(member.role)}</td>
                      <td>
                        <div className="font-bold">{member.full_name}</div>
                        {!member.is_active ? <Badge className="bg-[#fdeeee] text-[#8a2b25]">غیرفعال</Badge> : null}
                      </td>
                      <td className="ltr font-mono text-[10px]">{member.national_id ?? "ثبت نشده"}</td>
                      <td className="ltr font-mono text-[10px]">{member.mobile ?? "ثبت نشده"}</td>
                      <td className="ltr font-mono text-[10px]">{member.email}</td>
                      <td className="text-center">{member.discipline_code ?? "—"}</td>
                      <td className="text-[10px] text-[#3b4b57]">{spec?.job ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="حلقه‌های دسترسی پروژه"
          subtitle={`${persianDigits(circles.length)} حلقه — هر حلقه می‌تواند چند کاربر مهندسی داشته باشد`}
          actions={
            canManageCircles ? (
              <Button className="btn-sm" onClick={() => setCircleModalOpen(true)}>
                + حلقه جدید
              </Button>
            ) : null
          }
        />
        {circles.length === 0 ? (
          <EmptyState
            title="هنوز حلقه‌ای ساخته نشده است"
            description="برای سناریوی پنج نفر واحد مهندسی، ابتدا یک حلقه بسازید و سپس هر پنج نفر را به آن اضافه کنید."
          />
        ) : (
          <div className="space-y-2 p-2">
            {circles.map((circle) => (
              <div key={circle.id} className="border border-[#a8b6c2] bg-[#f7f9fb]">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#c3ccd6] bg-gradient-to-b from-[#eaf1f7] to-[#d7e2ec] px-2 py-1.5">
                  <div>
                    <span className="font-bold text-[#1f3864]">{circle.name}</span>
                    <span className="mr-2 ltr font-mono text-[10px] text-[#5c6b78]">{circle.code}</span>
                    {circle.description ? <span className="mr-2 text-[9.5px] text-[#5c6b78]">— {circle.description}</span> : null}
                  </div>
                  <div className="flex items-center gap-1 text-[9.5px] text-[#4a5a67]">
                    <Badge className="bg-white text-[#1f3864]">{persianDigits(circle.member_count)} عضو</Badge>
                    <Badge className={circle.external_member_count ? "bg-[#fff4db] text-[#8a5a10]" : "bg-white text-[#4a5a67]"}>
                      {circle.external_member_count ? "دسترسی خارجی فعال" : "فقط داخل حلقه"}
                    </Badge>
                    {canManageCircles ? (
                      <Button className="btn-sm" onClick={() => {
                        setNewMember((current) => ({ ...current, circle_id: circle.id }));
                        setMemberModalOpen(true);
                      }}>
                        افزودن فرد
                      </Button>
                    ) : null}
                  </div>
                </div>
                {circle.members.length === 0 ? (
                  <p className="px-2 py-3 text-center text-[10px] text-[#7a8794]">این حلقه هنوز عضوی ندارد.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="table-base">
                      <thead>
                        <tr>
                          <th>نام و نام خانوادگی</th>
                          <th>نقش پروژه</th>
                          <th style={{ width: 150 }}>کد ملی</th>
                          <th style={{ width: 130 }}>موبایل</th>
                          <th style={{ width: 210 }}>دسترسی خارج از حلقه</th>
                          <th style={{ width: 90 }}>عملیات</th>
                        </tr>
                      </thead>
                      <tbody>
                        {circle.members.map((circleMember) => (
                          <tr key={circleMember.id}>
                            <td>
                              <div className="font-bold">{circleMember.full_name}</div>
                              <div className="ltr text-[9px] text-[#7a8794]">{circleMember.email}</div>
                            </td>
                            <td>{circleMember.roles.length ? circleMember.roles.map(roleLabel).join("، ") : "بدون نقش"}</td>
                            <td className="ltr font-mono text-[10px]">{circleMember.national_id ?? "ثبت نشده"}</td>
                            <td className="ltr font-mono text-[10px]">{circleMember.mobile ?? "ثبت نشده"}</td>
                            <td>
                              <label className="flex cursor-pointer items-center gap-1 text-[10px]">
                                <input
                                  type="checkbox"
                                  checked={circleMember.can_access_external}
                                  disabled={busy || !canManageCircles}
                                  onChange={(event) => void toggleExternal(circle, circleMember.id, event.target.checked)}
                                />
                                <span>{circleMember.can_access_external ? "این فرد به خارج دسترسی دارد" : "فقط همین حلقه"}</span>
                              </label>
                            </td>
                            <td>
                              <Button
                                className="btn-sm"
                                variant="danger"
                                disabled={busy || !canManageCircles}
                                onClick={() => void removeCircleMember(circle, circleMember.id)}
                              >
                                حذف
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="m-1 border border-[#a8a284] bg-[#fffdf3] px-2 py-1 text-[10px] leading-5 text-[#3b4b57]">
        <b>نمونه پیشنهادی واحد مهندسی:</b> یک حلقه با کد <span className="ltr font-mono">ENG</span> بسازید، پنج حساب با
        کد ملی و موبایل معتبر ایجاد کنید، نقش‌های DCC، کنترل پروژه، PMO و کارشناسان مربوط را به آن‌ها بدهید و
        هر پنج نفر را داخل حلقه قرار دهید. فقط برای یک نفر گزینه دسترسی خارجی را فعال کنید؛ سامانه اجازه فعال‌کردن
        نفر دوم را نمی‌دهد و ترانسمیتال‌های حلقه نیز خارج از این مرز نمایش داده نمی‌شوند.
      </div>

      <Modal
        open={userModalOpen}
        title="تعریف کاربر جدید"
        onClose={() => setUserModalOpen(false)}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end gap-1">
            <Button onClick={() => setUserModalOpen(false)}>انصراف</Button>
            <Button variant="primary" form="new-user-form" type="submit" disabled={busy}>ثبت حساب</Button>
          </div>
        }
      >
        <form id="new-user-form" className="space-y-2" onSubmit={createUser}>
          <div className="border border-[#c3ccd6] bg-[#fffdf3] px-2 py-1.5 text-[10px] leading-5 text-[#5b4d1a]">
            کد ملی و شماره موبایل الزامی است و برای جلوگیری از تعریف حساب تکراری کنترل می‌شود. نقش این فرد را
            در مرحله بعد، هنگام افزودن به پروژه، انتخاب کنید.
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="نام و نام خانوادگی *">
              <Input required value={newUser.full_name} onChange={(event) => setNewUser({ ...newUser, full_name: event.target.value })} />
            </Field>
            <Field label="ایمیل / نام کاربری *" hint="برای ورود به سامانه">
              <Input required type="email" dir="ltr" value={newUser.email} onChange={(event) => setNewUser({ ...newUser, email: event.target.value })} />
            </Field>
            <Field label="کد ملی *" hint="۱۰ رقم با رقم کنترل معتبر">
              <Input required dir="ltr" inputMode="numeric" maxLength={10} value={newUser.national_id} onChange={(event) => setNewUser({ ...newUser, national_id: event.target.value })} />
            </Field>
            <Field label="شماره موبایل *" hint="نمونه: 09121234567">
              <Input required dir="ltr" inputMode="tel" maxLength={14} value={newUser.mobile} onChange={(event) => setNewUser({ ...newUser, mobile: event.target.value })} />
            </Field>
            <Field label="گذرواژه *" hint="حداقل ۸ نویسه">
              <Input required type="password" dir="ltr" minLength={8} value={newUser.password} onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} />
            </Field>
            {user?.is_superuser ? (
              <label className="flex items-center gap-1 self-end pb-1 text-[10.5px] text-[#3b4b57]">
                <input type="checkbox" checked={newUser.is_superuser} onChange={(event) => setNewUser({ ...newUser, is_superuser: event.target.checked })} />
                مدیر سیستم
              </label>
            ) : null}
          </div>
        </form>
      </Modal>

      <Modal
        open={circleModalOpen}
        title="ایجاد حلقه دسترسی پروژه"
        onClose={() => setCircleModalOpen(false)}
        footer={
          <div className="flex justify-end gap-1">
            <Button onClick={() => setCircleModalOpen(false)}>انصراف</Button>
            <Button variant="primary" form="new-circle-form" type="submit" disabled={busy}>ایجاد حلقه</Button>
          </div>
        }
      >
        <form id="new-circle-form" className="space-y-2" onSubmit={createCircle}>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="کد حلقه *" hint="نمونه: ENG">
              <Input required dir="ltr" maxLength={64} value={newCircle.code} onChange={(event) => setNewCircle({ ...newCircle, code: event.target.value })} />
            </Field>
            <Field label="نام حلقه *">
              <Input required value={newCircle.name} onChange={(event) => setNewCircle({ ...newCircle, name: event.target.value })} placeholder="واحد مهندسی" />
            </Field>
          </div>
          <Field label="شرح حلقه">
            <textarea className="input min-h-16 resize-y" value={newCircle.description} onChange={(event) => setNewCircle({ ...newCircle, description: event.target.value })} placeholder="مثلاً پنج نفر پرسنل واحد مهندسی" maxLength={2000} />
          </Field>
        </form>
      </Modal>

      <Modal
        open={memberModalOpen}
        title="افزودن فرد به پروژه و حلقه"
        onClose={() => setMemberModalOpen(false)}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end gap-1">
            <Button onClick={() => setMemberModalOpen(false)}>انصراف</Button>
            <Button variant="primary" form="new-member-form" type="submit" disabled={busy || !usersForPicker.length}>ثبت عضویت</Button>
          </div>
        }
      >
        <form id="new-member-form" className="space-y-2" onSubmit={addMember}>
          {!usersForPicker.length ? (
            <div className="border border-[#c98a86] bg-[#fdeeee] px-2 py-1.5 text-[10.5px] leading-5 text-[#8a2b25]">
              ابتدا از دکمه «تعریف کاربر جدید» حساب بسازید یا از مدیر سیستم بخواهید حساب‌های افراد را ثبت کند.
            </div>
          ) : null}
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="کاربر *">
              <Select required value={newMember.user_id} onChange={(event) => setNewMember({ ...newMember, user_id: event.target.value })}>
                <option value="">انتخاب شخص…</option>
                {usersForPicker.map((item) => (
                  <option key={item.id} value={item.id}>{item.full_name} — {item.email}</option>
                ))}
              </Select>
            </Field>
            <Field label="نقش در پروژه *">
              <Select value={newMember.role} onChange={(event) => setNewMember({ ...newMember, role: event.target.value as Role })}>
                {ROLE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </Select>
            </Field>
            <Field label="دیسیپلین">
              <Select value={newMember.discipline_id} onChange={(event) => setNewMember({ ...newMember, discipline_id: event.target.value })}>
                <option value="">بدون دیسیپلین</option>
                {disciplines.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
              </Select>
            </Field>
            <Field label="حلقه دسترسی *">
              <Select required value={newMember.circle_id} onChange={(event) => setNewMember({ ...newMember, circle_id: event.target.value })}>
                <option value="">انتخاب حلقه…</option>
                {circles.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
              </Select>
            </Field>
          </div>
          <label className="flex items-start gap-1 border border-[#d8c58c] bg-[#fffdf3] px-2 py-1.5 text-[10.5px] leading-5 text-[#5b4d1a]">
            <input type="checkbox" checked={newMember.can_access_external} onChange={(event) => setNewMember({ ...newMember, can_access_external: event.target.checked })} />
            <span><b>اجازه دسترسی خارج از این حلقه</b> — فقط یک نفر در هر حلقه می‌تواند این گزینه را داشته باشد.</span>
          </label>
          {selectedCircle ? <p className="text-[9.5px] text-[#5c6b78]">حلقه انتخاب‌شده: {selectedCircle.name} · {persianDigits(selectedCircle.member_count)} عضو فعلی</p> : null}
        </form>
      </Modal>
    </div>
  );
}
