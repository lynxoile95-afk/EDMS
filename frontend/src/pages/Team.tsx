import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/project";
import type { ProjectMember } from "@/lib/types";
import { Card, CardHeader, EmptyState, ErrorBox, Spinner } from "@/components/ui";
import { persianDigits } from "@/lib/format";

/** The roles the review route needs, in route order. */
const ROUTE_ROLES = [
  { role: "submitter", step: "۱", label: "پیمانکار", job: "برگه ارسال را می‌سازد و برای کنترل اسناد می‌فرستد" },
  { role: "dcc", step: "۲", label: "کارشناس کنترل اسناد مهندسی", job: "محتوا را کنترل می‌کند و به مدیر می‌فرستد" },
  { role: "engineering_manager", step: "۳", label: "مدیر مهندسی", job: "برگه را به دیسیپلین مربوطه ارجاع می‌دهد" },
  { role: "project_manager", step: "۳", label: "مدیر پروژه", job: "در مرحله مدیریت هم‌کار می‌کند و ارجاع می‌دهد" },
  { role: "reviewer", step: "۴", label: "کارشناس دیسیپلین", job: "برگه ارجاع‌شده به دیسیپلین خودش را بررسی می‌کند" },
];

const ROLE_LABELS: Record<string, string> = {
  submitter: "پیمانکار",
  dcc: "کنترل اسناد",
  engineering_manager: "مدیر مهندسی",
  project_manager: "مدیر پروژه",
  reviewer: "کارشناس دیسیپلین",
  system_admin: "مدیر سیستم",
};

export function Team() {
  const { project } = useProject();
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    setLoading(true);
    api
      .get<ProjectMember[]>(`/projects/${project.id}/members`)
      .then(setMembers)
      .catch((err) => setError(err instanceof ApiError ? err.detail : "بارگذاری اعضا ناموفق بود."))
      .finally(() => setLoading(false));
  }, [project]);

  if (!project) return <EmptyState title="پروژه‌ای انتخاب نشده است" />;

  return (
    <div className="space-y-4">
      <Card className="is-main">
        <CardHeader
          title="کاربران و نقش‌های مرحلهٔ گردش"
          subtitle={`${persianDigits(members.length)} عضویت در این پروژه — هر نقش یک حساب جدا`}
        />
        {loading ? (
          <Spinner />
        ) : error ? (
          <div className="p-4">
            <ErrorBox message={error} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th style={{ width: 46 }}>مرحله</th>
                  <th style={{ width: 190 }}>نقش</th>
                  <th>کاربر</th>
                  <th style={{ width: 220 }} className="ltr">
                    ورود
                  </th>
                  <th style={{ width: 90 }}>دیسیپلین</th>
                  <th>مسئولیت در گردش</th>
                </tr>
              </thead>
              <tbody>
                {members.map((member) => {
                  const spec = ROUTE_ROLES.find((item) => item.role === member.role);
                  return (
                    <tr key={member.id}>
                      <td className="cell-center">{spec?.step ?? "—"}</td>
                      <td>{ROLE_LABELS[member.role] ?? member.role}</td>
                      <td>{member.full_name}</td>
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
        <div className="m-1 border border-[#a8a284] bg-[#fffdf3] px-2 py-1 text-[10px] leading-5 text-[#3b4b57]">
          چهار کارشناس دیسیپلین (برق، سیویل، معماری، تاسیسات) هر کدام یک حساب جدا دارند. برگه‌ای که
          مدیر مهندسی به یک دیسیپلین ارجاع می‌دهد، فقط در کارتابل همان کارشناس دیده می‌شود؛ کارشناسان
          بقیهٔ دیسیپلین‌ها آن را نمی‌بینند.
        </div>
      </Card>
    </div>
  );
}
