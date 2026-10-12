"""
سكربت تعبئة البيانات التجريبية (Seed Script)
===========================================
ينشئ بيانات واقعية للتجربة والاستعراض:

  * الرياضات (12 رياضة)
  * خطط اشتراك المنصة
  * ناديان موثّقان + ناديان بانتظار الموافقة
  * مستخدمون لكل رتبة: مدير منصة، مديرا ناديين، إداري، مدربان، أولياء
  * أفواج، رياضيون، حصص، حضور، تقييمات
  * خطط اشتراك، فواتير، دفعات، مصاريف

**تنبيه**: هذا السكربت للتجربة فقط. كل كلمة مرور الحسابات التجريبية
هي ``Mashia@2024`` ويجب تغييرها فوراً في أي بيئة حقيقية.

التشغيل::

    python -m scripts.seed          # تعبئة
    python -m scripts.seed --reset  # حذف كل شيء ثم تعبئة
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core import schema  # noqa: E402
from app.core.db import Base, dispose_engine, engine, utcnow# noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.security import generate_card_code, hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Attendance,
    AttendanceMethod,
    AttendanceStatus,
    Athlete,
    Club,
    ClubNews,
    ClubSport,
    ClubStatus,
    ClubSubscription,
    Evaluation,
    EvaluationType,
    Expense,
    ExpenseCategory,
    FeePlan,
    Gender,
    Group,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    Level,
    ParentLink,
    Payment,
    PaymentMethod,
    PeriodType,
    PlatformPlan,
    PlatformPlanPeriod,
    RegistrationRequest,
    RegistrationStatus,
    SessionStatus,
    Sport,
    SportCategory,
    StaffJobTitle,
    StaffMember,
    Status,
    SubscriptionStatus,
    TrainingSession,
    User,
    UserRole,
)

PASSWORD = "Mashia@2024"
random.seed(20240)

# =====================================================================
#  البيانات المرجعية
# =====================================================================
SPORTS = [
    ("كرة القدم", "Football", "⚽", SportCategory.TEAM),
    ("كرة السلة", "Basketball", "🏀", SportCategory.TEAM),
    ("كرة اليد", "Handball", "🤾", SportCategory.TEAM),
    ("الكرة الطائرة", "Volleyball", "🏐", SportCategory.TEAM),
    ("السباحة", "Swimming", "🏊", SportCategory.INDIVIDUAL),
    ("ألعاب القوى", "Athletics", "🏃", SportCategory.INDIVIDUAL),
    ("الجودو", "Judo", "🥋", SportCategory.INDIVIDUAL),
    ("الملاكمة", "Boxing", "🥊", SportCategory.INDIVIDUAL),
    ("التنيس", "Tennis", "🎾", SportCategory.INDIVIDUAL),
    ("تايكوندو", "Taekwondo", "🥋", SportCategory.INDIVIDUAL),
    ("الجمباز", "Gymnastics", "🤸", SportCategory.INDIVIDUAL),
    ("رياضة أخرى", "Other", "⚡", SportCategory.TEAM),
]

PLANS = [
    ("المجانية", 0, PlatformPlanPeriod.MONTHLY, 30, None, None, None,
     ["حتى 30 رياضي", "أفواج غير محدودة", "لوحة تحكم أساسية"]),
    ("الاحترافية", 4500, PlatformPlanPeriod.MONTHLY, 30, 150, 20, 10,
     ["حتى 150 رياضي", "استيراد وتصدير Excel", "بطاقات QR", "تقارير متقدمة"]),
    ("المؤسسية", 12000, PlatformPlanPeriod.MONTHLY, 30, None, None, None,
     ["رياضيون غير محدودون", "كل الميزات", "دعم مخصص", "واجهة برمجية API"]),
]

CLUBS = [
    {
        "name": "نادي التوفيق الرياضي",
        "description": (
            "نادي متعدد الرياضات تأسس عام 2010، يضم مدربين معتمدين ومرافق تدريبية "
            "مجهّزة. نشتغل على تكوين الرياضي وإكسابهم قيم الانضباط والروح الرياضية."
        ),
        "wilaya": "الجزائر",
        "commune": "باب الوادي",
        "city": "الجزائر الوسطى",
        "address": "شارع ديدوش مراد، رقم 45",
        "phone": "021541020",
        "email": "contact@toufik-sport.dz",
        "website": "https://toufik-sport.dz",
        "min_age": 6,
        "max_age": 45,
        "primary_color": "#0F766E",
        "sports": ["كرة القدم", "كرة السلة", "السباحة", "ألعاب القوى"],
    },
    {
        "name": "أكاديمية النجوم",
        "description": (
            "أكاديمية متخصصة في تكوين المواهب الشابة في كرة القدم، مع فئات عمرية "
            "من 6 إلى 18 سنة وفق منهجية مدروسة ومتدرجة."
        ),
        "wilaya": "وهران",
        "commune": "بئر الجير",
        "city": "وهران",
        "address": "طريق الز Maurice، حي النصر",
        "phone": "041331245",
        "email": "info@noujoum-academy.dz",
        "min_age": 6,
        "max_age": 18,
        "primary_color": "#1D4ED8",
        "sports": ["كرة القدم", "كرة اليد", "التايكوندو"],
    },
    {
        "name": "نادي النخبة للرياضات القتالية",
        "description": "نادي متخصص في رياضات النخبة: الجودو، الملاكمة، والتايكوندو.",
        "wilaya": "قسنطينة",
        "city": "قسنطينة",
        "phone": "031920455",
        "email": "contact@nihba-club.dz",
        "min_age": 8,
        "max_age": 35,
        "primary_color": "#B91C1C",
        "sports": ["الجودو", "الملاكمة", "التايكوندو"],
    },
]

FIRST_NAMES_M = [
    "أحمد", "محمد", "يوسف", "عمر", "أيمن", "بلال", "رياض", "إلياس",
    "أدين", "سمير", "نور الدين", "زكرياء", "أنيس", "حمزة", "إسماعيل",
]
FIRST_NAMES_F = [
    "أمينة", "فاطمة", "مريم", "سارة", "نور", "هدى", "لينا", "شيماء",
    "رانيا", "مريم", "بسمة", "إيمان", "خديجة", "آية",
]
LAST_NAMES = [
    "بن سالم", "حمداني", "بوعلام", "مرزوق", "قاسمي", "زيدان",
    "شريف", "بلحاج", "عمراني", "سعيدي", "بوزيد", "خليفي",
    "مباركي", "تمتالي", "لعروسي", "بن يوسف", "حاج علي", "طالبي",
]

WILAYAS = [
    "الجزائر", "وهران", "قسنطينة", "عنابة", "باتنة", "سطيف",
    "تلمسان", "بجاية", "وهران", "غرداية", "ورقلة", "تمنراست",
]


# =====================================================================
#  أدوات
# =====================================================================
def _pick_name(rng: random.Random) -> tuple[str, str, str]:
    """اسم عربي عشوائي: (الاسم، العائلة، الجنس)."""
    male = rng.random() < 0.68
    first = rng.choice(FIRST_NAMES_M if male else FIRST_NAMES_F)
    last = rng.choice(LAST_NAMES)
    return first, last, ("male" if male else "female")


def _phone(rng: random.Random) -> str:
    prefix = rng.choice(["0555", "0661", "0770", "0698"])
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(6))


def _birthdate(rng: random.Random, min_age: int = 6, max_age: int = 30) -> dt.date:
    age = rng.randint(min_age, max_age)
    return dt.date.today() - dt.timedelta(days=age * 365 + rng.randint(0, 364))


def _unique_code(db, existing: set[str]) -> str:
    while True:
        code = generate_card_code()
        if code not in existing:
            existing.add(code)
            return code


class Sequence:
    """
    مولّد أرقام متسلسلة فريدة.

    نحتاجه لأن ``users.email`` و ``users.phone`` فريدان على مستوى
    المنصة كلها -- والمولّد يضمن عدم التكرار دون استعلام القاعدة.
    """

    def __init__(self, prefix: str = "", width: int = 4, start: int = 1) -> None:
        self.prefix = prefix
        self.width = width
        self.value = start

    def next_id(self) -> int:
        """الرقم التالي."""
        current = self.value
        self.value += 1
        return current

    def next_phone(self) -> str:
        """
        رقم هاتف جزائري فريد.

        الصيغة: ``05`` + 8 أرقام = 10 أرقام.
        ``next_id`` يتقدّم مع كل استدعاء، فلا يمكن التكرار.
        """
        prefixes = ("055", "066", "077")
        prefix = prefixes[self.value % len(prefixes)]
        return f"{prefix}{self.next_id():07d}"


#: المدة الافتراضية لكل دورية اشتراك (بالأيام)
_PERIOD_DAYS = {
    PeriodType.MONTHLY.value: 30,
    PeriodType.QUARTERLY.value: 90,
    PeriodType.SEMI_ANNUAL.value: 180,
    PeriodType.ANNUAL.value: 365,
    PeriodType.ONE_TIME.value: 365,
}

#: التسميات العربية لدوريات الاشتراك
_PERIOD_LABELS = {
    PeriodType.MONTHLY.value: "شهري",
    PeriodType.QUARTERLY.value: "ثلاثي",
    PeriodType.SEMI_ANNUAL.value: "نصف سنوي",
    PeriodType.ANNUAL.value: "سنوي",
    PeriodType.ONE_TIME.value: "دفعة واحدة",
}


def _default_duration(period: str) -> int:
    """مدة الاشتراك بالأيام لكل دورية."""
    return _PERIOD_DAYS.get(str(period), 30)


period_label = _PERIOD_LABELS


# =====================================================================
#  التعبئة
# =====================================================================
async def reset_database() -> None:
    """
    إعادة بناء المخطط من الصفر عبر الترحيلات.

    نستخدم ملفات Alembic لا ``create_all``، لأن ``create_all`` تُنتج قاعدة
    بيانات لا يعرفها Alembic فيُحكم عليها بأنها «ليست محدَّثة» ويُقترح
    ترحيل فارغ يخدع المطور.
    """
    print("🗑️  حذف الجداول القديمة...")
    await schema.sync_schema(reset=True, quiet=False)


async def seed() -> dict:
    """تعبئة البيانات التجريبية."""
    rng = random.Random(20240)

    async with SessionLocal() as db:
        print("🌱 تعبئة البيانات التجريبية...\n")

        # -------------------------------------------------------
        # 1) الرياضات
        # -------------------------------------------------------
        print("  ⚽ الرياضات...")
        sports: dict[str, Sport] = {}
        for name_ar, name_en, icon, category in SPORTS:
            existing = await db.execute(select(Sport).where(Sport.name_ar == name_ar))
            sport = existing.scalar_one_or_none()
            if sport is None:
                sport = Sport(
                    name_ar=name_ar,
                    name_en=name_en,
                    slug=name_en.lower().replace(" ", "-"),
                    icon=icon,
                    category=category,
                )
                db.add(sport)
                await db.flush()
            sports[name_ar] = sport

        # -------------------------------------------------------
        # 2) خطط اشتراك المنصة
        # -------------------------------------------------------
        print("  💳 خطط اشتراك المنصة...")
        plans: list[PlatformPlan] = []
        for idx, (name, price, period, days, max_a, max_g, max_s, feats) in enumerate(PLANS):
            plan = PlatformPlan(
                name=name,
                description=f"خطة {name} لإدارة النادي",
                price=price,
                period=period,
                duration_days=days,
                max_athletes=max_a,
                max_groups=max_g,
                max_staff=max_s,
                features=feats,
                is_active=True,
                is_featured=(name == "الاحترافية"),
                sort_order=idx,
            )
            db.add(plan)
            await db.flush()
            plans.append(plan)

        # -------------------------------------------------------
        # 3) مدير المنصة
        # -------------------------------------------------------
        print("  👑 مدير المنصة...")
        super_admin = User(
            email="admin@mashia.dz",
            phone="0555000001",
            password_hash=hash_password(PASSWORD),
            full_name="مدير المنصة",
            role=UserRole.SUPER_ADMIN,
            is_active=True,
            is_verified=True,
        )
        db.add(super_admin)

        # -------------------------------------------------------
        # 4) الأندية وطواقمها
        # -------------------------------------------------------
        print("  🏟️  الأندية وطواقمها...")
        club_subscription_ids: set[int] = set()
        used_codes: set[str] = set()
        club_admin_users: list[User] = []
        parents: dict[str, User] = {}
        parent_seq = Sequence(start=1000)

        for idx, club_data in enumerate(CLUBS):
            is_active = idx < 2

            slug = club_data["name"].lower().replace(" ", "-").replace("ـ", "")

            # نستخدم رمزاً لاتينياً نظيفاً لعناوين البريد بدلاً من الاسم
            # العربي، حتى يبقى العنوان قابلاً للكتابة والنقل بسهولة.
            email_domain = ["toufik-sport.dz", "noujoum-academy.dz", "nihba-club.dz"][idx]

            # ``sports`` ليست عموداً في الجدول بل علاقة، لذلك نستخرجها
            club_columns = {k: v for k, v in club_data.items() if k != "sports"}

            club = Club(
                **club_columns,
                slug=slug,
                status=ClubStatus.ACTIVE if is_active else ClubStatus.PENDING,
                is_public=True,
                is_verified=is_active,
                plan_id=plans[1].id if is_active else None,
                subscription_start=dt.date.today() - dt.timedelta(days=90) if is_active else None,
                subscription_end=dt.date.today() + dt.timedelta(days=275) if is_active else None,
                rating=round(rng.uniform(3.8, 4.9), 2),
                rating_count=rng.randint(8, 60),
                accepts_online_registration=True,
                currency="DZD",
                default_monthly_price=3000,
                settings={"work_hours": "09:00 - 20:00"},
            )
            db.add(club)
            await db.flush()

            # ربط الرياضات
            for sport_name in club_data["sports"]:
                sport = sports.get(sport_name)
                if sport:
                    db.add(ClubSport(club_id=club.id, sport_id=sport.id))

            print(f"     • {club.name} ({'نشط' if is_active else 'بانتظار الموافقة'})")

            if not is_active:
                continue

            # --- مدير النادي ---
            admin_email = f"admin{idx + 1}@{email_domain}"
            admin = User(
                email=admin_email,
                phone=_phone(rng),
                password_hash=hash_password(PASSWORD),
                full_name=f"مدير {club.name}",
                role=UserRole.CLUB_ADMIN,
                club_id=club.id,
                is_active=True,
            )
            db.add(admin)
            await db.flush()

            db.add(StaffMember(
                club_id=club.id,
                user_id=admin.id,
                job_title=StaffJobTitle.MANAGER,
                role=UserRole.CLUB_ADMIN,
                hire_date=dt.date.today() - dt.timedelta(days=rng.randint(200, 900)),
                salary=65000,
                is_active=True,
            ))
            club_admin_users.append(admin)

            # --- موظف استقبال ---
            receptionist = User(
                email=f"reception{idx + 1}@{email_domain}",
                phone=_phone(rng),
                password_hash=hash_password(PASSWORD),
                full_name=f"موظف الاستقبال - {club.name}",
                role=UserRole.RECEPTIONIST,
                club_id=club.id,
                is_active=True,
            )
            db.add(receptionist)
            await db.flush()
            db.add(StaffMember(
                club_id=club.id,
                user_id=receptionist.id,
                job_title=StaffJobTitle.RECEPTIONIST,
                role=UserRole.RECEPTIONIST,
                hire_date=dt.date.today() - dt.timedelta(days=rng.randint(100, 500)),
                salary=38000,
                is_active=True,
            ))

            # --- المدربون ---
            coaches: list[User] = []
            for n, (coach_name, title, salary) in enumerate([
                (f"المدرب {club.name}", StaffJobTitle.HEAD_COACH, 75000),
                ("لوفي بن عمر", StaffJobTitle.ASSISTANT_COACH, 55000),
            ]):
                coach = User(
                    email=f"coach{idx + 1}{n}@{email_domain}",
                    phone=_phone(rng),
                    password_hash=hash_password(PASSWORD),
                    full_name=coach_name,
                    role=UserRole.COACH,
                    club_id=club.id,
                    is_active=True,
                )
                db.add(coach)
                await db.flush()

                staff_row = StaffMember(
                    club_id=club.id,
                    user_id=coach.id,
                    job_title=title,
                    role=UserRole.COACH,
                    specialty="تكوين الفئات العمرية",
                    qualifications="شهادة اللياقة البدنية والحركية",
                    hire_date=dt.date.today() - dt.timedelta(days=rng.randint(150, 800)),
                    salary=salary,
                    is_active=True,
                )
                db.add(staff_row)
                await db.flush()
                coaches.append(coach)
            await db.flush()

            # -------------------------------------------------------
            # 5) خطط اشتراك النادي
            # -------------------------------------------------------
            print("     💰 خطط الاشتراك...")
            fee_plans: list[FeePlan] = []
            club_sports = [sports[s] for s in club_data["sports"] if s in sports]
            for sport in club_sports[:3]:
                for period, price in [
                    (PeriodType.MONTHLY, 2500),
                    (PeriodType.SEMI_ANNUAL, 13000),
                    (PeriodType.ANNUAL, 24000),
                ]:
                    plan = FeePlan(
                        club_id=club.id,
                        name=f"{sport.name_ar} — اشتراك {period_label.get(period, period)}",
                        amount=price,
                        period=period,
                        duration_days=_default_duration(period),
                        sport_id=sport.id,
                        installments=1,
                        is_active=True,
                        sort_order=len(fee_plans),
                    )
                    db.add(plan)
                    fee_plans.append(plan)
            await db.flush()

            # -------------------------------------------------------
            # 6) الأفواج
            # -------------------------------------------------------
            print("     👥 الأفواج...")
            groups: list[Group] = []
            group_specs = [
                ("فوج الصغار", 7, 10, Level.BEGINNER, 18),
                ("فوج الأشبال", 11, 13, Level.AMATEUR, 20),
                ("فوج الأشouin", 14, 16, Level.AMATEUR, 20),
                ("فوج الكبار", 17, 25, Level.ADVANCED, 16),
            ]
            for group_spec_idx, (g_name, min_age, max_age, level, capacity) in enumerate(group_specs):
                group = Group(
                    club_id=club.id,
                    name=g_name,
                    code=f"G{group_spec_idx + 1}",
                    sport_id=club_sports[group_spec_idx % len(club_sports)].id,
                    level=level,
                    min_age=min_age,
                    max_age=max_age,
                    capacity=capacity,
                    location=f"ملعب رقم {group_spec_idx + 1}",
                    schedule_days="الإثنين,الأربعاء,الجمعة",
                    start_time=dt.time(16 + group_spec_idx, 0),
                    end_time=dt.time(17 + group_spec_idx, 30),
                    monthly_fee=2500 + group_spec_idx * 300,
                    color=["#0F766E", "#1D4ED8", "#B91C1C", "#D97706"][group_spec_idx],
                    status=Status.ACTIVE,
                    is_active=True,
                )
                db.add(group)
                await db.flush()
                groups.append(group)

            # إسناد المدربين للأفواج
            staff_coaches = list((await db.execute(
                select(StaffMember).where(
                    StaffMember.club_id == club.id, StaffMember.role == UserRole.COACH
                )
            )).scalars().all())
            for idx, group in enumerate(groups):
                if staff_coaches:
                    group.coach_id = staff_coaches[idx % len(staff_coaches)].id
            await db.flush()

            # -------------------------------------------------------
            # 7) الرياضيون
            # -------------------------------------------------------
            print("     🏃 الرياضيون...")
            athletes: list[Athlete] = []

            for group in groups:
                count = rng.randint(6, min(12, group.capacity))
                for _ in range(count):
                    first, last, gender = _pick_name(rng)
                    age = rng.randint(group.min_age or 7, group.max_age or 20)
                    full_name = f"{first} {last}"

                    athlete = Athlete(
                        club_id=club.id,
                        card_code=_unique_code(db, used_codes),
                        first_name=first,
                        last_name=last,
                        full_name=full_name,
                        birth_date=_birthdate(rng, max(5, (group.min_age or 7) - 2),
                                             (group.max_age or 25) + 2),
                        gender=gender,
                        blood_type=rng.choice(["A+", "O+", "B+", "AB+", "O-"]),
                        phone=_phone(rng),
                        wilaya=rng.choice(WILAYAS),
                        sport_id=group.sport_id,
                        level=group.level,
                        status=Status.ACTIVE,
                        joined_at=dt.date.today() - dt.timedelta(days=rng.randint(10, 400)),
                        attendance_rate=rng.uniform(55, 98),
                        has_medical_clearance=rng.random() > 0.25,
                    )
                    db.add(athlete)
                    await db.flush()

                    db.add(GroupAthlete(
                        club_id=club.id,
                        group_id=group.id,
                        athlete_id=athlete.id,
                        joined_at=athlete.joined_at,
                        is_active=True,
                    ))
                    group.current_count = (group.current_count or 0) + 1
                    athletes.append(athlete)

                    # --- ولي الأمر ---
                    # نربط أولياء الأبناء في نفس النادي بحساب واحد،
                    # لأن الولي قد يكون له أكثر من ابن في النادي نفسه.
                    parent_key = athlete.phone
                    parent = parents.get(parent_key)
                    if parent is None and rng.random() > 0.15:
                        parent = User(
                            email=f"parent{parent_seq.next_id()}@example.dz",
                            phone=parent_seq.next_phone(),
                            password_hash=hash_password(PASSWORD),
                            full_name=f"{rng.choice(['والد', 'والدة'])} {last}",
                            role=UserRole.PARENT,
                            club_id=None,
                            is_active=True,
                        )
                        db.add(parent)
                        await db.flush()
                        parents[parent_key] = parent

                        athlete.guardian_name = parent.full_name
                        athlete.guardian_phone = parent.phone

                    if parent is not None:
                        db.add(ParentLink(
                            club_id=club.id,
                            parent_id=parent.id,
                            athlete_id=athlete.id,
                            relationship_type=rng.choice(["الأب", "الأم", "الجد"]),
                            is_primary=True,
                            is_payer=True,
                        ))

            await db.flush()
            print(f"       {len(athletes)} رياضي، {len(parents)} ولي أمر")

            # -------------------------------------------------------
            # 8) الحصص والحضور
            # -------------------------------------------------------
            print("     📅 الحصص والحضور...")
            sessions_created = 0
            for offset in range(-21, 8):
                day = dt.date.today() + dt.timedelta(days=offset)
                for group in groups:
                    if rng.random() > 0.65:
                        continue

                    start_hour = group.start_time.hour if group.start_time else 17
                    end_hour = min(23, start_hour + 1)
                    session = TrainingSession(
                        club_id=club.id,
                        group_id=group.id,
                        coach_id=group.coach_id,
                        session_date=day,
                        start_time=dt.time(start_hour, 0),
                        end_time=dt.time(end_hour, 30),
                        location=group.location,
                        status=SessionStatus.COMPLETED if day < dt.date.today() else SessionStatus.SCHEDULED,
                        expected_count=group.current_count,
                    )
                    db.add(session)
                    await db.flush()
                    sessions_created += 1

                    if day >= dt.date.today():
                        continue

                    # تسجيل الحضور لهذا اليوم
                    members = list((await db.execute(
                        select(GroupAthlete.athlete_id).where(
                            GroupAthlete.group_id == group.id,
                            GroupAthlete.is_active.is_(True),
                        )
                    )).scalars().all())

                    present = 0
                    absent = 0
                    for athlete_id in members:
                        roll = rng.random()
                        if roll < 0.82:
                            status = AttendanceStatus.PRESENT
                            present += 1
                        elif roll < 0.90:
                            status = AttendanceStatus.LATE
                            present += 1
                        elif roll < 0.96:
                            status = AttendanceStatus.ABSENT
                            absent += 1
                        else:
                            status = AttendanceStatus.EXCUSED

                        db.add(Attendance(
                            club_id=club.id,
                            session_id=session.id,
                            athlete_id=athlete_id,
                            group_id=group.id,
                            session_date=day,
                            status=status,
                            method=AttendanceMethod.MANUAL,
                            minutes_late=random.randint(5, 25) if status == AttendanceStatus.LATE else 0,
                            notes="عذر طبي" if status == AttendanceStatus.EXCUSED else None,
                            recorded_at=dt.datetime.combine(day, dt.time(start_hour, 10)),
                        ))

                    session.present_count = present
                    session.absent_count = absent

            await db.flush()
            print(f"       {sessions_created} حصة تدريبية")

            # -------------------------------------------------------
            # 9) التقييمات
            # -------------------------------------------------------
            print("     ⭐ التقييمات...")
            eval_count = 0
            for athlete in rng.sample(athletes, min(20, len(athletes))):
                for months_ago in range(1, 4):
                    db.add(Evaluation(
                        club_id=club.id,
                        athlete_id=athlete.id,
                        eval_date=dt.date.today() - dt.timedelta(days=months_ago * 30),
                        type=EvaluationType.PERIODIC,
                        physical_score=rng.randint(4, 10),
                        technique_score=rng.randint(4, 10),
                        discipline_score=rng.randint(5, 10),
                        teamwork_score=rng.randint(5, 10),
                        strengths="سرعة الاستجابة والالتزام بالتدريبات",
                        weaknesses="يحتاج إلى العمل على المهارة الدفاعية",
                        notes="تقدم ملحوظ مقارنة بالتقييم السابق",
                        coach_id=rng.choice(groups).coach_id,
                    ))
                    eval_count += 1
            await db.flush()
            print(f"       {eval_count} تقييم")

            # -------------------------------------------------------
            # 10) الفواتير والدفعات
            # -------------------------------------------------------
            print("     🧾 الفواتير والدفعات...")
            invoice_seq = Sequence(prefix="INV", width=6, start=1)

            for athlete in athletes:
                plan = rng.choice(fee_plans)
                start = dt.date.today() - dt.timedelta(days=rng.randint(1, 45))
                end = start + dt.timedelta(days=plan.duration_days - 1)

                total = float(plan.amount)
                discount = 0.0
                if rng.random() > 0.6:
                    discount = round(total * 0.1, 2)
                total -= discount

                # حالة السداد: 70% مدفوعة، 20% متأخرة، 10% مدفوعة جزئياً
                roll = rng.random()
                if roll < 0.70:
                    paid, status = total, InvoiceStatus.PAID
                elif roll < 0.90:
                    paid, status = 0.0, InvoiceStatus.OVERDUE
                else:
                    paid, status = round(total * 0.5, 2), InvoiceStatus.PARTIAL

                due_date = start if status != InvoiceStatus.OVERDUE else end - dt.timedelta(days=5)

                group = next((g for g in groups if g.sport_id == plan.sport_id), None)
                invoice = Invoice(
                    club_id=club.id,
                    invoice_number=f"INV-{start.strftime('%Y%m%d')}-{invoice_seq.next_id():06d}",
                    athlete_id=athlete.id,
                    fee_plan_id=plan.id,
                    group_id=group.id if group else None,
                    period_start=start,
                    period_end=end,
                    issue_date=start,
                    due_date=due_date,
                    subtotal=float(plan.amount),
                    discount=discount,
                    total=total,
                    paid=paid,
                    status=status,
                    is_active=True,
                )
                db.add(invoice)
                await db.flush()

                if paid > 0:
                    db.add(Payment(
                        club_id=club.id,
                        receipt_number=f"REC-{start.strftime('%Y%m%d')}-{invoice_seq.next_id():06d}",
                        invoice_id=invoice.id,
                        athlete_id=athlete.id,
                        amount=paid,
                        paid_at=dt.datetime.combine(start, dt.time(10, 30)),
                        payment_date=start,
                        method=rng.choice(
                            [PaymentMethod.CASH, PaymentMethod.BANK_TRANSFER, PaymentMethod.CARD]
                        ),
                        status="completed",
                        recorded_by_id=club_admin_users[-1].id,
                    ))

            await db.flush()

            # -------------------------------------------------------
            # 11) المصاريف
            # -------------------------------------------------------
            print("     💸 المصاريف...")
            expense_specs = [
                (ExpenseCategory.RENT, "إيجار الملعب", 45000),
                (ExpenseCategory.SALARY, "رواتب الطاقم", 320000),
                (ExpenseCategory.EQUIPMENT, "شراء كرات ومستلزمات", 18000),
                (ExpenseCategory.MAINTENANCE, "صيانة الملاعب", 25000),
                (ExpenseCategory.UTILITIES, "فاتورة الكهرباء والماء", 15000),
                (ExpenseCategory.MARKETING, "إعلانات تسويقية", 22000),
                (ExpenseCategory.MEDICAL, "علبة الإسعاف", 12000),
            ]
            for month_offset in range(6):
                base = dt.date.today() - dt.timedelta(days=30 * month_offset)
                for category, title, amount in expense_specs:
                    if rng.random() < 0.35:
                        continue
                    db.add(Expense(
                        club_id=club.id,
                        title=title,
                        category=category,
                        amount=round(amount * rng.uniform(0.85, 1.15), 2),
                        expense_date=base + dt.timedelta(days=rng.randint(0, 25)),
                        vendor=rng.choice(["مورّد SportPro", "شركة Algerie Sport", "—"]),
                        approved=True,
                        approved_by_id=club_admin_users[-1].id,
                        recorded_by_id=club_admin_users[-1].id,
                    ))
            await db.flush()

            # -------------------------------------------------------
            # 12) الأخبار
            # -------------------------------------------------------
            print("     📰 الأخبار...")
            news_items = [
                ("انطلاق موسم التدريب الجديد", "news",
                 "يعلن النادي عن انطلاق موسم التدريب الجديد لجميع الفئات العمرية. "
                 "التسجيل مفتوح عبر الموقع الإلكتروني أو مباشرة في مقر النادي.", None),
                ("انتصار في المباراة الأخيرة", "result",
                 "فاز فريق النجوم بنتيجة 3-1 في المباراة الأخيرة ضمن مرحلة التصفيات.", None),
                ("دورة في رياضة تايكوندو", "event",
                 "تنظم الأكاديمية دورة تدريبية في رياضات النخبة لفئة 12-16 سنة.",
                 dt.date.today() + dt.timedelta(days=21)),
                ("فتح باب التسجيل لموسم الصيف", "announcement",
                 "فتح باب التسجيل في أندية كرة القدم والسباحة لموسم الصيف. "
                 "الأماكن محدودة والتسجيل المبكر مضمون.", None),
            ]
            for club in (await db.execute(select(Club).where(Club.status == ClubStatus.ACTIVE))).scalars().all():
                for title, kind, content, event_date in news_items:
                    db.add(ClubNews(
                        club_id=club.id,
                        title=title,
                        excerpt=content[:180],
                        content=content,
                        kind=kind,
                        is_published=True,
                        published_at=utcnow() - dt.timedelta(days=rng.randint(0, 20)),
                        event_date=event_date,
                        location="مقر النادي" if event_date else None,
                        views_count=rng.randint(10, 400),
                        likes_count=rng.randint(0, 60),
                        is_active=True,
                    ))

            # إعلان عام على مستوى المنصة
            db.add(ClubNews(
                club_id=None,
                title="انطلاق النسخة الأولى من المنصة الشاملة",
                excerpt="منصة موحّدة لتسيير الأندية الرياضية: رياضيون، أفواج، حضور، ومالية.",
                content=(
                    "يسرّنا إعلان إطلاق المنصة الشاملة، الحل الأمثل لتسيير الأندية "
                    "والجمعيات الرياضية. توفر المنصة كل ما يحتاجه النادي: إدارة "
                    "الرياضيين، برمجة الأفواج والحصص، تسجيل الحضور عبر QR، "
                    "التقييمات الفنية، والتسيير المالي الكامل."
                ),
                kind="announcement",
                is_published=True,
                published_at=utcnow(),
                views_count=1500,
                likes_count=180,
                is_active=True,
            ))

            # -------------------------------------------------------
            # 13) طلبات تسجيل واردة
            # -------------------------------------------------------
            print("     📝 طلبات التسجيل...")
            active_club = (await db.execute(
                select(Club).where(Club.status == ClubStatus.ACTIVE).limit(1)
            )).scalar_one()

            for status, count in [
                (RegistrationStatus.PENDING, 5),
                (RegistrationStatus.APPROVED, 3),
                (RegistrationStatus.REJECTED, 2),
            ]:
                for _ in range(count):
                    first, last, gender = _pick_name(rng)
                    db.add(RegistrationRequest(
                        club_id=active_club.id,
                        full_name=f"{first} {last}",
                        birth_date=_birthdate(rng, 7, 16),
                        gender=gender,
                        parent_name=f"{rng.choice(['والد', 'والدة'])} {last}",
                        phone=_phone(rng),
                        email=f"candidat{rng.randint(1000, 9999)}@example.dz",
                        wilaya=rng.choice(WILAYAS),
                        sport_id=active_club.sports[0].id if active_club.sports else None,
                        level=rng.choice(["beginner", "amateur"]),
                        message="أرغب في تسجيل ابني/ابنتي في النادي",
                        status=status,
                        submitted_at=utcnow() - dt.timedelta(days=rng.randint(0, 25)),
                        processed_at=(
                            utcnow() - dt.timedelta(days=rng.randint(0, 10))
                            if status != RegistrationStatus.PENDING else None
                        ),
                        rejection_reason=(
                            "العدد مكتمل في هذه الفئة حالياً"
                            if status == RegistrationStatus.REJECTED else None
                        ),
                        is_active=True,
                    ))

            # -------------------------------------------------------
            # 14) اشتراكات الأندية في المنصة
            #    (تُسجَّل مرة واحدة لكل نادي)
            # -------------------------------------------------------
            if not club_subscription_ids:
                for other_club in (
                    await db.execute(
                        select(Club).where(Club.status == ClubStatus.ACTIVE)
                    )
                ).scalars().all():
                    if other_club.id in club_subscription_ids:
                        continue
                    db.add(ClubSubscription(
                        club_id=other_club.id,
                        plan_id=plans[1].id,
                        amount=float(plans[1].price),
                        period_start=dt.date.today() - dt.timedelta(days=90),
                        period_end=other_club.subscription_end,
                        status=SubscriptionStatus.ACTIVE,
                        paid_at=utcnow() - dt.timedelta(days=90),
                        invoice_number=f"PLAT-{other_club.id}-001",
                    ))
                    club_subscription_ids.add(other_club.id)

            await db.commit()

        # تحديث عدّادات الأندية
        for club in (await db.execute(select(Club))).scalars().all():
            count = await db.execute(
                select(func.count(Athlete.id)).where(
                    Athlete.club_id == club.id, Athlete.is_active.is_(True)
                )
            )
            club.members_count = count.scalar() or 0

        await db.commit()

        summary = {
            "sports": len(sports),
            "plans": len(plans),
            "clubs": (await db.execute(select(func.count(Club.id)))).scalar(),
            "users": (await db.execute(select(func.count(User.id)))).scalar(),
            "athletes": (await db.execute(select(func.count(Athlete.id)))).scalar(),
            "groups": (await db.execute(select(func.count(Group.id)))).scalar(),
            "sessions": (await db.execute(select(func.count(TrainingSession.id)))).scalar(),
            "attendance": (await db.execute(select(func.count(Attendance.id)))).scalar(),
            "evaluations": (await db.execute(select(func.count(Evaluation.id)))).scalar(),
            "invoices": (await db.execute(select(func.count(Invoice.id)))).scalar(),
            "payments": (await db.execute(select(func.count(Payment.id)))).scalar(),
            "expenses": (await db.execute(select(func.count(Expense.id)))).scalar(),
            "news": (await db.execute(select(func.count(ClubNews.id)))).scalar(),
        }

    return summary


# =====================================================================
#  ملخص النتائج
# =====================================================================
def print_summary(summary: dict) -> None:
    """طباعة ملخص البيانات المُنشأة وحسابات الدخول التجريبية."""
    labels = {
        "sports": "الرياضات", "plans": "خطط المنصة", "clubs": "الأندية",
        "users": "المستخدمون", "athletes": "الرياضيون", "groups": "الأفواج",
        "sessions": "الحصص", "attendance": "سجلات الحضور",
        "evaluations": "التقييمات", "invoices": "الفواتير",
        "payments": "الدفعات", "expenses": "المصاريف", "news": "الأخبار",
    }

    line = "=" * 58
    print(f"\n{line}")
    print("  تم إنشاء البيانات التجريبية بنجاح")
    print(line)

    for key, value in summary.items():
        label = labels.get(key, key)
        print(f"  {label:<20} {value:>7}")

    print(f"\n{'-' * 58}")
    print("  حسابات الدخول التجريبية")
    print(f"{'-' * 58}")
    print(f"  كلمة المرور للجميع:  {PASSWORD}")
    print()
    print("  مدير المنصة:        admin@mashia.dz")
    print("  مدير نادي التوفيق:  admin1@toufik-sport.dz")
    print(f"{'-' * 58}")
    print("  تشغيل التطبيق:  python -m uvicorn app.main:app --reload")
    print("  المنصة:        http://localhost:8000")
    print("  توثيق API:     http://localhost:8000/api/docs")
    print(f"{line}\n")


async def main() -> int:
    """نقطة التشغيل الرئيسية."""
    parser = argparse.ArgumentParser(description="تعبئة بيانات المنصة التجريبية")
    parser.add_argument(
        "--reset", action="store_true",
        help="حذف كل الجداول وإنشاؤها من جديد قبل التعبئة",
    )
    args = parser.parse_args()

    if args.reset:
        await reset_database()
    else:
        # نطبّق الترحيلات الناقصة فقط — آمنة للتشغيل المتكرر
        await schema.sync_schema(reset=False, quiet=False)

    try:
        summary = await seed()
    except Exception:
        await dispose_engine()
        raise

    await dispose_engine()
    print_summary(summary)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nتم الإيقاف.")
        sys.exit(1)