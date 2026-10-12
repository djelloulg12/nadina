"""
خدمة التدريب (Training Service)
===============================
الأفواج، برمجة الحصص، تسجيل الحضور (يدوي/QR)، والتقييمات الفنية.
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
from app.core.security import decode_qr_payload
from app.models import (
    Athlete,
    Attendance,
    AttendanceMethod,
    AttendanceStatus,
    Evaluation,
    EvaluationType,
    Group,
    GroupAthlete,
    SessionStatus,
    StaffMember,
    Status,
    TrainingSession,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)


# =====================================================================
#  الأفواج
# =====================================================================
async def create_group(db: AsyncSession, data: dict[str, Any]) -> Group:
    """إنشاء فوج تدريبي."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("اسم الفوج مطلوب")

    coach_id = data.get("coach_id")
    if coach_id:
        await _verify_coach(db, coach_id)

    capacity = int(data.get("capacity") or 20)
    if capacity < 1:
        raise ValidationError("سعة الفوج يجب أن تكون 1 على الأقل")

    group = Group(
        name=name,
        code=data.get("code"),
        sport_id=data.get("sport_id"),
        level=data.get("level", "beginner"),
        min_age=data.get("min_age"),
        max_age=data.get("max_age"),
        gender=data.get("gender"),
        coach_id=coach_id,
        capacity=capacity,
        current_count=0,
        color=data.get("color"),
        location=data.get("location"),
        schedule_days=data.get("schedule_days"),
        start_time=data.get("start_time"),
        end_time=data.get("end_time"),
        monthly_fee=data.get("monthly_fee"),
        description=data.get("description"),
        status=Status.ACTIVE,
        is_active=True,
        club_id=data.get("club_id"),
    )
    db.add(group)
    await db.flush()
    return group


async def update_group(db: AsyncSession, group_id: int, data: dict[str, Any]) -> Group:
    """تحديث بيانات فوج."""
    group = await get_group(db, group_id)

    if data.get("coach_id") and data["coach_id"] != group.coach_id:
        await _verify_coach(db, data["coach_id"])

    # فحص السعة لا تقل عن العدد الحالي
    if data.get("capacity"):
        new_cap = int(data["capacity"])
        if new_cap < (group.current_count or 0):
            raise BusinessRuleError(
                f"لا يمكن خفض السعة إلى {new_cap} لأن الفوج يضم "
                f"{group.current_count} رياضي"
            )

    for field, value in data.items():
        if field in ("club_id", "created_at", "current_count") or value is None:
            continue
        if hasattr(group, field):
            setattr(group, field, value)

    await db.flush()
    await db.refresh(group)
    return group


async def get_group(db: AsyncSession, group_id: int) -> Group:
    """جلب فوج بمعرّفه مع احترام نطاق المستخدم."""
    group = await safe_get(db, Group, group_id)
    if group is None:
        raise NotFoundError("الفوج غير موجود")
    return group


async def delete_group(db: AsyncSession, group_id: int) -> None:
    """حذف فوج (منطقي) مع التأكد من خلوّه من الرياضيين."""
    group = await get_group(db, group_id)

    count = await db.execute(
        select(func.count(GroupAthlete.id)).where(
            GroupAthlete.group_id == group_id,
            GroupAthlete.is_active.is_(True),
        )
    )
    member_count = count.scalar() or 0
    if member_count > 0:
        raise BusinessRuleError(
            f"لا يمكن حذف فوج يضم {member_count} رياضي. "
            "يرجى نقل الرياضيين إلى أفواج أخرى أولاً"
        )

    group.is_active = False
    group.status = Status.INACTIVE
    group.deleted_at = utcnow()
    await db.flush()


async def list_groups(
    db: AsyncSession,
    *,
    search: str | None = None,
    coach_id: int | None = None,
    sport_id: int | None = None,
    active_only: bool = True,
) -> list[Group]:
    """قائمة الأفواج مع فلاتر."""
    stmt = select(Group)
    if active_only:
        stmt = stmt.where(Group.is_active.is_(True))
    if search:
        stmt = stmt.where(Group.name.ilike(f"%{search.strip()}%"))
    if coach_id:
        stmt = stmt.where(Group.coach_id == coach_id)
    if sport_id:
        stmt = stmt.where(Group.sport_id == sport_id)

    stmt = stmt.order_by(Group.name)
    return list((await db.execute(stmt)).scalars().all())


async def get_group_athletes(db: AsyncSession, group_id: int) -> list[Athlete]:
    """قائمة رياضي فوج."""
    result = await db.execute(
        select(Athlete)
        .join(GroupAthlete, GroupAthlete.athlete_id == Athlete.id)
        .where(
            GroupAthlete.group_id == group_id,
            GroupAthlete.is_active.is_(True),
            Athlete.is_active.is_(True),
        )
        .order_by(Athlete.full_name)
    )
    return list(result.scalars().all())


async def _verify_coach(db: AsyncSession, coach_id: int) -> StaffMember:
    """التأكد أن المدرب عضو فعّال في طاقم النادي."""
    result = await db.execute(
        select(StaffMember).where(StaffMember.id == coach_id)
    )
    staff = result.scalar_one_or_none()
    if staff is None:
        raise NotFoundError("المدرب غير موجود")
    if staff.role != UserRole.COACH:
        raise BusinessRuleError("العضو المحدد ليس مدرباً")
    if not staff.is_active:
        raise BusinessRuleError("هذا المدرب غير مُفعّل")
    return staff


# =====================================================================
#  الحصص التدريبية
# =====================================================================
async def create_session(db: AsyncSession, data: dict[str, Any]) -> TrainingSession:
    """برمجة حصة تدريبية جديدة."""
    session_date = data.get("session_date")
    if isinstance(session_date, str):
        session_date = dt.date.fromisoformat(session_date)
    if session_date is None:
        raise ValidationError("تاريخ الحصة مطلوب")

    start_time = data.get("start_time")
    end_time = data.get("end_time")
    if not start_time or not end_time:
        raise ValidationError("توقيت بداية ونهاية الحصة مطلوب")
    if start_time >= end_time:
        raise ValidationError("وقت البداية يجب أن يكون قبل وقت النهاية")

    group_id = data.get("group_id")
    if group_id:
        group = await get_group(db, group_id)
        expected = len(await get_group_athletes(db, group_id))
    else:
        group = None
        expected = int(data.get("expected_count") or 0)

    session = TrainingSession(
        group_id=group_id,
        coach_id=data.get("coach_id") or (group.coach_id if group else None),
        title=data.get("title"),
        session_date=session_date,
        start_time=start_time,
        end_time=end_time,
        location=data.get("location") or (group.location if group else None),
        session_type=data.get("session_type"),
        objectives=data.get("objectives"),
        notes=data.get("notes"),
        status=SessionStatus.SCHEDULED,
        expected_count=expected,
        club_id=data.get("club_id"),
    )
    db.add(session)
    await db.flush()
    return session


async def update_session(db: AsyncSession, session_id: int, data: dict[str, Any]) -> TrainingSession:
    """تعديل حصة تدريبية."""
    session = await safe_get(db, TrainingSession, session_id)
    if session is None:
        raise NotFoundError("الحصة غير موجودة")

    if session.status == SessionStatus.COMPLETED and data.get("session_date"):
        raise BusinessRuleError("لا يمكن تعديل حصة مكتملة")

    for field, value in data.items():
        if field in ("club_id", "created_at") or value is None:
            continue
        if hasattr(session, field):
            setattr(session, field, value)

    await db.flush()
    await db.refresh(session)
    return session


async def cancel_session(db: AsyncSession, session_id: int, reason: str) -> TrainingSession:
    """إلغاء حصة تدريبية مع تسجيل السبب."""
    session = await safe_get(db, TrainingSession, session_id)
    if session is None:
        raise NotFoundError("الحصة غير موجودة")

    session.status = SessionStatus.CANCELLED
    session.cancelled_reason = reason
    await db.flush()
    return session


async def list_sessions(
    db: AsyncSession,
    *,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    group_id: int | None = None,
    coach_id: int | None = None,
    status: str | None = None,
) -> list[TrainingSession]:
    """استعلام عن الحصص في فترة."""
    stmt = select(TrainingSession)
    if start_date:
        stmt = stmt.where(TrainingSession.session_date >= start_date)
    if end_date:
        stmt = stmt.where(TrainingSession.session_date <= end_date)
    if group_id:
        stmt = stmt.where(TrainingSession.group_id == group_id)
    if coach_id:
        stmt = stmt.where(TrainingSession.coach_id == coach_id)
    if status:
        stmt = stmt.where(TrainingSession.status == status)

    stmt = stmt.order_by(TrainingSession.session_date.desc(), TrainingSession.start_time)
    return list((await db.execute(stmt)).scalars().all())


async def get_session(db: AsyncSession, session_id: int) -> TrainingSession:
    """
    جلب حصة بمعرّفها مع تحميل علاقاتها.

    نحمّل ``group`` و ``coach`` صراحةً لأن ``with_loader_criteria`` (نظام
    العزل) يستبدل التحميل الكسول وعلاقاته، فبدون تحميل صريح تفشل
    محاولات الوصول إلى ``session.group`` لاحقاً.
    """
    from sqlalchemy.orm import selectinload

    session = await safe_get(db, TrainingSession, session_id)
    if session is None:
        raise NotFoundError("الحصة غير موجودة")

    result = await db.execute(
        select(TrainingSession)
        .where(TrainingSession.id == session_id)
        .options(selectinload(TrainingSession.group), selectinload(TrainingSession.coach))
    )
    loaded = result.scalar_one_or_none()
    if loaded is None:
        raise NotFoundError("الحصة غير موجودة")
    return loaded


# =====================================================================
#  تسجيل الحضور
# =====================================================================
async def record_attendance(
    db: AsyncSession,
    *,
    session_id: int,
    athlete_id: int,
    status: AttendanceStatus = AttendanceStatus.PRESENT,
    method: AttendanceMethod = AttendanceMethod.MANUAL,
    notes: str | None = None,
    recorded_by_id: int | None = None,
    minutes_late: int = 0,
) -> Attendance:
    """
    تسجيل حضور رياضي في حصة.

    يقبل الحضور المخالف (حضور ثم غياب في نفس الحصة) ويحدّثه بدل رفضه،
    لأن هذا يحدث في الواقع تكراراً عند تصحيح المدرب لسجل خاطئ.
    """
    session = await get_session(db, session_id)
    athlete = await safe_get(db, Athlete, athlete_id)
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")

    # فحوص سلامة: الرياضي يجب أن يكون من نفس النادي
    if athlete.club_id != session.club_id:
        raise BusinessRuleError("الرياضي لا ينتمي لنفس النادي")

    existing_result = await db.execute(
        select(Attendance).where(
            Attendance.session_id == session_id,
            Attendance.athlete_id == athlete_id,
        )
    )
    record = existing_result.scalar_one_or_none()

    if record:
        record.status = status
        record.method = method
        record.notes = notes
        record.minutes_late = minutes_late
        record.recorded_by_id = recorded_by_id
        record.recorded_at = utcnow()
    else:
        record = Attendance(
            club_id=session.club_id,
            session_id=session_id,
            athlete_id=athlete_id,
            group_id=session.group_id,
            session_date=session.session_date,
            status=status,
            method=method,
            minutes_late=minutes_late,
            notes=notes,
            recorded_by_id=recorded_by_id,
            recorded_at=utcnow(),
        )
        db.add(record)

    await db.flush()
    await _refresh_session_counts(db, session_id)
    return record


async def record_attendance_by_card(
    db: AsyncSession,
    *,
    session_id: int,
    card_code: str,
    status: AttendanceStatus = AttendanceStatus.PRESENT,
    recorded_by_id: int | None = None,
) -> Attendance:
    """
    تسجيل الحضور بمسح بطاقة QR أو إدخال كودها يدوياً.

    ``card_code`` يمكن أن يكون:
      * كود البطاقة مباشرة (``ABC-1234``)، أو
      * محتوى رمز QR الكامل (``MASHIA:<id>:<code>:<salt>``).
    """
    session = await get_session(db, session_id)

    # استخراج الكود من محتوى QR إن كان بصيغته الكاملة
    decoded = decode_qr_payload(card_code)
    if decoded:
        _, extracted_code = decoded
        card_code = extracted_code

    card_code = (card_code or "").strip().upper()
    if not card_code:
        raise ValidationError("كود البطاقة مطلوب")

    result = await db.execute(
        select(Athlete).where(Athlete.card_code == card_code)
    )
    athlete = result.scalar_one_or_none()

    if athlete is None:
        raise NotFoundError("لا يوجد رياضي بهذا الكود")
    if not athlete.is_active:
        raise BusinessRuleError("هذا الرياضي غير مُفعّل")
    if athlete.club_id != session.club_id:
        # لا نكشف أن البطاقة موجودة في نادي آخر
        logger.warning(
            "محاولة تسجيل حضور ببطاقة من نادي آخر: card=%s session_club=%s",
            card_code, session.club_id,
        )
        raise NotFoundError("لا يوجد رياضي بهذا الكود")

    return await record_attendance(
        db,
        session_id=session_id,
        athlete_id=athlete.id,
        status=status,
        method=AttendanceMethod.QR,
        recorded_by_id=recorded_by_id,
    )


async def bulk_record_attendance(
    db: AsyncSession,
    *,
    session_id: int,
    records: list[dict[str, Any]],
    default_status: AttendanceStatus = AttendanceStatus.PRESENT,
    recorded_by_id: int | None = None,
) -> int:
    """
    تسجيل حضور دفعة واحدة (قائمة فوج كاملة أو ملف Excel مستورد).

    يُرجع عدد السجلات المُعالجة. الأخطاء الفردية لا تُوقف الدفعة كاملة --
    نجمعها ونرجع ملخصاً.
    """
    processed = 0
    for record in records:
        try:
            status = AttendanceStatus(record.get("status", default_status.value))
        except ValueError:
            status = default_status

        await record_attendance(
            db,
            session_id=session_id,
            athlete_id=int(record["athlete_id"]),
            status=status,
            method=AttendanceMethod(record.get("method", AttendanceMethod.MANUAL.value)),
            notes=record.get("notes"),
            recorded_by_id=recorded_by_id,
        )
        processed += 1
    return processed


async def mark_absent_for_remaining(
    db: AsyncSession,
    *,
    session_id: int,
    recorded_by_id: int | None = None,
) -> int:
    """
    تحديد المتبقين من أفواج الحصة كـ "غائب".

    اختصار مفيد للمدرب: يسجّل الحاضرين، ثم يضغط زراً واحداً ليتحول الباقي
    تلقائياً إلى غائب -- بدلاً من تحديد كل غائب يدوياً.
    """
    session = await get_session(db, session_id)
    if not session.group_id:
        raise BusinessRuleError("الحصة غير مرتبطة بفوج")

    athletes = await get_group_athletes(db, session.group_id)
    existing = await db.execute(
        select(Attendance.athlete_id).where(Attendance.session_id == session_id)
    )
    already = {row[0] for row in existing.all()}

    count = 0
    for athlete in athletes:
        if athlete.id in already:
            continue
        await record_attendance(
            db,
            session_id=session_id,
            athlete_id=athlete.id,
            status=AttendanceStatus.ABSENT,
            method=AttendanceMethod.AUTO,
            recorded_by_id=recorded_by_id,
            notes="تحديد آلي كغائب",
        )
        count += 1
    return count


async def _refresh_session_counts(db: AsyncSession, session_id: int) -> None:
    """تحديث عدّادات الحضور في الحصة بعد كل تسجيل."""
    session = await get_session(db, session_id)
    rows = await db.execute(
        select(Attendance.status, func.count(Attendance.id))
        .where(Attendance.session_id == session_id)
        .group_by(Attendance.status)
    )
    summary = {row[0]: row[1] for row in rows.all()}

    present = summary.get(AttendanceStatus.PRESENT, 0) + summary.get(AttendanceStatus.LATE, 0)
    session.present_count = present
    session.absent_count = summary.get(AttendanceStatus.ABSENT, 0)
    await db.flush()


async def get_session_attendance(
    db: AsyncSession, session_id: int
) -> list[tuple[Attendance, Athlete]]:
    """سجل حضور الحصة مع بيانات الرياضيين."""
    result = await db.execute(
        select(Attendance, Athlete)
        .join(Athlete, Athlete.id == Attendance.athlete_id)
        .where(Attendance.session_id == session_id)
        .order_by(Athlete.full_name)
    )
    return list(result.all())


async def get_athlete_attendance_history(
    db: AsyncSession,
    athlete_id: int,
    *,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    limit: int = 50,
) -> list[Attendance]:
    """سجل حضور رياضي معبّر عنه زمنياً."""
    stmt = select(Attendance).where(Attendance.athlete_id == athlete_id)
    if start_date:
        stmt = stmt.where(Attendance.session_date >= start_date)
    if end_date:
        stmt = stmt.where(Attendance.session_date <= end_date)
    stmt = stmt.order_by(Attendance.session_date.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def compute_attendance_rate(
    db: AsyncSession,
    athlete_id: int,
    days: int = 30,
) -> float | None:
    """نسبة حضور رياضي خلال آخر N يوماً."""
    since = dt.date.today() - dt.timedelta(days=days)
    rows = await db.execute(
        select(Attendance.status, func.count(Attendance.id))
        .where(
            Attendance.athlete_id == athlete_id,
            Attendance.session_date >= since,
        )
        .group_by(Attendance.status)
    )
    summary = {row[0]: row[1] for row in rows.all()}
    present = summary.get(AttendanceStatus.PRESENT, 0) + summary.get(AttendanceStatus.LATE, 0)
    absent = summary.get(AttendanceStatus.ABSENT, 0)
    excused = summary.get(AttendanceStatus.EXCUSED, 0)
    total = present + absent + excused
    if total == 0:
        return None
    return round((present / total) * 100, 1)


# =====================================================================
#  التقييمات
# =====================================================================
async def create_evaluation(
    db: AsyncSession, data: dict[str, Any]
) -> Evaluation:
    """إنشاء تقييم جديد لرياضي."""
    athlete = await safe_get(db, Athlete, int(data["athlete_id"]))
    if athlete is None:
        raise NotFoundError("الرياضي غير موجود")

    scores = {
        "physical_score": data.get("physical_score"),
        "technique_score": data.get("technique_score"),
        "discipline_score": data.get("discipline_score"),
        "teamwork_score": data.get("teamwork_score"),
    }

    for name, value in scores.items():
        if value is None:
            continue
        if not 1 <= int(value) <= 10:
            raise ValidationError("درجات التقييم يجب أن تكون بين 1 و 10")

    present = [v for v in scores.values() if v is not None]
    if not present:
        raise ValidationError("يجب إدخال درجة واحدة على الأقل")

    eval_date = data.get("eval_date")
    if isinstance(eval_date, str):
        eval_date = dt.date.fromisoformat(eval_date)

    evaluation = Evaluation(
        club_id=athlete.club_id,
        athlete_id=athlete.id,
        group_id=data.get("group_id"),
        coach_id=data.get("coach_id"),
        session_id=data.get("session_id"),
        eval_date=eval_date or dt.date.today(),
        type=data.get("type", EvaluationType.PERIODIC.value),
        overall_score=round(sum(present) / len(present), 2),
        strengths=data.get("strengths"),
        weaknesses=data.get("weaknesses"),
        notes=data.get("notes"),
        recommendations=data.get("recommendations"),
        parent_notified=False,
        **scores,
    )
    db.add(evaluation)
    await db.flush()
    await db.refresh(evaluation)
    return evaluation


async def list_evaluations(
    db: AsyncSession,
    *,
    athlete_id: int | None = None,
    group_id: int | None = None,
    coach_id: int | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    limit: int = 50,
) -> list[Evaluation]:
    """استعلام عن التقييمات."""
    stmt = select(Evaluation)
    if athlete_id:
        stmt = stmt.where(Evaluation.athlete_id == athlete_id)
    if group_id:
        stmt = stmt.where(Evaluation.group_id == group_id)
    if coach_id:
        stmt = stmt.where(Evaluation.coach_id == coach_id)
    if start_date:
        stmt = stmt.where(Evaluation.eval_date >= start_date)
    if end_date:
        stmt = stmt.where(Evaluation.eval_date <= end_date)

    stmt = stmt.order_by(Evaluation.eval_date.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_athlete_progress(
    db: AsyncSession, athlete_id: int
) -> dict[str, Any]:
    """
    تطور رياضي عبر التقييمات: يعطي صورة اتجاه واضحة للولي.

    نقارن متوسط آخر تقييم بالنصف الأول من التقييمات لقياس الاتجاه.
    """
    evaluations = await list_evaluations(
        db, athlete_id=athlete_id, limit=100
    )

    if not evaluations:
        return {
            "evaluations": [],
            "trend": None,
            "latest": None,
            "average_score": None,
        }

    scores = [(e.eval_date, e.average_score) for e in reversed(evaluations) if e.average_score]
    if len(scores) < 2:
        trend = None
    else:
        first_half = sum(s for _, s in scores[: len(scores) // 2]) / (len(scores) // 2)
        second_half = scores[len(scores) // 2 :]
        recent = sum(s for _, s in second_half) / len(second_half)
        trend = round(recent - first_half, 2)

    all_scores = [s for _, s in scores]
    return {
        "evaluations": evaluations,
        "trend": trend,
        "latest": evaluations[0],
        "average_score": round(sum(all_scores) / len(all_scores), 2),
        "best": max(all_scores),
        "count": len(evaluations),
    }