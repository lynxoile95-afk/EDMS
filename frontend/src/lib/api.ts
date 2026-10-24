/**
 * Thin fetch wrapper.
 *
 * - always same-origin (`/api/v1`) so it works behind nginx and the preview proxy
 * - attaches the bearer token
 * - transparently refreshes an expired access token once
 * - surfaces the backend's Persian error messages
 */

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";

const ACCESS_KEY = "edms.access_token";
const REFRESH_KEY = "edms.refresh_token";

export interface ApiErrorPayload {
  detail?: string | { message?: string; issues?: { rule_code: string; message_fa: string }[] };
  errors?: { field: string; message: string }[];
}

export class ApiError extends Error {
  status: number;
  payload: ApiErrorPayload | null;

  constructor(status: number, message: string, payload: ApiErrorPayload | null = null) {
    super(message);
    this.status = status;
    this.payload = payload;
  }

  /** A human-readable detail, including structured conflict payloads. */
  get detail(): string {
    const detail = this.payload?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) {
      const issues = detail.issues?.map((issue) => `${issue.rule_code}: ${issue.message_fa}`).join(" | ");
      return issues ? `${detail.message} (${issues})` : detail.message;
    }
    if (this.payload?.errors?.length) {
      return this.payload.errors.map((error) => `${error.field}: ${error.message}`).join(" | ");
    }
    return this.message;
  }
}

/**
 * Token store.
 *
 * Tokens live in localStorage *and* in cookies. Some reverse proxies (including
 * sandbox preview proxies and corporate middle boxes) consume or strip the
 * `Authorization` header, which used to make every request after login fail with
 * «احراز هویت لازم است.» while the login itself succeeded. Cookies travel on a
 * different channel, and `X-Access-Token` is a third one, so a single stripped
 * header can no longer log the user out.
 */
const COOKIE_ACCESS = "edms_access_token";
const COOKIE_REFRESH = "edms_refresh_token";

function writeCookie(name: string, value: string, maxAgeSeconds: number) {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAgeSeconds}; SameSite=Lax${secure}`;
}

function dropCookie(name: string) {
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/**
 * Headers that carry the credential on *three* channels.
 *
 * Some preview proxies strip `Authorization`, which is why the same token is also
 * sent as `X-Access-Token` and as a cookie. Binary downloads go through the same
 * helper — before this existed they only sent `Authorization` and every preview
 * silently failed with 401 (the PDF viewer then showed «فایل قابل نمایش نیست»).
 */
function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const access = tokens.access;
  if (access) {
    headers.set("Authorization", `Bearer ${access}`);
    headers.set("X-Access-Token", access);
  }
  headers.set("X-Requested-With", "XMLHttpRequest");
  return headers;
}

export const tokens = {
  get access(): string | null {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh(): string | null {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh?: string, expiresInSeconds = 7200) {
    localStorage.setItem(ACCESS_KEY, access);
    writeCookie(COOKIE_ACCESS, access, expiresInSeconds);
    if (refresh) {
      localStorage.setItem(REFRESH_KEY, refresh);
      writeCookie(COOKIE_REFRESH, refresh, 7 * 24 * 3600);
    }
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    dropCookie(COOKIE_ACCESS);
    dropCookie(COOKIE_REFRESH);
  },
};

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = authHeaders(init.headers);
  if (!(init.body instanceof FormData) && init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE_URL}${path}`, { ...init, headers, credentials: "include" });

  if (response.status === 401 && retry && tokens.refresh) {
    const refreshed = await tryRefresh();
    if (refreshed) return request<T>(path, init, false);
    tokens.clear();
    onUnauthorized?.();
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: ApiErrorPayload | null = null;
  if (text) {
    try {
      payload = JSON.parse(text) as ApiErrorPayload;
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const message =
      typeof payload?.detail === "string"
        ? payload.detail
        : `خطای سرور (${response.status})`;
    if (response.status === 401) {
      tokens.clear();
      onUnauthorized?.();
    }
    throw new ApiError(response.status, message, payload);
  }

  return (text ? JSON.parse(text) : undefined) as T;
}

async function tryRefresh(): Promise<boolean> {
  const refresh = tokens.refresh;
  try {
    const response = await fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      // `credentials: include` lets the refresh cookie do the work when the body
      // token is unavailable (e.g. the storage was cleared by another tab).
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(refresh ? { refresh_token: refresh } : {}),
    });
    if (!response.ok) return false;
    const data = (await response.json()) as {
      access_token: string;
      refresh_token: string;
      expires_in?: number;
    };
    tokens.set(data.access_token, data.refresh_token, data.expires_in ?? 7200);
    return true;
  } catch {
    return false;
  }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
  /** Fetch a protected binary resource as a blob URL (used by the PDF viewer). */
  async blobUrl(path: string, retry = true): Promise<string> {
    const response = await fetch(`${BASE_URL}${path}`, {
      headers: authHeaders(),
      credentials: "include",
      redirect: "follow",
    });
    if (response.status === 401 && retry && tokens.refresh) {
      if (await tryRefresh()) return api.blobUrl(path, false);
    }
    if (!response.ok) {
      const message =
        response.status === 401
          ? "برای دریافت فایل باید دوباره وارد شوید."
          : response.status === 404
            ? "فایل در مخزن پیدا نشد."
            : `دریافت فایل ناموفق بود (کد ${response.status}).`;
      throw new ApiError(response.status, message);
    }
    return URL.createObjectURL(await response.blob());
  },
  async download(path: string, filename: string): Promise<void> {
    const url = await api.blobUrl(path);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  },
};
