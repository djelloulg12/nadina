"""
مساعدات طبقة الويب (Web Helpers)
================================
دوال مشتركة لصفحات HTML: جلب المستخدم الحالي، بناء سياق القالب،
وتوجيه كل رتبة إلى مساحتها الخاصة.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import role_label
from app.core.security import create_session_token, read_session_token
from app.models import User, UserRole

logger = logging.getLogger(__name__)


# =====================================================================
#  الجلسات (كوكي موقّع)
# =====================================================================
SESSION_MARKER = "uid"


def set_session_cookie(response, user: User) -> None:
    """كتابة كوكي جلسة موقّع للمستخدم."""
    token = create_session_token(
        {
            SESSION_MARKER: user.id,
            "role": user.role.value,
            "club_id": user.club_id,
        }
    )
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=True,          # لا يمكن الوصول للكوكي عبر JavaScript
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path="/",
    )


def clear_session_cookie(response) -> None:
    """حذف كوكي الجلسة."""
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def read_session_user_id(request: Request) -> int | None:
    """قراءة معرّف المستخدم من كوكي الجلسة."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        return None
    data = read_session_token(token)
    if not data or SESSION_MARKER not in data:
        return None
    try:
        return int(data[SESSION_MARKER])
    except (TypeError, ValueError):
        return None


async def get_session_user(db: AsyncSession, request: Request) -> User | None:
    """
    جلب المستخدم الحالي من الجلسة.

    يُعيد ``None`` عند غياب الجلسة أو تعطّلها (لا يرفع استثناءً --
    لأن كثيراً من الصفحات تعمل للزوار أيضاً).
    """
    user_id = read_session_user_id(request)
    if user_id is None:
        return None

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    return user


async def current_user_or_redirect(
    db: AsyncSession, request: Request
) -> User:
    """جلب المستخدم الحالي أو إعادة التوجيه لصفحة الدخول."""
    from fastapi.responses import RedirectResponse
    from urllib.parse import urlencode

    user = await get_session_user(db, request)
    if user is None:
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(
            url=f"/auth/login?{urlencode({'next': next_url})}",
            status_code=303,
        )
    return user


async def current_club_or_redirect(
    db: AsyncSession, request: Request
) -> tuple[User, Any]:
    """جلب المستخدم وناديه، أو إعادة التوجيه إن لم يكن له نادي."""
    from fastapi.responses import RedirectResponse

    from app.models import Club

    user = await current_user_or_redirect(db, request)
    if not isinstance(user, RedirectResponse):  # pragma: no cover
        return user, None

    if user.club_id is None:
        return user, None

    club = await db.get(Club, user.club_id)
    return user, club


# =====================================================================
#  توجيه حسب الرتبة
# =====================================================================
HOME_ROUTES: dict[UserRole, str] = {
    UserRole.SUPER_ADMIN: "/admin",
    UserRole.CLUB_ADMIN: "/club",
    UserRole.RECEPTIONIST: "/club",
    UserRole.COACH: "/coach",
    UserRole.PARENT: "/parent",
    UserRole.ATHLETE: "/parent",
}


def resolve_home_route(user: User) -> str:
    """الصفحة الرئيسية المناسبة لكل رتبة."""
    return HOME_ROUTES.get(user.role, "/")


def ensure_role(user: User, *roles: UserRole) -> str | None:
    """
    التأكد أن المستخدم من الأدوار المطلوبة.

    يُعيد رسالة خطأ عربية أو ``None`` عند النجاح.
    """
    if user.role not in roles:
        allowed = "، ".join(role_label(r) for r in roles)
        return (
            f"هذه الصفحة مخصصة لـ: {allowed}. "
            f"أنت مسجّل الدخول بحساب: {role_label(user.role)}"
        )
    return None


# =====================================================================
#  سياق القالب
# =====================================================================
async def page_context(
    request: Request,
    db: AsyncSession | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    السياق الأساسي لكل صفحات الواجهة.

    يمرَّر لكل قالب عبر ``**page_context`` في كل مسار، فتبقى كل الصفحات
    متسقة في الترويسة والجرس والثيم.
    """
    context: dict[str, Any] = {
        "request": request,
        "current_user": None,
        "current_club": None,
        "unread_notifications": 0,
        "APP_NAME": settings.APP_NAME,
        "APP_VERSION": settings.APP_VERSION,
        "active_nav": "",
    }

    if db is not None:
        try:
            user = await get_session_user(db, request)
        except Exception:  # pragma: no cover - لا نُفشل الصفحة بسبب الجلسات
            logger.debug("تعذّر قراءة الجلسة", exc_info=True)
            user = None

        if user is not None:
            context["current_user"] = user
            context["user_role_label"] = role_label(user.role)

            if user.club_id:
                from app.models import Club

                context["current_club"] = await db.get(Club, user.club_id)

            # عدّاد الإشعارات
            try:
                from app.services.notifications import unread_count

                context["unread_notifications"] = await unread_count(db, user.id)
            except Exception:  # pragma: no cover
                context["unread_notifications"] = 0

    context.update(extra)
    return context


def active(request: Request, name: str) -> str:
    """
    تحديد عنصر التنقل النشط.

    نقارن أول جزء من المسار، فيعمل التمييز على كل الصفحات الفرعية.
    """
    path = request.url.path
    if name == "home" and path == "/":
        return "active"
    return "active" if path.startswith(f"/{name}") else ""


# =====================================================================
#  رسائل الفلاش
# =====================================================================
def flash(request: Request) -> dict[str, str]:
    """
    رسائل الفلاش عبر الجلسات الموقّعة (بدون كوكي إضافي).

    نخزّنها في كوكي صغير يُقرأ مرة واحدة ثم يُمسح -- أبسط من جدول
    جلسات ولا يحتاج تخزيناً إضافياً.
    """
    from itsdangerous import URLSafeSerializer

    raw = request.cookies.get("mashia_flash")
    if not raw:
        return {}

    try:
        serializer = URLSafeSerializer(settings.SECRET_KEY, salt="mashia.flash.v1")
        return serializer.loads(raw) or {}
    except Exception:
        return {}


def with_flash(response, kind: str, message: str):  # noqa: ANN001, ANN202
    """إضافة رسالة فلاش إلى الاستجابة."""
    from itsdangerous import URLSafeSerializer

    serializer = URLSafeSerializer(settings.SECRET_KEY, salt="mashia.flash.v1")
    token = serializer.dumps({"kind": kind, "message": message})
    response.set_cookie(
        key="mashia_flash",
        value=token,
        max_age=30,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        path="/",
    )
    return response