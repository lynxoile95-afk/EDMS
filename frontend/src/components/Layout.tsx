import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProject } from "@/lib/project";
import type { Notification } from "@/lib/types";
import { persianDigits } from "@/lib/format";
import { IconArchive, IconBell, IconComment, IconDoc, IconEdit, IconFlow, IconGrid, IconHome, IconSearch, IconUpload, IconUsers } from "@/components/icons";

const TOP_LINKS: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/projects", label: "پروژه‌ها", icon: <IconGrid /> },
  { to: "/dashboard", label: "خانه", icon: <IconHome /> },
  { to: "/transmittals", label: "مدارک مهندسی", icon: <IconDoc /> },
  { to: "/mdl", label: "MDL", icon: <IconArchive /> },
  { to: "/review", label: "وظایف من", icon: <IconComment /> },
  { to: "/upload", label: "بارگذاری", icon: <IconUpload /> },
  { to: "/registration", label: "ثبت برگه", icon: <IconEdit /> },
];

const MENU: { title: string; icon: ReactNode; items: { to: string; label: string; icon: ReactNode; badge?: "queue" | "inbox" }[] }[] = [
  {
    title: "Project Control",
    icon: <IconGrid />,
    items: [
      { to: "/projects", label: "تعریف و انتخاب پروژه", icon: <IconGrid /> },
    ],
  },
  {
    title: "Engineering Documents",
    icon: <IconDoc />,
    items: [
      { to: "/dashboard", label: "کارتابل من", icon: <IconHome />, badge: "inbox" },
      { to: "/transmittals", label: "برگه‌های ارسال", icon: <IconDoc /> },
      { to: "/transmittals?mine=1", label: "کارتابل ارجاع من", icon: <IconUsers /> },
      { to: "/mdl", label: "MDL — فهرست مدارک", icon: <IconArchive /> },
      { to: "/upload", label: "ثبت و بارگذاری مدرک", icon: <IconUpload /> },
      { to: "/registration", label: "ثبت برگهٔ ارسال (Registration)", icon: <IconEdit /> },
      { to: "/review", label: "صف بازبینی (DCC)", icon: <IconComment />, badge: "queue" },
      { to: "/team", label: "کاربران و نقش‌ها", icon: <IconUsers /> },
    ],
  },
  {
    title: "Documents Center",
    icon: <IconArchive />,
    items: [
      { to: "/mdl", label: "فهرست مدارک (MDL)", icon: <IconArchive /> },
      { to: "/transmittals", label: "پیگیری ارسال‌ها", icon: <IconBell /> },
    ],
  },
];

const TITLES: Record<string, string> = {
  "/projects": "تعریف و انتخاب پروژه",
  "/dashboard": "کارتابل من",
  "/mdl": "فهرست مدارک پروژه (MDL)",
  "/upload": "بارگذاری مدارک با هوش مصنوعی",
  "/transmittals/new": "ثبت ترانسمیتال دستی",
  "/registration": "ثبت و بارگذاری مدرک — برگهٔ ارسال",
  "/review": "صف بازبینی کنترل مدارک",
  "/team": "کاربران و نقش‌های گردش",
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
  const [inboxCount, setInboxCount] = useState(0);
  const [queueCount, setQueueCount] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    document.title = location.pathname === "/upload"
      ? "بارگذاری مدارک با هوش مصنوعی"
      : location.pathname === "/transmittals/new"
        ? "ثبت ترانسمیتال دستی"
        : "سامانه هوشمند مدیریت اسناد مهندسی | EDMS-AI";
  }, [location.pathname]);

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

      if (!project) {
        setInboxCount(0);
        setQueueCount(0);
        return;
      }

      // The «کارتابل من» badge represents actionable rows, not every unread
      // notification. Rejected/ informational upload notices remain available
      // in the notification panel but must not keep a closed AI row in the desk.
      Promise.all([
        api.get<{ total: number }>(`/projects/${project.id}/review-queue?review_status=pending&size=1`),
        api.get<{ total: number }>(`/projects/${project.id}/transmittals?mine=true&size=1`),
      ])
        .then(([queue, transmittals]) => {
          if (!active) return;
          setQueueCount(queue.total);
          setInboxCount(queue.total + transmittals.total);
        })
        .catch(() => undefined);
    };
    const refreshAfterInboxChange = () => load();
    window.addEventListener("edms:inbox-changed", refreshAfterInboxChange);
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("edms:inbox-changed", refreshAfterInboxChange);
    };
  }, [location.pathname, project]);

  const breadcrumb = useMemo(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    const label = TITLES[location.pathname] ?? TITLES[`/${segments[0] ?? ""}`] ?? "";
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
        <div className="masthead-brand flex min-w-0 items-center gap-3">
          <img
            className="masthead-logo"
            src="/aidana-logo.png"
            alt="AIDANA — سامانه هوشمند مدیریت اسناد مهندسی"
          />
          <div className="min-w-0">
            <h1 className="masthead-title">سامانه هوشمند مدیریت اسناد مهندسی</h1>
            <p className="masthead-sub">Engineering Document Management System — EDMS-AI</p>
          </div>
        </div>
        <div className="masthead-tools flex items-center gap-2">
          <div className="masthead-search">
            <IconSearch />
            <input
              className="input"
              aria-label="جست‌وجوی سراسری"
              placeholder="جستجو در سایت…"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  const value = (event.target as HTMLInputElement).value.trim();
                  navigate(`/mdl${value ? `?q=${encodeURIComponent(value)}` : ""}`);
                }
              }}
            />
          </div>
          <div className="project-picker">
            <span>پروژه:</span>
            <select
              className="input"
              value={project?.id ?? ""}
              onChange={(event) => select(event.target.value)}
              aria-label="پروژه فعال"
            >
              {projects.length === 0 ? <option value="">—</option> : null}
              {projects.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.code} — {item.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="masthead-utilities">
          <div className="masthead-user">
            <span className="masthead-user-avatar"><IconUsers /></span>
            <span className="masthead-user-name">{user?.full_name ?? "کاربر"}</span>
            <span className="online-dot" />
          </div>
          <button
            type="button"
            className="masthead-icon-button masthead-notification-button"
            aria-label="اعلان‌ها"
            onClick={() => setPanelOpen((open) => !open)}
          >
            <IconBell />
            {unread > 0 ? <span className="masthead-notification-count">{persianDigits(unread)}</span> : null}
          </button>
          <button type="button" className="masthead-icon-button" aria-label="راهنما" title="راهنمای سامانه">
            ?
          </button>
        </div>
      </header>

      <div className="top-band" />

      {/* ---------- main navigation ---------- */}
      <nav className="topnav">
        <div className="topnav-user">
          <span className="user-avatar" aria-hidden="true">{(user?.full_name ?? "کاربر").slice(0, 1)}</span>
          <span className="status-dot bg-[#7fd18c]" />
          <span className="font-bold">{user?.full_name}</span>
          <span className="text-[9.5px] text-[#243842]">
            ({project?.member_role === "dcc"
              ? "کنترل مدارک"
              : project?.member_role === "project_control"
                ? "کنترل پروژه"
                : project?.member_role === "pmo"
                  ? "واحد PMO"
                  : user?.is_superuser
                    ? "مدیر سیستم"
                    : "کاربر"})
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
            <span className="topnav-link-icon"><IconBell /></span>
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
        <span className="breadcrumb-page-label">
          {location.pathname === "/upload"
            ? "بارگذاری مدارک با هوش مصنوعی"
            : location.pathname === "/transmittals/new"
              ? "ثبت ترانسمیتال دستی"
              : "EDMS Result Page"}
        </span>
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
      <div className={clsx("shell", sidebarCollapsed && "is-menu-collapsed")}>
        <aside className="menu-rail">
          <button
            type="button"
            className="menu-collapse"
            onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            aria-label={sidebarCollapsed ? "باز کردن منوی کناری" : "جمع کردن منوی کناری"}
            title={sidebarCollapsed ? "باز کردن منوی کناری" : "جمع کردن منوی کناری"}
          >
            <span>{sidebarCollapsed ? "‹" : "⌄"}</span>
            {!sidebarCollapsed ? <span className="sr-only">منوی سامانه</span> : null}
          </button>
          {MENU.map((group) => (
            <div key={group.title}>
              {!sidebarCollapsed ? (
                <p className="menu-group-title">
                  <span className="menu-group-title-icon">{group.icon}</span>
                  <span>{group.title}</span>
                  <span className="menu-group-title-chevron">⌄</span>
                </p>
              ) : null}
              {group.items.map((item) => (
                <NavLink
                  key={`${group.title}-${item.label}`}
                  to={item.to}
                  className={({ isActive }) =>
                    clsx("menu-item", isActive && location.pathname === item.to && "is-active")
                  }
                  title={sidebarCollapsed ? item.label : undefined}
                >
                  <span className="menu-item-content">
                    <span className="menu-item-icon">{item.icon}</span>
                    {!sidebarCollapsed ? <span className="menu-item-label">{item.label}</span> : null}
                  </span>
                  {!sidebarCollapsed && item.badge === "inbox" && inboxCount > 0 ? (
                    <span className="menu-item-count">{persianDigits(inboxCount)}</span>
                  ) : null}
                  {!sidebarCollapsed && item.badge === "queue" && queueCount > 0 ? (
                    <span className="menu-item-count">{persianDigits(queueCount)}</span>
                  ) : null}
                </NavLink>
              ))}
            </div>
          ))}
          {!sidebarCollapsed ? (
            <p className="menu-group-title">
              <span className="menu-group-title-icon"><IconFlow /></span>
              <span>System</span>
              <span className="menu-group-title-chevron">⌄</span>
            </p>
          ) : null}
          <Link className="menu-item" to="/review" title={sidebarCollapsed ? "پردازش هوشمند" : undefined}>
            <span className="menu-item-content">
              <span className="menu-item-icon"><IconFlow /></span>
              {!sidebarCollapsed ? <span className="menu-item-label">پردازش هوشمند</span> : null}
            </span>
            {!sidebarCollapsed ? <span className="menu-item-state">فعال</span> : null}
          </Link>
          <button className="menu-item" onClick={() => setPanelOpen(true)} title={sidebarCollapsed ? "اعلان‌های من" : undefined}>
            <span className="menu-item-content">
              <span className="menu-item-icon"><IconBell /></span>
              {!sidebarCollapsed ? <span className="menu-item-label">اعلان‌های من</span> : null}
            </span>
            {!sidebarCollapsed ? <span className="menu-item-state">{persianDigits(unread)}</span> : null}
          </button>
        </aside>

        <main className="shell-main">
          {project || location.pathname === "/projects" ? (
            <Outlet />
          ) : (
            <div className="card p-6 text-center text-[11px] text-[#4a5a67]">
              <p>ابتدا یک پروژه را تعریف و انتخاب کنید.</p>
              <Link className="btn btn-primary mt-3 inline-block" to="/projects">
                رفتن به مدیریت پروژه‌ها
              </Link>
            </div>
          )}
        </main>
      </div>

      <footer className="site-footer border-t px-3 py-1.5 text-center text-[9.5px]">
        سامانه هوشمند مدیریت اسناد مهندسی — نسخه ۱.۰ · تاریخ شمسی · Asia/Tehran
      </footer>
    </div>
  );
}
