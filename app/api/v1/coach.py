"""مسارات مساحة المدرب (/api/v1/coach).

مخصصة للمدربين. كل استعلامات المدرب مقيدة بأفواجه ورياضييها فقط --
سواء عبر نطاق المستأجر (club_id) أو عبر فلاتر ``coach_id`` الصريحة.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_coach
from app.core.db import get_db
from app.models import (
    Athlete,
    Attendance,
    Evaluation,
    Group,
    SessionStatus,
    StaffMember,
    TrainingSession,
    User,
)
from app.schemas import (
    AthleteResponse,
    EvaluationCreateRequest,
    EvaluationResponse,
    GroupResponse,
    MessageResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
)
from app.services import cards as card_service
from app.services import notifications as notification_service
from app.services import training as training_service

router = APIRouter(prefix="/coach", tags=["مساحة المدرب"])


async def _my_groups(db: AsyncSession, staff: StaffMember) -> list[Group]:
    """الأفواج التي يدرّبها هذا المدرب فقط."""
    from app.services.training import list_groups

    return await list_groups(db, coach_id=staff.id)


# =====================================================================
#  أفوازي
# =====================================================================
@router.get("/groups", summary="أفوازي")
async def my_groups(
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """قائمة الأفواج المسندة للمدرب مع إحصاءاتها."""
    groups = await _my_groups(db, staff)

    result = []
    for group in groups:
        athletes = await training_service.get_group_athletes(db, group.id)
        result.append({
            "id": group.id,
            "name": group.name,
            "code": group.code,
            "level": group.level,
            "capacity": group.capacity,
            "current_count": group.current_count,
            "color": group.color,
            "location": group.location,
            "schedule_days": group.schedule_days,
            "time_label": group.time_label,
            "athletes_count": len(athletes),
        })
    return result


@router.get("/groups/{group_id}/athletes", summary="رياضي فوجي")
async def group_athletes(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """
    قائمة رياضي أحد أفوازي.

    نتحقق أن الفوج ضمن أفوازي -- عزل المستأجر وحده لا يكفي لأن المدرب
    قد يكون مطلوباً على قراءة فوج زميله.
    """
    groups = await _my_groups(db, staff)
    group_ids = {g.id for g in groups}
    if group_id not in group_ids:
        from app.core.exceptions import PermissionDeniedError

        raise PermissionDeniedError("هذا الفوج ليس ضمن أفواجك")

    athletes = await training_service.get_group_athletes(db, group_id)
    return [
        {
            **AthleteResponse.model_validate(a).model_dump(),
            "attendance_rate": await training_service.compute_attendance_rate(
                db, a.id, 30
            ),
        }
        for a in athletes
    ]


# =====================================================================
#  حصصي
# =====================================================================
@router.get("/sessions", summary="حصصي")
async def my_sessions(
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
):
    """الحصص المسندة للمدرب."""
    sessions = await training_service.list_sessions(
        db,
        start_date=start_date,
        end_date=end_date,
        coach_id=staff.id,
        status=status_filter,
    )
    return [
        {
            "id": s.id,
            "group_id": s.group_id,
            "group_name": s.group.name if s.group else None,
            "title": s.title,
            "session_date": s.session_date,
            "time_label": s.time_label,
            "location": s.location,
            "status": s.status,
            "expected_count": s.expected_count,
            "present_count": s.present_count,
            "absent_count": s.absent_count,
        }
        for s in sessions
    ]


@router.post("/sessions", response_model=SessionResponse, status_code=201, summary="إنشاء حصة")
async def create_session(
    payload: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """إضافة حصة تدريبية لأحد أفواج المدرب."""
    data = payload.model_dump()
    data["coach_id"] = staff.id

    if data.get("group_id"):
        groups = await _my_groups(db, staff)
        if data["group_id"] not in {g.id for g in groups}:
            from app.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError("لا يمكنك إنشاء حصة لفوج ليس من أفواجك")

    session = await training_service.create_session(db, data)
    await db.commit()
    await db.refresh(session)
    return SessionResponse.model_validate(session)


@router.get("/sessions/today", summary="حصص اليوم")
async def today_sessions(
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """حصص اليوم مع حالة تسجيل الحضور -- أول ما يفتحه المدرب صباحاً."""
    today = dt.date.today()
    sessions = await training_service.list_sessions(
        db, start_date=today, end_date=today, coach_id=staff.id
    )
    return [
        {
            "id": s.id,
            "group_id": s.group_id,
            "group_name": s.group.name if s.group else "-",
            "time_label": s.time_label,
            "location": s.location,
            "status": s.status,
            "expected_count": s.expected_count,
            "present_count": s.present_count,
            "absent_count": s.absent_count,
            "attendance_done": s.present_count + s.absent_count > 0,
        }
        for s in sessions
    ]


@router.put("/sessions/{session_id}", summary="تعديل حصة")
async def update_session(
    session_id: int,
    payload: SessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """تعديل حصة مسندة للمدرب."""
    session = await training_service.get_session(db, session_id)
    if session.coach_id != staff.id:
        from app.core.exceptions import PermissionDeniedError

        raise PermissionDeniedError("هذه الحصة ليست من حصصك")

    updated = await training_service.update_session(
        db, session_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return MessageResponse(message="تم تعديل الحصة")


# =====================================================================
#  تسجيل الحضور
# =====================================================================
@router.get("/sessions/{session_id}/attendance-sheet", summary="ورقة الحضور")
async def attendance_sheet(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """
    ورقة حضور الحصة: قائمة رياضي الفوج مع حالتهم الحالية.

    هذه هي الشاشة الأساسية لمسح QR -- تعرض كل رياضي وكود بطاقته وحالته
    وزر «تم» بجانب كل واحد.
    """
    session = await training_service.get_session(db, session_id)
    if session.coach_id != staff.id:
        from app.core.exceptions import PermissionDeniedError

        raise PermissionDeniedError("هذه الحصة ليست من حصصك")

    athletes = (
        await training_service.get_group_athletes(db, session.group_id)
        if session.group_id
        else []
    )
    records = await training_service.get_session_attendance(db, session_id)
    status_by_athlete = {r[0].athlete_id: r[0] for r in records}

    return {
        "session": {
            "id": session.id,
            "group_id": session.group_id,
            "group_name": session.group.name if session.group else None,
            "session_date": session.session_date,
            "time_label": session.time_label,
            "location": session.location,
            "status": session.status,
            "expected_count": session.expected_count,
            "present_count": session.present_count,
            "absent_count": session.absent_count,
        },
        "roster": [
            {
                "athlete_id": a.id,
                "full_name": a.full_name,
                "card_code": a.card_code,
                "photo_url": a.photo_url,
                "status": (
                    status_by_athlete[a.id].status
                    if a.id in status_by_athlete
                    else None
                ),
                "recorded_at": (
                    status_by_athlete[a.id].recorded_at
                    if a.id in status_by_athlete
                    else None
                ),
            }
            for a in athletes
        ],
    }


@router.post(
    "/sessions/{session_id}/scan",
    response_model=MessageResponse,
    summary="مسح بطاقة QR",
)
async def scan_card(
    session_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """
    تسجيل حضور بمسح رمز QR.

    يقبل ``{"card_code": "ABC-1234"}`` أو محتوى QR الكامل.
    """
    card_code = (payload.get("card_code") or "").strip()
    if not card_code:
        from app.core.exceptions import ValidationError

        raise ValidationError("كود البطاقة مطلوب")

    record = await training_service.record_attendance_by_card(
        db, session_id=session_id, card_code=card_code,
        recorded_by_id=staff.user_id,
    )
    athlete = await db.get(Athlete, record.athlete_id)
    await db.commit()

    return MessageResponse(
        message=f"✓ {athlete.full_name if athlete else 'تم'}",
        data={
            "athlete_id": record.athlete_id,
            "athlete_name": athlete.full_name if athlete else None,
            "photo_url": athlete.photo_url if athlete else None,
            "status": record.status,
        },
    )


@router.post("/sessions/{session_id}/manual", response_model=MessageResponse, summary="تسجيل يدوي")
async def record_manual(
    session_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """تسجيل حضور رياضي يدوياً (مثال: استثناء أو تأخير)."""
    athlete_id = int(payload.get("athlete_id", 0))
    if not athlete_id:
        from app.core.exceptions import ValidationError

        raise ValidationError("معرّف الرياضي مطلوب")

    record = await training_service.record_attendance(
        db,
        session_id=session_id,
        athlete_id=athlete_id,
        status=payload.get("status", "present"),
        method="manual",
        notes=payload.get("notes"),
        recorded_by_id=staff.user_id,
    )
    await db.commit()
    return MessageResponse(message="تم التسجيل", data={"attendance_id": record.id})


@router.post(
    "/sessions/{session_id}/finish",
    response_model=MessageResponse,
    summary="إنهاء الحصة",
)
async def finish_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """
    إنهاء الحصة: تحديد المتبقين كغياب + إشعار أولياء + إكمال الحصة.

    هذه هي نقطة الإغلاق، وتُنفّذ بخطوة واحدة بدل أربع خطوات يدوية.
    """
    session = await training_service.get_session(db, session_id)
    if session.coach_id != staff.id:
        from app.core.exceptions import PermissionDeniedError

        raise PermissionDeniedError("هذه الحصة ليست من حصصك")

    marked = await training_service.mark_absent_for_remaining(
        db, session_id=session_id, recorded_by_id=staff.user_id
    )

    records = await training_service.get_session_attendance(db, session_id)
    notified = await notification_service.notify_absence_bulk(
        db, [r[0] for r in records]
    )

    session.status = SessionStatus.COMPLETED
    await db.commit()

    return MessageResponse(
        message="تم إنهاء الحصة وتسجيل الغياب",
        data={"marked_absent": marked, "parents_notified": notified},
    )


# =====================================================================
#  التقييمات
# =====================================================================
@router.post(
    "/evaluations",
    response_model=EvaluationResponse,
    status_code=201,
    summary="تقييم رياضي",
)
async def create_evaluation(
    payload: EvaluationCreateRequest,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """إضافة تقييم فني لرياضي في أفوازي."""
    data = payload.model_dump()
    data["coach_id"] = staff.id

    evaluation = await training_service.create_evaluation(db, data)

    # إشعار الولي بالتقييم الجديد
    await notification_service.notify_evaluation(db, evaluation)

    await db.commit()
    await db.refresh(evaluation)
    return EvaluationResponse.model_validate(evaluation)


@router.get("/evaluations", summary="تقييماتي")
async def my_evaluations(
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
    athlete_id: int | None = Query(None),
):
    """التقييمات التي كتبها المدرب."""
    evaluations = await training_service.list_evaluations(
        db, athlete_id=athlete_id, coach_id=staff.id
    )
    return [EvaluationResponse.model_validate(e) for e in evaluations]


@router.get("/athletes/{athlete_id}/progress", summary="تطور رياضي")
async def athlete_progress(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """منحنى تطور رياضي عبر تقييماتي."""
    return await training_service.get_athlete_progress(db, athlete_id)


@router.get("/athletes/{athlete_id}/qr", summary="QR البطاقة")
async def athlete_qr(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """رمز QR لرياضي (لعرضه عند الحاجة)."""
    from fastapi.responses import StreamingResponse
    import io

    png = await card_service.get_athlete_qr(db, athlete_id)
    return StreamingResponse(
        io.BytesIO(png), media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="qr-{athlete_id}.png"'},
    )


# =====================================================================
#  إحصائيات المدرب
# =====================================================================
@router.get("/stats", summary="إحصائياتي")
async def my_stats(
    db: AsyncSession = Depends(get_db),
    staff: StaffMember = Depends(require_coach),
):
    """ملخص سريع: عدد الأفواج والرياضيين والحصص والغيابات هذا الأسبوع."""
    groups = await _my_groups(db, staff)
    group_ids = [g.id for g in groups]

    if not group_ids:
        return {
            "groups": 0, "athletes": 0, "sessions_this_week": 0,
            "absences_this_week": 0,
        }

    from sqlalchemy import func

    from app.models import GroupAthlete

    athlete_count = await db.execute(
        select(func.count(Athlete.id)).where(
            Athlete.id.in_(
                select(GroupAthlete.athlete_id).where(
                    GroupAthlete.group_id.in_(group_ids),
                    GroupAthlete.is_active.is_(True),
                )
            )
        )
    )

    week_start = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    session_count = await db.execute(
        select(func.count(TrainingSession.id)).where(
            TrainingSession.coach_id == staff.id,
            TrainingSession.session_date >= week_start,
        )
    )
    absence_count = await db.execute(
        select(func.count(Attendance.id))
        .join(TrainingSession, TrainingSession.id == Attendance.session_id)
        .where(
            TrainingSession.coach_id == staff.id,
            Attendance.session_date >= week_start,
            Attendance.status == "absent",
        )
    )

    return {
        "groups": len(groups),
        "athletes": athlete_count.scalar() or 0,
        "sessions_this_week": session_count.scalar() or 0,
        "absences_this_week": absence_count.scalar() or 0,
    }