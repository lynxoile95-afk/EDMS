import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Field, Input } from "@/components/ui";

export function Login() {
  const { login, expired } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "ورود ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-[#dfe3e8]">
      <div className="flag-band" />
      <header className="masthead">
        <div className="flex items-center gap-3">
          <div className="masthead-logo">EDMS</div>
          <div>
            <h1 className="masthead-title">سامانه مدیریت اسناد مهندسی</h1>
            <p className="masthead-sub">Engineering Document Management System — EDMS-AI</p>
          </div>
        </div>
        <span className="text-[10px] text-[#5c6b78]">نسخه ۱.۰</span>
      </header>
      <div className="top-band" />
      <div className="topnav">
        <div className="topnav-user">
          <span className="status-dot bg-[#7fd18c]" />
          <span>سرویس برخط</span>
        </div>
        <div className="topnav-links">
          <span className="topnav-link">Help</span>
          <span className="topnav-link">Report</span>
          <span className="topnav-link">Search</span>
        </div>
      </div>

      <div className="flex flex-1 items-center justify-center p-4">
        <form className="win-window max-w-[620px]" onSubmit={submit}>
          <div className="win-titlebar">
            <span>ورود به سامانه — User Login</span>
            <span className="text-[10px] font-normal">✕</span>
          </div>
          <div className="win-body">
            <div className="section-bar mb-2">Authentication Information — اطلاعات احراز هویت</div>
            <table className="kv-table">
              <tbody>
                <tr>
                  <th>ایمیل سازمانی</th>
                  <td>
                    <Input
                      type="email"
                      dir="ltr"
                      autoComplete="username"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      placeholder="name@company.com"
                      required
                    />
                  </td>
                </tr>
                <tr>
                  <th>رمز عبور</th>
                  <td>
                    <Input
                      type="password"
                      dir="ltr"
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="••••••••"
                      required
                    />
                  </td>
                </tr>
                <tr>
                  <th>سرور</th>
                  <td className="text-[10px] text-[#5c6b78]">
                    احراز هویت محلی (قابل اتصال به LDAP / Keycloak در استقرار سازمانی)
                  </td>
                </tr>
              </tbody>
            </table>

            {error ? (
              <p className="mt-2 border border-[#c98a86] bg-[#fdeeee] px-2 py-1 text-[10.5px] text-[#8a2b25]">{error}</p>
            ) : expired ? (
              <p className="mt-2 border border-[#e0c07a] bg-[#fdf6e3] px-2 py-1 text-[10.5px] leading-5 text-[#8a5a10]">
                نشست قبلی شما منقضی یا باطل شده است (مثلاً پس از راه‌اندازی مجدد سرویس). لطفاً دوباره وارد شوید.
              </p>
            ) : null}

            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <Field label="حساب دمو (کنترل مدارک)">
                <Button
                  type="button"
                  className="w-full"
                  onClick={() => {
                    setEmail("dcc@example.com");
                    setPassword("Demo1234!");
                  }}
                >
                  dcc@example.com / Demo1234!
                </Button>
              </Field>
              <Field label="حساب مدیر سیستم">
                <Button
                  type="button"
                  className="w-full"
                  onClick={() => {
                    setEmail("admin@example.com");
                    setPassword("Admin123!");
                  }}
                >
                  admin@example.com / Admin123!
                </Button>
              </Field>
            </div>
          </div>
          <div className="win-footer">
            <Button type="submit" variant="primary" disabled={busy} className="min-w-[120px]">
              {busy ? "در حال ورود…" : "ورود"}
            </Button>
            <Button type="button" onClick={() => { setEmail(""); setPassword(""); setError(null); }}>
              پاک کردن
            </Button>
          </div>
        </form>
      </div>

      <div className="strip-green mx-4 mb-2" />
      <footer className="pb-2 text-center text-[9.5px] text-[#5c6b78]">
        سامانه مدیریت اسناد مهندسی · تقویم شمسی · Asia/Tehran · Data stays on-premise
      </footer>
    </div>
  );
}
