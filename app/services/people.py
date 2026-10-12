"""
خدمة الرياضيين والأشخاص (People Service)
========================================
إدارة الرياضيين، ربط الأولياء، وطاقم النادي.

كل العمليات هنا تعمل داخل نطاق النادي تلقائياً thanks لنظام العزل، لكن
نُضيف فحوصاً صريحة للأحكام (business rules) التي لا يغطيها العزل:

* لا يمكن ربط رياضي بفوج من نادي آخر.
* لا يمكن حذف رياضي له فواتير غير مسددة (قاعدة محاسبية).
* بطاقة الرياضي (``card_code``) فريدة عبر المنصة كلها.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import safe_get, utcnow
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.security import generate_card_code
from app.models import (
    Attendance,
    AttendanceStatus,
    Athlete,
    Club,
    Evaluation,
    Group,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    Level,
    ParentLink,
    StaffMember,
    Status,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)


# =====================================================================
#  أدوات
# =====================================================================
def build_full_name(first_name: str, last_name: str) -> str:
    """بناء الاسم الكامل مع تطبيع المسافات."""
    return f"{first_name.strip()} {last_name.strip()}".strip()


async def _unique_card_code(db: AsyncSession) -> str:
    """توليد كود بطاقة فريد عبر المنصة."""
    for _ in range(20):
        code = generate_card_code()
        exists = await db.execute(
            select(Athlete.id).where(Athlete.card_code == code).limit(1)
        )
        if exists.scalar_one_or_none() is None:
            return code
    raise ConflictError("تعذّر توليد كود بطاقة فريد. يرجى المحاولة مرة أخرى")


async def get_athlete(db: AsyncSession, athlete_id: int) -> Athlete:
    """جلب رياضي بمعرّفه، مع احترام نطاق المستخدم الحالي."""
    athlete = await safe_get(db, Athlete, athlete_id)
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")
    return athlete


# =====================================================================
#  إنشاء وتحديث الرياضيين
# =====================================================================
async def create_athlete(db: AsyncSession, data: dict[str, Any]) -> Athlete:
    """
    إنشاء ملف رياضي جديد.

    ``data`` يجب أن يحتوي على ``club_id`` -- وهو يُحقن تلقائياً من سياق
    المستأجر إذا لم يُذكر (حارس الكتابة في ``tenancy.py`` يملؤه).
    """
    # --- التحقق من البيانات الأساسية ---
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()

    if not first_name or not last_name:
        raise ValidationError("الاسم الأول واسم العائلة مطلوبان")

    birth_date = data.get("birth_date")
    if birth_date:
        if isinstance(birth_date, str):
            birth_date = dt.date.fromisoformat(birth_date)
        if birth_date > dt.date.today():
            raise ValidationError("تاريخ الميلاد لا يمكن أن يكون في المستقبل")
        min_age = (dt.date.today() - birth_date).days / 365.25
        if min_age < 3:
            raise ValidationError("العمر الأدنى للتسجيل في النادي هو 3 سنوات")
        if min_age > 70:
            raise ValidationError("يرجى مراجعة تاريخ الميلاد")

    # --- بطاقة ---
    card_code = data.get("card_code") or await _unique_card_code(db)
    if data.get("card_code"):
        exists = await db.execute(
            select(Athlete.id).where(Athlete.card_code == card_code).limit(1)
        )
        if exists.scalar_one_or_none():
            raise ConflictError("كود البطاقة مستخدم مسبقاً")

    athlete = Athlete(
        card_code=card_code,
        first_name=first_name,
        last_name=last_name,
        full_name=build_full_name(first_name, last_name),
        birth_date=birth_date,
        gender=data.get("gender"),
        nationality=data.get("nationality"),
        national_id=data.get("national_id"),
        blood_type=data.get("blood_type"),
        phone=data.get("phone"),
        email=data.get("email"),
        address=data.get("address"),
        wilaya=data.get("wilaya"),
        guardian_name=data.get("guardian_name"),
        guardian_phone=data.get("guardian_phone"),
        guardian_email=data.get("guardian_email"),
        guardian_relation=data.get("guardian_relation"),
        emergency_contact_name=data.get("emergency_contact_name"),
        emergency_contact_phone=data.get("emergency_contact_phone"),
        medical_notes=data.get("medical_notes"),
        allergies=data.get("allergies"),
        chronic_conditions=data.get("chronic_conditions"),
        injuries=data.get("injuries"),
        has_medical_clearance=data.get("has_medical_clearance", False),
        photo_url=data.get("photo_url"),
        notes=data.get("notes"),
        sport_id=data.get("sport_id"),
        preferred_position=data.get("preferred_position"),
        level=data.get("level", Level.BEGINNER),
        jersey_number=data.get("jersey_number"),
        status=Status.ACTIVE,
        joined_at=dt.date.today(),
        club_id=data.get("club_id"),
    )

    db.add(athlete)
    await db.flush()

    # ربط بالأفواج إن حُدِّدت
    group_ids = data.get("group_ids") or []
    if group_ids:
        await _attach_to_groups(db, athlete, group_ids)

    # ربط بولي الأمر إن كُشف بريد أو هاتف الولي
    await _maybe_create_parent(db, athlete, data)

    await db.refresh(athlete)
    return athlete


async def update_athlete(db: AsyncSession, athlete_id: int, data: dict[str, Any]) -> Athlete:
    """تحديث بيانات رياضي مع protection ضد تعديلات غير مسموحة."""
    athlete = await get_athlete(db, athlete_id)

    # حقول ممنوع تعديلها بعد الإنشاء
    protected = {"card_code", "club_id", "created_at"}

    for field, value in data.items():
        if field in protected or value is None:
            continue
        if field == "full_name":
            value = build_full_name(
                value.get("first_name", athlete.first_name),
                value.get("last_name", athlete.last_name),
            )
            setattr(athlete, "first_name", value.split()[0] if value else athlete.first_name)
        if hasattr(athlete, field):
            setattr(athlete, field, value)

    if "first_name" in data and "last_name" in data:
        athlete.full_name = build_full_name(athlete.first_name, athlete.last_name)

    # إعادة الربط بالأفواج إن طُلب
    if "group_ids" in data:
        await _detach_all_groups(db, athlete)
        if data["group_ids"]:
            await _attach_to_groups(db, athlete, data["group_ids"])

    await db.flush()
    await db.refresh(athlete)
    return athlete


async def delete_athlete(db: AsyncSession, athlete_id: int, soft: bool = True) -> None:
    """
    حذف رياضي.

    بشكل افتراضي الحذف **منطقي** (تعطيل) للحفاظ على السجلات المالية.
    """
    athlete = await get_athlete(db, athlete_id)

    # قاعدة محاسبية: لا يمكن حذف رياضي عليه فواتير غير مسددة
    unpaid = await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.athlete_id == athlete.id,
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
        )
    )
    if (unpaid.scalar() or 0) > 0:
        raise BusinessRuleError(
            "لا يمكن حذف رياضي عليه فواتير غير مسددة. "
            "يرجى تسوية الوضع المالي أولاً أو تعطيله بدل حذفه"
        )

    if soft:
        athlete.is_active = False
        athlete.status = Status.INACTIVE
        athlete.deleted_at = utcnow()
        # إخراج من كل الأفواج
        await _detach_all_groups(db, athlete)
    else:
        await db.delete(athlete)

    await db.flush()


# =====================================================================
#  إدارة الأفواج (ربط الرياضيين)
# =====================================================================
async def _attach_to_groups(
    db: AsyncSession,
    athlete: Athlete,
    group_ids: list[int],
) -> None:
    """
    ربط رياضي بعدة أفواج.

    **فحص حرج**: نتأكد أن كل فوج ينتمي لنفس النادي. نظام العزل يمنع
    *القراءة* عبر النادي، لكن هذا الفحص يضمن أيضاً أن مراجع الربط
    (``group_athletes.club_id``) متسقة مع الكيانين.
    """
    if not group_ids:
        return

    groups = await _load_groups_same_club(db, athlete.club_id, group_ids)

    existing = await db.execute(
        select(GroupAthlete).where(
            GroupAthlete.athlete_id == athlete.id,
            GroupAthlete.group_id.in_([g.id for g in groups]),
        )
    )
    already = {row.group_id for row in existing.scalars().all()}

    for group in groups:
        if group.id in already:
            continue
        if group.is_full:
            raise BusinessRuleError(
                f"الفوج «{group.name}» مكتمل العدد ({group.capacity} رياضي)"
            )
        db.add(
            GroupAthlete(
                club_id=athlete.club_id,
                group_id=group.id,
                athlete_id=athlete.id,
                joined_at=dt.date.today(),
                is_active=True,
            )
        )
        group.current_count = (group.current_count or 0) + 1

    await db.flush()


async def _load_groups_same_club(
    db: AsyncSession,
    club_id: int,
    group_ids: list[int],
) -> list[Group]:
    """جلب الأفواج مع التحقق الصريح من انتمائها للنادي."""
    result = await db.execute(select(Group).where(Group.id.in_(group_ids)))
    groups = list(result.scalars().all())

    found_ids = {g.id for g in groups}
    missing = set(group_ids) - found_ids
    if missing:
        raise NotFoundError(f"أفواج غير موجودة: {sorted(missing)}")

    # فحص إضافي: لا يمكن ربط رياضي من نادي مع فوج من نادي آخر
    foreign = [g for g in groups if g.club_id != club_id]
    if foreign:
        names = "، ".join(g.name for g in foreign)
        raise BusinessRuleError(f"لا يمكن ربط رياضي بفوج من نادي آخر: {names}")

    return groups


async def _detach_all_groups(db: AsyncSession, athlete: Athlete) -> None:
    """إخراج الرياضي من كل أفواجه."""
    result = await db.execute(
        select(GroupAthlete).where(
            GroupAthlete.athlete_id == athlete.id,
            GroupAthlete.is_active.is_(True),
        )
    )
    links = list(result.scalars().all())
    if not links:
        return

    group_ids = [link.group_id for link in links]
    for link in links:
        await db.delete(link)

    await db.flush()

    # تحديث العدادات
    groups = await db.execute(select(Group).where(Group.id.in_(group_ids)))
    for group in groups.scalars().all():
        group.current_count = max(0, (group.current_count or 1) - 1)

    await db.flush()


async def remove_athlete_from_group(
    db: AsyncSession, athlete_id: int, group_id: int
) -> None:
    """إخراج رياضي من فوج واحد."""
    result = await db.execute(
        select(GroupAthlete).where(
            GroupAthlete.athlete_id == athlete_id,
            GroupAthlete.group_id == group_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise NotFoundError("الرياضي ليس عضواً في هذا الفوج")

    await db.delete(link)
    await db.flush()

    group = await db.get(Group, group_id)
    if group:
        group.current_count = max(0, (group.current_count or 1) - 1)
        await db.flush()


async def get_athlete_groups(db: AsyncSession, athlete_id: int) -> list[Group]:
    """أفواج رياضي certain."""
    result = await db.execute(
        select(Group)
        .join(GroupAthlete, GroupAthlete.group_id == Group.id)
        .where(
            GroupAthlete.athlete_id == athlete_id,
            GroupAthlete.is_active.is_(True),
        )
    )
    return list(result.scalars().all())


# =====================================================================
#  ربط الأولياء
# =====================================================================
async def _maybe_create_parent(
    db: AsyncSession,
    athlete: Athlete,
    data: dict[str, Any],
) -> ParentLink | None:
    """
    إنشاء حساب ولي وربطه بالرياضي تلقائياً إذا لم يكن موجوداً.

    منحت الأولياء حساباً تلقائياً هو ما يجعل "بوابة الأولياء الموحدة"
    ممكنة دون تعاون إضافي من النادي.
    """
    parent_email = (data.get("parent_email") or data.get("guardian_email") or "").strip().lower()
    parent_phone = (data.get("parent_phone") or data.get("guardian_phone") or "").strip()

    if not parent_email and not parent_phone:
        return None

    parent: User | None = None

    if parent_email:
        result = await db.execute(select(User).where(User.email == parent_email))
        parent = result.scalar_one_or_none()

    if parent is None and parent_phone:
        result = await db.execute(select(User).where(User.phone == parent_phone))
        parent = result.scalar_one_or_none()

    if parent is None:
        from app.core.security import generate_token

        parent = User(
            email=parent_email or f"parent_{generate_token(6)}@pending.local",
            phone=parent_phone or None,
            password_hash=generate_token(32),   # حساب غير مُفعّل حتى يُنشئ كلمة سر
            full_name=data.get("guardian_name") or athlete.first_name,
            role=UserRole.PARENT,
            club_id=None,       # الولي قد يكون له أبناء في عدة أندية
            is_active=False,    # يُفعَّل عند أول دخول (إعداد كلمة المرور)
        )
        db.add(parent)
        await db.flush()

    link = ParentLink(
        club_id=athlete.club_id,
        parent_id=parent.id,
        athlete_id=athlete.id,
        relationship_type=data.get("guardian_relation"),
        is_primary=True,
        is_payer=True,
        can_pickup=True,
    )
    db.add(link)
    await db.flush()
    return link


async def link_parent(
    db: AsyncSession,
    athlete_id: int,
    parent_id: int,
    *,
    relationship: str | None = None,
    is_primary: bool = False,
    is_payer: bool = True,
) -> ParentLink:
    """ربط حساب ولي موجود برياضي."""
    athlete = await get_athlete(db, athlete_id)
    parent = await db.get(User, parent_id)

    if parent is None:
        raise NotFoundError("حساب الولي غير موجود")
    if parent.role not in (UserRole.PARENT, UserRole.ATHLETE):
        raise BusinessRuleError("هذا الحساب ليس حساب ولي أمر")

    exists = await db.execute(
        select(ParentLink).where(
            ParentLink.parent_id == parent_id,
            ParentLink.athlete_id == athlete_id,
        )
    )
    if exists.scalar_one_or_none():
        raise ConflictError("هذا الولي مرتبط بهذا الرياضي بالفعل")

    if is_primary:
        await _clear_primary_parent(db, athlete_id)

    link = ParentLink(
        club_id=athlete.club_id,
        parent_id=parent_id,
        athlete_id=athlete_id,
        relationship_type=relationship,
        is_primary=is_primary,
        is_payer=is_payer,
        can_pickup=is_payer,
    )
    db.add(link)
    await db.flush()
    return link


async def unlink_parent(db: AsyncSession, athlete_id: int, parent_id: int) -> None:
    """فك ارتباط ولي برياضي."""
    result = await db.execute(
        select(ParentLink).where(
            ParentLink.athlete_id == athlete_id,
            ParentLink.parent_id == parent_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise NotFoundError("لا يوجد ارتباط بين هذا الولي والرياضي")
    await db.delete(link)
    await db.flush()


async def _clear_primary_parent(db: AsyncSession, athlete_id: int) -> None:
    """إلغاء صفة "الولي الأساسي" عن بقية الأولياء."""
    result = await db.execute(
        select(ParentLink).where(
            ParentLink.athlete_id == athlete_id,
            ParentLink.is_primary.is_(True),
        )
    )
    for link in result.scalars().all():
        link.is_primary = False
    await db.flush()


async def get_athlete_parents(db: AsyncSession, athlete_id: int) -> list[tuple[ParentLink, User]]:
    """قائمة أولياء رياضي مع حساباتهم."""
    result = await db.execute(
        select(ParentLink, User)
        .join(User, User.id == ParentLink.parent_id)
        .where(ParentLink.athlete_id == athlete_id)
        .order_by(ParentLink.is_primary.desc(), ParentLink.id)
    )
    return list(result.all())


async def get_parent_children(db: AsyncSession, parent_id: int) -> list[Athlete]:
    """كل أبناء الولي في كل الأندية (الولي الموحد)."""
    result = await db.execute(
        select(Athlete)
        .join(ParentLink, ParentLink.athlete_id == Athlete.id)
        .where(ParentLink.parent_id == parent_id)
        .order_by(Athlete.full_name)
    )
    return list(result.scalars().all())


# =====================================================================
#  البحث والتصفية
# =====================================================================
async def search_athletes(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: str | None = None,
    group_id: int | None = None,
    sport_id: int | None = None,
    level: str | None = None,
    has_debt: bool | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Athlete], int]:
    """
    بحث وتصفية الرياضيين ضمن نطاق المستخدم.

    يُرجع ``(النتائج، العدد الكلي)`` للترقيم في الواجهة.
    """
    conditions = []

    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                Athlete.full_name.ilike(pattern),
                Athlete.card_code.ilike(pattern),
                Athlete.phone.ilike(pattern),
                Athlete.guardian_phone.ilike(pattern),
                Athlete.guardian_name.ilike(pattern),
                Athlete.national_id.ilike(pattern),
            )
        )

    if status:
        conditions.append(Athlete.status == status)
    else:
        conditions.append(Athlete.is_active.is_(True))

    if sport_id:
        conditions.append(Athlete.sport_id == sport_id)
    if level:
        conditions.append(Athlete.level == level)

    if group_id:
        subquery = select(GroupAthlete.athlete_id).where(GroupAthlete.group_id == group_id)
        conditions.append(Athlete.id.in_(subquery))

    if has_debt:
        unpaid = select(Invoice.athlete_id).where(
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE])
        )
        conditions.append(Athlete.id.in_(unpaid) if has_debt else ~Athlete.id.in_(unpaid))

    # ---- العدد الكلي ----
    count_stmt = select(func.count(Athlete.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    # ---- النتائج ----
    stmt = select(Athlete).order_by(Athlete.full_name)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    rows = (await db.execute(stmt)).scalars().all()
    return list(rows), total


async def get_athlete_profile(
    db: AsyncSession, athlete_id: int
) -> dict[str, Any]:
    """
    الملف الكامل لرياضي: البيانات + الأفواج + الولي + ملخص الحضور والمالية.

    تُجمَّع في استدعاء واحد لأن شاشات الملف تعرض كل هذه الأقسام معاً،
    و ``N+1`` في هذه الشاشة يعني تأخيراً ملحوظاً عند Squash الك Clubs.
    """
    athlete = await get_athlete(db, athlete_id)

    groups = await get_athlete_groups(db, athlete_id)
    parents = await get_athlete_parents(db, athlete_id)

    # ملخص الحضور لـ 30 يوماً
    since = dt.date.today() - dt.timedelta(days=30)
    attendance_rows = await db.execute(
        select(Attendance.status, func.count(Attendance.id))
        .where(
            Attendance.athlete_id == athlete_id,
            Attendance.session_date >= since,
        )
        .group_by(Attendance.status)
    )
    attendance_summary = {row[0]: row[1] for row in attendance_rows.all()}

    present = attendance_summary.get(AttendanceStatus.PRESENT, 0) + attendance_summary.get(
        AttendanceStatus.LATE, 0
    )
    absent = attendance_summary.get(AttendanceStatus.ABSENT, 0)
    excused = attendance_summary.get(AttendanceStatus.EXCUSED, 0)
    total_att = present + absent + excused

    # آخر التقييمات
    evals_result = await db.execute(
        select(Evaluation)
        .where(Evaluation.athlete_id == athlete_id)
        .order_by(Evaluation.eval_date.desc())
        .limit(5)
    )
    evaluations = list(evals_result.scalars().all())

    # الوضع المالي
    invoices_result = await db.execute(
        select(Invoice)
        .where(
            Invoice.athlete_id == athlete_id,
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
        )
        .order_by(Invoice.due_date)
    )
    unpaid_invoices = list(invoices_result.scalars().all())
    debt = sum(float(inv.amount_due) for inv in unpaid_invoices)

    return {
        "athlete": athlete,
        "groups": groups,
        "parents": parents,
        "attendance_summary": attendance_summary,
        "attendance_rate": round((present / total_att) * 100, 1) if total_att else None,
        "attendance_period_days": 30,
        "evaluations": evaluations,
        "unpaid_invoices": unpaid_invoices,
        "debt": debt,
    }


# =====================================================================
#  طاقم النادي
# =====================================================================
async def create_staff_member(
    db: AsyncSession,
    *,
    user_id: int,
    job_title: str,
    role: UserRole,
    salary: float | None = None,
    **extra: Any,
) -> StaffMember:
    """إضافة عضو لطاقم النادي."""
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("المستخدم غير موجود")

    existing = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user_id,
            StaffMember.club_id == extra.get("club_id"),
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError("هذا المستخدم عضو في طاقم النادي بالفعل")

    staff = StaffMember(
        user_id=user_id,
        job_title=job_title,
        role=role,
        salary=salary,
        hire_date=extra.get("hire_date") or dt.date.today(),
        specialty=extra.get("specialty"),
        qualifications=extra.get("qualifications"),
        bio=extra.get("bio"),
        photo_url=extra.get("photo_url"),
        sport_id=extra.get("sport_id"),
        contract_end=extra.get("contract_end"),
        employment_type=extra.get("employment_type"),
        notes=extra.get("notes"),
        status=Status.ACTIVE,
        is_active=True,
        club_id=extra.get("club_id"),
    )
    db.add(staff)
    await db.flush()
    return staff


async def list_staff(db: AsyncSession, *, active_only: bool = True) -> list[tuple[StaffMember, User]]:
    """قائمة طاقم النادي مع حسابات المستخدمين."""
    stmt = (
        select(StaffMember, User)
        .join(User, User.id == StaffMember.user_id)
        .order_by(StaffMember.job_title, User.full_name)
    )
    if active_only:
        stmt = stmt.where(StaffMember.is_active.is_(True))

    return list((await db.execute(stmt)).all())


async def delete_staff_member(db: AsyncSession, staff_id: int) -> None:
    """
    حذف عضو من الطاقم.

    **قاعدة مهمة**: لا يمكن حذف مدرب مرتبط بأفواج نشطة -- لأن ذلك يترك
    الأفواج بلا مدرب دون تنبيه.
    """
    result = await db.execute(
        select(StaffMember).where(StaffMember.id == staff_id)
    )
    staff = result.scalar_one_or_none()
    if staff is None:
        raise NotFoundError("عضو الطاقم غير موجود")

    groups = await db.execute(
        select(func.count(Group.id)).where(
            Group.coach_id == staff_id, Group.is_active.is_(True)
        )
    )
    if (groups.scalar() or 0) > 0:
        raise BusinessRuleError(
            "لا يمكن حذف هذا المدرب لأنه مكلَّف بتدريب أفواج نشطة. "
            "يرجى إسناد الأفواج إلى مدرب آخر أولاً"
        )

    staff.is_active = False
    staff.status = Status.INACTIVE
    staff.deleted_at = utcnow()
    await db.flush()