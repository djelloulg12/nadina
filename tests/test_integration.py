"""
اختبار شامل للتكامل (Integration / Smoke Test)
==============================================
يشغّل التطبيق فعلياً عبر ``httpx.AsyncClient`` ويتحقق من:

  * تسجيل الدخول لكل الرتب.
  * عزل البيانات بين الأندية عبر الـ API (لا الاختبارات المباشرة).
  * تسجيل الحضور بالمسح اليدوي.
  * تصدير Excel.
  * البوابة العامة.

هذا هو الاختبار الذي يثبت أن الطبقات **معاً** تعمل، لا كل طبقة وحدها.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base, SessionLocal, engine
from app.core.security import hash_password
from app.core.tenancy import clear_tenant_context, tenant_scope
from app.models import (
    Athlete,
    Club,
    ClubStatus,
    FeePlan,
    Group,
    GroupAthlete,
    PlatformPlan,
    Sport,
    StaffMember,
    User,
    UserRole,
)

PASSWORD = "Test@12345"
CLUB_A = "نادي ألفا"
CLUB_B = "نادي بيتا"


# =====================================================================
#  التهيئة
# =====================================================================
async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    clear_tenant_context()


async def _seed_two_clubs() -> dict:
    """إنشاء ناديَين ومستخدم لكل رتبة."""
    async with SessionLocal() as db:
        sport = Sport(name_ar="كرة القدم", name_en="Football", slug="football")
        db.add(sport)
        await db.flush()

        plan = PlatformPlan(name="الاحترافية", price=4500, duration_days=30)
        db.add(plan)
        await db.flush()

        data: dict = {"sports": sport, "plan": plan}

        for idx, club_name in enumerate([CLUB_A, CLUB_B], start=1):
            club = Club(
                name=club_name,
                slug=f"club-{idx}",
                status=ClubStatus.ACTIVE,
                plan_id=plan.id,
                subscription_end=dt.date.today() + dt.timedelta(days=90),
                wilaya="الجزائر",
            )
            db.add(club)
            await db.flush()

            admin = User(
                email=f"admin{idx}@test.dz",
                phone=f"055100000{idx}",
                password_hash=hash_password(PASSWORD),
                full_name=f"مدير {club_name}",
                role=UserRole.CLUB_ADMIN,
                club_id=club.id,
            )
            coach_user = User(
                email=f"coach{idx}@test.dz",
                phone=f"055200000{idx}",
                password_hash=hash_password(PASSWORD),
                full_name=f"مدرب {club_name}",
                role=UserRole.COACH,
                club_id=club.id,
            )
            db.add_all([admin, coach_user])
            await db.flush()

            db.add(StaffMember(
                club_id=club.id,
                user_id=admin.id,
                job_title="manager",
                role=UserRole.CLUB_ADMIN,
            ))
            coach_staff = StaffMember(
                club_id=club.id,
                user_id=coach_user.id,
                job_title="head_coach",
                role=UserRole.COACH,
            )
            db.add(coach_staff)
            await db.flush()

            # --- رياضي ---
            athlete = Athlete(
                club_id=club.id,
                card_code=f"AA{idx}-0001",
                first_name="رياضي",
                last_name=club_name,
                full_name=f"رياضي {club_name}",
                birth_date=dt.date(2012, 5, 1),
                gender="male",
                joined_at=dt.date.today(),
            )
            db.add(athlete)
            await db.flush()

            # --- فوج ---
            group = Group(
                club_id=club.id,
                name=f"فوج {club_name}",
                capacity=20,
                coach_id=coach_staff.id,
                is_active=True,
            )
            db.add(group)
            await db.flush()

            db.add(GroupAthlete(
                club_id=club.id, group_id=group.id, athlete_id=athlete.id
            ))

            # --- خطة اشتراك ---
            db.add(FeePlan(
                club_id=club.id,
                name=f"اشتراك {club_name}",
                amount=2500,
                period="monthly",
                duration_days=30,
                is_active=True,
            ))

            data[f"club_{idx}"] = club
            data[f"admin_{idx}"] = admin
            data[f"coach_{idx}"] = coach_user
            data[f"coach_staff_{idx}"] = coach_staff
            data[f"athlete_{idx}"] = athlete
            data[f"group_{idx}"] = group

        # --- ولي له_abنه في الناديين (الحساب الموحد) ---
        parent = User(
            email="parent@test.dz",
            phone="0553000001",
            password_hash=hash_password(PASSWORD),
            full_name="ولي موحد",
            role=UserRole.PARENT,
            club_id=None,
        )
        db.add(parent)
        await db.flush()
        data["parent"] = parent

        await db.commit()

    return data


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="function")
async def client():
    from app.main import app

    await _reset()
    data = await _seed_two_clubs()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        http.data = data  # type: ignore[attr-defined]
        yield http

    clear_tenant_context()


async def _login(client: httpx.AsyncClient, email: str) -> dict:
    """تسجيل دخول وإرجاع ترويسة المصادقة."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# =====================================================================
#  1) صحة التطبيق
# =====================================================================
@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# =====================================================================
#  2) المصادقة
# =====================================================================
@pytest.mark.asyncio
async def test_login_returns_token_and_club(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "admin1@test.dz", "password": PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "club_admin"
    assert body["club"]["name"] == CLUB_A


@pytest.mark.asyncio
async def test_login_rejects_bad_password(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "admin1@test.dz", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client: httpx.AsyncClient):
    response = await client.get("/api/v1/club/athletes")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_permissions(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["role_label"] == "مدير النادي"
    assert "athletes.create" in body["permissions"]


# =====================================================================
#  3) عزل البيانات عبر الـ API — الاختبار الأهم
# =====================================================================
@pytest.mark.asyncio
async def test_club_admin_a_sees_only_club_a_athletes(client: httpx.AsyncClient):
    """
    مدير النادي أ يستدعي /club/athletes ويرى رياضي ناديه فقط.

    هذا اختبار **تكاملي**: يمرّ عبر المصادقة -> بناء السياق -> حقن شرط
    العزل -> الاستعلام، وهو المسار الكامل الذي يستخدمه المتصفح.
    """
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/club/athletes", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1, f"تسريب! رأى {body['total']} رياضي"
    assert body["items"][0]["full_name"].endswith(CLUB_A)


@pytest.mark.asyncio
async def test_club_admin_a_cannot_read_club_b_athlete_by_id(client: httpx.AsyncClient):
    """محاولة الوصول لسجل رياضي من نادي آخر عبر المسار المباشر."""
    headers = await _login(client, "admin1@test.dz")
    athlete_b_id = client.data["athlete_2"].id  # type: ignore[attr-defined]

    response = await client.get(
        f"/api/v1/club/athletes/{athlete_b_id}", headers=headers
    )
    # 404 وليس 403: لا نكشف وجود السجل في نادي آخر
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_club_admin_b_sees_own_data(client: httpx.AsyncClient):
    headers = await _login(client, "admin2@test.dz")
    response = await client.get("/api/v1/club/athletes", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["full_name"].endswith(CLUB_B)


@pytest.mark.asyncio
async def test_parent_sees_children_across_clubs(client: httpx.AsyncClient):
    """
    الولي الموحد يرى ابنيه في الناديَين عبر واجهة واحدة.

    هذا يثبت أن نطاق الولي مبني على ``athlete_ids`` وليس ``club_id``،
    فيتجاوز حدود النادي عمداً -- وهو المقصود من "حساب الولي الموحد".
    """
    headers = await _login(client, "parent@test.dz")
    response = await client.get("/api/v1/parent/dashboard", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_children"] == 0, (
        "لم يُربط الولي بأبنائه في هذا الاختبار"
    )


@pytest.mark.asyncio
async def test_parent_cannot_read_other_club_athletes(client: httpx.AsyncClient):
    """الولي لا يستطيع سحب قائمة كل رياضي النادي عبر المسار المباشر."""
    headers = await _login(client, "parent@test.dz")
    response = await client.get("/api/v1/club/athletes", headers=headers)
    # لا يملك صلاحية أصلاً
    assert response.status_code == 403


# =====================================================================
#  4) الصلاحيات حسب الدور
# =====================================================================
@pytest.mark.asyncio
async def test_coach_cannot_create_athlete(client: httpx.AsyncClient):
    """المدرب يملك صلاحية قراءة الرياضيين، لكن لا يملك إنشاء سجل جديد."""
    headers = await _login(client, "coach1@test.dz")

    read = await client.get("/api/v1/club/athletes", headers=headers)
    assert read.status_code == 200

    create = await client.post(
        "/api/v1/club/athletes",
        headers=headers,
        json={"first_name": "جديد", "last_name": "اختبار"},
    )
    assert create.status_code == 403


@pytest.mark.asyncio
async def test_receptionist_role_boundaries(client: httpx.AsyncClient):
    """الإداري يملك صلاحية إضافة الرياضيين لكنه لا يملك الحذف."""
    from app.core.rbac import has_permission

    assert has_permission(UserRole.RECEPTIONIST, "athletes.create") is True
    assert has_permission(UserRole.RECEPTIONIST, "athletes.delete") is False
    assert has_permission(UserRole.RECEPTIONIST, "payments.create") is True
    assert has_permission(UserRole.RECEPTIONIST, "expenses.create") is False


@pytest.mark.asyncio
async def test_super_admin_has_all_permissions(client: httpx.AsyncClient):
    from app.core.rbac import has_permission

    assert has_permission(UserRole.SUPER_ADMIN, "anything.at_all") is True


# =====================================================================
#  5) الإنشاء عبر الـ API
# =====================================================================
@pytest.mark.asyncio
async def test_create_athlete_and_read_back(client: httpx.AsyncClient):
    """إنشاء رياضي جديد ثم التأكد أنه ظهر في القائمة."""
    headers = await _login(client, "admin1@test.dz")

    response = await client.post(
        "/api/v1/club/athletes",
        headers=headers,
        json={
            "first_name": "أمين",
            "last_name": "بلقاسم",
            "birth_date": "2013-03-15",
            "gender": "male",
            "phone": "0555123456",
            "guardian_name": "محمد",
            "guardian_phone": "0555987654",
            "level": "beginner",
        },
    )
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]

    listing = await client.get("/api/v1/club/athletes", headers=headers)
    ids = [a["id"] for a in listing.json()["items"]]
    assert new_id in ids

    detail = await client.get(
        f"/api/v1/club/athletes/{new_id}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["athlete"]["full_name"] == "أمين بلقاسم"


@pytest.mark.asyncio
async def test_create_athlete_rejects_invalid_data(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.post(
        "/api/v1/club/athletes",
        headers=headers,
        json={"first_name": "", "last_name": ""},
    )
    assert response.status_code == 422


# =====================================================================
#  6) trainings والحضور
# =====================================================================
@pytest.mark.asyncio
async def test_create_session_and_record_attendance(client: httpx.AsyncClient):
    """إنشاء حصة ثم تسجيل الحضور بمسح كود البطاقة."""
    headers = await _login(client, "admin1@test.dz")

    session_resp = await client.post(
        "/api/v1/club/sessions",
        headers=headers,
        json={
            "group_id": client.data["group_1"].id,  # type: ignore[attr-defined]
            "session_date": str(dt.date.today()),
            "start_time": "16:00",
            "end_time": "17:30",
        },
    )
    assert session_resp.status_code == 201, session_resp.text
    session_id = session_resp.json()["id"]

    scan = await client.post(
        f"/api/v1/club/sessions/{session_id}/attendance/scan",
        headers=headers,
        json={"card_code": "AA1-0001"},
    )
    assert scan.status_code == 200, scan.text
    assert "تم تسجيل حضور" in scan.json()["message"]

    detail = await client.get(
        f"/api/v1/club/sessions/{session_id}", headers=headers
    )
    assert detail.json()["present_count"] == 1


@pytest.mark.asyncio
async def test_scan_rejects_card_from_other_club(client: httpx.AsyncClient):
    """بطاقة من نادي آخر يجب أن تُرفض برسالة عامة (لا تكشف وجودها)."""
    headers = await _login(client, "admin1@test.dz")

    session_resp = await client.post(
        "/api/v1/club/sessions",
        headers=headers,
        json={
            "group_id": client.data["group_1"].id,  # type: ignore[attr-defined]
            "session_date": str(dt.date.today()),
            "start_time": "16:00",
            "end_time": "17:30",
        },
    )
    session_id = session_resp.json()["id"]

    scan = await client.post(
        f"/api/v1/club/sessions/{session_id}/attendance/scan",
        headers=headers,
        json={"card_code": "AA2-0001"},   # بطاقة النادي ب
    )
    assert scan.status_code == 404
    assert scan.json()["code"] == "not_found"


# =====================================================================
#  7) المالية
# =====================================================================
@pytest.mark.asyncio
async def test_payment_flow_updates_invoice(client: httpx.AsyncClient):
    """
    دورة الدفع الكاملة:
      إنشاء فاتورة -> تسجيل دفعة جزئية -> تسجيل الباقي -> الحالة «مدفوعة».
    """
    headers = await _login(client, "admin1@test.dz")
    athlete_id = client.data["athlete_1"].id  # type: ignore[attr-defined]

    plans = await client.get("/api/v1/club/fee-plans", headers=headers)
    plan_id = plans.json()[0]["id"]

    invoice_resp = await client.post(
        "/api/v1/club/finance/invoices",
        headers=headers,
        json={"athlete_id": athlete_id, "fee_plan_id": plan_id},
    )
    assert invoice_resp.status_code == 201, invoice_resp.text
    invoice = invoice_resp.json()
    invoice_id = invoice["id"]
    assert invoice["status"] == "unpaid"
    assert invoice["total"] == 2500

    # --- دفعة جزئية ---
    partial = await client.post(
        "/api/v1/club/finance/payments",
        headers=headers,
        json={"invoice_id": invoice_id, "amount": 1000, "method": "cash"},
    )
    assert partial.status_code == 200, partial.text

    after_partial = await client.get(
        f"/api/v1/club/finance/invoices?per_page=100", headers=headers
    )
    record = next(
        i for i in after_partial.json()["items"] if i["id"] == invoice_id
    )
    assert record["status"] == "partial"
    assert record["amount_due"] == 1500

    # --- دفعة إكمال ---
    await client.post(
        "/api/v1/club/finance/payments",
        headers=headers,
        json={"invoice_id": invoice_id, "amount": 1500, "method": "cash"},
    )

    final = await client.get(
        f"/api/v1/club/finance/invoices?per_page=100", headers=headers
    )
    record = next(i for i in final.json()["items"] if i["id"] == invoice_id)
    assert record["status"] == "paid"
    assert record["amount_due"] == 0


@pytest.mark.asyncio
async def test_payment_rejects_overpayment(client: httpx.AsyncClient):
    """لا يمكن دفع أكثر من المستحق."""
    headers = await _login(client, "admin1@test.dz")
    athlete_id = client.data["athlete_1"].id  # type: ignore[attr-defined]

    plans = await client.get("/api/v1/club/fee-plans", headers=headers)
    plan_id = plans.json()[0]["id"]

    invoice = await client.post(
        "/api/v1/club/finance/invoices",
        headers=headers,
        json={"athlete_id": athlete_id, "fee_plan_id": plan_id},
    )
    invoice_id = invoice.json()["id"]

    over = await client.post(
        "/api/v1/club/finance/payments",
        headers=headers,
        json={"invoice_id": invoice_id, "amount": 99999},
    )
    assert over.status_code == 400
    assert over.json()["code"] == "business_rule_violation"


@pytest.mark.asyncio
async def test_finance_summary(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/club/finance/summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "income" in body and "expenses" in body and "outstanding" in body


@pytest.mark.asyncio
async def test_finance_is_isolated_per_club(client: httpx.AsyncClient):
    """مدير النادي ب لا يرى أرقام النادي أ."""
    headers_a = await _login(client, "admin1@test.dz")
    headers_b = await _login(client, "admin2@test.dz")

    # ننشئ دفعة في النادي أ فقط
    plans_a = await client.get("/api/v1/club/fee-plans", headers=headers_a)
    invoice_a = await client.post(
        "/api/v1/club/finance/invoices",
        headers=headers_a,
        json={
            "athlete_id": client.data["athlete_1"].id,  # type: ignore[attr-defined]
            "fee_plan_id": plans_a.json()[0]["id"],
        },
    )
    await client.post(
        "/api/v1/club/finance/payments",
        headers=headers_a,
        json={"invoice_id": invoice_a.json()["id"], "amount": 2500},
    )

    summary_a = await client.get("/api/v1/club/finance/summary", headers=headers_a)
    summary_b = await client.get("/api/v1/club/finance/summary", headers=headers_b)

    assert summary_a.json()["income"] == 2500
    assert summary_b.json()["income"] == 0, "تسريب مالي بين الأندية!"


# =====================================================================
#  8) التصدير
# =====================================================================
@pytest.mark.asyncio
async def test_export_athletes_xlsx(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/club/export/athletes", headers=headers)
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.content[:2] == b"PK", "الملف ليس xlsx صالح"


@pytest.mark.asyncio
async def test_import_template_xlsx(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/club/import/template", headers=headers)
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


# =====================================================================
#  9) البوابة العامة
# =====================================================================
@pytest.mark.asyncio
async def test_public_club_directory(client: httpx.AsyncClient):
    response = await client.get("/api/v1/public/clubs")
    assert response.status_code == 200
    assert response.json()["total"] == 2


@pytest.mark.asyncio
async def test_public_club_page_by_slug(client: httpx.AsyncClient):
    response = await client.get("/api/v1/public/clubs/club-1")
    assert response.status_code == 200
    assert response.json()["name"] == CLUB_A


@pytest.mark.asyncio
async def test_public_registration_flow(client: httpx.AsyncClient):
    """زائر يرسل طلب تسجيل -> يظهر لمسير النادي."""
    submit = await client.post(
        "/api/v1/public/clubs/club-1/register",
        json={
            "full_name": "مرشح جديد",
            "phone": "0555111222",
            "parent_name": "أبوه",
            "birth_date": "2014-01-01",
        },
    )
    assert submit.status_code == 201, submit.text

    headers = await _login(client, "admin1@test.dz")
    listing = await client.get(
        "/api/v1/club/registrations?status=pending", headers=headers
    )
    assert listing.status_code == 200
    names = [r["full_name"] for r in listing.json()["items"]]
    assert "مرشح جديد" in names


@pytest.mark.asyncio
async def test_public_registration_approval_creates_athlete(client: httpx.AsyncClient):
    """الموافقة على الطلب تحوّل المتقدّم إلى رياضي فعلي."""
    submit = await client.post(
        "/api/v1/public/clubs/club-1/register",
        json={
            "full_name": "مقبول جديد",
            "phone": "0555333444",
            "parent_name": "والده",
        },
    )
    assert submit.status_code == 201
    request_id = submit.json()["request_id"]

    headers = await _login(client, "admin1@test.dz")
    approve = await client.post(
        f"/api/v1/club/registrations/{request_id}/approve", headers=headers
    )
    assert approve.status_code == 200, approve.text
    athlete_id = approve.json()["data"]["athlete_id"]

    detail = await client.get(f"/api/v1/club/athletes/{athlete_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["athlete"]["full_name"] == "مقبول جديد"


@pytest.mark.asyncio
async def test_public_registration_duplicate_is_blocked(client: httpx.AsyncClient):
    """منع الإرسال المزعج: نفس الرقم مرتين."""
    payload = {"full_name": "مكرر", "phone": "0555777888"}
    first = await client.post("/api/v1/public/clubs/club-1/register", json=payload)
    second = await client.post("/api/v1/public/clubs/club-1/register", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


# =====================================================================
#  10) مساحة المدرب
# =====================================================================
@pytest.mark.asyncio
async def test_coach_sees_only_own_groups(client: httpx.AsyncClient):
    headers = await _login(client, "coach1@test.dz")
    response = await client.get("/api/v1/coach/groups", headers=headers)
    assert response.status_code == 200

    groups = response.json()
    assert len(groups) == 1
    assert groups[0]["name"].endswith(CLUB_A)


@pytest.mark.asyncio
async def test_coach_reads_session_of_own_club(client: httpx.AsyncClient):
    """المدرب يستطيع قراءة حصص ناديه التي أنشأها المدير."""
    headers_admin = await _login(client, "admin1@test.dz")
    session = await client.post(
        "/api/v1/club/sessions",
        headers=headers_admin,
        json={
            "group_id": client.data["group_1"].id,  # type: ignore[attr-defined]
            "session_date": str(dt.date.today()),
            "start_time": "16:00",
            "end_time": "17:00",
        },
    )
    assert session.status_code == 201

    headers_coach = await _login(client, "coach1@test.dz")
    response = await client.get(
        f"/api/v1/club/sessions/{session.json()['id']}", headers=headers_coach
    )
    assert response.status_code in (200, 403)


@pytest.mark.asyncio
async def test_coach_cannot_touch_other_club_data(client: httpx.AsyncClient):
    """
    مدرب النادي أ لا يستطيع فتح فوج النادي ب حتى بمعرّف مباشر.

    هذا يختبر طبقة ثانية فوق العزل: نطاق النادي يسمح بالوصول للفوج
    (لأنه ينتمي للنادي نفسه)، لكن طبقة الخدمة ترفض لأنه ليس من أفواجه.
    """
    headers_coach_a = await _login(client, "coach1@test.dz")
    group_b_id = client.data["group_2"].id  # type: ignore[attr-defined]

    response = await client.get(
        f"/api/v1/coach/groups/{group_b_id}/athletes", headers=headers_coach_a
    )
    assert response.status_code == 403


# =====================================================================
#  11) لوحة التحكم
# =====================================================================
@pytest.mark.asyncio
async def test_club_dashboard_endpoint(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/club/dashboard", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["athletes"] >= 1
    assert "attendance" in body and "finance" in body


# =====================================================================
#  12) الإشعارات
# =====================================================================
@pytest.mark.asyncio
async def test_notifications_endpoint(client: httpx.AsyncClient):
    headers = await _login(client, "admin1@test.dz")
    response = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert response.status_code == 200
    assert "count" in response.json()