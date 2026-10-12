"""
نقطة انطلاق التطبيق (FastAPI Application)
=========================================
يجمع كل الأجزاء: المسارات، الوسائط، معالجات الأخطاء، ودورة حياة التطبيق.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# خادم Uvicorn يوفّر وسيط ترويسات الوكيل العكسي. كان هذا الوسيط في
# Starlette، لكن Starlette 1.x نقله إلى uvicorn -- فنستورده منه.
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.db import dispose_engine, healthcheck
from app.core.errors import register_exception_handlers, wants_json
from app.core.tenancy import clear_tenant_context, setup_tenancy
from app.web.templates_setup import templates

# =====================================================================
#  السجلات
# =====================================================================
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mashia")


# =====================================================================
#  دورة حياة التطبيق
# =====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    التهيئة عند الإقلاع والتنظيف عند الإيقاف.

    نضمن هنا تفعيل نظام عزل المستأجرين **قبل** أي طلب يصل، لأن أول
    استعلام بدون سياق قد يمرّ بلا فلترة.
    """
    setup_tenancy()

    logger.info("=" * 62)
    logger.info("  %s — %s", settings.APP_NAME, settings.APP_TAGLINE)
    logger.info("  الإصدار: %s | البيئة: %s", settings.APP_VERSION, settings.APP_ENV)
    logger.info("  قاعدة البيانات: %s", settings.DB_DRIVER)
    logger.info("=" * 62)

    if await healthcheck():
        logger.info("تم الاتصال بقاعدة البيانات بنجاح")
    else:
        logger.error("تعذّر الاتصال بقاعدة البيانات")

    yield

    clear_tenant_context()
    await dispose_engine()
    logger.info("تم إيقاف التطبيق وتنظيف الموارد")


# =====================================================================
#  إنشاء التطبيق
# =====================================================================
app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_TAGLINE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    contact={"name": "دعم المنصة"},
    license_info={"name": "MIT"},
)

# --- الوسائط ---
app.add_middleware(GZipMiddleware, minimum_size=1000)

# النطاقات المسموح بها: في التطوير نسمح للجميع، وفي الإنتاج نقيّدها
# بمصادر صريحة. (``*`` مع ``allow_credentials`` مخالف لمواصفة CORS،
# فلا يجتمعان.)
allowed_origins = (
    ["*"]
    if settings.DEBUG
    else [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# خلف وكيل عكسي (Render / Nginx / Fly) يستقبل التطبيق طلبات HTTP بينما
# المتصفح يتصل عبر HTTPS. دون هذا الوسيط يبني التطبيق روابط ``http://``
# ويُبلّغ ``request.url.scheme`` خطأ — وهو ما يكسر روابط إعادة التوجيه
# والكوكيز ذات ``secure=True``.
#
# ``trusted_hosts`` هنا تعني عنوان *الوكيل* الذي نثق بترويساته (وليس
# ترويس X-Forwarded-For التي يصنعها المستخدم). في التطوير نقبل المحلي
# فقط، وفي الإنتاج نوثّق عناوين Render.
proxy_trust = (
    ["127.0.0.1", "::1"]
    if settings.DEBUG
    else [h.strip() for h in settings.TRUSTED_PROXIES.split(",") if h.strip()]
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=proxy_trust)

# حصر المضيف المقبول: يمنع Right/Host header injection في سلوك إعادة
# التوجيه وإنشاء الروابط.
if not settings.DEBUG and settings.ALLOWED_HOSTS.strip():
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[h.strip() for h in settings.ALLOWED_HOSTS.split(",") if h.strip()],
    )


@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    """
    ضمان عزل السياق بين الطلبات.

    كل طلب في FastAPI يعمل في سياق ``ContextVar`` خاص به، لكن الوسيط
    هنا يضمن نقطة بداية نظيفة لكل طلب حتى في المسارات التي لا تمر
    بالمصادقة (الواجهة العامة) -- فلا تتسرّب حالة طلب إلى آخر.
    """
    started = time.perf_counter()
    clear_tenant_context()

    try:
        response = await call_next(request)
    finally:
        clear_tenant_context()

    elapsed_ms = (time.perf_counter() - started) * 1000

    # ترويسات الأداء والتشخيص
    response.headers["X-Process-Time"] = f"{elapsed_ms:.1f}ms"
    if elapsed_ms > 500:
        logger.warning(
            "طلب بطيء: %s %s — %.0fms",
            request.method, request.url.path, elapsed_ms,
        )

    return response


# --- معالجات الأخطاء ---
register_exception_handlers(app)


# =====================================================================
#  الملفات الثابتة و PWA
# =====================================================================
settings.static_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/static",
    StaticFiles(directory=str(settings.static_dir)),
    name="static",
)


# =====================================================================
#  المسارات
# =====================================================================
app.include_router(api_router)


@app.get("/api/health", tags=["النظام"], summary="فحص صحة التطبيق")
async def health_check():
    """فحص سريع: التطبيق يعمل وقاعدة البيانات متاحة."""
    db_ok = await healthcheck()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "database": "connected" if db_ok else "unavailable",
    }


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    """ملف تطبيق الويب (PWA) المستخدم في تثبيت التطبيق على الهاتف."""
    from fastapi.responses import FileResponse

    path = settings.static_dir / "manifest.webmanifest"
    if path.exists():
        return FileResponse(path, media_type="application/manifest+json")
    return JSONResponse({}, status_code=status.HTTP_404_NOT_FOUND)


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service Worker — يجب أن يكون في الجذر ليمتد لنطاق الموقع كله."""
    from fastapi.responses import FileResponse

    path = settings.static_dir / "sw.js"
    if path.exists():
        return FileResponse(
            path,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )
    return JSONResponse({}, status_code=status.HTTP_404_NOT_FOUND)


# =====================================================================
#  صفحات الويب
# =====================================================================
# الصفحة الرئيسية "/" تُعرَّف في ``app/web/routes.py`` مع بقية صفحات
# البوابة العامة، لأن عرضها يحتاج سياق القالب الكامل (أخبار، أندية،
# رياضات) وليس مجرد توجيه بسيط.


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):  # noqa: ANN001, ANN201
    """
    صفحة 404 جميلة للواجهة، و JSON للـ API.
    """
    if wants_json(request):
        return JSONResponse(
            status_code=404,
            content={
                "error": True,
                "code": "not_found",
                "message": "العنصر المطلوب غير موجود",
                "path": str(request.url.path),
            },
        )

    from app.web.helpers import page_context

    try:
        return templates.TemplateResponse(
            request=request,
            name="errors/404.html",
            context={
                **page_context(request),
                "page_title": "الصفحة غير موجودة",
            },
            status_code=404,
        )
    except Exception:  # pragma: no cover - القالب غير موجود
        return JSONResponse(
            status_code=404,
            content={"error": True, "message": "الصفحة غير موجودة"},
        )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):  # noqa: ANN001, ANN201
    """صفحة خطأ 500 (تُستخدم في وضع التصحيح فقط)."""
    if wants_json(request) or not settings.DEBUG:
        return JSONResponse(
            status_code=500,
            content={
                "error": True,
                "code": "internal_error",
                "message": "حدث خطأ داخلي غير متوقع",
            },
        )

    try:
        return templates.TemplateResponse(
            request=request,
            name="errors/500.html",
            context={"page_title": "خطأ في الخادم"},
            status_code=500,
        )
    except Exception:  # pragma: no cover
        return JSONResponse(
            status_code=500,
            content={"error": True, "message": "خطأ في الخادم"},
        )


# --- تسجيل مسارات الصفحات ---
# نستورد ``router`` من حزمة ``app.web`` وليس الوحدة ``app.web.routes``:
# الوحدة تحتوي موجّهاً فارغاً يُستخدم كقاعدة للوحدات الفرعية فقط،
# بينما ``router`` هو الموجّه المُجمِّع الذي يضم كل الصفحات.
from app.web import router as web_router  # noqa: E402

app.include_router(web_router)


def run() -> None:
    """تشغيل خادم التطوير."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )


if __name__ == "__main__":
    run()