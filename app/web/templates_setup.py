"""
طبقة الويب (Web Layer)
======================
تجهيز محرّك قوالب Jinja2 مع دوال مساعدة عربية جاهزة للواجهة.

كل دالة تنسيق هنا بدل تكرارها في كل قالب: هكذا يبقى التنسيق متسقاً،
وتغييره مرة واحدة يسري على المنصة كلها.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates
from markupsafe import escape

from app.core.config import settings
from app.core.rbac import permissions_for, role_label
from app.core.db import utcnow

# =====================================================================
#  محرّك القوالب
# =====================================================================
templates = Jinja2Templates(directory=str(settings.templates_dir))


# =====================================================================
#  دوال التنسيق
# =====================================================================
def money(value: Any, currency: str = "دج") -> str:
    """تنسيق مبلغ مالي: ``2 500.00 دج``."""
    try:
        return f"{float(value or 0):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"0.00 {currency}"


def money_short(value: Any, currency: str = "دج") -> str:
    """مبلغ مختصر: ``2 500 دج`` أو ``1.2 م دج``."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return f"0 {currency}"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f} م{currency}"
    if abs(number) >= 10_000:
        return f"{number / 1000:.1f} ألف{currency}"
    return f"{number:,.0f} {currency}"


def number(value: Any) -> str:
    """رقم بفواصل الآلاف."""
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "0"


def percent(value: Any, decimals: int = 1) -> str:
    """نسبة مئوية: ``87.5%``."""
    try:
        return f"{float(value or 0):.{decimals}f}%"
    except (TypeError, ValueError):
        return "0%"


def arabic_date(value: Any, fmt: str = "%d/%m/%Y") -> str:
    """تاريخ بصيغة يوم/شهر/سنة."""
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value.strftime(fmt)
    return str(value)


def arabic_datetime(value: Any) -> str:
    """تاريخ ووقت."""
    if not value:
        return "-"
    if isinstance(value, dt.datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, dt.date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def time_only(value: Any) -> str:
    """وقت بصيغة 24 ساعة."""
    if isinstance(value, dt.time):
        return value.strftime("%H:%M")
    return str(value) if value else "-"


ARABIC_MONTHS = [
    "جانفي", "فيفري", "مارس", "أفريل", "ماي", "يونيو",
    "جويلية", "أوت", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]

ARABIC_DAYS = [
    "الإثنين", "الثلاثاء", "الأربعاء", "الخميس",
    "الجمعة", "السبت", "الأحد",
]


def arabic_month(value: Any) -> str:
    """اسم الشهر بالعربية."""
    if isinstance(value, (dt.date, dt.datetime)):
        return ARABIC_MONTHS[value.month - 1]
    return str(value)


def arabic_day(value: Any) -> str:
    """اسم اليوم بالعربية."""
    if isinstance(value, (dt.date, dt.datetime)):
        return ARABIC_DAYS[value.weekday()]
    return str(value)


def relative_time(value: Any) -> str:
    """وقت نسبي: «قبل 5 دقائق»، «أمس»، «قبل 3 أيام»."""
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, dt.datetime):
        return str(value)

    seconds = (utcnow() - value).total_seconds()
    if seconds < 0:
        return "بعد قليل"
    if seconds < 60:
        return "الآن"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return "قبل دقيقة" if minutes == 1 else (
            "قبل دقيقتين" if minutes == 2 else f"قبل {minutes} دقائق"
        )
    if seconds < 86400:
        hours = int(seconds // 3600)
        return "قبل ساعة" if hours == 1 else f"قبل {hours} ساعات"
    if seconds < 172800:
        return "أمس"
    days = int(seconds // 86400)
    if days < 30:
        return "قبل يوم" if days == 1 else (
            "قبل يومين" if days == 2 else f"قبل {days} أيام"
        )
    if days < 365:
        months = days // 30
        return "قبل شهر" if months == 1 else f"قبل {months} أشهر"
    years = days // 365
    return "قبل سنة" if years == 1 else f"قبل {years} سنوات"


def truncate(text: Any, length: int = 80) -> str:
    """قصّ نص طويل مع نقاط."""
    text = str(text or "")
    return text if len(text) <= length else text[:length].rstrip() + "..."


def initials(name: Any) -> str:
    """الأحرف الأولى من الاسم للصورة الرمزية."""
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "؟"
    if len(parts) == 1:
        return parts[0][0]
    return f"{parts[0][0]}{parts[-1][0]}"


_STATUS_COLORS = {
    "active": "success", "present": "success", "paid": "success",
    "completed": "success", "approved": "success", "confirmed": "success",
    "pending": "warning", "late": "warning",
    "scheduled": "info", "partial": "info", "processing": "info",
    "excused": "info", "sent": "info",
    "unpaid": "danger", "overdue": "danger", "absent": "danger",
    "rejected": "danger", "suspended": "danger", "failed": "danger",
    "cancelled": "secondary", "expired": "secondary",
    "inactive": "secondary", "draft": "secondary",
}


def status_badge(status: Any) -> str:
    """الحالة -> صنف CSS موحّد (الألوان معرّفة في ملف CSS فقط)."""
    return _STATUS_COLORS.get(str(status), "secondary")


_STATUS_LABELS = {
    "active": "نشط", "inactive": "غير نشط", "pending": "قيد المراجعة",
    "suspended": "موقوف", "expired": "منتهٍ", "rejected": "مرفوض",
    "present": "حاضر", "absent": "غائب", "late": "متأخر", "excused": "بعذر",
    "paid": "مدفوعة", "unpaid": "غير مدفوعة", "partial": "مدفوعة جزئياً",
    "overdue": "متأخرة", "cancelled": "ملغاة",
    "approved": "مقبول", "completed": "مكتملة", "failed": "فشلت",
    "scheduled": "مجدولة", "ongoing": "جارية",
    "male": "ذكر", "female": "أنثى",
    "beginner": "مبتدئ", "amateur": "هاوٍ", "advanced": "متقدم", "elite": "نخبة",
    "club_admin": "مدير النادي", "coach": "مدرب", "receptionist": "إداري",
    "parent": "ولي أمر", "athlete": "رياضي", "super_admin": "مدير المنصة",
}


def status_label(status: Any) -> str:
    """الحالة -> اسم عربي."""
    return _STATUS_LABELS.get(str(status), str(status))


# =====================================================================
#  الصلاحيات والمساعدات
# =====================================================================
def has_permission(user: Any, permission: str) -> bool:
    """فحص صلاحية داخل القوالب."""
    from app.core.rbac import has_permission as _has

    if user is None:
        return False
    return _has(getattr(user, "role", None), permission)


def user_can_any(user: Any, *permissions: str) -> bool:
    """فحص أي من الصلاحيات."""
    return any(has_permission(user, p) for p in permissions)


def query_string(request, **overrides) -> str:
    """بناء سلسلة استعلام مع تعديل بعض المعاملات."""
    params = {k: v for k, v in request.query_params.items()}
    for key, value in overrides.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    return f"?{urlencode(params)}" if params else ""


def page_range(current: int, total: int, window: int = 2) -> list[int]:
    """نطاق صفحات للترقيم (يمنع شريطاً ضخماً)."""
    if total <= 1:
        return [1] if total == 1 else []
    start = max(1, current - window)
    end = min(total, current + window)
    if start > 2:
        start = max(2, start - 1)
    if end < total - 1:
        end = min(total - 1, end + 1)
    return list(range(start, end + 1))


def options(items: Any, value_attr: str = "id", label_attr: str = "name") -> list[tuple]:
    """
    تحويل قائمة كائنات إلى خيارات ``<option>``.

    الاستخدام::

        {{ ui.select('sport', 'الرياضة', options(sports, 'id', 'name_ar')) }}

    نبنيها في Python بدل تركيبها داخل القالب، لأن ``zip`` ليست فلتراً
    متاحاً في Jinja -- والبناء في القالب يجعل منطق العرض غير قابل لإعادة
    الاستخدام.
    """
    if not items:
        return []
    result: list[tuple] = []
    for item in items:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            result.append((item[0], item[1]))
            continue
        value = getattr(item, value_attr, None)
        label = getattr(item, label_attr, None)
        if value is not None:
            result.append((value, label if label is not None else str(value)))
    return result


# =====================================================================
#  تسجيل الدوال في محرّك القوالب
# =====================================================================
templates.env.filters.update(
    money=money,
    money_short=money_short,
    number=number,
    percent=percent,
    date=arabic_date,
    datetime=arabic_datetime,
    time=time_only,
    month=arabic_month,
    day=arabic_day,
    relative=relative_time,
    truncate=truncate,
    status_badge=status_badge,
    status_label=status_label,
    # دوال مُسجَّلة كـ *فلاتر* أيضاً لتعمل في الاتجاهين داخل القوالب:
    # ``{{ name|initials }}`` و ``{{ initials(name) }}`` كلاهما صحيح.
    initials=initials,
    role_label=role_label,
)

templates.env.globals.update(
    APP_NAME=settings.APP_NAME,
    APP_TAGLINE=settings.APP_TAGLINE,
    APP_DESIGNER=settings.APP_DESIGNER,
    APP_VERSION=settings.APP_VERSION,
    today=dt.date.today,
    now=dt.datetime.now,
    initials=initials,
    has_permission=has_permission,
    user_can=has_permission,
    user_can_any=user_can_any,
    role_label=role_label,
    role_permissions=permissions_for,
    query_string=query_string,
    page_range=page_range,
    options=options,
    escape=escape,
    ARABIC_MONTHS=ARABIC_MONTHS,
    ARABIC_DAYS=ARABIC_DAYS,
)

__all__ = ["templates"]