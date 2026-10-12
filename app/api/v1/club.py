"""مسارات إدارة النادي (/api/v1/club).

تغطي: الرياضيين، الأفواج، الحصص، الحضور، التقييمات، المالية، الطاقم،
الإعلانات، وطلبات التسجيل. كل المسارات محمية بنظام الصلاحيات.
"""

from __future__ import annotations

import datetime as dt

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_club,
    get_current_staff,
    get_current_user,
    require_permission,
)
from app.core.config import settings
from app.core.db import get_db
from app.core.exceptions import (
    FileTooLargeError,
    UnsupportedFileTypeError,
    ValidationError,
)
from app.core.rbac import Action, Resource
from app.models import User, UserRole
from app.schemas import (
    AthleteCreateRequest,
    AthleteResponse,
    AthleteSearchQuery,
    AthleteUpdateRequest,
    AttendanceBulkRequest,
    AttendanceRecordRequest,
    AttendanceScanRequest,
    EvaluationCreateRequest,
    EvaluationResponse,
    ExpenseCreateRequest,
    FeePlanCreateRequest,
    FeePlanResponse,
    FeePlanUpdateRequest,
    GroupCreateRequest,
    GroupResponse,
    GroupUpdateRequest,
    InvoiceCreateRequest,
    InvoiceResponse,
    MessageResponse,
    NewsCreateRequest,
    NewsResponse,
    PaginatedResponse,
    PaymentCreateRequest,
    RegistrationDecisionRequest,
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
    UserCreateRequest,
    UserResponse,
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

router = APIRouter(prefix="/club", tags=["إدارة النادي"])


# =====================================================================
#  الرياضيين
# =====================================================================
@router.get(
    "/athletes",
    response_model=PaginatedResponse[AthleteResponse],
    summary="قائمة الرياضيين",
)
async def list_athletes(
    params: AthleteSearchQuery = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.ATHLETES}.{Action.READ}")),
):
    """بحث وتصفية الرياضيين ضمن النادي الحالي."""
    athletes, total = await people_service.search_athletes(
        db,
        search=params.search,
        status=params.status,
        group_id=params.group_id,
        sport_id=params.sport_id,
        level=params.level,
        has_debt=params.has_debt,
        page=params.page,
        per_page=params.per_page,
    )
    return PaginatedResponse.create(
        [AthleteResponse.model_validate(a) for a in athletes],
        total, params.page, params.per_page,
    )


@router.post(
    "/athletes",
    response_model=AthleteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إضافة رياضي",
)
async def create_athlete(
    payload: AthleteCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATHLETES}.{Action.CREATE}")),
):
    """إنشاء ملف رياضي جديد (مع ربطه بالأفواج وأولياء الأمر)."""
    data = payload.model_dump()
    data["club_id"] = user.club_id
    athlete = await people_service.create_athlete(db, data)

    await auth_service.log_audit(
        db, user=user, action="create", entity_type="Athlete",
        entity_id=athlete.id, description=f"إضافة رياضي: {athlete.full_name}",
    )
    await db.commit()
    await db.refresh(athlete)
    return AthleteResponse.model_validate(athlete)


@router.get("/athletes/{athlete_id}", summary="ملف رياضي كامل")
async def get_athlete(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.ATHLETES}.{Action.READ}")),
):
    """
    الملف الكامل: البيانات، الأفواج، الولي، الحضور، التقييمات، والمالية.

    خدمة ``get_athlete_profile`` تُرجع كائنات ORM لأنها تُستخدم أيضاً في
    القوالب (حيث العمل بالكائنات طبيعي). هنا نحوّلها إلى بنى JSON صريحة
    حتى يكون عقد الـ API مستقراً ولا يتسرّب نموذج قاعدة بيانات إلى
    الاستجابة مع كل تغيّر في الشيفرة.
    """
    profile = await people_service.get_athlete_profile(db, athlete_id)
    recent = await training_service.get_athlete_attendance_history(
        db, athlete_id, limit=10
    )

    return {
        "athlete": AthleteResponse.model_validate(profile["athlete"]).model_dump(),
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "color": g.color,
                "schedule_days": g.schedule_days,
                "time_label": g.time_label,
                "location": g.location,
            }
            for g in profile["groups"]
        ],
        "parents": [
            {
                "user_id": parent.id,
                "full_name": parent.full_name,
                "email": parent.email,
                "phone": parent.phone,
                "relationship": link.relationship_type,
                "is_primary": link.is_primary,
                "is_payer": link.is_payer,
                "invite_accepted": link.invite_accepted,
            }
            for link, parent in profile["parents"]
        ],
        "attendance_summary": {
            key: value for key, value in profile["attendance_summary"].items()
        },
        "attendance_rate": profile["attendance_rate"],
        "attendance_period_days": profile["attendance_period_days"],
        "evaluations": [
            EvaluationResponse.model_validate(e).model_dump()
            for e in profile["evaluations"]
        ],
        "unpaid_invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "period_start": str(inv.period_start),
                "period_end": str(inv.period_end),
                "due_date": str(inv.due_date),
                "total": float(inv.total),
                "paid": float(inv.paid),
                "amount_due": float(inv.amount_due),
                "status": inv.status,
                "status_label": inv.status_label,
                "days_overdue": inv.days_overdue,
            }
            for inv in profile["unpaid_invoices"]
        ],
        "debt": float(profile["debt"]),
        "recent_attendance": [
            {
                "id": r.id,
                "session_id": r.session_id,
                "session_date": str(r.session_date),
                "status": r.status,
                "status_label": r.status_label,
                "minutes_late": r.minutes_late,
                "notes": r.notes,
            }
            for r in recent
        ],
    }


@router.put("/athletes/{athlete_id}", response_model=AthleteResponse, summary="تعديل رياضي")
async def update_athlete(
    athlete_id: int,
    payload: AthleteUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATHLETES}.{Action.UPDATE}")),
):
    """تحديث بيانات رياضي."""
    athlete = await people_service.update_athlete(
        db, athlete_id, payload.model_dump(exclude_unset=True)
    )
    await auth_service.log_audit(
        db, user=user, action="update", entity_type="Athlete",
        entity_id=athlete_id, description=f"تعديل رياضي: {athlete.full_name}",
    )
    await db.commit()
    await db.refresh(athlete)
    return AthleteResponse.model_validate(athlete)


@router.delete("/athletes/{athlete_id}", response_model=MessageResponse, summary="حذف رياضي")
async def delete_athlete(
    athlete_id: int,
    permanent: bool = Query(False, description="حذف نهائي بدل التعطيل"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATHLETES}.{Action.DELETE}")),
):
    """تعطيل أو حذف رياضي (مع فحص الديون المستحقة)."""
    await people_service.delete_athlete(db, athlete_id, soft=not permanent)
    await auth_service.log_audit(
        db, user=user, action="delete", entity_type="Athlete",
        entity_id=athlete_id, description="حذف رياضي",
    )
    await db.commit()
    return MessageResponse(message="تم حذف الرياضي بنجاح")


@router.post(
    "/athletes/{athlete_id}/groups/{group_id}",
    response_model=MessageResponse,
    summary="ربط رياضي بفوج",
)
async def add_athlete_to_group(
    athlete_id: int,
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.UPDATE}")),
):
    athlete = await people_service.get_athlete(db, athlete_id)
    await people_service._attach_to_groups(db, athlete, [group_id])
    await db.commit()
    return MessageResponse(message="تم ربط الرياضي بالفوج بنجاح")


@router.delete(
    "/athletes/{athlete_id}/groups/{group_id}",
    response_model=MessageResponse,
    summary="إخراج رياضي من فوج",
)
async def remove_athlete_from_group(
    athlete_id: int,
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.UPDATE}")),
):
    await people_service.remove_athlete_from_group(db, athlete_id, group_id)
    await db.commit()
    return MessageResponse(message="تم إخراج الرياضي من الفوج")


@router.get("/athletes/{athlete_id}/attendance", summary="سجل حضور رياضي")
async def athlete_attendance(
    athlete_id: int,
    days: int = Query(90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.ATTENDANCE}.{Action.READ}")),
):
    """سجل الحضور التفصيلي لرياضي."""
    since = dt.date.today() - dt.timedelta(days=days)
    records = await training_service.get_athlete_attendance_history(
        db, athlete_id, start_date=since
    )
    rate = await training_service.compute_attendance_rate(db, athlete_id, days)
    return {
        "athlete_id": athlete_id,
        "period_days": days,
        "attendance_rate": rate,
        "records": [
            {
                "id": r.id,
                "session_id": r.session_id,
                "session_date": r.session_date,
                "status": r.status,
                "status_label": r.status_label,
                "method": r.method,
                "minutes_late": r.minutes_late,
                "notes": r.notes,
            }
            for r in records
        ],
    }


@router.get("/athletes/{athlete_id}/evaluations", summary="تقييمات رياضي")
async def athlete_evaluations(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.EVALUATIONS}.{Action.READ}")),
):
    """تطور التقييم الفني لرياضي."""
    return await training_service.get_athlete_progress(db, athlete_id)


# =====================================================================
#  الأفواج
# =====================================================================
@router.get("/groups", response_model=list[GroupResponse], summary="قائمة الأفواج")
async def list_groups(
    db: AsyncSession = Depends(get_db),
    search: str | None = Query(None),
    coach_id: int | None = Query(None),
    _: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.READ}")),
):
    """كل أفواج النادي."""
    groups = await training_service.list_groups(db, search=search, coach_id=coach_id)
    return [_group_response(g) for g in groups]


@router.post(
    "/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إنشاء فوج",
)
async def create_group(
    payload: GroupCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.CREATE}")),
):
    data = payload.model_dump()
    data["club_id"] = user.club_id
    group = await training_service.create_group(db, data)
    await db.commit()
    await db.refresh(group)
    return _group_response(group)


@router.get("/groups/{group_id}", summary="تفاصيل فوج")
async def get_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.READ}")),
):
    group = await training_service.get_group(db, group_id)
    athletes = await training_service.get_group_athletes(db, group_id)
    return {
        **_group_response(group),
        "athletes": [AthleteResponse.model_validate(a) for a in athletes],
    }


@router.put("/groups/{group_id}", response_model=GroupResponse, summary="تعديل فوج")
async def update_group(
    group_id: int,
    payload: GroupUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.UPDATE}")),
):
    group = await training_service.update_group(
        db, group_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return _group_response(group)


@router.delete("/groups/{group_id}", response_model=MessageResponse, summary="حذف فوج")
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.GROUPS}.{Action.DELETE}")),
):
    await training_service.delete_group(db, group_id)
    await db.commit()
    return MessageResponse(message="تم حذف الفوج بنجاح")


def _group_response(group) -> dict:
    """تحويل فوج لاستجابة JSON (مع اسم المدرب)."""
    return {
        "id": group.id,
        "name": group.name,
        "code": group.code,
        "sport_id": group.sport_id,
        "level": group.level,
        "coach_id": group.coach_id,
        "coach_name": group.coach.name if group.coach else None,
        "capacity": group.capacity,
        "current_count": group.current_count,
        "available_slots": group.available_slots,
        "color": group.color,
        "location": group.location,
        "schedule_days": group.schedule_days,
        "time_label": group.time_label,
        "monthly_fee": group.monthly_fee,
        "status": group.status,
        "is_active": group.is_active,
    }


# =====================================================================
#  الحصص
# =====================================================================
@router.get("/sessions", response_model=list[SessionResponse], summary="قائمة الحصص")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    group_id: int | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    _: User = Depends(require_permission(f"{Resource.SESSIONS}.{Action.READ}")),
):
    """الحصص في فترة محددة."""
    sessions = await training_service.list_sessions(
        db, start_date=start_date, end_date=end_date,
        group_id=group_id, status=status_filter,
    )
    return [_session_response(s) for s in sessions]


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="برمجة حصة",
)
async def create_session(
    payload: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.SESSIONS}.{Action.CREATE}")),
):
    data = payload.model_dump()
    data["club_id"] = user.club_id
    session = await training_service.create_session(db, data)
    await db.commit()
    # نحدّد العلاقات المطلوبة صراحةً: ``refresh`` بلا وسائط لا يعيد تحميل
    # العلاقات، والوصول إليها بعد ذلك محاولة تحميل كسول داخل سياق
    # async -- وهي ترمي ``MissingGreenlet``.
    await db.refresh(session, ["group", "coach"])
    return _session_response(session)


@router.get("/sessions/{session_id}", summary="تفاصيل حصة")
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.SESSIONS}.{Action.READ}")),
):
    session = await training_service.get_session(db, session_id)
    records = await training_service.get_session_attendance(db, session_id)
    return {
        **_session_response(session),
        "attendance": [
            {
                "id": r[0].id,
                "athlete_id": r[0].athlete_id,
                "athlete_name": r[1].full_name,
                "card_code": r[1].card_code,
                "photo_url": r[1].photo_url,
                "status": r[0].status,
                "status_label": r[0].status_label,
                "method": r[0].method,
                "minutes_late": r[0].minutes_late,
                "notes": r[0].notes,
            }
            for r in records
        ],
    }


@router.put("/sessions/{session_id}", response_model=SessionResponse, summary="تعديل حصة")
async def update_session(
    session_id: int,
    payload: SessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.SESSIONS}.{Action.UPDATE}")),
):
    session = await training_service.update_session(
        db, session_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return _session_response(session)


@router.delete("/sessions/{session_id}", response_model=MessageResponse, summary="إلغاء حصة")
async def cancel_session(
    session_id: int,
    reason: str = Query(..., description="سبب الإلغاء"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.SESSIONS}.{Action.DELETE}")),
):
    await training_service.cancel_session(db, session_id, reason)
    await db.commit()
    return MessageResponse(message="تم إلغاء الحصة")


def _session_response(session) -> dict:
    return {
        "id": session.id,
        "group_id": session.group_id,
        "group_name": session.group.name if session.group else None,
        "coach_id": session.coach_id,
        "title": session.title,
        "session_date": session.session_date,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "time_label": session.time_label,
        "location": session.location,
        "status": session.status,
        "expected_count": session.expected_count,
        "present_count": session.present_count,
        "absent_count": session.absent_count,
        "attendance_rate": session.attendance_rate,
    }


# =====================================================================
#  الحضور
# =====================================================================
@router.post(
    "/sessions/{session_id}/attendance",
    response_model=MessageResponse,
    summary="تسجيل حضور",
)
async def record_attendance(
    session_id: int,
    payload: AttendanceRecordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATTENDANCE}.{Action.CREATE}")),
):
    """تسجيل حضور رياضي يدوياً."""
    record = await training_service.record_attendance(
        db,
        session_id=session_id,
        athlete_id=payload.athlete_id,
        status=payload.status,
        method="manual",
        notes=payload.notes,
        minutes_late=payload.minutes_late,
        recorded_by_id=user.id,
    )
    await db.commit()
    return MessageResponse(
        message="تم تسجيل الحضور بنجاح",
        data={"attendance_id": record.id, "status": record.status},
    )


@router.post(
    "/sessions/{session_id}/attendance/scan",
    response_model=MessageResponse,
    summary="تسجيل حضور بمسح QR",
)
async def scan_attendance(
    session_id: int,
    payload: AttendanceScanRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATTENDANCE}.{Action.CREATE}")),
):
    """تسجيل الحضور بمسح رمز QR للبطاقة أو إدخال الكود يدوياً."""
    record = await training_service.record_attendance_by_card(
        db,
        session_id=session_id,
        card_code=payload.card_code,
        status=payload.status,
        recorded_by_id=user.id,
    )
    athlete = await people_service.get_athlete(db, record.athlete_id)
    await db.commit()

    return MessageResponse(
        message=f"تم تسجيل حضور {athlete.full_name}",
        data={
            "attendance_id": record.id,
            "athlete_id": athlete.id,
            "athlete_name": athlete.full_name,
            "status": record.status,
        },
    )


@router.post(
    "/sessions/{session_id}/attendance/bulk",
    response_model=MessageResponse,
    summary="تسجيل حضور دفعة واحدة",
)
async def bulk_attendance(
    session_id: int,
    payload: AttendanceBulkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATTENDANCE}.{Action.CREATE}")),
):
    """تسجيل حضور قائمة كاملة (من QR أو Excel)."""
    count = await training_service.bulk_record_attendance(
        db,
        session_id=session_id,
        records=[r.model_dump() for r in payload.records],
        recorded_by_id=user.id,
    )
    await db.commit()
    return MessageResponse(message=f"تم تسجيل {count} سجل حضور", data={"count": count})


@router.post(
    "/sessions/{session_id}/attendance/mark-absent",
    response_model=MessageResponse,
    summary="تحديد المتبقين كغياب",
)
async def mark_absent_remaining(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.ATTENDANCE}.{Action.UPDATE}")),
):
    """
    تحديد الرياضيين الذين لم يُسجَّل حضورهم كـ "غائب".

    اختصار للمدرب: يسجّل الحاضرين ثم يضغط زراً واحداً.
    """
    count = await training_service.mark_absent_for_remaining(
        db, session_id=session_id, recorded_by_id=user.id
    )
    await db.commit()

    # إشعار أولياء الغائبين
    session = await training_service.get_session(db, session_id)
    records = await training_service.get_session_attendance(db, session_id)
    await notification_service.notify_absence_bulk(db, [r[0] for r in records])

    return MessageResponse(
        message=f"تم تحديد {count} رياضي كغائب وإشعار أولياءهم",
        data={"count": count},
    )


@router.get(
    "/sessions/{session_id}/roster",
    summary="كشف حضور الحصة (Excel)",
)
async def export_roster(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.REPORTS}.{Action.READ}")),
):
    """كشف حضور الحصة جاهزاً للطباعة."""
    content = await excel_service.export_attendance_roster(db, session_id)
    return StreamingResponse(
        io_bytes(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="roster-{session_id}.xlsx"'
        },
    )


def io_bytes(data: bytes):
    """تغليف bytes في كائن قابل للتدفق."""
    import io

    return io.BytesIO(data)


# =====================================================================
#  التقييمات
# =====================================================================
@router.post(
    "/evaluations",
    response_model=EvaluationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إضافة تقييم",
)
async def create_evaluation(
    payload: EvaluationCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.EVALUATIONS}.{Action.CREATE}")),
    staff=Depends(get_current_staff),
):
    """تقييم فني جديد لرياضي."""
    data = payload.model_dump()
    data["coach_id"] = staff.id
    evaluation = await training_service.create_evaluation(db, data)
    await db.commit()
    await db.refresh(evaluation)
    return EvaluationResponse.model_validate(evaluation)


@router.get("/evaluations", response_model=list[EvaluationResponse], summary="قائمة التقييمات")
async def list_evaluations(
    db: AsyncSession = Depends(get_db),
    athlete_id: int | None = Query(None),
    group_id: int | None = Query(None),
    coach_id: int | None = Query(None),
    _: User = Depends(require_permission(f"{Resource.EVALUATIONS}.{Action.READ}")),
):
    """تقييمات النادي مع فلاتر."""
    evaluations = await training_service.list_evaluations(
        db, athlete_id=athlete_id, group_id=group_id, coach_id=coach_id
    )
    return [EvaluationResponse.model_validate(e) for e in evaluations]


# =====================================================================
#  المالية
# =====================================================================
@router.get("/finance/summary", summary="الملخص المالي")
async def finance_summary(
    db: AsyncSession = Depends(get_db),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    _: User = Depends(require_permission(f"{Resource.FINANCE}.{Action.READ}")),
):
    """ملخص الإيرادات والمصاريف والديون."""
    return await finance_service.financial_summary(
        db, start_date=start_date, end_date=end_date
    )


@router.get("/finance/revenue-chart", summary="بيانات الرسم البياني")
async def revenue_chart(
    db: AsyncSession = Depends(get_db),
    months: int = Query(12, ge=3, le=24),
    _: User = Depends(require_permission(f"{Resource.FINANCE}.{Action.READ}")),
):
    """الإيرادات والمصاريف شهر بشهر."""
    return await finance_service.revenue_by_month(db, months)


@router.get("/finance/debtors", summary="تقرير المدينين")
async def debtors(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(require_permission(f"{Resource.FINANCE}.{Action.READ}")),
):
    """الرياضيون عليهم مبالغ غير مسددة، مرتّبين بالأعلى."""
    return await finance_service.debtors_report(db, limit)


# --- خطط الاشتراك ---
@router.get("/fee-plans", response_model=list[FeePlanResponse], summary="خطط الاشتراك")
async def list_fee_plans(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.FINANCE}.{Action.READ}")),
):
    plans = await finance_service.list_fee_plans(db)
    return [FeePlanResponse.model_validate(p) for p in plans]


@router.post(
    "/fee-plans",
    response_model=FeePlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إنشاء خطة اشتراك",
)
async def create_fee_plan(
    payload: FeePlanCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.FINANCE}.{Action.CREATE}")),
):
    data = payload.model_dump()
    data["club_id"] = user.club_id
    plan = await finance_service.create_fee_plan(db, data)
    await db.commit()
    await db.refresh(plan)
    return FeePlanResponse.model_validate(plan)


@router.put(
    "/fee-plans/{plan_id}",
    response_model=FeePlanResponse,
    summary="تعديل خطة اشتراك",
)
async def update_fee_plan(
    plan_id: int,
    payload: FeePlanUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.FINANCE}.{Action.UPDATE}")),
):
    plan = await finance_service.update_fee_plan(
        db, plan_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return FeePlanResponse.model_validate(plan)


# --- الفواتير ---
@router.get(
    "/finance/invoices",
    response_model=PaginatedResponse[InvoiceResponse],
    summary="قائمة الفواتير",
)
async def list_invoices(
    db: AsyncSession = Depends(get_db),
    athlete_id: int | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    overdue_only: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    _: User = Depends(require_permission(f"{Resource.INVOICES}.{Action.READ}")),
):
    invoices, total = await finance_service.list_invoices(
        db, athlete_id=athlete_id, status=status_filter,
        overdue_only=overdue_only, page=page, per_page=per_page,
    )
    return PaginatedResponse.create(
        [_invoice_response(i) for i in invoices], total, page, per_page
    )


@router.post(
    "/finance/invoices",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إصدار فاتورة",
)
async def create_invoice(
    payload: InvoiceCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.INVOICES}.{Action.CREATE}")),
):
    invoice = await finance_service.create_invoice(db, **payload.model_dump())
    await db.commit()
    await db.refresh(invoice)
    return _invoice_response(invoice)


@router.post(
    "/invoices/{invoice_id}/cancel",
    response_model=MessageResponse,
    summary="إلغاء فاتورة",
)
async def cancel_invoice(
    invoice_id: int,
    reason: str = Query(..., description="سبب الإلغاء"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.INVOICES}.{Action.DELETE}")),
):
    await finance_service.cancel_invoice(db, invoice_id, reason)
    await db.commit()
    return MessageResponse(message="تم إلغاء الفاتورة")


@router.post(
    "/invoices/{invoice_id}/subscriptions",
    response_model=list[InvoiceResponse],
    summary="إصدار كل أقساط الاشتراك",
)
async def create_subscription_invoices(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.INVOICES}.{Action.CREATE}")),
):
    """توزيع الاشتراك على عدة فواتير (أقساط)."""
    invoice = await finance_service.get_invoice(db, invoice_id)
    if not invoice.fee_plan_id:
        raise ValidationError("الفاتورة غير مرتبطة بخطة اشتراك")
    invoices = await finance_service.create_subscription_invoices(
        db, invoice.athlete_id, invoice.fee_plan_id
    )
    await db.commit()
    return [_invoice_response(i) for i in invoices]


def _invoice_response(invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "athlete_id": invoice.athlete_id,
        "athlete_name": invoice.athlete.full_name if invoice.athlete else None,
        "fee_plan_id": invoice.fee_plan_id,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "subtotal": float(invoice.subtotal),
        "discount": float(invoice.discount),
        "total": float(invoice.total),
        "paid": float(invoice.paid),
        "amount_due": float(invoice.amount_due),
        "status": invoice.status,
        "status_label": invoice.status_label,
        "is_overdue": invoice.is_overdue,
        "days_overdue": invoice.days_overdue,
    }


# --- الدفعات ---
@router.post("/finance/payments", response_model=MessageResponse, summary="تسجيل دفعة")
async def create_payment(
    payload: PaymentCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.PAYMENTS}.{Action.CREATE}")),
):
    """تسجيل دفعة وتحديث حالة الفاتورة تلقائياً."""
    try:
        payload.validate_target()
    except ValueError as exc:
        raise ValidationError(str(exc)) from None

    payment = await finance_service.record_payment(
        db,
        **payload.model_dump(),
        recorded_by_id=user.id,
    )
    await notification_service.notify_payment_received(db, payment)
    await db.commit()

    return MessageResponse(
        message=f"تم تسجيل الدفعة رقم {payment.receipt_number}",
        data={
            "payment_id": payment.id,
            "receipt_number": payment.receipt_number,
            "amount": float(payment.amount),
        },
    )


@router.get("/payments", summary="سجل الدفعات")
async def list_payments(
    db: AsyncSession = Depends(get_db),
    athlete_id: int | None = Query(None),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    _: User = Depends(require_permission(f"{Resource.PAYMENTS}.{Action.READ}")),
):
    payments, total = await finance_service.list_payments(
        db, athlete_id=athlete_id, start_date=start_date,
        end_date=end_date, page=page, per_page=per_page,
    )
    return PaginatedResponse.create(
        [
            {
                "id": p.id,
                "receipt_number": p.receipt_number,
                "invoice_id": p.invoice_id,
                "athlete_id": p.athlete_id,
                "athlete_name": p.athlete.full_name if p.athlete else None,
                "amount": float(p.amount),
                "payment_date": p.payment_date,
                "method": p.method,
                "method_label": p.method_label,
                "status": p.status,
                "reference": p.reference,
            }
            for p in payments
        ],
        total, page, per_page,
    )


@router.get("/payments/export", summary="تصدير تقرير الدفعات")
async def export_payments(
    db: AsyncSession = Depends(get_db),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    _: User = Depends(require_permission(f"{Resource.IMPORT_EXPORT}.{Action.READ}")),
):
    """تقرير دفعات مفصّل بصيغة Excel."""
    end = end_date or dt.date.today()
    start = start_date or (end - dt.timedelta(days=30))
    content = await excel_service.export_payments(db, start, end)
    return StreamingResponse(
        io_bytes(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="payments-{start}-{end}.xlsx"'
        },
    )


# --- المصاريف ---
@router.post("/expenses", response_model=MessageResponse, status_code=201, summary="تسجيل مصروف")
async def create_expense(
    payload: ExpenseCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.EXPENSES}.{Action.CREATE}")),
):
    data = payload.model_dump()
    data["club_id"] = user.club_id
    data["recorded_by_id"] = user.id
    data["approved_by_id"] = user.id
    expense = await finance_service.create_expense(db, data)
    await db.commit()
    return MessageResponse(message="تم تسجيل المصروف", data={"expense_id": expense.id})


@router.get("/expenses", summary="قائمة المصاريف")
async def list_expenses(
    db: AsyncSession = Depends(get_db),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    _: User = Depends(require_permission(f"{Resource.EXPENSES}.{Action.READ}")),
):
    expenses, total = await finance_service.list_expenses(
        db, category=category, page=page, per_page=per_page
    )
    return PaginatedResponse.create(
        [
            {
                "id": e.id,
                "title": e.title,
                "category": e.category,
                "category_label": e.category_label,
                "amount": float(e.amount),
                "expense_date": e.expense_date,
                "vendor": e.vendor,
                "approved": e.approved,
            }
            for e in expenses
        ],
        total, page, per_page,
    )


# =====================================================================
#  الطاقم
# =====================================================================
@router.post("/staff", response_model=UserResponse, status_code=201, summary="إضافة عضو طاقم")
async def create_staff(
    payload: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.STAFF}.{Action.CREATE}")),
):
    """
    إنشاء حساب عضو طاقم جديد.

    نطلب بريداً أو هاتفاً فريداً على مستوى المنصة كلها (لأن الدخول
    موحّد)، ولا يمكن استخدام نفس الرقم لناديين.
    """
    new_user = await auth_service.create_user(
        db,
        identifier=payload.email or payload.phone or "",
        password=payload.password,
        full_name=payload.full_name,
        role=UserRole(payload.role),
        club_id=user.club_id,
        phone=payload.phone,
        email=str(payload.email) if payload.email else None,
    )
    staff = await people_service.create_staff_member(
        db,
        user_id=new_user.id,
        job_title=payload.job_title or "other",
        role=UserRole(payload.role),
        salary=payload.salary,
        hire_date=payload.hire_date,
        specialty=payload.specialty,
        club_id=user.club_id,
    )
    await db.commit()
    return UserResponse.model_validate(new_user)


@router.get("/staff", summary="قائمة الطاقم")
async def list_staff(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.STAFF}.{Action.READ}")),
):
    """أعضاء طاقم النادي."""
    members = await people_service.list_staff(db)
    return [
        {
            "id": s.id,
            "user_id": s.user_id,
            "full_name": u.full_name,
            "email": u.email,
            "phone": u.phone,
            "job_title": s.job_title,
            "role": s.role,
            "specialty": s.specialty,
            "salary": float(s.salary) if s.salary else None,
            "hire_date": s.hire_date,
            "is_active": s.is_active,
            "status": s.status,
        }
        for s, u in members
    ]


@router.delete("/staff/{staff_id}", response_model=MessageResponse, summary="حذف عضو طاقم")
async def delete_staff(
    staff_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.STAFF}.{Action.DELETE}")),
):
    """حذف عضو (مع فحص الأفواج المرتبطة)."""
    await people_service.delete_staff_member(db, staff_id)
    await db.commit()
    return MessageResponse(message="تم حذف العضو من الطاقم")


# =====================================================================
#  الأخبار والإعلانات
# =====================================================================
@router.get("/news", response_model=list[NewsResponse], summary="أخبار النادي")
async def list_club_news(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.NEWS}.{Action.READ}")),
):
    items, _ = await club_service.list_news(db, published_only=False, per_page=50)
    return [NewsResponse.model_validate(n) for n in items]


@router.post("/news", response_model=NewsResponse, status_code=201, summary="نشر خبر")
async def create_news(
    payload: NewsCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.NEWS}.{Action.CREATE}")),
):
    data = payload.model_dump()
    data["author_id"] = user.id
    news = await club_service.create_news(db, data, club_id=user.club_id)
    await db.commit()
    await db.refresh(news)
    return NewsResponse.model_validate(news)


@router.delete("/news/{news_id}", response_model=MessageResponse, summary="حذف خبر")
async def delete_news(
    news_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.NEWS}.{Action.DELETE}")),
):
    await club_service.delete_news(db, news_id)
    await db.commit()
    return MessageResponse(message="تم حذف الخبر")


# =====================================================================
#  طلبات التسجيل
# =====================================================================
@router.get("/registrations", summary="طلبات التسجيل الواردة")
async def list_registrations(
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    _: User = Depends(require_permission(f"{Resource.REGISTRATIONS}.{Action.READ}")),
):
    """طلبات التسجيل التي أرسلها الزوار للنادي."""
    items, total = await club_service.list_registration_requests(
        db, status=status_filter, page=page, per_page=per_page
    )
    return PaginatedResponse.create(
        [
            {
                "id": r.id,
                "full_name": r.full_name,
                "birth_date": r.birth_date,
                "gender": r.gender,
                "parent_name": r.parent_name,
                "phone": r.phone,
                "email": r.email,
                "wilaya": r.wilaya,
                "message": r.message,
                "status": r.status,
                "status_label": r.status_label,
                "submitted_at": r.submitted_at,
                "waiting_days": r.waiting_days,
            }
            for r in items
        ],
        total, page, per_page,
    )


@router.post("/registrations/{request_id}/approve", summary="الموافقة على طلب")
async def approve_registration(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.REGISTRATIONS}.{Action.UPDATE}")),
):
    """الموافقة على الطلب وإنشاء ملف الرياضي تلقائياً."""
    result = await club_service.approve_registration(db, request_id, user.id)
    await db.commit()
    return MessageResponse(
        message=f"تمت الموافقة وإنشاء ملف {result['athlete'].full_name}",
        data={"athlete_id": result["athlete"].id},
    )


@router.post("/registrations/{request_id}/reject", response_model=MessageResponse, summary="رفض طلب")
async def reject_registration(
    request_id: int,
    payload: RegistrationDecisionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.REGISTRATIONS}.{Action.UPDATE}")),
):
    """رفض طلب تسجيل مع تسجيل السبب."""
    reason = payload.reason or "لم يذكر السبب"
    await club_service.reject_registration(db, request_id, reason, user.id)
    await db.commit()
    return MessageResponse(message="تم رفض الطلب")


# =====================================================================
#  البطاقات
# =====================================================================
@router.get("/athletes/{athlete_id}/qr", summary="رمز QR للرياضي")
async def athlete_qr(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.CARDS}.{Action.READ}")),
):
    """توليد رمز QR الخاص بالبطاقة الرياضية."""
    png = await card_service.get_athlete_qr(db, athlete_id)
    return StreamingResponse(
        io_bytes(png), media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="qr-{athlete_id}.png"'},
    )


@router.get("/athletes/{athlete_id}/card", summary="بطاقة الرياضي")
async def athlete_card(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.CARDS}.{Action.READ}")),
):
    """البطاقة الرياضية جاهزة للطباعة."""
    content = await card_service.generate_athlete_card(db, athlete_id)
    media_type = (
        "application/pdf"
        if content[:4] == b"%PDF"
        else "text/html; charset=utf-8"
    )
    headers = {"Content-Disposition": f'inline; filename="card-{athlete_id}.html"'}
    if media_type == "application/pdf":
        headers["Content-Disposition"] = f'inline; filename="card-{athlete_id}.pdf"'
    return StreamingResponse(io_bytes(content), media_type=media_type, headers=headers)


# =====================================================================
#  الاستيراد والتصدير
# =====================================================================
@router.get("/import/template", summary="تحميل قالب الاستيراد")
async def import_template(
    _: User = Depends(require_permission(f"{Resource.IMPORT_EXPORT}.{Action.READ}")),
):
    """قالب Excel جاهز لاستيراد الرياضيين."""
    content = excel_service.build_import_template()
    return StreamingResponse(
        io_bytes(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="athletes-template.xlsx"'},
    )


@router.post("/import/athletes", summary="استيراد رياضيين من Excel")
async def import_athletes(
    file: UploadFile = File(..., description="ملف .xlsx"),
    dry_run: bool = Query(False, description="محاكاة بدون حفظ"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(f"{Resource.IMPORT_EXPORT}.{Action.CREATE}")),
):
    """
    استيراد قائمة رياضيين من ملف Excel.

    استخدم ``dry_run=true`` أولاً للتحقق من صحة الملف.
    """
    if not file.filename or not file.filename.endswith((".xlsx", ".xlsm")):
        raise UnsupportedFileTypeError("الملف يجب أن يكون بصيغة Excel (.xlsx)")

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise FileTooLargeError(
            f"حجم الملف يتجاوز {settings.MAX_UPLOAD_SIZE_MB} ميغابايت"
        )

    result = await excel_service.import_athletes(db, content, dry_run=dry_run)

    if not dry_run:
        await auth_service.log_audit(
            db, user=user, action="import", entity_type="Athlete",
            description=f"استيراد {result['imported']} رياضي من Excel",
        )
        await club_service.update_club_counts(db, user.club_id)
        await db.commit()
    else:
        await db.rollback()

    return result


@router.get("/export/athletes", summary="تصدير الرياضيين")
async def export_athletes(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.IMPORT_EXPORT}.{Action.READ}")),
):
    """تصدير قائمة الرياضيين إلى Excel."""
    content = await excel_service.export_athletes(db)
    await auth_service.log_audit(
        db, user=_, action="export", entity_type="Athlete",
        description="تصدير قائمة الرياضيين",
    )
    await db.commit()
    return StreamingResponse(
        io_bytes(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="athletes.xlsx"'},
    )


# =====================================================================
#  لوحة التحكم
# =====================================================================
@router.get("/dashboard", summary="مؤشرات لوحة التحكم")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(f"{Resource.REPORTS}.{Action.READ}")),
):
    """
    كل مؤشرات لوحة تحكم النادي في استدعاء واحد.

    مصمم ليكون أول طلب في الصفحة، لتقليل زمن ظهور المحتوى.
    """
    summary = await report_service.club_dashboard(db)
    schedule = await report_service.today_schedule(db)
    absences = await report_service.recent_absences(db)
    chart = await report_service.attendance_chart(db)

    # تحويل التواريخ والنصوص إلى نصوص قابلة للتسلسل
    for item in schedule:
        item["time"] = str(item["time"])
        item["end_time"] = str(item["end_time"])

    return {
        **summary,
        "today_schedule": [
            {
                "session_id": item["session"]["id"],
                "group_name": item["group"].name if item["group"] else "-",
                "group_color": item["group"].color if item["group"] else None,
                "time": item["time"],
                "end_time": item["end_time"],
                "status": item["session"]["status"],
                "present": item["session"]["present_count"],
                "expected": item["session"]["expected_count"],
                "is_completed": item["is_completed"],
            }
            for item in schedule
        ],
        "recent_absences": absences,
        "attendance_chart": [
            {**c, "date": str(c["date"])} for c in chart
        ],
    }