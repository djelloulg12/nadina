"""
اعتماديات FastAPI (Dependencies)
================================
هنا يُحلّ هوية المستخدم، وتُبنى **سياق المستأجر** الذي تعتمد عليه كل
طبقة الأعمال في المنصة.

سلسلة التنفيذ لكل طلب:

1. :func:`get_db`            -> جلسة قاعدة البيانات
2. :func:`resolve_session`   -> قراءة الكوكي/التوكن وتخمين المستخدم
3. :func:`build_context`     -> إنشاء ``TenantContext`` وكتابته في ``ContextVar``
4. :func:`get_current_user`  -> المستخدم الجاهز للخدمة

من خلال هذه السلسلة تصبح طبقة الخدمات "أعمى" عن مصدر الهوية: ترى كائن
:class:`~app.models.platform.User` وسياق عزل جاهزاً، ولا تحتاج أن تعرف
هل جاء الطلب من متصفح أو تطبيق أو أداة اختبار.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    PermissionDeniedError,
    SubscriptionRequiredError,
)
from app.core.rbac import has_permission
from app.core.security import (
    TokenType,
    decode_token,
    read_session_token,
)
from app.core.tenancy import (
    TenantContext,
    build_tenant_context,
    clear_tenant_context,
    get_tenant_context,
    set_tenant_context,
)
from app.models import (
    Club,
    ClubStatus,
    ParentLink,
    StaffMember,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)


# =====================================================================
# 1) استخراج الرمز من الطلب
# =====================================================================
SESSION_MARKER = "uid"


def _extract_bearer_token(request: Request) -> str | None:
    """قراءة ``Authorization: Bearer <token>``."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _extract_session_token(request: Request) -> str | None:
    """قراءة كوكي الجلسة الموقّع."""
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


async def resolve_user_id(request: Request) -> int | None:
    """
    تحديد معرّف المستخدم من الطلب دون تحميل الكائن كاملاً.

    يدعم آليتين:
      * ``Authorization: Bearer <jwt>``  -> للـ REST API والتطبيقات.
      * كوكي الجلسة الموقّع               -> لواجهة الويب.

    يُعيد ``None`` عند غياب الرمز أو فساده. لا يرفع استثناءً -- لأن
    ``resolve_user_id`` تُستخدم أيضاً للمسارات التي تسمح بالزوار.
    """
    token = _extract_bearer_token(request)
    if token:
        try:
            payload = decode_token(token, TokenType.ACCESS)
            return int(payload["uid"])
        except AuthenticationError:
            return None
        except (KeyError, ValueError):
            return None

    cookie = _extract_session_token(request)
    if cookie:
        data = read_session_token(cookie)
        if data and SESSION_MARKER in data:
            try:
                return int(data[SESSION_MARKER])
            except (TypeError, ValueError):
                return None

    return None


# =====================================================================
# 2) بناء سياق المستأجر
# =====================================================================
async def _load_parent_scope(
    db: AsyncSession,
    user_id: int,
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """
    حساب نطاق الولي: أبناؤه، أنديتهم، وأفواجهم.

    هذا يحقق "حساب الولي الموحد" -- الولي يرى أبناؤه في **كل** الأندية
    التي يسجّلون فيها، دون أن يرى رياضيّ غيري في نفس النادي.
    """
    rows = await db.execute(
        select(ParentLink.athlete_id, ParentLink.club_id)
        .where(ParentLink.parent_id == user_id)
    )
    links = rows.all()
    athlete_ids = tuple({r[0] for r in links})
    club_ids = tuple({r[1] for r in links})
    if not athlete_ids:
        return (), (), ()

    from app.models import GroupAthlete

    group_rows = await db.execute(
        select(GroupAthlete.group_id).where(
            GroupAthlete.athlete_id.in_(athlete_ids),
            GroupAthlete.is_active.is_(True),
        )
    )
    group_ids = tuple({r[0] for r in group_rows.all()})
    return athlete_ids, club_ids, group_ids


async def establish_tenant_context(
    db: AsyncSession,
    user: User,
    request: Request | None = None,
) -> TenantContext:
    """
    بناء سياق العزل المناسب لهذا المستخدم.

    * مدير المنصة  -> نطاق عام (يرى كل الأندية).
    * طاقم النادي -> نطاق ``club_id`` واحد.
    * الولي       -> نطاق مبني على أبنائه عبر أندية متعددة.
    * الرياضي     -> نطاق مبني على ملفه الشخصي.
    """
    if user.role == UserRole.SUPER_ADMIN:
        ctx = TenantContext(
            user_id=user.id,
            role=user.role,
            club_id=None,
            is_platform_admin=True,
        )
    elif user.role == UserRole.PARENT:
        athlete_ids, club_ids, group_ids = await _load_parent_scope(db, user.id)
        ctx = TenantContext(
            user_id=user.id,
            role=user.role,
            club_id=None,
            athlete_ids=athlete_ids,
            child_club_ids=club_ids,
            child_group_ids=group_ids,
        )
    elif user.role == UserRole.ATHLETE and user.club_id:
        from app.models import Athlete, GroupAthlete

        rows = await db.execute(
            select(Athlete.id, Athlete.club_id)
            .join(GroupAthlete, GroupAthlete.athlete_id == Athlete.id)
            .where(Athlete.user_id == user.id, GroupAthlete.is_active.is_(True))
            .limit(1)
        )
        row = rows.first()
        athlete_id = row[0] if row else None
        group_rows = await db.execute(
            select(GroupAthlete.group_id).where(
                GroupAthlete.athlete_id == athlete_id,
                GroupAthlete.is_active.is_(True),
            )
        ) if athlete_id else None
        group_ids = tuple({r[0] for r in group_rows.all()}) if group_rows else ()

        ctx = TenantContext(
            user_id=user.id,
            role=user.role,
            club_id=row[1] if row else user.club_id,
            athlete_ids=(athlete_id,) if athlete_id else (),
            child_group_ids=group_ids,
        )
    else:
        ctx = build_tenant_context(user.id, user.role, user.club_id)

    clear_tenant_context()
    set_tenant_context(ctx)
    if request is not None:
        request.state.tenant_context = ctx
    return ctx


# =====================================================================
# 3) الاعتماديات الأساسية
# =====================================================================
async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    المستخدم الحالي إن وُجد، بدون رفع خطأ.

    يُستخدم في المسارات التي تعمل للزوار والمستخدمين معاً (مثل البحث في
    البوابة العامة أو عرض صفحة النادي).
    """
    user_id = await resolve_user_id(request)
    if user_id is None:
        return None

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    if user.is_locked:
        logger.warning("محاولة استخدام حساب مقفل: %s", user.id)
        return None

    await establish_tenant_context(db, user, request)
    request.state.user = user
    return user


async def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    """المستخدم الحالي، مع رفع خطأ 401 إن لم يكن مسجَّلاً."""
    if user is None:
        raise AuthenticationError("يجب تسجيل الدخول للمتابعة")
    return user


async def get_active_user(
    user: User = Depends(get_current_user),
) -> User:
    """المستخدم الحالي مع التأكد أن حسابه مُفعّل (مستخدَم في الصور الحساسة)."""
    if not user.is_active:
        raise AccountInactiveError()
    if user.is_locked:
        raise AccountLockedError()
    return user


# =====================================================================
# 4) اعتماديات الأدوار والصلاحيات
# =====================================================================
def require_roles(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    """
    إنشاء اعتماد يفرض أن يكون المستخدم من أحد هذه الأدوار.

    الاستخدام::

        @router.get("/admin-only")
        async def view(user: User = Depends(require_roles(UserRole.CLUB_ADMIN))):
            ...
    """

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            from app.core.rbac import role_label

            allowed = "، ".join(role_label(r) for r in roles)
            raise PermissionDeniedError(
                f"هذه الصفحة مخصصة لـ: {allowed}. أنت مسجَّل كـ: {role_label(user.role)}"
            )
        return user

    return _dependency


def require_permission(permission: str) -> Callable[..., Coroutine[Any, Any, User]]:
    """
    إنشاء اعتماد يفرض امتلاك رمز صلاحية محدد.

    مثال::

        @router.delete("/athletes/{id}")
        async def remove(id: int, user = Depends(require_permission("athletes.delete"))):
            ...
    """

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise PermissionDeniedError(
                f"ليست لديك صلاحية: {permission}"
            )
        return user

    return _dependency


def require_any_permission(*permissions: str) -> Callable[..., Coroutine[Any, Any, User]]:
    """اعتماد يفرض امتلاك أياً من هذه الصلاحيات."""

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if not any(has_permission(user.role, p) for p in permissions):
            raise PermissionDeniedError("ليست لديك صلاحية لتنفيذ هذا الإجراء")
        return user

    return _dependency


# =====================================================================
# 5) اعتماديات النادي
# =====================================================================
async def get_current_club(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Club:
    """
    النادي الحالي للمستخدم.

    لمدير المنصة بلا نادي محدّد، نبحث عن أول نادي نشط أو ننشئ سياقاً
    فارغاً -- والقرار يُتخذ في :func:`require_club_context`.
    """
    if user.club_id is None:
        if user.role == UserRole.SUPER_ADMIN:
            raise PermissionDeniedError(
                "يرجى تحديد النادي الذي تريد إدارته"
            )
        raise PermissionDeniedError("حسابك غير مرتبط بأي نادي")

    club = await db.get(Club, user.club_id)
    if club is None:
        raise PermissionDeniedError("النادي المرتبط بحسابك غير موجود")
    return club


async def require_active_club(
    club: Club = Depends(get_current_club),
) -> Club:
    """
    النادي الحالي مع التحقق من صلاحية التشغيل.

    يمنع طاقم النادي من العمل إذا انتهى اشتراك المنصة أو أُوقف --
    وهو ما يحمي المنصة من الاستخدام غير المدفوع.
    """
    if club.status != ClubStatus.ACTIVE:
        raise SubscriptionRequiredError(
            f"حالة النادي: {club.status.value}. يرجى التواصل مع دعم المنصة"
        )
    if club.is_subscription_expired:
        raise SubscriptionRequiredError(
            "انتهى اشتراك النادي في المنصة بتاريخ "
            f"{club.subscription_end}. يرجى التجديد للمتابعة"
        )
    return club


async def get_current_staff(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StaffMember:
    """
    سجل طاقة النادي للمستخدم الحالي.

    يُستخدم للتحقق من أن المستخدم فعلاً عضو في طاقم النادي، ولتحديد
    أفواده في حالة المدرب.
    """
    if user.club_id is None:
        raise PermissionDeniedError("حسابك غير مرتبط بأي نادي")

    result = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id,
            StaffMember.club_id == user.club_id,
            StaffMember.is_active.is_(True),
        )
    )
    staff = result.scalar_one_or_none()
    if staff is None:
        raise PermissionDeniedError("سجلك غير موجود في طاقم النادي")
    return staff


async def require_coach(
    staff: StaffMember = Depends(get_current_staff),
) -> StaffMember:
    """يتأكد أن المستخدم مدرب قبل عرض أدوات التدريب."""
    if staff.role != UserRole.COACH:
        raise PermissionDeniedError("هذه الأدوات مخصصة للمدربين فقط")
    return staff


async def require_club_manager(
    staff: StaffMember = Depends(get_current_staff),
) -> StaffMember:
    """يتأكد أن المستخدم مدير النادي أو إداري بصلاحيات إدارية."""
    if staff.role not in (UserRole.CLUB_ADMIN,):
        raise PermissionDeniedError("هذا الإجراء مخصص لمدير النادي")
    return staff


# =====================================================================
# 6) أدوات مساعدة للقوالب والمسارات
# =====================================================================
async def get_optional_user_from_session(request: Request) -> User | None:
    """قراءة المستخدم من الجلسة دون إنشاء جلسة قاعدة بيانات (للقوالب)."""
    return getattr(request.state, "user", None)


def current_context() -> TenantContext:
    """
    سياق المستأجر الحالي، مع قيمة افتراضية للزوار.

    دالة مريحة للقوالب التي لا تريد التعامل مع ``None``.
    """
    return get_tenant_context() or TenantContext(
        user_id=None, role=None, club_id=None
    )


def user_can(user: User | None, permission: str) -> bool:
    """
    فحص صلاحية داخل القوالب.

    الاستخدام في Jinja2::

        {% if user_can(user, 'finance.read') %} ... {% endif %}
    """
    if user is None:
        return False
    return has_permission(user.role, permission)


async def get_coach_group_ids(
    db: AsyncSession,
    staff: StaffMember,
) -> list[int]:
    """معرّفات الأفواج التي يدرّبها هذا المدرب."""
    from app.models import Group

    result = await db.execute(
        select(Group.id).where(
            Group.coach_id == staff.id,
            Group.is_active.is_(True),
        )
    )
    return [r[0] for r in result.all()]


async def get_unread_notification_count(
    db: AsyncSession,
    user: User,
) -> int:
    """عدد الإشعارات غير المقروءة (يظهر بجانب جرس الإشعارات)."""
    from sqlalchemy import func

    from app.models import Notification

    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
    )
    return result.scalar() or 0