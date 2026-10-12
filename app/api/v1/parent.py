"""مسارات بوابة ولي الأمر (/api/v1/parent).

**حساب الولي الموحد**: يعرض كل أبنائه عبر الأندية المختلفة في واجهة واحدة.
نطاق العزل هنا مبني على ``athlete_ids`` (وليس ``club_id``)، لذا يمكن للوالد
أن يرى ابنين في ناديين مختلفين دون أن يرى رياضي غيره في نفس النادي.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import NotFoundError
from app.models import (
    Athlete,
    Club,
    Evaluation,
    Group,
    GroupAthlete,
    Invoice,
    ParentLink,
    User,
    UserRole,
)
from app.schemas import EvaluationResponse, InvoiceResponse, MessageResponse
from app.services import people as people_service
from app.services import reports as report_service
from app.services import training as training_service

router = APIRouter(prefix="/parent", tags=["بوابة ولي الأمر"])


async def _require_child(db: AsyncSession, user: User, athlete_id: int) -> Athlete:
    """
    التأكد أن الرياضي المطلوب من أبناء الولي.

    طبقة تحقق صريحة: حتى لو أفلت النظام الفلترة (نرجو ألا يحدث)، لا
    نكشف بيانات رياضي ليس ابن هذا الولي.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(Athlete)
        .join(ParentLink, ParentLink.athlete_id == Athlete.id)
        .where(
            ParentLink.parent_id == user.id,
            Athlete.id == athlete_id,
        )
    )
    child = result.scalar_one_or_none()
    if child is None:
        # نستخدم 404 لا 403 حتى لا نكشف وجود الرياضي في نظامنا
        raise NotFoundError("هذا الرياضي ليس من أبنائك")
    return child


# =====================================================================
#  لوحة الولي
# =====================================================================
@router.get("/dashboard", summary="لوحة الولي الموحدة")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """
    ملخص كل الأبناء عبر الأندية.

    لكل ابن: نسبة حضور آخر 30 يوماً، الديون، آخر تقييم، والأفواج.
    """
    data = await report_service.parent_dashboard(db, user.id)

    for child in data.get("children", []):
        athlete = child["athlete"]
        club = athlete.club
        child["club_name"] = club.name if club else "-"
        child["club_slug"] = club.slug if club else None
        child["photo_url"] = athlete.photo_url
        child["card_code"] = athlete.card_code
        child["birth_date"] = str(athlete.birth_date) if athlete.birth_date else None

    next_payment = data.get("summary", {}).get("next_payment")
    if next_payment:
        next_payment["due_date"] = str(next_payment["due_date"])

    return data


@router.get("/children", summary="قائمة أبنائي")
async def my_children(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """كل أبناء الولي مع أنديتهم وأفواجهم."""
    children = await people_service.get_parent_children(db, user.id)

    result = []
    for child in children:
        groups = await people_service.get_athlete_groups(db, child.id)
        club = await db.get(Club, child.club_id)
        result.append({
            "id": child.id,
            "full_name": child.full_name,
            "photo_url": child.photo_url,
            "birth_date": child.birth_date,
            "age": child.age,
            "age_group": child.age_group,
            "gender": child.gender_label,
            "club_name": club.name if club else "-",
            "club_slug": club.slug if club else None,
            "club_color": club.primary_color if club else None,
            "groups": [{"id": g.id, "name": g.name, "color": g.color} for g in groups],
            "status": child.status,
        })

    return result


# =====================================================================
#  تتبع الحضور
# =====================================================================
@router.get("/children/{athlete_id}/attendance", summary="سجل حضور الابن")
async def child_attendance(
    athlete_id: int,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """
    سجل الحضور التفصيلي مع نسبة الالتحاق.

    هذه الشاشة تجيب على سؤال الولي الأكثر تكراراً: «هل ابني ينزل بانتظام؟»
    """
    await _require_child(db, user, athlete_id)

    since = dt.date.today() - dt.timedelta(days=days)
    records = await training_service.get_athlete_attendance_history(
        db, athlete_id, start_date=since
    )
    rate = await training_service.compute_attendance_rate(db, athlete_id, days)

    summary: dict[str, int] = {}
    for record in records:
        summary[record.status] = summary.get(record.status, 0) + 1

    return {
        "athlete_id": athlete_id,
        "period_days": days,
        "attendance_rate": rate,
        "summary": {
            "present": summary.get("present", 0),
            "absent": summary.get("absent", 0),
            "late": summary.get("late", 0),
            "excused": summary.get("excused", 0),
        },
        "records": [
            {
                "id": r.id,
                "session_id": r.session_id,
                "date": r.session_date,
                "status": r.status,
                "status_label": r.status_label,
                "notes": r.notes,
            }
            for r in records
        ],
    }


@router.get("/children/{athlete_id}/absences", summary="الغيابات فقط")
async def child_absences(
    athlete_id: int,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """قائمة الغيابات فقط (ما يهم الولي فعلاً)."""
    await _require_child(db, user, athlete_id)

    since = dt.date.today() - dt.timedelta(days=days)
    records = await training_service.get_athlete_attendance_history(
        db, athlete_id, start_date=since
    )
    absences = [r for r in records if r.status == "absent"]

    return {
        "athlete_id": athlete_id,
        "period_days": days,
        "total": len(absences),
        "absences": [
            {
                "date": r.session_date,
                "session_id": r.session_id,
                "notes": r.notes,
            }
            for r in absences
        ],
    }


# =====================================================================
#  التقييمات
# =====================================================================
@router.get("/children/{athlete_id}/evaluations", summary="تقييمات الابن")
async def child_evaluations(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """تقييمات المدرب وتطور الابن عبر الزمن."""
    await _require_child(db, user, athlete_id)
    return await training_service.get_athlete_progress(db, athlete_id)


@router.get("/children/{athlete_id}/schedule", summary="جدول الحصص")
async def child_schedule(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """الأفواج التي ينتمي إليها الابن وأيام وتوقيتات التدريب."""
    child = await _require_child(db, user, athlete_id)
    groups = await people_service.get_athlete_groups(db, athlete_id)

    club = await db.get(Club, child.club_id)

    return {
        "athlete": {
            "id": child.id,
            "full_name": child.full_name,
            "club_name": club.name if club else "-",
        },
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "location": g.location,
                "schedule_days": g.schedule_days,
                "days": g.schedule_days_list,
                "time_label": g.time_label,
                "coach_name": g.coach.name if g.coach else None,
                "color": g.color,
            }
            for g in groups
        ],
    }


# =====================================================================
#  الوضع المالي
# =====================================================================
@router.get("/invoices", summary="فواتير أبنائي")
async def my_invoices(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
    status_filter: str | None = Query(None, alias="status"),
):
    """الفواتير غير المسددة لكل الأبناء."""
    children = await people_service.get_parent_children(db, user.id)
    child_ids = [c.id for c in children]

    if not child_ids:
        return []

    from sqlalchemy import select

    stmt = select(Invoice).where(Invoice.athlete_id.in_(child_ids))
    if status_filter:
        stmt = stmt.where(Invoice.status == status_filter)
    else:
        stmt = stmt.where(
            Invoice.status.in_(["unpaid", "partial", "overdue"]),
            Invoice.is_active.is_(True),
        )
    stmt = stmt.order_by(Invoice.due_date)

    invoices = list((await db.execute(stmt)).scalars().all())
    names = {c.id: c.full_name for c in children}

    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "athlete_id": inv.athlete_id,
            "athlete_name": names.get(inv.athlete_id, "-"),
            "period_start": inv.period_start,
            "period_end": inv.period_end,
            "due_date": inv.due_date,
            "total": float(inv.total),
            "paid": float(inv.paid),
            "amount_due": float(inv.amount_due),
            "status": inv.status,
            "status_label": inv.status_label,
            "is_overdue": inv.is_overdue,
            "days_overdue": inv.days_overdue,
        }
        for inv in invoices
    ]


@router.get("/payments", summary="سجل دفعاتي")
async def my_payments(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
    limit: int = Query(20, ge=1, le=100),
):
    """آخر الدفعات المسجلة لكل الأبناء."""
    from sqlalchemy import select

    from app.models import Payment

    children = await people_service.get_parent_children(db, user.id)
    child_ids = [c.id for c in children]
    if not child_ids:
        return []

    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.athlete_id.in__(child_ids))
                .order_by(Payment.payment_date.desc())
                .limit(limit)
            )
        ).scalars().all()
    )
    names = {c.id: c.full_name for c in children}

    return [
        {
            "id": p.id,
            "receipt_number": p.receipt_number,
            "athlete_id": p.athlete_id,
            "athlete_name": names.get(p.athlete_id, "-"),
            "amount": float(p.amount),
            "payment_date": p.payment_date,
            "method": p.method,
            "method_label": p.method_label,
        }
        for p in payments
    ]


# =====================================================================
#  بيانات البطاقة
# =====================================================================
@router.get("/children/{athlete_id}/card", summary="بطاقة الابن")
async def child_card(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(UserRole.PARENT)),
):
    """بطاقة الابن جاهزة للعرض أو الطباعة."""
    from fastapi.responses import StreamingResponse
    import io

    from app.services import cards as card_service

    await _require_child(db, user, athlete_id)
    content = await card_service.generate_athlete_card(db, athlete_id)
    media_type = "application/pdf" if content[:4] == b"%PDF" else "text/html; charset=utf-8"
    return StreamingResponse(
        io.BytesIO(content), media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="card-{athlete_id}"'},
    )