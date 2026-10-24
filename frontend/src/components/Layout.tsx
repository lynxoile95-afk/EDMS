import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { Notification } from "@/lib/types";
import { persianDigits } from "@/lib/format";

const TOP_LINKS = [
  { to: "/dashboard", label: "خانه", icon: "⌂" },
  { to: "/transmittals", label: "مدارک مهندسی", icon: "▤" },
  { to: "/mdl", label: "MDL", icon: "☰" },
  { to: "/review", label: "وظایف من", icon: "⚑" },
  { to: "/upload", label: "بارگذاری", icon: "⇧" },
];

const MENU: { title: string; items: { to: string; label: string; icon: string; badge?: "queue" | "unread" }[] }[] = [
  {
    title: "Engineering Documents",
    items: [
      { to: "/dashboard", label: "کارتابل من", icon: "▣", badge: "unread" },
      { to: "/transmittals", label: "برگه‌های ارسال", icon: "✉" },
      { to: "/transmittals?mine=1", label: "کارتابل ارجاع من", icon: "⇄" },
      { to: "/mdl", label: "MDL — فهرست مدارک", icon: "☰" },
      { to: "/upload", label: "ثبت و بارگذاری مدرک", icon: "⇧" },
      { to: "/review", label: "صف بازبینی (DCC)", icon: "⚑", badge: "queue" },
    ],
  },
  {
    title: "Documents Center",
    items: [
      { to: "/mdl", label: "فهرست مدارک (MDL)", icon: "☰" },
      { to: "/transmittals", label: "پیگیری ارسال‌ها", icon: "◎" },
    ],
  },
];

const TITLES: Record<string, string> = {
  "/dashboard": "کارتابل من",
  "/mdl": "فهرست مدارک پروژه (MDL)",
  "/upload": "ثبت و بارگذاری مدرک",
  "/review": "صف بازبینی کنترل مدارک",
  "/transmittals": "برگه‌های ارسال",
  "/login": "ورود به سامانه",
};

export function Layout() {
  const { user, logout } = useAuth();
  const { projects, project, select } = useProject();
  const navigate = useNavigate();
  const location = useLocation();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [queueCount, setQueueCount] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);

  useEffect(() => {
    let active = true;
    const load = () => {
      api
        .get<{ items: Notification[]; unread: number }>("/notifications?limit=15")
        .then((data) => {
          if (!active) return;
          setNotifications(data.items);
          setUnread(data.unread);
        })
        .catch(() => undefined);
      if (project) {
        api
          .get<{ total: number }>(`/projects/${project.id}/review-queue?review_status=pending&size=1`)
          .then((data) => active && setQueueCount(data.total))
          .catch(() => undefined);
      }
    };
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [location.pathname, project]);

  const breadcrumb = useMemo(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    const label = TITLES[`/${segments[0] ?? ""}`] ?? "";
    return ["مرکز اسناد مهندسی", label, segments.length > 1 ? "جزئیات" : ""].filter(Boolean);
  }, [location.pathname]);

  const markAllRead = async () => {
    await api.post("/notifications/read-all");
    setUnread(0);
    setNotifications((items) => items.map((item) => ({ ...item, read_at: item.read_at ?? new Date().toISOString() })));
  };

  return (
    <div className="app-frame">
      <div className="flag-band" />

      {/* ---------- masthead ---------- */}
      <header className="masthead">
        <div className="flex min-w-0 items-center gap-3">
          <div className="masthead-logo">EDMS</div>
          <div className="min-w-0">
            <h1 className="masthead-title">سامانه مدیریت اسناد مهندسی</h1>
            <p className="masthead-sub">Engineering Document Management System — EDMS-AI</p>
          </div>
        </div>
        <div className="hidden items-center gap-2 md:flex">
          <input
            className="input w-52"
            placeholder="جستجو در سایت…"
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                const value = (event.target as HTMLInputElement).value.trim();
                navigate(`/mdl${value ? `?q=${encodeURIComponent(value)}` : ""}`);
              }
            }}
          />
          <span className="text-[10px] text-[#5c6b78]">پروژه:</span>
          <select
            className="input w-40"
            value={project?.id ?? ""}
            onChange={(event) => select(event.target.value)}
          >
            {projects.length === 0 ? <option value="">—</option> : null}
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.code} — {item.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="top-band" />

      {/* ---------- main navigation ---------- */}
      <nav className="topnav">
        <div className="topnav-user">
          <span className="status-dot bg-[#7fd18c]" />
          <span className="font-bold">{user?.full_name}</span>
          <span className="text-[9.5px] text-[#243842]">
            ({project?.member_role === "dcc" ? "کنترل مدارک" : user?.is_superuser ? "مدیر سیستم" : "کاربر"})
          </span>
          <button
            className="btn btn-sm"
            onClick={() => {
              logout();
              navigate("/login");
            }}
          >
            خروج
          </button>
        </div>
        <div className="topnav-links">
          {TOP_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) => clsx("topnav-link", isActive && "is-active")}
            >
              <span className="ml-1 text-[11px]">{link.icon}</span>
              {link.label}
            </NavLink>
          ))}
          <button className="topnav-link" onClick={() => setPanelOpen((open) => !open)}>
            <span className="ml-1">✉</span>
            اعلان‌ها
            {unread > 0 ? (
              <span className="mr-1 inline-block min-w-[15px] bg-[#b3403a] px-1 text-center text-[9px] text-white">
                {persianDigits(unread)}
              </span>
            ) : null}
          </button>
        </div>
      </nav>

      <div className="breadcrumb">
        {breadcrumb.map((part, index) => (
          <span key={`${part}-${index}`} className="flex items-center gap-1.5">
            {index > 0 ? <span className="text-[#9fb3bf]">‹</span> : null}
            <span className={index === breadcrumb.length - 1 ? "font-bold text-white" : ""}>{part}</span>
          </span>
        ))}
        <span className="flex-1" />
        {project ? <span className="text-[#cfe0e8]">{project.name}</span> : null}
      </div>

      {panelOpen ? (
        <div className="fixed inset-0 z-40" onClick={() => setPanelOpen(false)}>
          <div
            className="absolute left-2 top-[92px] w-80 border border-[#4a5a67] bg-white shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="card-header">
              <h2>کارتابل اعلان‌ها</h2>
              <button className="btn btn-sm" onClick={markAllRead}>
                خواندن همه
              </button>
            </div>
            <div className="max-h-80 divide-y divide-[#dbe2e9] overflow-y-auto">
              {notifications.length === 0 ? (
                <p className="px-3 py-6 text-center text-[10.5px] text-[#7a8794]">اعلانی وجود ندارد.</p>
              ) : (
                notifications.map((item) => (
                  <button
                    key={item.id}
                    className={clsx("block w-full px-2 py-1.5 text-right hover:bg-[#eef4fa]", !item.read_at && "bg-[#f4f9ff]")}
                    onClick={async () => {
                      if (!item.read_at) await api.post(`/notifications/${item.id}/read`);
                      setPanelOpen(false);
                      if (item.entity_type === "transmittal" && item.entity_id) {
                        navigate(`/transmittals/${item.entity_id}`);
                      } else if (item.entity_type === "extraction_result" && item.entity_id) {
                        navigate(`/review/${item.entity_id}`);
                      }
                    }}
                  >
                    <p className="text-[10.5px] font-bold text-[#1f3864]">{item.subject}</p>
                    <p className="line-clamp-2 text-[10px] leading-4 text-[#4a5a67]">{item.body}</p>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* ---------- body: content + right-hand menu ---------- */}
      <div className="shell">
        <aside className="menu-rail">
          {MENU.map((group) => (
            <div key={group.title}>
              <p className="menu-group-title">{group.title}</p>
              {group.items.map((item) => (
                <NavLink
                  key={`${group.title}-${item.label}`}
                  to={item.to}
                  className={({ isActive }) =>
                    clsx("menu-item", isActive && location.pathname === item.to && "is-active")
                  }
                >
                  <span className="flex items-center gap-1.5 text-right">
                    <span className="text-[11px]">{item.icon}</span>
                    {item.label}
                  </span>
                  {item.badge === "unread" && unread > 0 ? (
                    <span className="menu-item-count">{persianDigits(unread)}</span>
                  ) : null}
                  {item.badge === "queue" && queueCount > 0 ? (
                    <span className="menu-item-count">{persianDigits(queueCount)}</span>
                  ) : null}
                </NavLink>
              ))}
            </div>
          ))}
          <p className="menu-group-title">System</p>
          <Link className="menu-item" to="/review">
            <span>پردازش هوشمند</span>
            <span className="text-[10px] text-[#1f6b34]">فعال</span>
          </Link>
          <button className="menu-item" onClick={() => setPanelOpen(true)}>
            <span>اعلان‌های من</span>
            <span className="text-[10px] text-[#1f3864]">{persianDigits(unread)}</span>
          </button>
        </aside>

        <main className="shell-main">
          {/* The reference stamps every inner page with this label, top-right. */}
          <div className="mb-[3px] flex items-center justify-end gap-1 text-[9.5px] text-[#5c6b78]">
            <span className="border border-[#c3ccd6] bg-[#f7f9fb] px-1.5 py-[1px]">EDMS Result Page</span>
          </div>
          {project ? (
            <Outlet />
          ) : (
            <div className="card p-6 text-center text-[11px] text-[#4a5a67]">
              هنوز به هیچ پروژه‌ای دسترسی ندارید. با مدیر سیستم تماس بگیرید.
            </div>
          )}
        </main>
      </div>

      <footer className="border-t border-[#b9c3cd] bg-gradient-to-b from-[#f4f7fa] to-[#e2e8ee] px-3 py-1.5 text-center text-[9.5px] text-[#5c6b78]">
        سامانه مدیریت اسناد مهندسی — نسخه ۱.۰ · تاریخ شمسی · Asia/Tehran
      </footer>
    </div>
  );
}
