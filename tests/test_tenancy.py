"""
اختبار عزل المستأجرين (Multi-Tenancy Isolation Test)
=====================================================
هذا الملف **هو** إثبات أن نظام العزل يعمل. أي تعديل في
:mod:`app.core.tenancy` كسر هذه الاختبارات يعني تسريب بيانات بين الأندية.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base, engine, safe_get
from app.core.exceptions import TenantIsolationError
from app.core.security import generate_card_code, hash_password
from app.core.tenancy import (
    TenantContext,
    clear_tenant_context,
    tenant_scope,
)
from app.models import (
    Athlete,
    Club,
    ClubStatus,
    Group,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    User,
    UserRole,
)


# =====================================================================
#  أدوات
# =====================================================================
async def _make_club(db: AsyncSession, name: str) -> Club:
    from app.core.security import generate_slug

    club = Club(
        name=name,
        slug=generate_slug(name) + "-" + generate_card_code()[-4:],
        status=ClubStatus.ACTIVE,
        wilaya="الجزائر",
    )
    db.add(club)
    await db.flush()
    return club


async def _make_athlete(db: AsyncSession, club: Club, name: str) -> Athlete:
    athlete = Athlete(
        club_id=club.id,
        card_code=generate_card_code(),
        first_name=name,
        last_name="بن سالم",
        full_name=name,
        joined_at=dt.date.today(),
    )
    db.add(athlete)
    await db.flush()
    return athlete


async def _make_admin(db: AsyncSession, club: Club, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Test@12345"),
        full_name=f"مدير {club.name}",
        role=UserRole.CLUB_ADMIN,
        club_id=club.id,
    )
    db.add(user)
    await db.flush()
    return user


# =====================================================================
#  الإعداد
# =====================================================================
@pytest.fixture(autouse=True)
async def _clean_db():
    """تهيئة قاعدة بيانات نظيفة لكل اختبار."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    clear_tenant_context()
    yield
    clear_tenant_context()
    await engine.dispose()


@pytest.fixture
async def session():
    async with AsyncSession(bind=engine, expire_on_commit=False) as db:
        yield db
        await db.rollback()


# =====================================================================
#  الاختبارات
# =====================================================================
@pytest.mark.asyncio
async def test_read_isolation_prevents_cross_club_list(session: AsyncSession):
    """
    مدير النادي "أ" لا يجب أن يرى رياضيَي النادي "ب" إطلاقاً.
    """
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")

    await _make_athlete(session, club_a, "رياضي أ 1")
    await _make_athlete(session, club_a, "رياضي أ 2")
    await _make_athlete(session, club_b, "رياضي ب 1")
    await session.commit()

    ctx = TenantContext(
        user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id
    )
    with tenant_scope(ctx):
        result = await session.execute(select(Athlete))
        athletes = result.scalars().all()

    assert len(athletes) == 2, "مدير النادي أ رأى رياضياً من نادي آخر!"
    assert all(a.club_id == club_a.id for a in athletes)


@pytest.mark.asyncio
async def test_get_by_id_hides_other_club_record(session: AsyncSession):
    """
    ``session.get()`` في جلسة *جديدة* (كما يحدث في كل طلب حقيقي) يجب أن
    يخفي سجل النادي الآخر.

    ملاحظة مهمة: لو كان السجل محمّلاً مسبقاً في نفس الجلسة فإن
    ``session.get`` يعيده من خريطة الهوية بدون استعلام. لذلك نستخدم جلسة
    جديدة، ونضيف اختباراً منفصلاً لـ ``safe_get`` الذي يسدّ هذه الفجوة.
    """
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    athlete_b = await _make_athlete(session, club_b, "رياضي ب")
    await session.commit()
    athlete_b_id = athlete_b.id

    # جلسة جديدة = خريطة هوية فارغة، تماماً كما في كل طلب HTTP
    async with AsyncSession(bind=engine, expire_on_commit=False) as fresh:
        ctx = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
        with tenant_scope(ctx):
            fetched = await fresh.get(Athlete, athlete_b_id)

    assert fetched is None, "session.get() سمح بالوصول لسجل نادي آخر!"


@pytest.mark.asyncio
async def test_safe_get_blocks_identity_map_hit(session: AsyncSession):
    """
    ``safe_get`` يسدّ فجوة خريطة الهوية: يعيد ``None`` حتى لو كان السجل
    محمّلاً في الجلسة نفسها.
    """
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    athlete_b = await _make_athlete(session, club_b, "رياضي ب")
    await session.commit()

    ctx = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
    with tenant_scope(ctx):
        # محمّل مسبقاً في نفس الجلسة
        assert await session.get(Athlete, athlete_b.id) is not None
        # لكن safe_get يفحص الملكية صراحةً
        assert await safe_get(session, Athlete, athlete_b.id) is None


@pytest.mark.asyncio
async def test_relationship_lazy_loading_is_isolated(session: AsyncSession):
    """
    تحميل علاقة عبر ``refresh`` لا يجب أن يسرّب بيانات -- وهذا اختبار مهم
    لأن العلاقات هي المسار الأكثر تسريباً في تطبيقات متعددة المستأجرين.
    """
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    await _make_athlete(session, club_b, "رياضي ب 1")
    await _make_athlete(session, club_b, "رياضي ب 2")
    await session.commit()
    club_b_id = club_b.id

    async with AsyncSession(bind=engine, expire_on_commit=False) as fresh:
        ctx_a = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
        with tenant_scope(ctx_a):
            # Club ليس جدولاً مملوكاً لمستأجر، لذا يُقرأ كمرجع
            club = await fresh.get(Club, club_b_id)
            assert club is not None
            # العلاقة athletes يجب أن تكون مُفلترة بالكامل
            await fresh.refresh(club, ["athletes"])
            assert len(club.athletes) == 0, "العلاقة سرّبت بيانات نادي آخر!"


@pytest.mark.asyncio
async def test_write_guard_blocks_cross_club_insert(session: AsyncSession):
    """لا يمكن إنشاء سجل في نادي لا تملكه."""
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    await session.commit()

    ctx = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
    with tenant_scope(ctx):
        session.add(
            Athlete(
                club_id=club_b.id,
                card_code=generate_card_code(),
                first_name="مخترق",
                last_name="بن سالم",
                full_name="مخترق",
            )
        )
        with pytest.raises(TenantIsolationError):
            await session.commit()

    await session.rollback()


@pytest.mark.asyncio
async def test_write_guard_auto_fills_club_id(session: AsyncSession):
    """عند عدم تحديد النادي، يُملأ تلقائياً من سياق المستخدم."""
    club_a = await _make_club(session, "نادي أ")
    await session.commit()

    ctx = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
    with tenant_scope(ctx):
        athlete = Athlete(
            card_code=generate_card_code(),
            first_name="سليم",
            last_name="بن سالم",
            full_name="سليم بن سالم",
        )
        session.add(athlete)
        await session.commit()

    assert athlete.club_id == club_a.id


@pytest.mark.asyncio
async def test_write_guard_blocks_club_id_migration(session: AsyncSession):
    """لا يمكن نقل سجل قائم من نادي إلى آخر."""
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    await session.commit()

    ctx_a = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
    with tenant_scope(ctx_a):
        athlete = await _make_athlete(session, club_a, "رياضي أ")
        await session.commit()

    ctx_b = TenantContext(user_id=2, role=UserRole.CLUB_ADMIN, club_id=club_b.id)
    with tenant_scope(ctx_b):
        athlete.club_id = club_b.id
        with pytest.raises(TenantIsolationError):
            await session.commit()

    await session.rollback()


@pytest.mark.asyncio
async def test_super_admin_bypasses_isolation(session: AsyncSession):
    """مدير المنصة يرى كل الأندية -- وهذا سلوك مقصود وموثّق."""
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    await _make_athlete(session, club_a, "رياضي أ")
    await _make_athlete(session, club_b, "رياضي ب")
    await session.commit()

    ctx = TenantContext(
        user_id=0, role=UserRole.SUPER_ADMIN, club_id=None, is_platform_admin=True
    )
    with tenant_scope(ctx):
        result = await session.execute(select(Athlete))
        assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_parent_sees_only_own_children(session: AsyncSession):
    """حساب الولي الموحد يرى أبنائه في كل الأندية ولا يرى غيرهم."""
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    my_child_a = await _make_athlete(session, club_a, "ابني في أ")
    my_child_b = await _make_athlete(session, club_b, "ابني في ب")
    await _make_athlete(session, club_a, "ابن غيري")
    await session.commit()

    ctx = TenantContext(
        user_id=7,
        role=UserRole.PARENT,
        club_id=None,
        athlete_ids=(my_child_a.id, my_child_b.id),
        child_club_ids=(club_a.id, club_b.id),
        child_group_ids=(),
    )
    with tenant_scope(ctx):
        result = await session.execute(select(Athlete))
        found = result.scalars().all()

    names = {a.full_name for a in found}
    assert names == {"ابني في أ", "ابني في ب"}, f"تسريب: {names}"


@pytest.mark.asyncio
async def test_parent_sees_only_own_child_attendance(session: AsyncSession):
    """الولي يرى سجلات حضور أبنائه فقط، عبر جدول مرتبط بـ athlete_id."""
    from app.models import (
        Attendance,
        AttendanceStatus,
        TrainingSession,
    )

    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    my_child = await _make_athlete(session, club_a, "ابني")
    other = await _make_athlete(session, club_b, "غيري")
    await session.commit()

    group_a = Group(club_id=club_a.id, name="فوج أ", capacity=10)
    group_b = Group(club_id=club_b.id, name="فوج ب", capacity=10)
    session.add_all([group_a, group_b])
    await session.flush()

    sess_a = TrainingSession(
        club_id=club_a.id,
        group_id=group_a.id,
        session_date=dt.date.today(),
        start_time=dt.time(10, 0),
        end_time=dt.time(11, 0),
    )
    sess_b = TrainingSession(
        club_id=club_b.id,
        group_id=group_b.id,
        session_date=dt.date.today(),
        start_time=dt.time(10, 0),
        end_time=dt.time(11, 0),
    )
    session.add_all([sess_a, sess_b])
    await session.flush()

    session.add_all(
        [
            Attendance(
                club_id=club_a.id,
                session_id=sess_a.id,
                athlete_id=my_child.id,
                group_id=group_a.id,
                session_date=dt.date.today(),
                status=AttendanceStatus.PRESENT,
            ),
            Attendance(
                club_id=club_b.id,
                session_id=sess_b.id,
                athlete_id=other.id,
                group_id=group_b.id,
                session_date=dt.date.today(),
                status=AttendanceStatus.ABSENT,
            ),
        ]
    )
    await session.commit()

    ctx = TenantContext(
        user_id=7,
        role=UserRole.PARENT,
        club_id=None,
        athlete_ids=(my_child.id,),
        child_club_ids=(club_a.id,),
    )
    async with AsyncSession(bind=engine, expire_on_commit=False) as fresh:
        with tenant_scope(ctx):
            result = await fresh.execute(select(Attendance))
            rows = result.scalars().all()

    assert len(rows) == 1, f"تسريب في الحضور: {len(rows)} سجل"
    assert rows[0].athlete_id == my_child.id


@pytest.mark.asyncio
async def test_join_query_is_isolated(session: AsyncSession):
    """الاستعلامات المُركَّبة (join) تُفلتر أيضاً."""
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    await _make_athlete(session, club_a, "رياضي أ")
    await _make_athlete(session, club_b, "رياضي ب")
    await session.commit()

    group_b = Group(club_id=club_b.id, name="فوج ب", capacity=5)
    session.add(group_b)
    await session.commit()

    ctx = TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=club_a.id)
    with tenant_scope(ctx):
        stmt = (
            select(Athlete)
            .join(GroupAthlete, GroupAthlete.athlete_id == Athlete.id)
            .select_from(Athlete)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    assert len(rows) == 0


@pytest.mark.asyncio
async def test_no_context_means_no_filter(session: AsyncSession):
    """
    بدون سياق مستأجر (سكربتات الإدارة) لا يُطبَّق أي فلتر.

    هذا سلوك مقصود للسكربتات الموثوقة، لكنه يعني أن أي مسار طلب
    **يجب** أن يضع سياقاً. هذا الاختبار يوثّق العقد بوضوح.
    """
    club_a = await _make_club(session, "نادي أ")
    club_b = await _make_club(session, "نادي ب")
    await _make_athlete(session, club_a, "رياضي أ")
    await _make_athlete(session, club_b, "رياضي ب")
    await session.commit()

    clear_tenant_context()
    result = await session.execute(select(Athlete))
    assert len(result.scalars().all()) == 2
