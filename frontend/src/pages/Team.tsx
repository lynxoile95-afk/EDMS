import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { AccessCircle, ProjectMember, Role, SystemUser, Lookup } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorBox, Field, Input, Modal, Spinner } from "@/components/ui";
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
  { value: "project_control", label: "کارشناس کنترل پروژه" },
  { value: "pmo", label: "واحد PMO" },
  { value: "engineering_manager", label: "مدیر مهندسی" },
  { value: "project_manager", label: "مدیر پروژه" },
  { value: "reviewer", label: "کارشناس دیسیپلین" },
  { value: "system_admin", label: "مدیر سیستم" },
];

const ROLE_ALIASES: Record<string, Role> = {
  "کارشناس کنترل پروژه": "project_control",
  "کنترل پروژه": "project_control",
  "کارشناس کنترل مدارک": "dcc",
  "کارشناس کنترل اسناد": "dcc",
  "کنترل مدارک": "dcc",
  "کارشناس دیسیپلین": "reviewer",
  "کارشناس رشته": "reviewer",
  "واحد pmo": "pmo",
  "کارشناس pmo": "pmo",
  "مدیر پروژه": "project_manager",
  "مدیر مهندسی": "engineering_manager",
};

interface NewUserForm {
  full_name: string;
  email: string;
  national_id: string;
  mobile: string;
  password: string;
  is_superuser: boolean;
}

interface EditUserForm {
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
  user_text: string;
  role: Role;
  role_text: string;
  discipline_id: string;
  discipline_text: string;
  circle_id: string;
  circle_text: string;
  can_access_external: boolean;
}

interface EditMembershipForm {
  role_text: string;
  discipline_text: string;
}

const EMPTY_USER: NewUserForm = {
  full_name: "",
  email: "",
  national_id: "",
  mobile: "",
  password: "",
  is_superuser: false,
};

const EMPTY_EDIT_USER: EditUserForm = {
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
  user_text: "",
  role: "reviewer",
  role_text: "reviewer",
  discipline_id: "",
  discipline_text: "",
  circle_id: "",
  circle_text: "",
  can_access_external: false,
};

const EMPTY_MEMBERSHIP_EDIT: EditMembershipForm = {
  role_text: "",
  discipline_text: "",
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
  const [editUserModalOpen, setEditUserModalOpen] = useState(false);
  const [circleModalOpen, setCircleModalOpen] = useState(false);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [membershipEditModalOpen, setMembershipEditModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<SystemUser | null>(null);
  const [editingMembership, setEditingMembership] = useState<ProjectMember | null>(null);
  const [newUser, setNewUser] = useState<NewUserForm>(EMPTY_USER);
  const [editUser, setEditUser] = useState<EditUserForm>(EMPTY_EDIT_USER);
  const [membershipEdit, setMembershipEdit] = useState<EditMembershipForm>(EMPTY_MEMBERSHIP_EDIT);
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
  const userForValue = (value: string) => {
    const normalized = value.trim().toLowerCase();
    return usersForPicker.find(
      (item) => item.id === value || item.email.toLowerCase() === normalized || item.full_name.trim().toLowerCase() === normalized,
    );
  };
  const roleForValue = (value: string): Role | null => {
    const normalized = value.trim().toLowerCase().replace(/\u200c/g, " ").replace(/\s+/g, " ");
    return ROLE_ALIASES[normalized] ?? ROLE_OPTIONS.find(
      (item) => item.value === normalized || item.label.toLowerCase() === normalized,
    )?.value ?? null;
  };
  const disciplineForValue = (value: string) => {
    const normalized = value.trim().toLowerCase();
    return disciplines.find(
      (item) => item.id === value || item.code.toLowerCase() === normalized || item.name.trim().toLowerCase() === normalized,
    );
  };
  const circleForValue = (value: string) => {
    const normalized = value.trim().toLowerCase();
    return circles.find(
      (item) => item.id === value || item.code.toLowerCase() === normalized || item.name.trim().toLowerCase() === normalized,
    );
  };
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
      setNewMember((current) => ({
        ...current,
        user_id: created.id,
        user_text: created.email,
      }));
      setNewUser(EMPTY_USER);
      setUserModalOpen(false);
      setNotice(`حساب «${created.full_name}» ساخته شد. می‌توانید ابتدا سایر حساب‌ها را ثبت کنید و بعداً نقش پروژه و حلقه دسترسی را تعیین کنید.`);
    } catch (err) {
      setError(messageOf(err, "ایجاد حساب کاربر ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const openEditUser = (account: SystemUser) => {
    setEditingUser(account);
    setEditUser({
      full_name: account.full_name,
      email: account.email,
      national_id: account.national_id ?? "",
      mobile: account.mobile ?? "",
      password: "",
      is_superuser: account.is_superuser,
    });
    setError(null);
    setEditUserModalOpen(true);
  };

  const updateUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingUser) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const payload: Record<string, unknown> = {
        full_name: editUser.full_name.trim(),
        email: editUser.email.trim(),
        national_id: editUser.national_id.trim(),
        mobile: editUser.mobile.trim(),
        is_superuser: editUser.is_superuser,
      };
      if (editUser.password.trim()) payload.password = editUser.password;
      await api.patch<SystemUser>(`/admin/users/${editingUser.id}`, payload);
      setEditUserModalOpen(false);
      setEditingUser(null);
      setEditUser(EMPTY_EDIT_USER);
      await load();
      setNotice(`اطلاعات حساب «${editUser.full_name.trim()}» به‌روزرسانی شد.`);
    } catch (err) {
      setError(messageOf(err, "ویرایش اطلاعات کاربر ناموفق بود."));
    } finally {
      setBusy(false);
    }
  };

  const openEditMembership = (member: ProjectMember) => {
    setEditingMembership(member);
    setMembershipEdit({
      role_text: member.role,
      discipline_text: member.discipline_name ?? member.discipline_code ?? "",
    });
    setError(null);
    setMembershipEditModalOpen(true);
  };

  const updateMembership = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!project || !editingMembership) return;
    const role = roleForValue(membershipEdit.role_text);
    if (!role) {
      setError("نقش جدید را به‌صورت متنی وارد کنید؛ نمونه: project_control یا کارشناس کنترل پروژه.");
      return;
    }
    const disciplineText = membershipEdit.discipline_text;
    const discipline = disciplineText.trim() ? disciplineForValue(disciplineText) : undefined;
    const disciplineName = (discipline?.name ?? disciplineText.trim()) || null;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.patch<ProjectMember>(`/projects/${project.id}/members/${editingMembership.id}`, {
        role,
        discipline_id: discipline?.id ?? null,
        discipline_name: disciplineName,
      });
      setMembershipEditModalOpen(false);
      setEditingMembership(null);
      setMembershipEdit(EMPTY_MEMBERSHIP_EDIT);
      await load();
      setNotice(`نقش «${editingMembership.full_name}» به «${roleLabel(role)}» تغییر کرد.`);
    } catch (err) {
      setError(messageOf(err, "ویرایش نقش پروژه ناموفق بود."));
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
    if (!project) return;

    const account = userForValue(newMember.user_text) ?? userForValue(newMember.user_id);
    if (!account) {
      setError("کاربر را با تایپ نام یا ایمیل معتبر وارد کنید.");
      return;
    }
    const role = roleForValue(newMember.role_text) ?? (newMember.role_text.trim() ? null : newMember.role);
    if (!role) {
      setError("نقش پروژه را به‌صورت متنی وارد کنید؛ نمونه: reviewer یا کارشناس کنترل پروژه.");
      return;
    }
    const disciplineText = newMember.discipline_text;
    const discipline = disciplineText.trim() ? disciplineForValue(disciplineText) : undefined;
    const disciplineName = (discipline?.name ?? disciplineText.trim()) || null;
    const circle = newMember.circle_text.trim() ? circleForValue(newMember.circle_text) : undefined;
    if (newMember.circle_text.trim() && !circle) {
      setError("حلقه واردشده در پروژه یافت نشد؛ کد یا نام حلقه را تایپ کنید.");
      return;
    }
    const normalizedDisciplineName = disciplineName?.trim().toLowerCase() || null;
    const alreadyInProject = members.some((item) => {
      if (item.user_id !== account.id || item.role !== role) return false;
      if (discipline) return item.discipline_id === discipline.id;
      if (normalizedDisciplineName) {
        return item.discipline_name?.trim().toLowerCase() === normalizedDisciplineName;
      }
      return item.discipline_id === null;
    });

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (!alreadyInProject) {
        await api.post<ProjectMember>(`/projects/${project.id}/members`, {
          user_id: account.id,
          role,
          discipline_id: discipline?.id ?? null,
          discipline_name: disciplineName,
        });
      }
      if (circle) {
        await api.post<AccessCircle>(`/projects/${project.id}/circles/${circle.id}/members`, {
          user_id: account.id,
          can_access_external: newMember.can_access_external,
        });
      }
      setMemberModalOpen(false);
      setNewMember({ ...EMPTY_MEMBER, circle_id: circle?.id ?? "", circle_text: circle?.code ?? "" });
      await load();
      setNotice(
        circle
          ? "فرد با نقش پروژه و عضویت حلقه ثبت شد."
          : "فرد با نقش پروژه ثبت شد؛ عضویت حلقه را می‌توانید بعداً انجام دهید.",
      );
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
                  <Button className="btn-sm" onClick={() => setMemberModalOpen(true)}>
                    افزودن فرد به پروژه / حلقه
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
                  {user?.is_superuser ? <th style={{ width: 105 }}>ویرایش حساب</th> : null}
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
                      <td className="text-center">
                        {member.discipline_name ? <div>{member.discipline_name}</div> : <span>{member.discipline_code ?? "—"}</span>}
                        {member.discipline_name && member.discipline_code ? (
                          <div className="ltr font-mono text-[9px] text-[#71808d]">{member.discipline_code}</div>
                        ) : null}
                      </td>
                      <td className="text-[10px] text-[#3b4b57]">{spec?.job ?? "—"}</td>
                      {user?.is_superuser ? (
                        <td className="text-center">
                          <Button className="btn-sm" onClick={() => openEditMembership(member)}>
                            ویرایش نقش
                          </Button>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {user?.is_superuser ? (
        <Card>
          <CardHeader
            title="حساب‌های کاربران سامانه"
            subtitle="تمام اطلاعات حساب، شامل نام، ایمیل، کد ملی، موبایل و رمز عبور قابل ویرایش است. رمز عبور خالی یعنی بدون تغییر."
            actions={
              <Button className="btn-sm" variant="primary" onClick={() => setUserModalOpen(true)}>
                + تعریف حساب جدید
              </Button>
            }
          />
          {users.length === 0 ? (
            <EmptyState title="هنوز حسابی ثبت نشده است" description="از دکمه تعریف حساب جدید استفاده کنید." />
          ) : (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>نام و نام خانوادگی</th>
                    <th className="ltr">ایمیل / نام کاربری</th>
                    <th style={{ width: 150 }}>کد ملی</th>
                    <th style={{ width: 135 }}>موبایل</th>
                    <th style={{ width: 90 }}>وضعیت</th>
                    <th style={{ width: 105 }}>عملیات</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((account) => (
                    <tr key={account.id}>
                      <td className="font-bold">{account.full_name}</td>
                      <td className="ltr text-[10px]">{account.email}</td>
                      <td className="ltr font-mono text-[10px]">{account.national_id ?? "ثبت نشده"}</td>
                      <td className="ltr font-mono text-[10px]">{account.mobile ?? "ثبت نشده"}</td>
                      <td className="text-center">
                        <Badge className={account.is_active ? "bg-[#e8f5e9] text-[#1f6b34]" : "bg-[#fdeeee] text-[#8a2b25]"}>
                          {account.is_active ? "فعال" : "غیرفعال"}
                        </Badge>
                      </td>
                      <td className="text-center">
                        <Button className="btn-sm" onClick={() => openEditUser(account)}>
                          ویرایش اطلاعات
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}

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
                        setNewMember((current) => ({ ...current, circle_id: circle.id, circle_text: circle.code }));
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
        open={membershipEditModalOpen}
        title={`ویرایش نقش پروژه${editingMembership ? ` — ${editingMembership.full_name}` : ""}`}
        onClose={() => {
          setMembershipEditModalOpen(false);
          setEditingMembership(null);
        }}
        width="max-w-xl"
        footer={
          <div className="flex justify-end gap-1">
            <Button
              onClick={() => {
                setMembershipEditModalOpen(false);
                setEditingMembership(null);
              }}
            >
              انصراف
            </Button>
            <Button variant="primary" form="edit-membership-form" type="submit" disabled={busy}>
              ذخیره نقش
            </Button>
          </div>
        }
      >
        <form id="edit-membership-form" className="space-y-2" onSubmit={updateMembership}>
          <div className="border border-[#c3ccd6] bg-[#fffdf3] px-2 py-1.5 text-[10px] leading-5 text-[#5b4d1a]">
            نقش و دیسیپلین را مستقیم تایپ کنید؛ پیشنهادهای زیر فقط برای سرعت هستند و انتخاب از فهرست الزامی نیست.
          </div>
          <Field label="نقش جدید در پروژه *">
            <Input
              list="membership-role-options"
              value={membershipEdit.role_text}
              placeholder="project_control یا کنترل پروژه"
              onChange={(event) => setMembershipEdit({ ...membershipEdit, role_text: event.target.value })}
            />
          </Field>
          <Field label="دیسیپلین (اختیاری)" hint="نام جدید هم قابل ثبت است؛ خالی بگذارید تا دیسیپلین حذف شود.">
            <Input
              list="membership-discipline-options"
              value={membershipEdit.discipline_text}
              placeholder="کد یا نام دیسیپلین جدید"
              onChange={(event) => setMembershipEdit({ ...membershipEdit, discipline_text: event.target.value })}
            />
          </Field>
          <datalist id="membership-role-options">
            {ROLE_OPTIONS.map((item) => <option key={item.value} value={item.value} label={item.label} />)}
          </datalist>
          <datalist id="membership-discipline-options">
            {disciplines.map((item) => <option key={item.id} value={item.code} label={item.name} />)}
          </datalist>
        </form>
      </Modal>

      <Modal
        open={editUserModalOpen}
        title={`ویرایش اطلاعات کاربر${editingUser ? ` — ${editingUser.full_name}` : ""}`}
        onClose={() => {
          setEditUserModalOpen(false);
          setEditingUser(null);
        }}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end gap-1">
            <Button
              onClick={() => {
                setEditUserModalOpen(false);
                setEditingUser(null);
              }}
            >
              انصراف
            </Button>
            <Button variant="primary" form="edit-user-form" type="submit" disabled={busy}>
              ذخیره اطلاعات
            </Button>
          </div>
        }
      >
        <form id="edit-user-form" className="space-y-2" onSubmit={updateUser}>
          <div className="border border-[#c3ccd6] bg-[#fffdf3] px-2 py-1.5 text-[10px] leading-5 text-[#5b4d1a]">
            همه موارد این فرم قابل تایپ و ویرایش هستند. برای تغییر رمز عبور مقدار جدید را وارد کنید؛ اگر خالی بماند، رمز فعلی حفظ می‌شود.
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="نام و نام خانوادگی *">
              <Input required value={editUser.full_name} onChange={(event) => setEditUser({ ...editUser, full_name: event.target.value })} />
            </Field>
            <Field label="ایمیل / نام کاربری *">
              <Input required type="email" dir="ltr" value={editUser.email} onChange={(event) => setEditUser({ ...editUser, email: event.target.value })} />
            </Field>
            <Field label="کد ملی *" hint="۱۰ رقم با رقم کنترل معتبر">
              <Input required dir="ltr" inputMode="numeric" maxLength={10} value={editUser.national_id} onChange={(event) => setEditUser({ ...editUser, national_id: event.target.value })} />
            </Field>
            <Field label="شماره موبایل *" hint="نمونه: 09121234567">
              <Input required dir="ltr" inputMode="tel" maxLength={14} value={editUser.mobile} onChange={(event) => setEditUser({ ...editUser, mobile: event.target.value })} />
            </Field>
            <Field label="رمز عبور جدید" hint="اختیاری — حداقل ۸ نویسه">
              <Input type="password" dir="ltr" minLength={8} value={editUser.password} onChange={(event) => setEditUser({ ...editUser, password: event.target.value })} />
            </Field>
            <label className="flex items-center gap-1 self-end pb-1 text-[10.5px] text-[#3b4b57]">
              <input type="checkbox" checked={editUser.is_superuser} onChange={(event) => setEditUser({ ...editUser, is_superuser: event.target.checked })} />
              مدیر سیستم
            </label>
          </div>
        </form>
      </Modal>

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
        title="افزودن فرد به پروژه / حلقه"
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
            <Field label="کاربر (قابل تایپ) *" hint="نام یا ایمیل را تایپ کنید؛ پیشنهادها فقط برای کمک هستند.">
              <Input
                list="team-user-options"
                value={newMember.user_text}
                placeholder="نام یا email کاربر"
                onChange={(event) => {
                  const value = event.target.value;
                  const account = userForValue(value);
                  setNewMember({ ...newMember, user_text: value, user_id: account?.id ?? "" });
                }}
              />
            </Field>
            <Field label="نقش در پروژه (قابل تایپ) *" hint="کلید انگلیسی یا عنوان فارسی نقش را وارد کنید.">
              <Input
                list="team-role-options"
                value={newMember.role_text}
                placeholder="reviewer / dcc / PMO"
                onChange={(event) => {
                  const value = event.target.value;
                  setNewMember({ ...newMember, role_text: value, role: roleForValue(value) ?? newMember.role });
                }}
              />
            </Field>
            <Field label="دیسیپلین (اختیاری)" hint="نام جدید هم ثبت می‌شود؛ پیشنهادها فقط برای کمک هستند.">
              <Input
                list="team-discipline-options"
                value={newMember.discipline_text}
                placeholder="مثلاً CIV، سیویل یا نام جدید"
                onChange={(event) => {
                  const value = event.target.value;
                  const discipline = disciplineForValue(value);
                  setNewMember({ ...newMember, discipline_text: value, discipline_id: discipline?.id ?? "" });
                }}
              />
            </Field>
            <Field label="حلقه دسترسی (اختیاری)" hint="کد یا نام حلقه را تایپ کنید؛ در صورت خالی‌بودن فقط نقش پروژه ثبت می‌شود.">
              <Input
                list="team-circle-options"
                value={newMember.circle_text}
                placeholder="ENG یا واحد مهندسی"
                onChange={(event) => {
                  const value = event.target.value;
                  const circle = circleForValue(value);
                  setNewMember({
                    ...newMember,
                    circle_text: value,
                    circle_id: circle?.id ?? "",
                    can_access_external: value.trim() ? newMember.can_access_external : false,
                  });
                }}
              />
            </Field>
          </div>
          <datalist id="team-user-options">
            {usersForPicker.map((item) => <option key={item.id} value={item.email} label={item.full_name} />)}
          </datalist>
          <datalist id="team-role-options">
            {ROLE_OPTIONS.map((item) => <option key={item.value} value={item.value} label={item.label} />)}
          </datalist>
          <datalist id="team-discipline-options">
            {disciplines.map((item) => <option key={item.id} value={item.code} label={item.name} />)}
          </datalist>
          <datalist id="team-circle-options">
            {circles.map((item) => <option key={item.id} value={item.code} label={item.name} />)}
          </datalist>
          <label className="flex items-start gap-1 border border-[#d8c58c] bg-[#fffdf3] px-2 py-1.5 text-[10.5px] leading-5 text-[#5b4d1a]">
            <input
              type="checkbox"
              checked={newMember.can_access_external}
              disabled={!newMember.circle_id}
              onChange={(event) => setNewMember({ ...newMember, can_access_external: event.target.checked })}
            />
            <span><b>اجازه دسترسی خارج از حلقه</b> — پس از انتخاب حلقه فعال می‌شود؛ فقط یک نفر در هر حلقه می‌تواند این گزینه را داشته باشد.</span>
          </label>
          {selectedCircle ? <p className="text-[9.5px] text-[#5c6b78]">حلقه انتخاب‌شده: {selectedCircle.name} · {persianDigits(selectedCircle.member_count)} عضو فعلی</p> : <p className="text-[9.5px] text-[#5c6b78]">بدون انتخاب حلقه، فقط نقش پروژه ثبت می‌شود و عضویت حلقه برای مرحله بعد باقی می‌ماند.</p>}
        </form>
      </Modal>
    </div>
  );
}
