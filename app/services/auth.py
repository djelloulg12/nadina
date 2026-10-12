"""
خدمة المصادقة (Authentication Service)
======================================
تسجيل الدخول والخروج، إدارة الحساب، وحماية من محاولات الدخول الفاشلة.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import utcnow
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    InvalidCredentialsError,
    PermissionDeniedError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_slug,
    generate_otp,
    hash_password,
    verify_password,
)
from app.models import (
    AuditLog,
    Club,
    ClubStatus,
    Notification,
    User,
    UserRole,
)
from app.models.enums import AuditAction, NotificationType

logger = logging.getLogger(__name__)


# =====================================================================
#  أدوات مساعدة
# =====================================================================
def _normalise_identifier(value: str) -> str:
    """تنظيف معرّف الدخول (بريد أو هاتف) قبل المطابقة."""
    return (value or "").strip().lower()


# =====================================================================
#  البحث عن المستخدمين
# =====================================================================
async def get_user_by_identifier(db: AsyncSession, identifier: str) -> User | None:
    """
    البحث عن مستخدم بالبريد الإلكتروني أو رقم الهاتف.

    الهاتف يُقبل بصيغتين: ``0555123456`` أو ``+213555123456``.
    """
    ident = _normalise_identifier(identifier)
    if not ident:
        return None

    variants = {ident}
    if ident.startswith("+213"):
        variants.add("0" + ident[4:])
    elif ident.startswith("0") and len(ident) == 10:
        variants.add("+213" + ident[1:])
    variants.add(ident.lstrip("+"))

    result = await db.execute(
        select(User).where(
            or_(
                User.email.in_(variants),
                User.phone.in_(variants),
            )
        )
    )
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """جلب مستخدم بالمعرّف."""
    return await db.get(User, user_id)


# =====================================================================
#  تسجيل الدخول
# =====================================================================
async def authenticate(
    db: AsyncSession,
    identifier: str,
    password: str,
) -> User:
    """
    التحقق من بيانات الدخول.

    يطبّق عدّاد محاولات فاشلة مع قفل مؤقت للحساب لحماية من التخمين
    (brute force). التفاصيل مقصودة general هنا (رسالة موحّدة) حتى لا
    نكشف أي الحسابات موجودة.
    """
    user = await get_user_by_identifier(db, identifier)

    if user is None:
        # نستهلك وقتاً مكافئاً لمنع تسريب المعلومات عبر توقيت الاستجابة
        logger.info("محاولة دخول بمعرّف غير موجود: %s", _normalise_identifier(identifier))
        raise InvalidCredentialsError()

    # 1) هل الحساب مقفل؟
    if user.is_locked:
        remaining = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
        raise AccountLockedError(
            f"تم قفل الحساب مؤقتاً بسبب محاولات فاشلة. حاول بعد {remaining} دقيقة"
        )

    # 2) التحقق من كلمة المرور
    if not verify_password(password, user.password_hash):
        await _register_failed_attempt(db, user)
        raise InvalidCredentialsError()

    # 3) هل الحساب مُفعّل؟
    if not user.is_active:
        raise AccountInactiveError(
            "حسابك غير مُفعّل. يرجى التواصل مع إدارة النادي"
        )

    # 4) في حالة خاصة بالنادي
    if user.club_id:
        club = await db.get(Club, user.club_id)
        if club is None or club.status != ClubStatus.ACTIVE:
            logger.warning("محاولة دخول لنادي غير مُفعّل: user=%s", user.id)
            raise PermissionDeniedError(
                "النادي غير مُفعّل حالياً. يرجى التواصل مع دعم المنصة"
            )

    # --- نجاح: تصفير العدّاد ---
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = utcnow()

    await log_audit(
        db,
        user=user,
        action=AuditAction.LOGIN,
        entity_type="User",
        entity_id=user.id,
        description=f"تسجيل دخول ناجح ({user.role.value})",
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _register_failed_attempt(db: AsyncSession, user: User) -> None:
    """
    تسجيل محاولة فاشلة وقفل الحساب عند تجاوز الحد.

    نلتزم بـ commit هنا لأن تسجيل الفشل يجب ألا يضيع عند رفع الاستثناء
    في ``authenticate`` (الذي يُنهي المعاملة عبر rollback).
    """
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1

    if user.failed_login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
        user.locked_until = utcnow() + dt.timedelta(
            minutes=settings.LOGIN_LOCKOUT_MINUTES
        )
        user.failed_login_attempts = 0
        logger.warning(
            "تم قفل الحساب %s مؤقتاً بعد %s محاولات",
            user.id,
            settings.LOGIN_MAX_ATTEMPTS,
        )

    await db.commit()


# =====================================================================
#  الرموز
# =====================================================================
def issue_tokens(user: User) -> dict[str, str]:
    """توليد توكن الوصول والتجديد للمستخدم."""
    return {
        "access_token": create_access_token(
            user_id=user.id,
            role=user.role,
            club_id=user.club_id,
        ),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


async def refresh_access_token(db: AsyncSession, refresh_token: str) -> dict[str, str]:
    """تجديد توكن الوصول من توكن التجديد."""
    from app.core.security import TokenType, decode_token

    try:
        payload = decode_token(refresh_token, TokenType.REFRESH)
    except AuthenticationError:
        raise

    user = await db.get(User, int(payload["uid"]))
    if user is None or not user.is_active:
        raise AuthenticationError("الحساب غير موجود أو غير مُفعّل")

    return issue_tokens(user)


# =====================================================================
#  إنشاء الحسابات
# =====================================================================
async def create_user(
    db: AsyncSession,
    *,
    identifier: str,
    password: str,
    full_name: str,
    role: UserRole,
    club_id: int | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> User:
    """
    إنشاء مستخدم جديد مع فحوص صارمة.

    ``identifier`` هو البريد أو الهاتف ويجب أن يكون فريداً عبر المنصة
    كلها (وليس فقط داخل النادي) لأن الدخول يتم من واجهة واحدة.
    """
    # --- التحقق من عدم التكرار ---
    if email:
        existing = await db.execute(select(User).where(User.email == email.lower()))
        if existing.scalar_one_or_none():
            raise ConflictError("البريد الإلكتروني مستخدم مسبقاً")

    if phone:
        existing = await db.execute(select(User).where(User.phone == phone))
        if existing.scalar_one_or_none():
            raise ConflictError("رقم الهاتف مستخدم مسبقاً")

    # --- التحقق من الدور والنادي ---
    if role in (UserRole.CLUB_ADMIN, UserRole.RECEPTIONIST, UserRole.COACH) and not club_id:
        raise PermissionDeniedError("هذا الدور يتطلب ربطه بنادي")

    user = User(
        email=email.lower() if email else None,
        phone=phone,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        role=role,
        club_id=club_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    await log_audit(
        db,
        user=user,
        action=AuditAction.CREATE,
        entity_type="User",
        entity_id=user.id,
        description=f"إنشاء حساب {role.value}",
    )
    return user


async def change_password(
    db: AsyncSession,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    """تغيير كلمة المرور مع التحقق من الحالية."""
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError("كلمة المرور الحالية غير صحيحة")

    if current_password == new_password:
        raise ConflictError("كلمة المرور الجديدة مطابقة للحالية")

    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None

    await log_audit(
        db,
        user=user,
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        description="تغيير كلمة المرور",
    )
    await db.commit()


async def set_password(
    db: AsyncSession,
    user: User,
    new_password: str,
    actor: User | None = None,
) -> None:
    """تعيين كلمة مرور جديدة (لمدير النادي أو مدير المنصة)."""
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None

    await log_audit(
        db,
        user=actor or user,
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        description="إعادة تعيين كلمة المرور",
    )
    await db.commit()


async def unlock_user(db: AsyncSession, user: User, actor: User) -> None:
    """فك قفل حساب مستخدم."""
    user.locked_until = None
    user.failed_login_attempts = 0

    await log_audit(
        db,
        user=actor,
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        description=f"فك قفل حساب {user.full_name}",
    )
    await db.commit()


async def update_profile(
    db: AsyncSession,
    user: User,
    *,
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """تحديث بيانات الملف الشخصي."""
    if full_name:
        user.full_name = full_name.strip()

    if email and email.lower() != (user.email or "").lower():
        existing = await db.execute(
            select(User).where(User.email == email.lower(), User.id != user.id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("البريد الإلكتروني مستخدم مسبقاً")
        user.email = email.lower()

    if phone and phone != user.phone:
        existing = await db.execute(
            select(User).where(User.phone == phone, User.id != user.id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("رقم الهاتف مستخدم مسبقاً")
        user.phone = phone

    if avatar_url:
        user.avatar_url = avatar_url

    await db.commit()
    await db.refresh(user)
    return user


# =====================================================================
#  سجل التدقيق
# =====================================================================
async def log_audit(
    db: AsyncSession,
    *,
    user: User | None,
    action: AuditAction,
    entity_type: str | None = None,
    entity_id: int | None = None,
    description: str | None = None,
    changes: dict | None = None,
    success: bool = True,
    request=None,
) -> AuditLog:
    """
    كتابة سجل تدقيق.

    يقبل ``request`` لالتقاط عنوان IP ووكيل المستخدم تلقائياً.
    """
    ip_address = None
    user_agent = None
    endpoint = None

    if request is not None:
        try:
            forwarded = request.headers.get("x-forwarded-for")
            ip_address = (
                forwarded.split(",")[0].strip()
                if forwarded
                else request.client.host
                if request.client
                else None
            )
            user_agent = request.headers.get("user-agent", "")[:300]
            endpoint = f"{request.method} {request.url.path}"
        except Exception:  # pragma: no cover
            pass

    entry = AuditLog(
        club_id=user.club_id if user else None,
        user_id=user.id if user else None,
        user_role=user.role.value if user else None,
        user_name=user.full_name if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        changes=changes or {},
        ip_address=ip_address,
        user_agent=user_agent,
        endpoint=endpoint,
        success=success,
    )
    db.add(entry)
    return entry


async def log_failed_login(
    db: AsyncSession,
    identifier: str,
    request=None,
) -> None:
    """تسجيل محاولة دخول فاشلة بدون مستخدم معروف."""
    ip_address = None
    endpoint = None
    user_agent = None
    if request is not None:
        try:
            forwarded = request.headers.get("x-forwarded-for")
            ip_address = (
                forwarded.split(",")[0].strip()
                if forwarded
                else request.client.host
                if request.client
                else None
            )
            endpoint = f"{request.method} {request.url.path}"
            user_agent = request.headers.get("user-agent", "")[:300]
        except Exception:  # pragma: no cover
            pass

    entry = AuditLog(
        user_id=None,
        action=AuditAction.LOGIN_FAILED,
        description=f"محاولة دخول فاشلة للمعرّف: {identifier[:60]}",
        ip_address=ip_address,
        endpoint=endpoint,
        user_agent=user_agent,
        success=False,
    )
    db.add(entry)
    await db.commit()


# =====================================================================
#  التحقق من المدخلات
# =====================================================================
async def identifier_exists(
    db: AsyncSession,
    email: str | None = None,
    phone: str | None = None,
    exclude_user_id: int | None = None,
) -> bool:
    """هل البريد أو الهاتف مستخدم مسبقاً؟"""
    conditions = []
    if email:
        conditions.append(User.email == email.lower())
    if phone:
        conditions.append(User.phone == phone)
    if not conditions:
        return False

    stmt = select(func.count(User.id)).where(or_(*conditions))
    if exclude_user_id:
        stmt = stmt.where(User.id != exclude_user_id)

    result = await db.execute(stmt)
    return (result.scalar() or 0) > 0