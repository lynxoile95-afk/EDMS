"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.core.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("edms")

DESCRIPTION = """
سامانه مدیریت مدارک مهندسی هوشمند (EDMS-AI)

* استخراج خودکار کادر مشخصات از PDF و تطبیق با MDL
* تصمیم قاعده‌محور GREEN / YELLOW / RED (هوش مصنوعی پیشنهاد می‌دهد، قاعده تصمیم می‌گیرد)
* تولید خودکار برگه ارسال (Transmittal) بر پایه ماتریس توزیع مدارک (DDM)
* فهرست نظرها (Comment List)، گردش تأیید و لاگ ممیزی کامل
"""


_LANDING_HTML = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>سرویس API — سامانه مدیریت مدارک مهندسی</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: linear-gradient(225deg, #12283c, #1f4e79); font-family: Vazirmatn, Tahoma, sans-serif; }}
  .card {{ width: min(680px, 92vw); background: #fff; border-radius: 18px; padding: 32px;
          box-shadow: 0 24px 60px rgba(0,0,0,.35); }}
  h1 {{ margin: 0 0 6px; font-size: 20px; color: #12283c; }}
  p.sub {{ margin: 0 0 22px; color: #64748b; font-size: 13px; line-height: 1.9; }}
  .note {{ background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412; border-radius: 12px;
          padding: 12px 14px; font-size: 13px; line-height: 1.9; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  td {{ padding: 9px 4px; border-bottom: 1px solid #eef2f7; vertical-align: middle; }}
  td.k {{ color: #475569; width: 42%; }}
  code {{ direction: ltr; unicode-bidi: embed; background: #f1f5f9; padding: 2px 6px; border-radius: 6px;
         font-size: 12px; }}
  a {{ color: #1f4e79; font-weight: 600; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  footer {{ margin-top: 20px; color: #94a3b8; font-size: 11px; text-align: center; direction: ltr; }}
</style>
</head>
<body>
  <main class="card">
    <h1>این آدرس، سرویس API است — نه رابط کاربری</h1>
    <p class="sub">
      پیام <code>{{"detail": "احراز هویت لازم است."}}</code> طبیعی است: مسیرهای زیر به توکن دسترسی
      (JWT) نیاز دارند و باید از داخل رابط کاربری یا با هدر
      <code>Authorization: Bearer &lt;token&gt;</code> فراخوانی شوند.
    </p>
    <div class="note">
      برای ورود به برنامه، پیش‌نمایشِ <b>رابط کاربری</b> (پورت <code>5173</code> در حالت توسعه، یا
      پورت <code>8080</code> در استقرار Docker) را باز کنید — نه این پورت (<code>8000</code>).
    </div>
    <table>
      <tr><td class="k">مستندات تعاملی API</td><td><a href="/docs">/docs</a></td></tr>
      <tr><td class="k">Schema (OpenAPI)</td><td><a href="/openapi.json">/openapi.json</a></td></tr>
      <tr><td class="k">بررسی سلامت سرویس</td><td><a href="/health">/health</a></td></tr>
      <tr><td class="k">ورود و دریافت توکن</td><td><code>POST {prefix}/auth/login</code></td></tr>
      <tr><td class="k">اطلاعات کاربر جاری</td><td><code>GET {prefix}/auth/me</code></td></tr>
    </table>
    <footer>EDMS-AI · FastAPI · {prefix}</footer>
  </main>
</body>
</html>"""


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def correlation_and_timing(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        if duration_ms > 2000:
            logger.warning(
                "slow request %s %s %sms cid=%s",
                request.method, request.url.path, duration_ms, correlation_id,
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        """Translate raw pydantic errors into a Persian-friendly payload."""
        issues = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", []) if part != "body")
            issues.append({"field": location, "message": error.get("msg", ""), "type": error.get("type")})
        return JSONResponse(
            status_code=422,
            content={
                "detail": "داده‌های ورودی معتبر نیستند.",
                "errors": issues,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):  # pragma: no cover
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        detail = "خطای داخلی سرور رخ داد." if settings.is_production else f"{type(exc).__name__}: {exc}"
        return JSONResponse(status_code=500, content={"detail": detail})

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["system"], summary="بررسی سلامت سرویس")
    def health() -> dict:
        from app.db.session import ping

        return {"status": "ok", "database": ping(), "environment": settings.environment}

    @app.get("/", include_in_schema=False)
    def root(request: Request):
        """Landing page for the API port.

        The service has no server-rendered UI, so opening :8000 in a browser used
        to return bare JSON — and clicking any ``/api/v1/...`` link answered
        ``{"detail": "احراز هویت لازم است."}``, which looks like a broken login.
        Browsers now get an explanatory page with the real entry points; API
        clients that send ``Accept: application/json`` still get the JSON summary.
        """
        if "text/html" not in request.headers.get("accept", ""):
            return {
                "name": settings.app_name,
                "version": "1.0.0",
                "docs": "/docs",
                "api": settings.api_v1_prefix,
            }
        return HTMLResponse(content=_LANDING_HTML.format(prefix=settings.api_v1_prefix))

    @app.on_event("startup")
    def _startup() -> None:
        for warning in settings.validate_runtime():
            logger.warning("config: %s", warning)
        if settings.storage_backend == "s3":
            try:
                from app.services.storage import get_storage

                storage = get_storage()
                if hasattr(storage, "ensure_bucket"):
                    storage.ensure_bucket()
                logger.info("object storage ready")
            except Exception as exc:  # noqa: BLE001 - start anyway; uploads will surface the error
                logger.warning("object storage not reachable at startup: %s", exc)

    return app


app = create_app()
