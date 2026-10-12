"""
خدمة التقارير ولوحات المؤشرات (Reports & Dashboard Service)
===========================================================
كل الأرقام المعروضة في لوحات التحكم، محسوبة في استعلامات مُجمَّعة واحدة
عبر قاعدة البيانات (لا في Python) لتقليل عدد Round-trips.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Athlete,
    Attendance,
    AttendanceStatus,
    Evaluation,
    Expense,
    Group,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    RegistrationRequest,
    SessionStatus,
    StaffMember,
    TrainingSession,
)

logger = logging.getLogger(__name__)


# =====================================================================
#  لوحة تحكم النادي
# =====================================================================
async def club_dashboard(db: AsyncSession) -> dict[str, Any]:
    """
    ملخص شامل للوحة تحكم النادي.

    يجمع كل المؤشرات في Round-trip واحد قدر الإمكان لأن هذه أول شاشة
    يفتحها المستخدم كل يوم، وسرعتها هي انطباعه عن المنصة كلها.
    """
    today = dt.date.today()
    month_start = today.replace(day=1)
    week_start = today - dt.timedelta(days=today.weekday())   # بداية الأسبوع (الاثنين)

    # ---------- 1) عدّادات البناء ----------
    #   استعلامات منفصلة لكل عدّاد: استعلام واحد بثلاثة ``select_from``
    #   بدون شروط ربط ينتج **منتجاً كارتيزياً**، والمنتج الكارتيزي يقرأ
    #   صفاً واحداً بدل ثلاثة -- أي أرقام خاطئة صامتة.
    total_athletes = await db.execute(
        select(func.count(func.distinct(Athlete.id))).where(Athlete.is_active.is_(True))
    )
    total_groups = await db.execute(
        select(func.count(func.distinct(Group.id))).where(Group.is_active.is_(True))
    )
    total_staff = await db.execute(
        select(func.count(func.distinct(StaffMember.id))).where(
            StaffMember.is_active.is_(True)
        )
    )
    athlete_count = total_athletes.scalar() or 0
    group_count = total_groups.scalar() or 0
    staff_count = total_staff.scalar() or 0

    # ---------- 2) الحضور اليوم ----------
    today_attendance = await db.execute(
        select(
            func.count(Attendance.id).label("total"),
            func.sum(case((Attendance.status.in_(["present", "late"]), 1), else_=0)).label("present"),
            func.sum(case((Attendance.status == "absent", 1), else_=0)).label("absent"),
        ).where(Attendance.session_date == today)
    )
    att = today_attendance.one()
    att_total = att[0] or 0
    att_present = att[1] or 0
    att_absent = att[2] or 0
    att_rate = round((att_present / att_total) * 100, 1) if att_total else 0.0

    # ---------- 3) الحصص اليوم ----------
    sessions_today = await db.execute(
        select(
            func.count(TrainingSession.id).label("total"),
            func.sum(case((TrainingSession.status == "scheduled", 1), else_=0)).label("upcoming"),
            func.sum(case((TrainingSession.status == "completed", 1), else_=0)).label("done"),
        ).where(TrainingSession.session_date == today)
    )
    sess = sessions_today.one()
    sessions_total = sess[0] or 0
    sessions_completed = sess[2] or 0

    # ---------- 4) المالية هذا الشهر ----------
    income_stmt = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            func.date(Payment.payment_date) >= month_start,
            Payment.status == PaymentStatus.COMPLETED,
        )
    )
    month_income = income_stmt.scalar() or 0

    expense_stmt = await db.execute(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            func.date(Expense.expense_date) >= month_start,
            Expense.approved.is_(True),
        )
    )
    month_expenses = expense_stmt.scalar() or 0

    # ---------- 5) الديون ----------
    debt_stmt = await db.execute(
        select(
            func.coalesce(func.sum(Invoice.total - Invoice.paid), 0).label("debt"),
            func.count(Invoice.id).label("count"),
        ).where(
            Invoice.status.in_([
                InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE
            ]),
            Invoice.is_active.is_(True),
        )
    )
    debt_row = debt_stmt.one()
    total_debt = debt_row[0] or 0
    debt_invoices = debt_row[1] or 0

    overdue_stmt = await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.status.in_([
                InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE
            ]),
            Invoice.due_date < today,
        )
    )
    overdue_count = overdue_stmt.scalar() or 0

    # ---------- 6) طلبات التسجيل الجديدة ----------
    pending_requests = await db.execute(
        select(func.count(RegistrationRequest.id)).where(
            RegistrationRequest.status == "pending",
            RegistrationRequest.is_active.is_(True),
        )
    )
    pending_registrations = pending_requests.scalar() or 0

    # ---------- 7) رياضيون جدد هذا الشهر ----------
    new_athletes = await db.execute(
        select(func.count(Athlete.id)).where(
            Athlete.joined_at >= month_start,
            Athlete.is_active.is_(True),
        )
    )
    new_athletes_count = new_athletes.scalar() or 0

    # ---------- 8) نسبة إشغال الأفواج ----------
    avg_fill = 0.0
    if group_count:
        fill_stmt = await db.execute(
            select(
                func.coalesce(func.avg(Group.current_count), 0),
                func.coalesce(func.avg(Group.capacity), 1),
            ).where(Group.is_active.is_(True))
        )
        fill_row = fill_stmt.one()
        avg_cap = float(fill_row[1] or 1) or 1
        avg_fill = round((float(fill_row[0] or 0) / avg_cap) * 100, 1)

    # ---------- 9) المغادرون آخر 30 يوماً ----------
    left_count = await db.execute(
        select(func.count(Athlete.id)).where(
            Athlete.left_at.isnot(None),
            Athlete.left_at >= today - dt.timedelta(days=30),
        )
    )
    left_month = left_count.scalar() or 0

    return {
        "period": {"today": today, "month_start": month_start, "week_start": week_start},
        "counts": {
            "athletes": athlete_count,
            "groups": group_count,
            "staff": staff_count,
            "new_athletes_month": new_athletes_count,
            "left_month": left_month,
        },
        "attendance": {
            "today_total": att_total,
            "today_present": att_present,
            "today_absent": att_absent,
            "today_rate": att_rate,
        },
        "sessions": {
            "today_total": sessions_total,
            "today_completed": sessions_completed,
            "today_remaining": max(0, sessions_total - sessions_completed),
        },
        "finance": {
            "month_income": month_income,
            "month_expenses": month_expenses,
            "month_net": (month_income or 0) - (month_expenses or 0),
            "total_debt": total_debt,
            "debt_invoices": debt_invoices,
            "overdue_count": overdue_count,
        },
        "registrations": {"pending": pending_registrations},
        "occupancy": {"avg_fill_rate": avg_fill},
    }


async def today_schedule(db: AsyncSession) -> list[dict[str, Any]]:
    """جدول اليوم: الحصص مع معلومات الفوج والمدرب."""
    today = dt.date.today()
    rows = await db.execute(
        select(TrainingSession, Group)
        .join(Group, Group.id == TrainingSession.group_id, isouter=True)
        .where(TrainingSession.session_date == today)
        .order_by(TrainingSession.start_time)
    )

    return [
        {
            "session": row[0],
            "group": row[1],
            "time": row[0].start_time.strftime("%H:%M"),
            "end_time": row[0].end_time.strftime("%H:%M"),
            "is_completed": row[0].status == SessionStatus.COMPLETED,
        }
        for row in rows.all()
    ]


async def recent_absences(
    db: AsyncSession, days: int = 7, limit: int = 10
) -> list[dict[str, Any]]:
    """أكثر الرياضيين غياباً خلال آخر N يوماً (تستدعي الانتباه)."""
    since = dt.date.today() - dt.timedelta(days=days)
    rows = await db.execute(
        select(
            Athlete.id,
            Athlete.full_name,
            func.count(Attendance.id).label("absences"),
        )
        .join(Attendance, Attendance.athlete_id == Athlete.id)
        .where(
            Attendance.status == AttendanceStatus.ABSENT,
            Attendance.session_date >= since,
            Athlete.is_active.is_(True),
        )
        .group_by(Athlete.id)
        .order_by(func.count(Attendance.id).desc())
        .limit(limit)
    )
    return [
        {"athlete_id": r[0], "full_name": r[1], "absences": r[2]}
        for r in rows.all()
    ]


async def attendance_chart(
    db: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """بيانات الرسم البياني للحضور اليومي."""
    since = dt.date.today() - dt.timedelta(days=days - 1)

    rows = await db.execute(
        select(
            Attendance.session_date,
            func.sum(case((Attendance.status.in_(["present", "late"]), 1), else_=0)),
            func.sum(case((Attendance.status == "absent", 1), else_=0)),
        )
        .where(Attendance.session_date >= since)
        .group_by(Attendance.session_date)
        .order_by(Attendance.session_date)
    )

    data = {r[0]: {"present": r[1] or 0, "absent": r[2] or 0} for r in rows.all()}

    result = []
    for i in range(days):
        day = since + dt.timedelta(days=i)
        entry = data.get(day, {"present": 0, "absent": 0})
        total = entry["present"] + entry["absent"]
        result.append({
            "date": day,
            "label": day.strftime("%d/%m"),
            "present": entry["present"],
            "absent": entry["absent"],
            "rate": round((entry["present"] / total) * 100, 1) if total else 0,
        })
    return result


async def top_athletes(db: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    """
    الرياضيون الأعلى تقييماً -- شاشة تشجيع وتحفيز.

    نحسب المتوسط العام من التقييمات بدل الاعتماد على تقييم واحد، حتى لا
    يتأرجح الترتيب بتغيّر آراء المدربين أسبوعياً.
    """
    from app.models import Evaluation as EV

    rows = await db.execute(
        select(
            Athlete.id,
            Athlete.full_name,
            func.avg(
                func.coalesce(EV.overall_score, 0)
            ).label("avg_score"),
            func.count(EV.id).label("eval_count"),
        )
        .join(EV, EV.athlete_id == Athlete.id)
        .where(Athlete.is_active.is_(True), EV.overall_score.isnot(None))
        .group_by(Athlete.id)
        .order_by(func.avg(EV.overall_score).desc())
        .limit(limit)
    )
    return [
        {
            "athlete_id": r[0],
            "full_name": r[1],
            "average_score": round(float(r[2] or 0), 2),
            "evaluations": r[3],
        }
        for r in rows.all()
    ]


async def groups_occupancy(db: AsyncSession) -> list[dict[str, Any]]:
    """نسبة إشغال كل فوج (يكشف الأفواج شبه الفارغة)."""
    rows = await db.execute(
        select(
            Group.id,
            Group.name,
            Group.capacity,
            Group.current_count,
        )
        .where(Group.is_active.is_(True))
        .order_by(Group.name)
    )

    result = []
    for group in rows.all():
        capacity = group[2] or 1
        count = group[3] or 0
        result.append({
            "group_id": group[0],
            "name": group[1],
            "capacity": group[2],
            "current": group[3],
            "fill_rate": round((count / capacity) * 100, 1),
            "available": max(0, capacity - count),
        })
    return result


# =====================================================================
#  لوحة تحكم ولي الأمر
# =====================================================================
async def parent_dashboard(
    db: AsyncSession, parent_id: int
) -> dict[str, Any]:
    """
    ملخص بوابة الولي.

    يعرض كل الأبناء عبر الأندية (الولي الموحد)، مع مؤشرات سريعة لكل ابن.
    """
    from app.models import ParentLink

    # الأبناء
    children_rows = await db.execute(
        select(Athlete)
        .join(ParentLink, ParentLink.athlete_id == Athlete.id)
        .where(ParentLink.parent_id == parent_id)
        .order_by(Athlete.full_name)
    )
    children = list(children_rows.scalars().all())

    if not children:
        return {
            "children": [],
            "summary": {"total_children": 0},
        }

    child_ids = [c.id for c in children]

    # --- الحضور لـ 30 يوماً لكل ابن ---
    since = dt.date.today() - dt.timedelta(days=30)
    att_rows = await db.execute(
        select(
            Attendance.athlete_id,
            func.sum(case((Attendance.status.in_(["present", "late"]), 1), else_=0)),
            func.sum(case((Attendance.status == "absent", 1), else_=0)),
        )
        .where(
            Attendance.athlete_id.in_(child_ids),
            Attendance.session_date >= since,
        )
        .group_by(Attendance.athlete_id)
    )
    attendance = {
        r[0]: {"present": r[1] or 0, "absent": r[2] or 0} for r in att_rows.all()
    }

    # --- الديون ---
    debt_rows = await db.execute(
        select(
            Invoice.athlete_id,
            func.coalesce(func.sum(Invoice.total - Invoice.paid), 0),
        )
        .where(
            Invoice.athlete_id.in_(child_ids),
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
            Invoice.is_active.is_(True),
        )
        .group_by(Invoice.athlete_id)
    )
    debts = {r[0]: float(r[1] or 0) for r in debt_rows.all()}

    # --- آخر تقييم لكل ابن ---
    eval_rows = await db.execute(
        select(
            Evaluation.athlete_id,
            Evaluation.overall_score,
            Evaluation.eval_date,
        )
        .where(Evaluation.athlete_id.in_(child_ids))
        .order_by(Evaluation.eval_date.desc())
    )
    latest_evals: dict[int, Any] = {}
    for r in eval_rows.all():
        if r[0] not in latest_evals:
            latest_evals[r[0]] = r

    # --- الأفواج ---
    group_rows = await db.execute(
        select(GroupAthlete.athlete_id, Group.name)
        .join(Group, Group.id == GroupAthlete.group_id)
        .where(
            GroupAthlete.athlete_id.in_(child_ids),
            GroupAthlete.is_active.is_(True),
        )
    )
    child_groups: dict[int, list[str]] = {}
    for r in group_rows.all():
        child_groups.setdefault(r[0], []).append(r[1])

    # --- بناء الملخص لكل ابن ---
    enriched = []
    total_debt = 0.0
    for child in children:
        att = attendance.get(child.id, {"present": 0, "absent": 0})
        total_att = att["present"] + att["absent"]
        rate = round((att["present"] / total_att) * 100, 1) if total_att else None

        debt = debts.get(child.id, 0.0)
        total_debt += debt

        eval_row = latest_evals.get(child.id)
        enriched.append({
            "athlete": child,
            "age": child.age,
            "groups": child_groups.get(child.id, []),
            "attendance": {
                "present": att["present"],
                "absent": att["absent"],
                "rate": rate,
                "period_days": 30,
            },
            "debt": debt,
            "latest_evaluation": eval_row[1] if eval_row else None,
            "latest_evaluation_date": eval_row[2] if eval_row else None,
        })

    return {
        "children": enriched,
        "summary": {
            "total_children": len(enriched),
            "total_debt": total_debt,
            "next_payment": await _next_payment_due(db, child_ids),
        },
    }


async def _next_payment_due(db: AsyncSession, child_ids: list[int]) -> Any:
    """أقرب استحقاق مالي قادم بين الأبناء."""
    from app.models import Athlete as A

    result = await db.execute(
        select(Invoice, A.full_name)
        .join(A, A.id == Invoice.athlete_id)
        .where(
            Invoice.athlete_id.in_(child_ids),
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
            Invoice.due_date >= dt.date.today(),
            Invoice.is_active.is_(True),
        )
        .order_by(Invoice.due_date)
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return {
        "invoice": row[0],
        "athlete_name": row[1],
        "amount_due": float(row[0].amount_due),
        "due_date": row[0].due_date,
    }


# =====================================================================
#  لوحة تحكم مدير المنصة
# =====================================================================
async def platform_dashboard(db: AsyncSession) -> dict[str, Any]:
    """إحصائيات عامة عن المنصة كلها (لمدير المنصة فقط)."""
    from app.models import Club, ClubStatus, ClubSubscription, User
    from app.models.enums import UserRole as UR

    today = dt.date.today()

    total_clubs = await db.execute(
        select(func.count(Club.id)).where(Club.status == ClubStatus.ACTIVE)
    )
    pending_clubs = await db.execute(
        select(func.count(Club.id)).where(Club.status == ClubStatus.PENDING)
    )
    expired_clubs = await db.execute(
        select(func.count(Club.id)).where(Club.status == ClubStatus.EXPIRED)
    )

    total_athletes = await db.execute(
        select(func.count(Athlete.id)).where(Athlete.is_active.is_(True))
    )
    total_users = await db.execute(select(func.count(User.id)))

    # الأندية التي ستنتهي خلال 7 أيام
    expiring_soon = await db.execute(
        select(func.count(Club.id)).where(
            Club.subscription_end >= today,
            Club.subscription_end <= today + dt.timedelta(days=settings_expire_window()),
        )
    )

    # إيرادات المنصة الشهرية
    month_start = today.replace(day=1)
    revenue = await db.execute(
        select(func.coalesce(func.sum(ClubSubscription.amount), 0)).where(
            func.date(ClubSubscription.paid_at) >= month_start,
            ClubSubscription.status == "active",
        )
    )

    # أكبر الأندية
    top_clubs = await db.execute(
        select(Club.id, Club.name, Club.members_count)
        .where(Club.status == ClubStatus.ACTIVE)
        .order_by(Club.members_count.desc())
        .limit(10)
    )

    return {
        "clubs": {
            "total_active": total_clubs.scalar() or 0,
            "pending": pending_clubs.scalar() or 0,
            "expired": expired_clubs.scalar() or 0,
            "expiring_soon": expiring_soon.scalar() or 0,
        },
        "users": {"total": total_users.scalar() or 0},
        "athletes": {"total": total_athletes.scalar() or 0},
        "revenue": {"month": float(revenue.scalar() or 0)},
        "top_clubs": [
            {"id": r[0], "name": r[1], "members": r[2]}
            for r in top_clubs.all()
        ],
    }


def settings_expire_window() -> int:
    """مدة التنبيه قبل انتهاء الاشتراك."""
    from app.core.config import settings

    return settings.RENEWAL_ALERT_DAYS