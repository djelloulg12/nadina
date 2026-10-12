"""مسارات الإشعارات ولوحة تحكم مدير المنصة."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_permission, require_roles
from app.core.db import get_db
from app.core.rbac import Action, Resource
from app.models import (
    AuditLog,
    Club,
    ClubStatus,
    PlatformPlan,
    User,
    UserRole,
)
from app.schemas import (
    ClubResponse,
    MessageResponse,
    PaginatedResponse,
)
from app.services import clubs as club_service
from app.services import notifications as notification_service
from app.services import reports as report_service

router = APIRouter(tags=["إشعارات وإدارة المنصة"])


# =====================================================================
#  الإشعارات
# =====================================================================
@router.get("/notifications", summary="إشعاراتي")
async def my_notifications(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """قائمة إشعارات المستخدم."""
    items = await notification_service.list_notifications(
        db, user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return {
        "items": [
            {
                "id": n.id,
                "type": n.type,
                "type_label": n.type_label,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "priority": n.priority,
                "is_read": n.is_read,
                "created_at": n.created_at,
            }
            for n in items
        ],
        "unread": await notification_service.unread_count(db, user.id),
    }


@router.get("/notifications/unread-count", summary="عدد غير المقروء")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    عدد الإشعارات غير المقروءة.

    نقطة خفيفة جداً يستدعيها ``app.js`` دورياً لتحديث جرس الإشعارات.
    """
    return {"count": await notification_service.unread_count(db, user.id)}


@router.post("/notifications/{notification_id}/read", summary="تعليم كمقروء")
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await notification_service.mark_read(db, notification_id, user.id)
    await db.commit()
    return MessageResponse(message="تم")


@router.post("/notifications/read-all", summary="تعليم الكل كمقروء")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    count = await notification_service.mark_all_read(db, user.id)
    await db.commit()
    return MessageResponse(message=f"تم تعليم {count} إشعار كمقروء", data={"count": count})


# =====================================================================
#  لوحة تحكم مدير المنصة
# =====================================================================
platform_router = APIRouter(prefix="/admin", tags=["مدير المنصة"])


@platform_router.get("/dashboard", summary="إحصائيات المنصة")
async def platform_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """نظرة شاملة على كل الأندية والمستخدمين والإيرادات."""
    return await report_service.platform_dashboard(db)


@platform_router.get(
    "/clubs",
    response_model=PaginatedResponse[ClubResponse],
    summary="كل الأندية",
)
async def list_all_clubs(
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """كل الأندية بما فيها الموقوفة والمنتهية اشتراكاتها."""
    clubs, total = await club_service.search_clubs(
        db,
        search=search,
        status=ClubStatus(status_filter) if status_filter else None,
        public_only=False,
        page=page,
        per_page=per_page,
    )
    return PaginatedResponse.create(
        [ClubResponse.model_validate(c) for c in clubs], total, page, per_page
    )


@platform_router.post("/clubs/{club_id}/activate", summary="تفعيل نادي")
async def activate_club(
    club_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """تفعيل نادي معتمد."""
    await club_service.activate_club(db, club_id)
    await db.commit()
    return MessageResponse(message="تم تفعيل النادي")


@platform_router.post("/clubs/{club_id}/suspend", summary="إيقاف نادي")
async def suspend_club(
    club_id: int,
    reason: str = Query(..., description="سبب الإيقاف"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """إيقاف نادي (يمنع الطاقم من الدخول)."""
    await club_service.suspend_club(db, club_id, reason)
    await db.commit()
    return MessageResponse(message="تم إيقاف النادي")


@platform_router.post("/clubs/{club_id}/renew", summary="تجديد اشتراك نادي")
async def renew_club(
    club_id: int,
    plan_id: int | None = Query(None),
    months: int | None = Query(None, ge=1, le=36),
    amount: float | None = Query(None, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """تجديد اشتراك النادي في المنصة."""
    subscription = await club_service.renew_club_subscription(
        db, club_id, plan_id=plan_id, months=months, amount=amount
    )
    await db.commit()
    return MessageResponse(
        message=(
            f"تم تجديد الاشتراك حتى "
            f"{subscription.period_end.strftime('%Y/%m/%d')}"
        ),
        data={"period_end": str(subscription.period_end)},
    )


@platform_router.get("/plans", summary="خطط المنصة")
async def list_plans(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """خطط اشتراك الأندية المعروضة للعملاء."""
    plans = list(
        (
            await db.execute(
                select(PlatformPlan)
                .where(PlatformPlan.is_active.is_(True))
                .order_by(PlatformPlan.sort_order, PlatformPlan.price)
            )
        ).scalars().all()
    )
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "price": float(p.price),
            "period": p.period,
            "duration_days": p.duration_days,
            "max_athletes": p.max_athletes,
            "max_groups": p.max_groups,
            "max_staff": p.max_staff,
            "features": p.features,
            "is_featured": p.is_featured,
        }
        for p in plans
    ]


@platform_router.get("/audit-logs", summary="سجل التدقيق")
async def audit_logs(
    db: AsyncSession = Depends(get_db),
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    user_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    _: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
):
    """
    سجل التدقيق الكامل.

    غير قابل للتعديل أو الحذف -- هذا هو جوهر فائدة سجل التدقيق.
    """
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if user_id:
        conditions.append(AuditLog.user_id == user_id)

    stmt = select(AuditLog)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    logs = list((await db.execute(stmt)).scalars().all())

    from sqlalchemy import func

    count_stmt = select(func.count(AuditLog.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    return PaginatedResponse.create(
        [
            {
                "id": log.id,
                "user_name": log.user_name,
                "user_role": log.user_role,
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "description": log.description,
                "ip_address": log.ip_address,
                "success": log.success,
                "created_at": log.created_at,
            }
            for log in logs
        ],
        total, page, per_page,
    )