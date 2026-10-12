"""
معالجات الأخطاء (Exception Handlers)
====================================
تحويل استثناءات الأعمال إلى استجابات HTTP موحّدة ومفهومة بالعربية،
مع تسجيل الأحداث الهامة في سجل المنصة.
"""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import AppError, TenantIsolationError

logger = logging.getLogger(__name__)


# =====================================================================
#  استجابة خطأ موحّدة
# =====================================================================
def error_response(
    exc: AppError,
    request: Request,
    status_code: int | None = None,
) -> JSONResponse:
    """بناء استجابة JSON موحّدة من استثناء أعمال."""
    code = status_code or exc.status_code
    payload = exc.to_dict()
    payload["path"] = str(request.url.path)

    headers = {}
    if isinstance(exc, TenantIsolationError):
        # تحذير أمني: نسجّل محاولة اختراق مع كل التفاصيل
        logger.warning(
            "محاولة عزل مستأجر مرفوضة | المسار=%s | ctx=%s",
            request.url.path,
            _context_summary(),
        )

    return JSONResponse(status_code=code, content=payload, headers=headers)


def _context_summary() -> str:
    """ملخص مختصر لسياق المستأجر الحالي (للتسجيل)."""
    from app.core.tenancy import get_tenant_context

    ctx = get_tenant_context()
    if ctx is None:
        return "بدون سياق"
    return f"user={ctx.user_id} role={ctx.role} club={ctx.club_id} mode={ctx.scope_mode}"


# =====================================================================
#  التسجيل
# =====================================================================
def _render_error_page(request: Request, exc: AppError) -> HTMLResponse:
    """
    عرض استثناء أعمال كصفحة HTML أنيقة.

    نختار القالب حسب نوع الخطأ: 403 → صفحة منع الوصول، 404 → صفحة
    غير موجود، وأي خطأ آخر → صفحة خطأ عام. إن تعذّر تحميل القالب
    نرجع JSON احتياطياً حتى لا ينهار الطلب.
    """
    from app.web.templates_setup import templates

    template = {
        403: "errors/403.html",
        404: "errors/404.html",
    }.get(exc.status_code, "errors/400.html")

    try:
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={"message": exc.message, "page_title": exc.message},
            status_code=exc.status_code,
        )
    except Exception:  # pragma: no cover - القالب غير متاح
        return HTMLResponse(
            content=exc.message,
            status_code=exc.status_code,
        )


def register_exception_handlers(app: FastAPI) -> None:
    """
    تثبيت كل معالجات الأخطاء على تطبيق FastAPI.

    تُستدعى مرة واحدة من ``app.main``.
    """

    # -------------------------------------------------------------
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """معالج استثناءات الأعمال (JSON للـ API، صفحة HTML للواجهة)."""
        if not wants_json(request):
            return _render_error_page(request, exc)

        return error_response(exc, request)

    # -------------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """
        معالج أخطاء التحقق من المدخلات.

        يحوّل الأخطاء التقنية إلى رسائل عربية مفهومة، لأن رسالة
        ``field required`` لا تساعد المستخدم النهائي.
        """
        details = []
        for err in exc.errors():
            location = [str(p) for p in err.get("loc", []) if p not in ("body", "query")]
            field = " > ".join(location) or "الحقل"
            details.append({
                "field": field,
                "message": _translate_validation_error(err),
                "type": err.get("type"),
            })

        payload = {
            "error": True,
            "code": "validation_error",
            "message": "البيانات المُدخلة غير صحيحة",
            "status": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "details": details,
            "path": str(request.url.path),
        }
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=payload,
        )

    # -------------------------------------------------------------
    @app.exception_handler(PydanticValidationError)
    async def _pydantic_handler(
        request: Request,
        exc: PydanticValidationError,
    ) -> JSONResponse:
        """معالج أخطاء تحقق Pydantic الداخلية."""
        details = [
            {"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        payload = {
            "error": True,
            "code": "validation_error",
            "message": "البيانات المُدخلة غير صالحة",
            "status": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "details": details,
        }
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=payload,
        )

    # -------------------------------------------------------------
    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """معالج أخطاء HTTP القياسية (404، 405...)."""
        arabic = {
            401: "يجب تسجيل الدخول للمتابعة",
            403: "ليس لديك صلاحية للوصول إلى هذه الصفحة",
            404: "الصفحة المطلوبة غير موجودة",
            405: "طريقة الطلب غير مسموحة",
        }
        payload = {
            "error": True,
            "code": f"http_{exc.status_code}",
            "message": arabic.get(exc.status_code, str(exc.detail)),
            "status": exc.status_code,
            "path": str(request.url.path),
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=getattr(exc, "headers", None),
        )

    # -------------------------------------------------------------
    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """
        المصيد الأخير لكل خطأ غير متوقع.

        يسجّل التتبّع الكامل في الخادم، لكن لا يسرّب أي تفاصيل تقنية
        للمستخدم في بيئة الإنتاج -- تسريب رسائل الخطأ contiene أحياناً
        أسماء جداول ومعلومات اتصال.
        """
        error_id = f"ERR-{id(exc):x}"
        logger.error(
            "خطأ غير متوقع [%s] على %s: %s\n%s",
            error_id,
            request.url.path,
            exc,
            traceback.format_exc(),
        )

        payload = {
            "error": True,
            "code": "internal_error",
            "message": "حدث خطأ داخلي غير متوقع. يرجى المحاولة لاحقاً",
            "status": 500,
            "reference": error_id,
            "path": str(request.url.path),
        }
        if settings.DEBUG:
            payload["debug"] = {
                "type": type(exc).__name__,
                "detail": str(exc),
            }
        return JSONResponse(status_code=500, content=payload)


# =====================================================================
#  ترجمة رسائل التحقق
# =====================================================================
_FIELD_LABELS: dict[str, str] = {
    "email": "البريد الإلكتروني",
    "phone": "رقم الهاتف",
    "password": "كلمة المرور",
    "full_name": "الاسم الكامل",
    "first_name": "الاسم الأول",
    "last_name": "اسم العائلة",
    "birth_date": "تاريخ الميلاد",
    "card_code": "كود البطاقة",
    "amount": "المبلغ",
    "club_id": "النادي",
    "coach_id": "المدرب",
    "athlete_id": "الرياضي",
    "group_id": "الفوج",
    "session_date": "تاريخ الحصة",
    "due_date": "تاريخ الاستحقاق",
    "period_start": "بداية الفترة",
    "period_end": "نهاية الفترة",
    "method": "وسيلة الدفع",
    "category": "التصنيف",
    "role": "الرتبة",
    "name": "الاسم",
    "title": "العنوان",
    "content": "المحتوى",
    "wilaya": "الولاية",
    "city": "المدينة",
    "status": "الحالة",
}


def _translate_validation_error(err: dict) -> str:
    """تحويل رسالة خطأ Pydantic التقنية إلى رسالة عربية."""
    field = str(err.get("loc", [""])[-1]) if err.get("loc") else ""
    label = _FIELD_LABELS.get(field, field)
    error_type = err.get("type", "")

    translations = {
        "missing": f"الحقل '{label}' مطلوب",
        "string_too_short": f"قيمة '{label}' قصيرة جداً",
        "string_too_long": f"قيمة '{label}' طويلة جداً",
        "value_error": err.get("msg", "قيمة غير صالحة").replace("Value error, ", ""),
        "int_parsing": f"يجب أن يكون '{label}' عدداً صحيحاً",
        "float_parsing": f"يجب أن يكون '{label}' رقماً",
        "bool_parsing": f"يجب أن يكون '{label}' قيمة منطقية",
        "date_from_datetime_parsing": f"تاريخ '{label}' غير صالح",
        "json_invalid": "صيغة البيانات المُرسلة غير صالحة",
        "greater_than_equal": f"يجب أن تكون قيمة '{label}' أكبر أو مساوية للحد الأدنى",
        "less_than_equal": f"يجب أن تكون قيمة '{label}' أصغر أو مساوية للحد الأعلى",
    }

    if error_type in translations:
        return translations[error_type]

    msg = err.get("msg", "")
    return msg.replace("Field required", f"الحقل '{label}' مطلوب")


def wants_json(request: Request) -> bool:
    """
    هل يطلب العميل JSON أم صفحة HTML؟

    نحتاج هذا التمييز لأن نفس الاستثناء يُعرض بطريقتين: رسالة JSON
    للـ API، وصفحة خطأ جميلة للواجهة.

    القاعدة: مسار ``/api/`` دائماً JSON. وخارجه، نُرجع True **فقط** عند
    طلب صريح لـ JSON -- لأن ``*/*`` (الافتراضي في أدوات الاختبار و
    بعض العملاء) لا يهمّه النوع، والافتراض الآمن هنا هو إظهار صفحة
    HTML لا رسالة JSON.
    """
    if request.url.path.startswith("/api/"):
        return True

    accept = request.headers.get("accept", "")
    if not accept:
        return False
    return "application/json" in accept and "text/html" not in accept


def redirect_to_login(request: Request) -> RedirectResponse:
    """إعادة توجيه المستخدم غير المسجَّل إلى صفحة الدخول."""
    from urllib.parse import urlencode

    next_url = str(request.url.path)
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(
        url=f"/auth/login?{urlencode({'next': next_url})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )