"""
مسارات صفحات الويب (Server-Rendered Pages)
==========================================
كل صفحات الواجهة مبنية على الخادم. هنا نقرأ من قاعدة البيانات عبر
طبقة الخدمات نفسها التي تستخدمها الـ API -- فلا منطق مكرر، ولا خطر
اختلاف سلوك بين الواجهة والـ API.

**قاعدة معمارية**: طبقة الويب لا تحتوي منطق أعمال إطلاقاً. هي مسؤولة عن:
  1. قراءة المدخلات من النموذج.
  2. استدعاء الخدمة المناسبة.
  3. اختيار القالب أو إعادة التوجيه.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.exceptions import AppError, ValidationError
from app.core.security import hash_password, validate_password_strength
from app.core.tenancy import TenantContext, set_tenant_context, tenant_scope
from app.models import (
    Athlete,
    Club,
    ClubNews,
    ClubStatus,
    Evaluation,
    FeePlan,
    Group,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    RegistrationRequest,
    Sport,
    StaffMember,
    TrainingSession,
    User,
    UserRole,
)
from app.services import auth as auth_service
from app.services import cards as card_service
from app.services import clubs as club_service
from app.services import excel as excel_service
from app.services import finance as finance_service
from app.services import notifications as notification_service
from app.services import people as people_service
from app.services import reports as report_service
from app.services import training as training_service
from app.web.helpers import (
    clear_session_cookie,
    current_user_or_redirect,
    ensure_role,
    page_context,
    resolve_home_route,
    set_session_cookie,
)
from app.web.templates_setup import templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)


# =====================================================================
#  أدوات
# =====================================================================
def _render(
    request: Request,
    template: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    """عرض قالب مع سياق أساسي."""
    return templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _form_value(form: Any, key: str, default: Any = None) -> Any:
    """قراءة قيمة من النموذج مع تحويل النص الفارغ إلى ``None``."""
    value = form.get(key, default)
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return value


def _as_date(value: Any) -> dt.date | None:
    """تحويل نص إلى تاريخ."""
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        try:
            return dt.datetime.strptime(str(value), "%d/%m/%Y").date()
        except ValueError:
            return None


def _as_time(value: Any) -> dt.time | None:
    """تحويل نص إلى وقت."""
    if not value:
        return None
    if isinstance(value, dt.time):
        return value
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(str(value), fmt).time()
        except ValueError:
            continue
    return None


def _as_float(value: Any) -> float | None:
    """تحويل نص إلى رقم عشري."""
    if value in (None, "",):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _bind_context(db: AsyncSession, user: User, club_id: int | None = None) -> TenantContext:
    """
    تثبيت سياق العزل لصفحة الويب.

    **مهم**: صفحة الويب تقرأ وتكتب بنفس الجلسة التي يقرأ بها الـ API،
    لكن صفحة الويب ليست "طلب مصادَق" من منظور FastAPI -- فنّشئ السياق
    يدوياً قبل أي استعلام، تماماً كما يفعل ``get_current_user``.
    """
    from app.api.deps import establish_tenant_context

    return await establish_tenant_context(db, user)


async def _require_club_context(
    db: AsyncSession, request: Request, *roles: UserRole
):
    """مستخدم + نادي + سياق عزل، أو إعادة توجيه."""
    user = await current_user_or_redirect(db, request)
    if isinstance(user, RedirectResponse):
        return user, None, None, None

    error = ensure_role(user, *roles)
    if error:
        return (
            _render(
                request,
                "errors/403.html",
                {**await page_context(request, db), "message": error},
                status_code=403,
            ),
            None,
            None,
            None,
        )

    club = await db.get(Club, user.club_id) if user.club_id else None
    ctx = await _bind_context(db, user)

    return None, user, club, ctx


# =====================================================================
#  1) المصادقة
# =====================================================================
auth_router = APIRouter(prefix="/auth", include_in_schema=False)


@auth_router.get("/login", name="login")
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    """صفحة تسجيل الدخول."""
    return _render(
        request,
        "auth/login.html",
        {
            **await page_context(request, db),
            "page_title": "تسجيل الدخول",
            "next": request.query_params.get("next", ""),
        },
    )


@auth_router.post("/login")
async def login_submit(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    معالجة تسجيل الدخول.

    عند النجاح نكتب كوكي جلسة ونوجّه كل رتبة إلى مساحتها.
    """
    next_url = request.query_params.get("next") or request.cookies.get("mashia_next", "")

    try:
        user = await auth_service.authenticate(db, identifier, password)
    except AppError as exc:
        return _render(
            request,
            "auth/login.html",
            {
                **await page_context(request, db),
                "page_title": "تسجيل الدخول",
                "error": exc.message,
                "identifier": identifier,
            },
            status_code=exc.status_code,
        )

    response = _redirect(next_url or resolve_home_route(user))
    set_session_cookie(response, user)
    return response


@auth_router.get("/register", name="register")
async def register_page(request: Request, db: AsyncSession = Depends(get_db)):
    """صفحة طلب فتح حساب نادي جديد."""
    if not settings.ALLOW_PUBLIC_CLUB_REGISTRATION:
        return _render(
            request,
            "errors/403.html",
            {
                **await page_context(request, db),
                "message": "التسجيل الذاتي مغلق حالياً. يرجى التواصل مع إدارة المنصة",
            },
            status_code=403,
        )

    return _render(
        request,
        "auth/register.html",
        {
            **await page_context(request, db),
            "page_title": "فتح حساب نادي",
            "sports": list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all()),
        },
    )


@auth_router.post("/register")
async def register_submit(
    request: Request,
    club_name: str = Form(...),
    admin_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(...),
    wilaya: str = Form(""),
    city: str = Form(""),
    description: str = Form(""),
    sport_ids: list[int] = Form(default=[]),
    db: AsyncSession = Depends(get_db),
):
    """
    تسجيل نادي جديد.

    يُنشئ النادي بحالة ``pending`` ومديراً بانتظار موافقة إدارة المنصة،
    وهو ما يمنع انتحال الأندية.
    """
    form = await request.form()
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())
    base = {**await page_context(request, db), "page_title": "فتح حساب نادي", "sports": sports}

    def fail(message: str, code: int = 422):
        return _render(
            request,
            "auth/register.html",
            {**base, "error": message, "form": form},
            status_code=code,
        )

    if password != password_confirm:
        return fail("كلمتا المرور غير متطابقتين")

    try:
        validate_password_strength(password)
    except ValidationError as exc:
        return fail(exc.message)

    if await auth_service.identifier_exists(db, email=email or None, phone=phone):
        return fail("رقم الهاتف أو البريد الإلكتروني مستخدم مسبقاً")

    try:
        # --- إنشاء النادي ---
        club = await club_service.create_club(
            db,
            {
                "name": club_name,
                "description": description,
                "phone": phone,
                "email": email or None,
                "wilaya": wilaya or None,
                "city": city or None,
                "sport_ids": sport_ids,
                "accepts_online_registration": True,
            },
            auto_activate=False,
        )

        # --- إنشاء حساب المدير ---
        manager = await auth_service.create_user(
            db,
            identifier=email or phone,
            password=password,
            full_name=admin_name,
            role=UserRole.CLUB_ADMIN,
            club_id=club.id,
            phone=phone,
            email=email or None,
        )

        await auth_service.log_audit(
            db,
            user=manager,
            action="create",
            entity_type="Club",
            entity_id=club.id,
            description=f"طلب فتح حساب نادي: {club_name}",
        )
        await db.commit()

    except AppError as exc:
        await db.rollback()
        return fail(exc.message, exc.status_code)

    response = _redirect("/auth/pending")
    set_session_cookie(response, manager)
    return response


@auth_router.get("/pending", name="pending")
async def pending_page(request: Request, db: AsyncSession = Depends(get_db)):
    """صفحة انتظار موافقة إدارة المنصة على طلب الفتح."""
    return _render(
        request,
        "auth/pending.html",
        {**await page_context(request, db), "page_title": "بانتظار الموافقة"},
    )


@auth_router.get("/logout", name="logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    """تسجيل الخروج وحذف كوكي الجلسة."""
    user = await page_context(request, db)
    response = _redirect("/")
    clear_session_cookie(response)
    return response


# =====================================================================
#  2) البوابة العامة
# =====================================================================
public_router = APIRouter(include_in_schema=False)


@public_router.get("/", name="home")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """
    الصفحة الرئيسية.

    المستخدم المسجّل يُوجَّه إلى مساحته، والزائر يرى البوابة العامة.
    """
    ctx = {**await page_context(request, db)}

    if ctx.get("current_user") is not None:
        return _redirect(resolve_home_route(ctx["current_user"]))

    news, _ = await club_service.list_news(db, per_page=6)
    clubs, _ = await club_service.search_clubs(db, page=1, per_page=8)

    from app.models import ClubSport

    sports_rows = await db.execute(
        select(Sport.id, Sport.name_ar, Sport.icon, func_count(ClubSport.club_id))
        .join(ClubSport, ClubSport.sport_id == Sport.id)
        .group_by(Sport.id)
        .order_by(func_count(ClubSport.club_id).desc())
        .limit(12)
    )

    return _render(
        request,
        "public/home.html",
        {
            **ctx,
            "page_title": "الرئيسية",
            "active_nav": "home",
            "news": news,
            "clubs": clubs,
            "sports": [
                {"id": r[0], "name": r[1], "icon": r[2], "count": r[3]}
                for r in sports_rows.all()
            ],
        },
    )


def func_count(col):  # noqa: ANN001, ANN202
    """اختصار لـ ``func.count`` (لتفادي الاستيراد المتكرر)."""
    from sqlalchemy import func

    return func.count(col)


@public_router.get("/clubs", name="clubs_directory")
async def clubs_directory(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query(""),
    wilaya: str = Query(""),
    city: str = Query(""),
    sport_id: int | None = Query(None),
    page: int = Query(1, ge=1),
):
    """دليل الأندية العام مع البحث والتصفية."""
    clubs, total = await club_service.search_clubs(
        db, search=search or None, wilaya=wilaya or None,
        city=city or None, sport_id=sport_id, page=page, per_page=12,
    )
    wilayas = await club_service.get_public_wilayas(db)

    from app.models import ClubSport

    sports_rows = await db.execute(
        select(Sport.id, Sport.name_ar, Sport.icon, func_count(ClubSport.club_id))
        .join(ClubSport, ClubSport.sport_id == Sport.id)
        .group_by(Sport.id)
        .order_by(func_count(ClubSport.club_id).desc())
    )

    return _render(
        request,
        "public/clubs.html",
        {
            **await page_context(request, db),
            "page_title": "دليل الأندية",
            "active_nav": "clubs",
            "clubs": clubs,
            "total": total,
            "page": page,
            "per_page": 12,
            "wilayas": wilayas,
            "sports": [
                {"id": r[0], "name": r[1], "icon": r[2], "count": r[3]}
                for r in sports_rows.all()
            ],
            "filters": {"search": search, "wilaya": wilaya, "city": city, "sport_id": sport_id},
        },
    )


@public_router.get("/clubs/{slug}", name="club_public")
async def club_public_page(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """الصفحة العامة للنادي."""
    club = await club_service.get_club_by_slug(db, slug)
    if club.status != ClubStatus.ACTIVE or not club.is_public:
        return _render(
            request,
            "errors/404.html",
            {**await page_context(request, db), "message": "هذا النادي غير متاح حالياً"},
            status_code=404,
        )

    news, _ = await club_service.list_news(db, club_id=club.id, per_page=6)
    groups = await training_service.list_groups(db)

    return _render(
        request,
        "public/club.html",
        {
            **await page_context(request, db),
            "page_title": club.name,
            "active_nav": "clubs",
            "club": club,
            "news": news,
            "groups": groups,
        },
    )


@public_router.get("/news", name="news")
async def news_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    kind: str = Query(""),
    page: int = Query(1, ge=1),
):
    """موجز الأخبار العام."""
    items, total = await club_service.list_news(
        db, kind=kind or None, page=page, per_page=12
    )
    return _render(
        request,
        "public/news.html",
        {
            **await page_context(request, db),
            "page_title": "الأخبار",
            "active_nav": "news",
            "news": items,
            "total": total,
            "page": page,
            "per_page": 12,
            "kind": kind,
        },
    )


@public_router.get("/news/{news_id}", name="news_detail")
async def news_detail(request: Request, news_id: int, db: AsyncSession = Depends(get_db)):
    """تفاصيل خبر."""
    result = await db.execute(
        select(ClubNews).where(ClubNews.id == news_id, ClubNews.is_active.is_(True))
    )
    news = result.scalar_one_or_none()
    if news is None or (news.club_id is None and news.is_published is not True):
        return _render(
            request,
            "errors/404.html",
            {**await page_context(request, db), "message": "الخبر غير موجود"},
            status_code=404,
        )

    await club_service.increment_news_views(db, news_id)
    await db.commit()

    club = await db.get(Club, news.club_id) if news.club_id else None
    related, _ = await club_service.list_news(
        db, club_id=news.club_id, per_page=4
    ) if news.club_id else ([], 0)

    return _render(
        request,
        "public/news_detail.html",
        {
            **await page_context(request, db),
            "page_title": news.title,
            "news": news,
            "club": club,
            "related": related,
        },
    )


@public_router.get("/clubs/{slug}/register", name="register_at_club")
async def club_registration_form(
    request: Request, slug: str, db: AsyncSession = Depends(get_db)
):
    """استمارة التسجيل المبدئي في نادي."""
    club = await club_service.get_club_by_slug(db, slug)
    groups = await training_service.list_groups(db)
    sports = await db.execute(select(Sport).where(Sport.is_active.is_(True)))

    return _render(
        request,
        "public/register.html",
        {
            **await page_context(request, db),
            "page_title": f"التسجيل في {club.name}",
            "club": club,
            "groups": groups,
            "sports": list(sports.scalars().all()),
        },
    )


@public_router.post("/clubs/{slug}/register")
async def club_registration_submit(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    **form_data,
):
    """معالجة استمارة التسجيل."""
    form = await request.form()
    club = await club_service.get_club_by_slug(db, slug)
    groups = await training_service.list_groups(db)
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())
    base = {
        **await page_context(request, db),
        "page_title": f"التسجيل في {club.name}",
        "club": club,
        "groups": groups,
        "sports": sports,
    }

    payload = {
        "full_name": _form_value(form, "full_name"),
        "birth_date": _as_date(_form_value(form, "birth_date")),
        "gender": _form_value(form, "gender"),
        "parent_name": _form_value(form, "parent_name"),
        "phone": _form_value(form, "phone"),
        "email": _form_value(form, "email"),
        "address": _form_value(form, "address"),
        "wilaya": _form_value(form, "wilaya"),
        "sport_id": int(_form_value(form, "sport_id", 0) or 0) or None,
        "preferred_group_id": int(_form_value(form, "preferred_group_id", 0) or 0) or None,
        "level": _form_value(form, "level"),
        "message": _form_value(form, "message"),
    }

    try:
        await club_service.submit_registration(db, payload, club_id=club.id)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        return _render(
            request, "public/register.html",
            {**base, "error": exc.message, "form": form},
            status_code=exc.status_code,
        )

    return _render(
        request,
        "public/register_success.html",
        {**base, "page_title": "تم إرسال الطلب"},
    )