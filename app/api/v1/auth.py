"""مسارات المصادقة (/api/v1/auth).

تغطي: تسجيل الدخول، الرموز، الملف الشخصي، تغيير كلمة المرور، والخروج.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_optional
from app.core.db import get_db
from app.core.exceptions import AuthenticationError
from app.core.rbac import has_permission, permissions_for, role_label
from app.core.security import TokenType, decode_token
from app.models import Club, User
from app.schemas import (
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["المصادقة"])


# =====================================================================
#  تسجيل الدخول
# =====================================================================
@router.post(
    "/login",
    response_model=TokenResponse,
    summary="تسجيل الدخول",
    responses={401: {"description": "بيانات الدخول غير صحيحة"}},
)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    تسجيل الدخول بالبريد الإلكتروني أو رقم الهاتف.

    يُرجع توكن JWT لاستخدامه في ترويسة ``Authorization: Bearer``.
    """
    try:
        user = await auth_service.authenticate(
            db, payload.identifier, payload.password
        )
    except AuthenticationError:
        await auth_service.log_failed_login(db, payload.identifier, request)
        raise

    tokens = auth_service.issue_tokens(user)

    club = None
    if user.club_id:
        club = await db.get(Club, user.club_id)

    return {
        **tokens,
        "user": UserResponse.model_validate(user),
        "club": club,
    }


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="تجديد توكن الوصول",
)
async def refresh_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """تجديد جلسة منتهية باستخدام توكن التجديد."""
    tokens = await auth_service.refresh_access_token(db, payload.refresh_token)
    user = await auth_service.get_user_by_id(db, int(decode_token(
        payload.refresh_token, TokenType.REFRESH
    )["uid"]))

    return {**tokens, "user": UserResponse.model_validate(user), "club": None}


# =====================================================================
#  الملف الشخصي
# =====================================================================
@router.get(
    "/me",
    summary="بيانات المستخدم الحالي",
    responses={401: {"description": "غير مصادَق"}},
)
async def read_me(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    بيانات المستخدم الحالي مع صلاحياته.

    الواجهة تستخدم هذا لتحديد ما يظهر في القوائم والأزرار.
    """
    club = await db.get(Club, user.club_id) if user.club_id else None

    return {
        "user": UserResponse.model_validate(user),
        "club": club,
        "role_label": role_label(user.role),
        "permissions": permissions_for(user.role),
    }


@router.patch("/me", response_model=UserResponse, summary="تحديث الملف الشخصي")
async def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """تحديث الاسم أو البريد أو الهاتف."""
    updated = await auth_service.update_profile(
        db,
        user,
        full_name=payload.full_name,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        avatar_url=payload.avatar_url,
    )
    return UserResponse.model_validate(updated)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="تغيير كلمة المرور",
)
async def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """تغيير كلمة المرور بعد التحقق من الحالية."""
    try:
        payload.validate_match()
    except ValueError as exc:
        from app.core.exceptions import ValidationError

        raise ValidationError(str(exc)) from None

    await auth_service.change_password(
        db, user, payload.current_password, payload.new_password
    )
    return MessageResponse(message="تم تغيير كلمة المرور بنجاح")


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="تسجيل الخروج",
)
async def logout(
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    تسجيل الخروج.

    التوكنات الموقّعة لا تُلغى من جهة الخادم (STATELESS)، لذلك نطلب من
    الواجهة حذف الكوكي/التوكن. نسجّل العملية في سجل التدقيق.
    """
    from app.models.enums import AuditAction

    if user is not None:
        await auth_service.log_audit(
            db,
            user=user,
            action=AuditAction.LOGOUT,
            entity_type="User",
            entity_id=user.id,
            description="تسجيل خروج",
        )
        await db.commit()

    return MessageResponse(message="تم تسجيل الخروج بنجاح")


@router.get(
    "/permissions",
    summary="صلاحيات الدور الحالي",
)
async def my_permissions(user: User = Depends(get_current_user)):
    """قائمة صلاحيات المستخدم الحالي."""
    return {
        "role": user.role.value,
        "role_label": role_label(user.role),
        "permissions": permissions_for(user.role),
    }