"""
خدمة الأندية والمحتوى العام (Clubs & Content Service)
=====================================================
النوادي، دليل البحث العام، الأخبار والإعلانات، وطلبات التسجيل المبدئية.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import safe_get, utcnow
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.security import generate_slug
from app.models import (
    Athlete,
    Club,
    ClubNews,
    ClubStatus,
    ClubSubscription,
    PlatformPlan,
    RegistrationRequest,
    RegistrationStatus,
    Sport,
    StaffMember,
    Status,
    SubscriptionStatus,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)


# =====================================================================
#  الأندية
# =====================================================================
async def _unique_club_slug(db: AsyncSession, name: str, exclude_id: int | None = None) -> str:
    """توليد slug فريد للنادي."""
    base = generate_slug(name) or "club"
    candidate = base
    counter = 1

    while True:
        stmt = select(Club.id).where(Club.slug == candidate)
        if exclude_id:
            stmt = stmt.where(Club.id != exclude_id)
        exists = await db.execute(stmt.limit(1))
        if exists.scalar_one_or_none() is None:
            return candidate
        counter += 1
        candidate = f"{base}-{counter}"


async def create_club(
    db: AsyncSession,
    data: dict[str, Any],
    *,
    auto_activate: bool = False,
) -> Club:
    """
    إنشاء نادي جديد.

    ``auto_activate`` يُستخدم عند موافقة مدير المنصة، أو في وضع
    التطوير. الافتراضي ``pending`` حتى تراجعه إدارة المنصة.
    """
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("اسم النادي مطلوب")

    slug = data.get("slug") or await _unique_club_slug(db, name)

    club = Club(
        name=name,
        slug=slug,
        description=data.get("description"),
        logo_url=data.get("logo_url"),
        cover_url=data.get("cover_url"),
        primary_color=data.get("primary_color", "#0F766E"),
        address=data.get("address"),
        wilaya=data.get("wilaya"),
        commune=data.get("commune"),
        city=data.get("city"),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        phone=data.get("phone"),
        phone_2=data.get("phone_2"),
        email=data.get("email"),
        website=data.get("website"),
        facebook_url=data.get("facebook_url"),
        instagram_url=data.get("instagram_url"),
        min_age=data.get("min_age"),
        max_age=data.get("max_age"),
        status=ClubStatus.ACTIVE if auto_activate else ClubStatus.PENDING,
        plan_id=data.get("plan_id"),
        currency=data.get("currency", "DZD"),
        default_monthly_price=data.get("default_monthly_price"),
        is_public=data.get("is_public", True),
        accepts_online_registration=data.get("accepts_online_registration", True),
        settings=data.get("settings", {}),
    )
    db.add(club)
    await db.flush()

    # ربط الرياضات
    sport_ids = data.get("sport_ids") or []
    if sport_ids:
        await set_club_sports(db, club.id, sport_ids)

    return club


async def update_club(db: AsyncSession, club_id: int, data: dict[str, Any]) -> Club:
    """تحديث بيانات النادي."""
    club = await db.get(Club, club_id)
    if club is None:
        raise NotFoundError("النادي غير موجود")

    for field, value in data.items():
        if field in ("slug", "created_at") or value is None:
            continue
        if hasattr(club, field):
            setattr(club, field, value)

    if "name" in data and data["name"]:
        club.slug = await _unique_club_slug(db, data["name"], exclude_id=club_id)

    await db.flush()
    await db.refresh(club)
    return club


async def get_club(db: AsyncSession, club_id: int) -> Club:
    """جلب نادي بمعرّفه."""
    club = await db.get(Club, club_id)
    if club is None:
        raise NotFoundError("النادي غير موجود")
    return club


async def get_club_by_slug(db: AsyncSession, slug: str) -> Club:
    """جلب نادي بمعرّف الرابط (slug)."""
    result = await db.execute(select(Club).where(Club.slug == slug))
    club = result.scalar_one_or_none()
    if club is None:
        raise NotFoundError("النادي غير موجود")
    return club


async def set_club_sports(db: AsyncSession, club_id: int, sport_ids: list[int]) -> None:
    """تحديد الرياضات المتوفرة في النادي."""
    club = await get_club(db, club_id)
    if not sport_ids:
        club.sports = []
        await db.flush()
        return

    sports = await db.execute(select(Sport).where(Sport.id.in_(sport_ids)))
    found = list(sports.scalars().all())
    if len(found) != len(set(sport_ids)):
        raise NotFoundError("بعض الرياضات المحددة غير موجودة")

    club.sports = found
    await db.flush()


async def update_club_counts(db: AsyncSession, club_id: int) -> None:
    """تحديث عدّاد الأعضاء في النادي (يُستدعى دورياً)."""
    count = await db.execute(
        select(func.count(Athlete.id)).where(
            Athlete.club_id == club_id,
            Athlete.is_active.is_(True),
        )
    )
    club = await db.get(Club, club_id)
    if club:
        club.members_count = count.scalar() or 0
        await db.flush()


# =====================================================================
#  البحث العام في دليل الأندية
# =====================================================================
async def search_clubs(
    db: AsyncSession,
    *,
    search: str | None = None,
    wilaya: str | None = None,
    city: str | None = None,
    sport_id: int | None = None,
    min_age: int | None = None,
    max_age: int | None = None,
    status: ClubStatus | None = None,
    public_only: bool = True,
    page: int = 1,
    per_page: int = 12,
) -> tuple[list[Club], int]:
    """
    محرك البحث في دليل الأندية العامة.

    ملاحظة: هذا الاستعلام يقرأ من جدول ``clubs`` (جدول مركزي غير مملوك
    لمستأجر)، لذا لا يخضع لعزل الأندية -- وهو مقصود، فالبوابة عامة.
    التصفية بـ ``status=active`` و ``is_public`` تمنع ظهور الأندية
    الموقوفة أو الخاصة.
    """
    conditions = []

    if public_only:
        conditions.append(Club.is_public.is_(True))
        conditions.append(Club.status == ClubStatus.ACTIVE)
    elif status:
        conditions.append(Club.status == status)

    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                Club.name.ilike(pattern),
                Club.description.ilike(pattern),
                Club.city.ilike(pattern),
                Club.wilaya.ilike(pattern),
                Club.address.ilike(pattern),
            )
        )

    if wilaya:
        conditions.append(Club.wilaya == wilaya)
    if city:
        conditions.append(Club.city == city)
    if min_age is not None:
        conditions.append(or_(Club.min_age.is_(None), Club.min_age <= min_age))
    if max_age is not None:
        conditions.append(or_(Club.max_age.is_(None), Club.max_age >= max_age))

    if sport_id:
        from app.models import ClubSport

        subquery = select(ClubSport.club_id).where(ClubSport.sport_id == sport_id)
        conditions.append(Club.id.in_(subquery))

    count_stmt = select(func.count(Club.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(Club).order_by(Club.name)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    return list((await db.execute(stmt)).scalars().all()), total


async def get_public_wilayas(db: AsyncSession) -> list[str]:
    """قائمة الولايات المتوفرة في الدليل (لملء قائمة التصفية)."""
    rows = await db.execute(
        select(Club.wilaya)
        .where(Club.wilaya.isnot(None), Club.status == ClubStatus.ACTIVE, Club.is_public.is_(True))
        .distinct()
        .order_by(Club.wilaya)
    )
    return [r[0] for r in rows.all()]


async def get_public_sports(db: AsyncSession) -> list[tuple[int, int]]:
    """الرياضات المتوفرة في الأندية النشطة مع عدد الأندية لكل منها."""
    from app.models import ClubSport

    rows = await db.execute(
        select(Sport.id, func.count(ClubSport.club_id))
        .join(ClubSport, ClubSport.sport_id == Sport.id)
        .join(Club, Club.id == ClubSport.club_id)
        .where(Club.status == ClubStatus.ACTIVE, Club.is_public.is_(True))
        .group_by(Sport.id)
        .order_by(func.count(ClubSport.club_id).desc())
    )
    return [(r[0], r[1]) for r in rows.all()]


# =====================================================================
#  الأخبار والإعلانات
# =====================================================================
async def create_news(
    db: AsyncSession, data: dict[str, Any], club_id: int | None = None
) -> ClubNews:
    """
    نشر خبر أو إعلان.

    ``club_id=None`` يعني إعلاناً عاماً على مستوى المنصة يظهر لكل
    الأندية (يتميّز بعلامة ``__tenant_global_read__``).
    """
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not title:
        raise ValidationError("عنوان الخبر مطلوب")
    if not content:
        raise ValidationError("محتوى الخبر مطلوب")

    excerpt = data.get("excerpt")
    if not excerpt:
        excerpt = content[:197] + "..." if len(content) > 200 else content

    is_published = data.get("is_published", True)
    event_date = data.get("event_date")
    if isinstance(event_date, str):
        event_date = dt.date.fromisoformat(event_date)
    event_end_date = data.get("event_end_date")
    if isinstance(event_end_date, str):
        event_end_date = dt.date.fromisoformat(event_end_date)

    news = ClubNews(
        club_id=club_id,
        title=title,
        excerpt=excerpt,
        content=content,
        kind=data.get("kind", "news"),
        category=data.get("category"),
        cover_url=data.get("cover_url"),
        gallery=data.get("gallery", []),
        is_published=is_published,
        is_pinned=data.get("is_pinned", False),
        published_at=utcnow() if is_published else None,
        event_date=event_date,
        event_end_date=event_end_date,
        location=data.get("location"),
        sport_id=data.get("sport_id"),
        author_id=data.get("author_id"),
        is_active=True,
    )
    db.add(news)
    await db.flush()
    return news


async def update_news(db: AsyncSession, news_id: int, data: dict[str, Any]) -> ClubNews:
    """تعديل خبر."""
    news = await safe_get(db, ClubNews, news_id)
    if news is None:
        raise NotFoundError("الخبر غير موجود")

    for field, value in data.items():
        if field in ("club_id", "created_at", "views_count") or value is None:
            continue
        if hasattr(news, field):
            setattr(news, field, value)

    if data.get("is_published") and not news.published_at:
        news.published_at = utcnow()

    await db.flush()
    await db.refresh(news)
    return news


async def delete_news(db: AsyncSession, news_id: int) -> None:
    """حذف خبر (منطقي)."""
    news = await safe_get(db, ClubNews, news_id)
    if news is None:
        raise NotFoundError("الخبر غير موجود")
    news.is_active = False
    news.deleted_at = utcnow()
    await db.flush()


async def list_news(
    db: AsyncSession,
    *,
    club_id: int | None = None,
    kind: str | None = None,
    sport_id: int | None = None,
    search: str | None = None,
    include_global: bool = True,
    published_only: bool = True,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[ClubNews], int]:
    """استعلام عن الأخبار."""
    conditions = [ClubNews.is_active.is_(True)]

    if published_only:
        conditions.extend([ClubNews.is_published.is_(True), ClubNews.published_at.isnot(None)])

    if club_id is not None:
        if include_global:
            conditions.append(or_(ClubNews.club_id == club_id, ClubNews.club_id.is_(None)))
        else:
            conditions.append(ClubNews.club_id == club_id)

    if kind:
        conditions.append(ClubNews.kind == kind)
    if sport_id:
        conditions.append(ClubNews.sport_id == sport_id)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(ClubNews.title.ilike(pattern), ClubNews.excerpt.ilike(pattern))
        )

    count_stmt = select(func.count(ClubNews.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(ClubNews)
        .where(*conditions)
        .order_by(ClubNews.is_pinned.desc(), ClubNews.published_at.desc())
    )
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    return list((await db.execute(stmt)).scalars().all()), total


async def increment_news_views(db: AsyncSession, news_id: int) -> None:
    """زيادة عدّاد مشاهدات الخبر."""
    news = await safe_get(db, ClubNews, news_id)
    if news is not None:
        news.views_count = (news.views_count or 0) + 1
        await db.flush()


# =====================================================================
#  طلبات التسجيل المبدئية
# =====================================================================
async def submit_registration(
    db: AsyncSession, data: dict[str, Any], club_id: int
) -> RegistrationRequest:
    """
    استلام طلب تسجيل من زائر عبر البوابة العامة.

    التحقق من الأهلية: النادي يجب أن يقبل التسجيل عبر الموقع.
    """
    club = await get_club(db, club_id)

    if not club.accepts_online_registration:
        raise BusinessRuleError("هذا النادي لا يقبل طلبات التسجيل عبر الموقع حالياً")

    if club.status != ClubStatus.ACTIVE:
        raise BusinessRuleError("هذا النادي غير متاح للتسجيل حالياً")

    full_name = (data.get("full_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not full_name:
        raise ValidationError("الاسم الكامل مطلوب")
    if not phone:
        raise ValidationError("رقم الهاتف مطلوب")

    # منع الإرسال المزعج: نفس الرقم عند نفس النادي خلال 7 أيام
    from app.models import RegistrationRequest as RR

    recent = await db.execute(
        select(func.count(RR.id)).where(
            RR.club_id == club_id,
            RR.phone == phone,
            RR.submitted_at >= utcnow() - dt.timedelta(days=7),
            RR.status == RegistrationStatus.PENDING,
        )
    )
    if (recent.scalar() or 0) > 0:
        raise ConflictError(
            "لديك طلب قيد المراجعة في هذا النادي. سنتواصل معك قريباً"
        )

    request = RegistrationRequest(
        club_id=club_id,
        full_name=full_name,
        birth_date=data.get("birth_date"),
        gender=data.get("gender"),
        parent_name=data.get("parent_name"),
        phone=phone,
        email=data.get("email"),
        address=data.get("address"),
        wilaya=data.get("wilaya"),
        sport_id=data.get("sport_id"),
        preferred_group_id=data.get("preferred_group_id"),
        level=data.get("level"),
        message=data.get("message"),
        documents_url=data.get("documents_url"),
        status=RegistrationStatus.PENDING,
        is_active=True,
    )
    db.add(request)
    await db.flush()
    return request


async def list_registration_requests(
    db: AsyncSession,
    *,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[RegistrationRequest], int]:
    """قائمة طلبات التسجيل المرسلة للنادي."""
    conditions = [RegistrationRequest.is_active.is_(True)]
    if status:
        conditions.append(RegistrationRequest.status == status)

    count_stmt = select(func.count(RegistrationRequest.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(RegistrationRequest)
        .where(*conditions)
        .order_by(RegistrationRequest.submitted_at.desc())
    )
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    return list((await db.execute(stmt)).scalars().all()), total


async def approve_registration(
    db: AsyncSession, request_id: int, reviewer_id: int | None = None
) -> dict[str, Any]:
    """
    الموافقة على طلب تسجيل وتحويله إلى رياضي فعلي.

    هذه أهم دالة في هذه الخدمة -- هي نقطة الالتحاق بين "زائر" و"عضو".
    """
    request = await safe_get(db, RegistrationRequest, request_id)
    if request is None:
        raise NotFoundError("طلب التسجيل غير موجود")

    if request.status != RegistrationStatus.PENDING:
        raise ConflictError("تمت معالجة هذا الطلب مسبقاً")

    from app.services.people import create_athlete

    # نستخرج الاسم الأول واسم العائلة من الاسم الكامل المُدخل
    parts = (request.full_name or "رياضي").split()
    if len(parts) >= 2:
        first_name, last_name = parts[0], " ".join(parts[1:])
    else:
        first_name, last_name = (parts[0] if parts else "رياضي"), ""

    athlete = await create_athlete(
        db,
        {
            "club_id": request.club_id,
            "first_name": first_name,
            "last_name": last_name or first_name,
            "birth_date": request.birth_date,
            "gender": request.gender,
            "phone": request.phone,
            "email": request.email,
            "address": request.address,
            "wilaya": request.wilaya,
            "guardian_name": request.parent_name,
            "guardian_phone": request.phone,
            "sport_id": request.sport_id,
            "level": request.level,
            "notes": f"مصدر التسجيل: طلب رقم {request.id}",
            "group_ids": [request.preferred_group_id] if request.preferred_group_id else [],
        },
    )

    request.status = RegistrationStatus.APPROVED
    request.processed_at = utcnow()
    request.processed_by_id = reviewer_id
    request.created_athlete_id = athlete.id

    await db.flush()
    return {"request": request, "athlete": athlete}


async def reject_registration(
    db: AsyncSession, request_id: int, reason: str, reviewer_id: int | None = None
) -> RegistrationRequest:
    """رفض طلب تسجيل مع تسجيل السبب."""
    request = await safe_get(db, RegistrationRequest, request_id)
    if request is None:
        raise NotFoundError("طلب التسجيل غير موجود")
    if request.status != RegistrationStatus.PENDING:
        raise ConflictError("تمت معالجة هذا الطلب مسبقاً")

    request.status = RegistrationStatus.REJECTED
    request.rejection_reason = reason
    request.processed_at = utcnow()
    request.processed_by_id = reviewer_id
    await db.flush()
    return request


# =====================================================================
#  اشتراكات الأندية في المنصة
# =====================================================================
async def renew_club_subscription(
    db: AsyncSession,
    club_id: int,
    *,
    plan_id: int | None = None,
    months: int | None = None,
    amount: float | None = None,
) -> ClubSubscription:
    """
    تجديد اشتراك نادي في المنصة (إجراء مدير المنصة).

    إذا كان الاشتراك القديم سارياً، نمدّد من تاريخ انتهائه (لا نعيد
    العدّ من اليوم) حتى لا يخسر النادي أياماً دفع ثمنها.
    """
    club = await get_club(db, club_id)

    plan = await db.get(PlatformPlan, plan_id) if plan_id else club.plan
    duration_days = months * 30 if months else (plan.duration_days if plan else 30)

    today = dt.date.today()
    if club.subscription_end and club.subscription_end >= today:
        period_start = club.subscription_end + dt.timedelta(days=1)
    else:
        period_start = today
    period_end = period_start + dt.timedelta(days=duration_days - 1)

    subscription = ClubSubscription(
        club_id=club_id,
        plan_id=plan.id if plan else None,
        amount=amount if amount is not None else (float(plan.price) if plan else 0),
        period_start=period_start,
        period_end=period_end,
        status=SubscriptionStatus.ACTIVE,
        paid_at=utcnow(),
        invoice_number=f"PLAT-{club_id}-{period_start.strftime('%Y%m%d')}",
    )
    db.add(subscription)
    await db.flush()

    # --- تحديث حالة النادي واشتراكه ---
    club.plan_id = plan.id if plan else club.plan_id
    club.subscription_start = club.subscription_start or period_start
    club.subscription_end = period_end
    if club.status in (ClubStatus.EXPIRED, ClubStatus.PENDING):
        club.status = ClubStatus.ACTIVE

    await db.flush()
    return subscription


async def suspend_club(db: AsyncSession, club_id: int, reason: str) -> Club:
    """إيقاف نادي (إجراء إداري)."""
    club = await get_club(db, club_id)
    club.status = ClubStatus.SUSPENDED
    club.settings = {**(club.settings or {}), "suspension_reason": reason}
    await db.flush()
    return club


async def activate_club(db: AsyncSession, club_id: int) -> Club:
    """تفعيل نادي بعد التحقق من الاستيفاء."""
    club = await get_club(db, club_id)
    club.status = ClubStatus.ACTIVE
    club.is_verified = True
    await db.flush()
    return club