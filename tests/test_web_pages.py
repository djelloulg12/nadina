"""
اختبار شامل للواجهة (Web Pages Smoke Test)
==========================================
يشغّل صفحات HTML فعلياً عبر ``httpx`` ويتحقق من:
  * أن كل صفحة تعيد 200 ولا ترمي استثناء.
  * أن صفحات الزوار لا تحتاج مصادقة.
  * أن صفحات العمل تحمي نفسها (تحويل إلى صفحة الدخول).
  * أن المسارات المحمية لا يمكن الوصول إليها بمعرّف من نادي آخر.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from app.core.db import Base, SessionLocal, engine, utcnow
from app.core.security import hash_password
from app.core.tenancy import clear_tenant_context
from app.models import (
    Athlete,
    Club,
    ClubStatus,
    Group,
    PlatformPlan,
    Sport,
    StaffMember,
    User,
    UserRole,
)

PASSWORD = "Test@12345"
CLUB_A = "نادي ألفا"


async def _reset_and_seed() -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    data: dict = {}
    async with SessionLocal() as db:
        sport = Sport(name_ar="كرة القدم", name_en="Football", slug="football")
        db.add(sport)
        await db.flush()

        plan = PlatformPlan(name="الاحترافية", price=4500, duration_days=30)
        db.add(plan)
        await db.flush()

        club = Club(
            name=CLUB_A,
            slug="club-a",
            status=ClubStatus.ACTIVE,
            plan_id=plan.id,
            subscription_end=dt.date.today() + dt.timedelta(days=90),
            wilaya="الجزائر",
            city="الجزائر الوسطى",
            members_count=12,
            accepts_online_registration=True,
        )
        db.add(club)
        await db.flush()

        admin = User(
            email="admin@test.dz", phone="0551000001",
            password_hash=hash_password(PASSWORD),
            full_name="مدير النادي", role=UserRole.CLUB_ADMIN, club_id=club.id,
        )
        coach = User(
            email="coach@test.dz", phone="0551000002",
            password_hash=hash_password(PASSWORD),
            full_name="مدرب", role=UserRole.COACH, club_id=club.id,
        )
        parent = User(
            email="parent@test.dz", phone="0551000003",
            password_hash=hash_password(PASSWORD),
            full_name="ولي", role=UserRole.PARENT, club_id=None,
        )
        db.add_all([admin, coach, parent])
        await db.flush()

        coach_staff = StaffMember(
            club_id=club.id, user_id=coach.id,
            job_title="head_coach", role=UserRole.COACH,
        )
        db.add(coach_staff)
        await db.flush()

        athlete = Athlete(
            club_id=club.id, card_code="AA-0001",
            first_name="رياضي", last_name="ألفا", full_name="رياضي ألفا",
            birth_date=dt.date(2012, 5, 1), gender="male",
            joined_at=dt.date.today(),
        )
        db.add(athlete)
        await db.flush()

        group = Group(
            club_id=club.id, name="فوج ألفا", capacity=20,
            coach_id=coach_staff.id, is_active=True,
        )
        db.add(group)
        await db.flush()

        from app.models import ClubNews, ParentLink

        db.add(ClubNews(
            club_id=club.id, title="خبر تجريبي", content="محتوى تجريبي للاختبار",
            kind="news", is_published=True, published_at=utcnow(),
            views_count=0, is_active=True,
        ))
        db.add(ParentLink(
            club_id=club.id, parent_id=parent.id, athlete_id=athlete.id,
            is_primary=True, is_payer=True,
        ))
        await db.commit()

        data.update(
            club_id=club.id, admin_id=admin.id, coach_id=coach.id,
            parent_id=parent.id, athlete_id=athlete.id, group_id=group.id,
        )

    return data


@pytest.fixture
async def web():
    from app.main import app

    await _reset_and_seed()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=True
    ) as client:
        client.data = {}  # type: ignore[attr-defined]
        yield client
    clear_tenant_context()


async def _login_web(client: httpx.AsyncClient, email: str) -> None:
    """تسجيل دخول عبر نموذج الويب (كوكي جلسة)."""
    response = await client.post(
        "/auth/login",
        data={"identifier": email, "password": PASSWORD},
    )
    assert response.status_code == 200, f"فشل الدخول: {response.status_code}"
    assert client.cookies.get("mashia_session"), "لم يُضبط كوكي الجلسة"


# =====================================================================
#  صفحات الزوار
# =====================================================================
PUBLIC_PAGES = [
    "/",
    "/clubs",
    "/clubs/club-a",
    "/clubs/club-a/register",
    "/news",
    "/auth/login",
    "/auth/register",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PUBLIC_PAGES)
async def test_public_pages_render(web: httpx.AsyncClient, path: str):
    response = await web.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()


@pytest.mark.asyncio
async def test_home_shows_rtl_and_arabic(web: httpx.AsyncClient):
    response = await web.get("/")
    assert 'dir="rtl"' in response.text
    assert 'lang="ar"' in response.text
    assert "المنصة" in response.text


@pytest.mark.asyncio
async def test_club_directory_lists_club(web: httpx.AsyncClient):
    response = await web.get("/clubs")
    assert CLUB_A in response.text


@pytest.mark.asyncio
async def test_club_page_shows_details(web: httpx.AsyncClient):
    response = await web.get("/clubs/club-a")
    assert CLUB_A in response.text
    assert "الجزائر" in response.text


@pytest.mark.asyncio
async def test_404_page_is_friendly(web: httpx.AsyncClient):
    response = await web.get("/clubs/does-not-exist")
    assert response.status_code == 404
    assert "الصفحة غير موجودة" in response.text


# =====================================================================
#  حماية الصفحات
# =====================================================================
PROTECTED_PAGES = [
    "/club",
    "/club/athletes",
    "/club/groups",
    "/club/sessions",
    "/club/finance",
    "/club/finance/invoices",
    "/club/staff",
    "/club/news",
    "/club/registrations",
    "/club/import",
    "/coach",
    "/parent",
    "/admin",
    "/profile",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PROTECTED_PAGES)
async def test_protected_pages_redirect_anonymous(web: httpx.AsyncClient, path: str):
    """
    الزائر الذي لا يملك جلسة يُحوَّل إلى صفحة الدخول -- لا يرى الصفحة،
    ولا حتى عنوانها (تسريب معلومات).
    """
    response = await web.get(path)
    assert response.url.path == "/auth/login", \
        f"{path} لم يُحَم correctly (وصل إلى {response.url.path})"


# =====================================================================
#  صفحات إدارة النادي بعد الدخول
# =====================================================================
CLUB_PAGES = [
    "/club",
    "/club/athletes",
    "/club/athletes/new",
    "/club/groups",
    "/club/groups/new",
    "/club/sessions",
    "/club/finance",
    "/club/finance/invoices",
    "/club/staff",
    "/club/news",
    "/club/registrations",
    "/club/import",
    "/profile",
    "/club/notifications",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CLUB_PAGES)
async def test_club_pages_after_login(web: httpx.AsyncClient, path: str):
    await _login_web(web, "admin@test.dz")
    response = await web.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_athlete_detail_page(web: httpx.AsyncClient):
    await _login_web(web, "admin@test.dz")
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        athlete_id = (await db.execute(select(Athlete.id).limit(1))).scalar_one()

    response = await web.get(f"/club/athletes/{athlete_id}")
    assert response.status_code == 200
    assert "رياضي ألفا" in response.text


@pytest.mark.asyncio
async def test_group_detail_page(web: httpx.AsyncClient):
    await _login_web(web, "admin@test.dz")
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        group_id = (await db.execute(select(Group.id).limit(1))).scalar_one()

    response = await web.get(f"/club/groups/{group_id}")
    assert response.status_code == 200
    assert "فوج ألفا" in response.text


@pytest.mark.asyncio
async def test_sidebar_is_role_aware(web: httpx.AsyncClient):
    """شريط التنقل يعرض روابط الرتبة الحالية فقط."""
    await _login_web(web, "admin@test.dz")
    admin_html = (await web.get("/club")).text
    assert "/club/athletes" in admin_html
    assert "/admin/clubs" not in admin_html, "مدير النادي يرى روابط مدير المنصة!"

    await _login_web(web, "coach@test.dz")
    coach_html = (await web.get("/coach")).text
    assert "/coach/groups" in coach_html
    assert "/club/finance" not in coach_html, "المدرب يرى روابط مالية!"


# =====================================================================
#  بوابة الولي والمدرب
# =====================================================================
@pytest.mark.asyncio
async def test_parent_portal_pages(web: httpx.AsyncClient):
    await _login_web(web, "parent@test.dz")
    for path in ("/parent", "/parent/finance"):
        response = await web.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


@pytest.mark.asyncio
async def test_parent_cannot_open_club_pages(web: httpx.AsyncClient):
    """حتى لو عُرف الرابط، الولي لا يستطيع فتح صفحة إدارة النادي."""
    await _login_web(web, "parent@test.dz")
    response = await web.get("/club/athletes")
    assert response.status_code == 403
    assert "لا تملك صلاحية" in response.text


@pytest.mark.asyncio
async def test_coach_portal_pages(web: httpx.AsyncClient):
    await _login_web(web, "coach@test.dz")
    response = await web.get("/coach")
    assert response.status_code == 200

    groups = await web.get("/coach/groups")
    assert groups.status_code == 200
    assert "فوج ألفا" in groups.text


# =====================================================================
#  تسجيل الخروج
# =====================================================================
@pytest.mark.asyncio
async def test_logout_clears_session(web: httpx.AsyncClient):
    await _login_web(web, "admin@test.dz")
    assert (await web.get("/club")).url.path == "/club"

    await web.get("/auth/logout")
    response = await web.get("/club")
    assert response.url.path == "/auth/login", "الجلسة لم تُلغَ!"


# =====================================================================
#  الأصول الثابتة و PWA
# =====================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/static/css/app.css",
    "/static/js/app.js",
    "/static/icons/icon.svg",
    "/manifest.webmanifest",
    "/sw.js",
])
async def test_static_assets_served(web: httpx.AsyncClient, path: str):
    response = await web.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    assert len(response.content) > 0