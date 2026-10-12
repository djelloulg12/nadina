"""مسارات البوابة العامة (/api/v1/public) -- لا تحتاج مصادقة.

تتضمن: دليل الأندية، البحث، الأخبار، inscribاء التسجيل المبدئية.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models import Club, ClubStatus, Sport
from app.schemas import (
    ClubResponse,
    MessageResponse,
    NewsResponse,
    PaginatedResponse,
    RegistrationRequestCreate,
)
from app.services import clubs as club_service

router = APIRouter(prefix="/public", tags=["البوابة العامة"])


# =====================================================================
#  دليل الأندية
# =====================================================================
@router.get(
    "/clubs",
    response_model=PaginatedResponse[ClubResponse],
    summary="البحث في دليل الأندية",
)
async def search_clubs(
    db: AsyncSession = Depends(get_db),
    search: str | None = Query(None, description="اسم النادي أو المدينة"),
    wilaya: str | None = Query(None, description="الولاية"),
    city: str | None = Query(None),
    sport_id: int | None = Query(None),
    min_age: int | None = Query(None, ge=0, le=100),
    max_age: int | None = Query(None, ge=0, le=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(12, ge=1, le=60),
):
    """
    محرك البحث الذكي في الأندية.

    يراعي فقط الأندية النشطة والعامة.
    """
    if not settings.ALLOW_PUBLIC_CLUB_REGISTRATION:
        pass  # لا يؤثر على البحث

    clubs, total = await club_service.search_clubs(
        db,
        search=search,
        wilaya=wilaya,
        city=city,
        sport_id=sport_id,
        min_age=min_age,
        max_age=max_age,
        public_only=True,
        page=page,
        per_page=per_page,
    )
    return PaginatedResponse.create(
        [ClubResponse.model_validate(c) for c in clubs], total, page, per_page
    )


@router.get("/clubs/filters", summary="خيارات التصفية المتاحة")
async def club_filters(db: AsyncSession = Depends(get_db)):
    """قائمة الولايات والرياضات المتوفرة لتعبئة قوائم التصفية."""
    from sqlalchemy import func, select

    from app.models import ClubSport

    wilayas = await club_service.get_public_wilayas(db)

    sports_rows = await db.execute(
        select(Sport.id, Sport.name_ar, Sport.icon, func.count(ClubSport.club_id))
        .join(ClubSport, ClubSport.sport_id == Sport.id)
        .join(Club, Club.id == ClubSport.club_id)
        .where(Club.status == ClubStatus.ACTIVE, Club.is_public.is_(True))
        .group_by(Sport.id)
        .order_by(func.count(ClubSport.club_id).desc())
    )
    sports = [
        {"id": r[0], "name": r[1], "icon": r[2], "clubs_count": r[3]}
        for r in sports_rows.all()
    ]

    return {"wilayas": wilayas, "sports": sports}


@router.get(
    "/clubs/{slug}",
    response_model=ClubResponse,
    summary="صفحة نادي",
    responses={404: {"description": "النادي غير موجود"}},
)
async def get_club_page(slug: str, db: AsyncSession = Depends(get_db)):
    """صفحة النادي العامة: الشعار، النبذة، الموقع، الصور، والرياضات."""
    club = await club_service.get_club_by_slug(db, slug)
    if club.status != ClubStatus.ACTIVE or not club.is_public:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("هذا النادي غير متاح حالياً")
    return ClubResponse.model_validate(club)


@router.get(
    "/clubs/{slug}/news",
    response_model=PaginatedResponse[NewsResponse],
    summary="أخبار النادي",
)
async def club_news(
    slug: str,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
):
    """أخبار وإعلانات النادي + المحتوى العام."""
    club = await club_service.get_club_by_slug(db, slug)
    items, total = await club_service.list_news(
        db, club_id=club.id, page=page, per_page=per_page
    )
    return PaginatedResponse.create(
        [NewsResponse.model_validate(n) for n in items], total, page, per_page
    )


# =====================================================================
#  الأخبار العامة
# =====================================================================
@router.get(
    "/news",
    response_model=PaginatedResponse[NewsResponse],
    summary="موجز الأخبار العام",
)
async def news_feed(
    db: AsyncSession = Depends(get_db),
    kind: str | None = Query(None, pattern="^(news|announcement|result|event|tournament)$"),
    sport_id: int | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(12, ge=1, le=50),
):
    """حائط الأخبار الموحّد لكل الأندية."""
    items, total = await club_service.list_news(
        db, kind=kind, sport_id=sport_id, search=search, page=page, per_page=per_page
    )
    return PaginatedResponse.create(
        [NewsResponse.model_validate(n) for n in items], total, page, per_page
    )


@router.get("/news/{news_id}", summary="تفاصيل خبر")
async def news_detail(news_id: int, db: AsyncSession = Depends(get_db)):
    """عرض خبر مع زيادة عدّاد المشاهدات."""
    from sqlalchemy import select

    from app.models import ClubNews

    result = await db.execute(select(ClubNews).where(ClubNews.id == news_id))
    news = result.scalar_one_or_none()
    if news is None or not news.is_active:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("الخبر غير موجود")

    await club_service.increment_news_views(db, news_id)
    return NewsResponse.model_validate(news)


# =====================================================================
#  التسجيل المبدئي
# =====================================================================
@router.post(
    "/clubs/{slug}/register",
    status_code=status.HTTP_201_CREATED,
    summary="إرسال طلب تسجيل في نادي",
    responses={
        400: {"description": "النادي لا يقبل التسجيل عبر الموقع"},
        409: {"description": "يوجد طلب قيد المراجعة"},
    },
)
async def submit_registration(
    slug: str,
    payload: RegistrationRequestCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    التسجيل الإلكتروني المبدئي.

    يرسل النموذج لمسير النادي للموافقة. الأندية المغلقة للطلبات تُرفض
    برسالة واضحة.
    """
    club = await club_service.get_club_by_slug(db, slug)
    request = await club_service.submit_registration(
        db, payload.model_dump(), club_id=club.id
    )
    return {
        "success": True,
        "message": "تم إرسال طلب التسجيل بنجاح. سيتواصل معك تادي النادي قريباً.",
        "request_id": request.id,
        "submitted_at": request.submitted_at,
    }


# =====================================================================
#  رياضات
# =====================================================================
@router.get("/sports", summary="قائمة الرياضات")
async def list_sports(db: AsyncSession = Depends(get_db)):
    """كل الرياضات المفعّلة في المنصة."""
    from sqlalchemy import select

    result = await db.execute(
        select(Sport).where(Sport.is_active.is_(True)).order_by(Sport.name_ar)
    )
    return [
        {"id": s.id, "name": s.name_ar, "name_en": s.name_en,
         "slug": s.slug, "icon": s.icon, "category": s.category}
        for s in result.scalars().all()
    ]