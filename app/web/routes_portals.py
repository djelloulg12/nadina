"""
مسارات صفحات المدرب وولي الأمر ومدير المنصة
=============================================
صفحات مبسّطة موجّهة للهاتف، لأن المدرب وولي الأمر يستخدمان المنصة من
الجوال في الغالب.
"""

from __future__ import annotations

import datetime as dt
import io
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.exceptions import AppError
from app.models import (
    Athlete,
    Attendance,
    AttendanceStatus,
    AuditLog,
    Club,
    ClubStatus,
    Group,
    GroupAthlete,
    Invoice,
    Notification,
    Payment,
    PlatformPlan,
    StaffMember,
    TrainingSession,
    User,
    UserRole,
)
from app.services import clubs as club_service
from app.services import notifications as notification_service
from app.services import people as people_service
from app.services import reports as report_service
from app.services import training as training_service
from app.web.helpers import current_user_or_redirect, ensure_role, page_context
from app.web.templates_setup import templates

logger = logging.getLogger(__name__)


# =====================================================================
#  أدوات
# =====================================================================
def _render(request: Request, template: str, ctx: dict, code: int = 200):
    return templates.TemplateResponse(
        request=request, name=template, context=ctx, status_code=code
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


async def _guard(request: Request, db: AsyncSession, *roles: UserRole):
    """حارس موحّد (نفسه في routes.py)."""
    user = await current_user_or_redirect(db, request)
    if isinstance(user, RedirectResponse):
        return user, None, None

    error = ensure_role(user, *roles)
    if error:
        return (
            _render(
                request, "errors/403.html",
                {**await page_context(request, db), "message": error}, 403,
            ),
            None,
            None,
        )

    from app.api.deps import establish_tenant_context

    await establish_tenant_context(db, user)
    club = await db.get(Club, user.club_id) if user.club_id else None
    return None, user, club


def _fv(form, key, default=None):  # noqa: ANN001, ANN201
    value = form.get(key, default)
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return value


def _int(value, default=0):  # noqa: ANN001, ANN201
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# =====================================================================
#  مساحة المدرب
# =====================================================================
coach_router = APIRouter(prefix="/coach", include_in_schema=False)


@coach_router.get("", name="coach_dashboard")
@coach_router.get("/", name="coach_dashboard_slash")
async def coach_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """لوحة المدرب: حصص اليوم، أفواده، ومؤشرات سريعة."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    staff_row = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id,
            StaffMember.club_id == user.club_id,
        )
    )
    staff_member = staff_row.scalar_one_or_none()

    groups = await training_service.list_groups(db, coach_id=staff_member.id if staff_member else None)
    today = dt.date.today()
    sessions = await training_service.list_sessions(
        db, start_date=today, end_date=today,
        coach_id=staff_member.id if staff_member else None,
    )

    week_start = today - dt.timedelta(days=today.weekday())
    absences = await db.execute(
        select(func.count(Attendance.id))
        .join(TrainingSession, TrainingSession.id == Attendance.session_id)
        .where(
            TrainingSession.coach_id == (staff_member.id if staff_member else -1),
            Attendance.session_date >= week_start,
            Attendance.status == AttendanceStatus.ABSENT,
        )
    )

    return _render(
        request,
        "coach/dashboard.html",
        {
            **await page_context(request, db),
            "page_title": "لوحة المدرب",
            "active_nav": "dashboard",
            "club": club,
            "groups": groups,
            "today_sessions": sessions,
            "absences_this_week": absences.scalar() or 0,
        },
    )


@coach_router.get("/groups", name="coach_groups")
async def coach_groups(request: Request, db: AsyncSession = Depends(get_db)):
    """أفواج المدرب."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    staff_row = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id, StaffMember.club_id == user.club_id
        )
    )
    staff = staff_row.scalar_one_or_none()
    groups = await training_service.list_groups(db, coach_id=staff.id if staff else None)

    return _render(
        request,
        "coach/groups.html",
        {
            **await page_context(request, db),
            "page_title": "أفواجي",
            "active_nav": "groups",
            "club": club,
            "groups": groups,
        },
    )


@coach_router.get("/groups/{group_id}", name="coach_group_athletes")
async def coach_group_athletes(
    request: Request, group_id: int, db: AsyncSession = Depends(get_db)
):
    """رياضي فوج المدرب مع نسبة حضور كل واحد."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    staff_row = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id, StaffMember.club_id == user.club_id
        )
    )
    staff = staff_row.scalar_one_or_none()
    my_groups = await training_service.list_groups(db, coach_id=staff.id if staff else None)

    if group_id not in {g.id for g in my_groups}:
        return _render(
            request, "errors/403.html",
            {
                **await page_context(request, db),
                "message": "هذا الفوج ليس ضمن أفواجك",
            },
            403,
        )

    group = await training_service.get_group(db, group_id)
    athletes = await training_service.get_group_athletes(db, group_id)

    enriched = []
    for athlete in athletes:
        enriched.append({
            "athlete": athlete,
            "rate": await training_service.compute_attendance_rate(db, athlete.id, 30),
        })

    return _render(
        request,
        "coach/group_athletes.html",
        {
            **await page_context(request, db),
            "page_title": group.name,
            "active_nav": "groups",
            "club": club,
            "group": group,
            "athletes": enriched,
        },
    )


@coach_router.get("/sessions/{session_id}", name="coach_attendance_sheet")
async def coach_attendance_sheet(
    request: Request, session_id: int, db: AsyncSession = Depends(get_db)
):
    """
    ورقة الحضور: الشاشة الأساسية لمسح QR.

    تعرض كل رياضي بكود بطاقته وحالته، مع زر «تم» لكل واحد.
    """
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    try:
        session = await training_service.get_session(db, session_id)
    except AppError as exc:
        return _render(
            request, "errors/404.html",
            {**await page_context(request, db), "message": exc.message},
            exc.status_code,
        )

    staff_row = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id, StaffMember.club_id == user.club_id
        )
    )
    staff = staff_row.scalar_one_or_none()
    if staff is None or session.coach_id != staff.id:
        return _render(
            request, "errors/403.html",
            {**await page_context(request, db), "message": "هذه الحصة ليست من حصصك"},
            403,
        )

    athletes = (
        await training_service.get_group_athletes(db, session.group_id)
        if session.group_id else []
    )
    records = await training_service.get_session_attendance(db, session_id)
    status_map = {r[0].athlete_id: r[0] for r in records}

    return _render(
        request,
        "coach/attendance_sheet.html",
        {
            **await page_context(request, db),
            "page_title": "تسجيل الحضور",
            "active_nav": "sessions",
            "club": club,
            "session": session,
            "roster": [
                {
                    "athlete": a,
                    "record": status_map.get(a.id),
                    "status": status_map[a.id].status if a.id in status_map else None,
                }
                for a in athletes
            ],
            "recorded_count": len(records),
        },
    )


@coach_router.post("/sessions/{session_id}/record", name="coach_attendance_record")
async def coach_record_attendance(
    request: Request, session_id: int, db: AsyncSession = Depends(get_db)
):
    """تسجيل حضور من ورقة الحضور."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    form = await request.form()
    athlete_id = _int(_fv(form, "athlete_id", 0))
    status = _fv(form, "status", "present")

    if athlete_id:
        try:
            await training_service.record_attendance(
                db,
                session_id=session_id,
                athlete_id=athlete_id,
                status=status,
                method="manual",
                recorded_by_id=user.id,
            )
            await db.commit()
        except AppError as exc:
            logger.warning("فشل تسجيل الحضور: %s", exc.message)
            await db.rollback()

    return _redirect(f"/coach/sessions/{session_id}")


@coach_router.post("/sessions/{session_id}/finish", name="coach_session_finish")
async def coach_finish_session(
    request: Request, session_id: int, db: AsyncSession = Depends(get_db)
):
    """إنهاء الحصة: تحديد الغائبين + إشعار الأولياء."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    try:
        await training_service.mark_absent_for_remaining(
            db, session_id=session_id, recorded_by_id=user.id
        )
        records = await training_service.get_session_attendance(db, session_id)
        await notification_service.notify_absence_bulk(db, [r[0] for r in records])

        session = await training_service.get_session(db, session_id)
        session.status = "completed"
        await db.commit()
    except AppError as exc:
        logger.warning("فشل إنهاء الحصة: %s", exc.message)
        await db.rollback()

    return _redirect("/coach")


@coach_router.get("/evaluations", name="coach_evaluations")
async def coach_evaluations_page(request: Request, db: AsyncSession = Depends(get_db)):
    """قائمة التقييمات التي كتبها المدرب."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    staff_row = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id,
            StaffMember.club_id == user.club_id,
        )
    )
    staff = staff_row.scalar_one_or_none()

    evaluations = (
        await training_service.list_evaluations(db, coach_id=staff.id if staff else None)
        if staff else []
    )

    my_groups = await training_service.list_groups(db, coach_id=staff.id if staff else None)
    athlete_list: list = []
    for group in my_groups:
        athlete_list.extend(await training_service.get_group_athletes(db, group.id))

    return _render(
        request,
        "coach/evaluations.html",
        {
            **await page_context(request, db),
            "page_title": "التقييمات",
            "active_nav": "evaluations",
            "club": club,
            "evaluations": evaluations,
            "athletes": athlete_list,
        },
    )


@coach_router.post("/evaluations", name="coach_evaluation_create")
async def coach_evaluation_create(request: Request, db: AsyncSession = Depends(get_db)):
    """إضافة تقييم لرياضي."""
    err, user, club = await _guard(request, db, UserRole.COACH)
    if err:
        return err

    form = await request.form()
    athlete_id = _int(_fv(form, "athlete_id", 0))

    staff_row = await db.execute(
        select(StaffMember).where(
            StaffMember.user_id == user.id, StaffMember.club_id == user.club_id
        )
    )
    staff = staff_row.scalar_one_or_none()

    def _score(key):
        raw = _fv(form, key)
        return int(raw) if raw and str(raw).isdigit() else None

    payload = {
        "athlete_id": athlete_id,
        "physical_score": _score("physical_score"),
        "technique_score": _score("technique_score"),
        "discipline_score": _score("discipline_score"),
        "teamwork_score": _score("teamwork_score"),
        "strengths": _fv(form, "strengths"),
        "weaknesses": _fv(form, "weaknesses"),
        "notes": _fv(form, "notes"),
        "recommendations": _fv(form, "recommendations"),
        "coach_id": staff.id if staff else None,
    }

    try:
        evaluation = await training_service.create_evaluation(db, payload)
        await notification_service.notify_evaluation(db, evaluation)
        await db.commit()
    except AppError as exc:
        logger.warning("فشل حفظ التقييم: %s", exc.message)
        await db.rollback()

    return _redirect("/coach/evaluations")


# =====================================================================
#  بوابة ولي الأمر
# =====================================================================
parent_router = APIRouter(prefix="/parent", include_in_schema=False)


@parent_router.get("", name="parent_dashboard")
@parent_router.get("/", name="parent_dashboard_slash")
async def parent_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """لوحة الولي الموحدة: كل الأبناء عبر الأندية."""
    err, user, club = await _guard(request, db, UserRole.PARENT, UserRole.ATHLETE)
    if err:
        return err

    data = await report_service.parent_dashboard(db, user.id)

    for child in data.get("children", []):
        athlete = child["athlete"]
        athlete_club = await db.get(Club, athlete.club_id)
        child["club_name"] = athlete_club.name if athlete_club else "-"
        child["club_slug"] = athlete_club.slug if athlete_club else None
        child["club_color"] = athlete_club.primary_color if athlete_club else None
        child["photo_url"] = athlete.photo_url

    next_payment = data.get("summary", {}).get("next_payment")
    if next_payment:
        next_payment["due_date"] = str(next_payment["due_date"])

    return _render(
        request,
        "parent/dashboard.html",
        {
            **await page_context(request, db),
            "page_title": "أبنائي",
            "active_nav": "dashboard",
            "children": data.get("children", []),
            "summary": data.get("summary", {}),
            "next_payment": next_payment,
        },
    )


@parent_router.get("/children/{athlete_id}", name="parent_child_detail")
async def parent_child_detail(
    request: Request, athlete_id: int, db: AsyncSession = Depends(get_db)
):
    """ملف الابن: الحضور والتقييمات والمالية."""
    err, user, club = await _guard(request, db, UserRole.PARENT, UserRole.ATHLETE)
    if err:
        return err

    # تحقق: الابن من أبناء هذا الولي
    child_ids = [c.id for c in await people_service.get_parent_children(db, user.id)]
    if athlete_id not in child_ids:
        return _render(
            request, "errors/404.html",
            {**await page_context(request, db), "message": "هذا الرياضي ليس من أبنائك"},
            404,
        )

    profile = await people_service.get_athlete_profile(db, athlete_id)
    progress = await training_service.get_athlete_progress(db, athlete_id)
    groups = await people_service.get_athlete_groups(db, athlete_id)

    return _render(
        request,
        "parent/child_detail.html",
        {
            **await page_context(request, db),
            "page_title": profile["athlete"].full_name,
            "active_nav": "dashboard",
            # القالب يستخدم ``profile.x`` في معظم الأقسام
            "profile": profile,
            **profile,
            "progress": progress,
            "groups": groups,
        },
    )


@parent_router.get("/finance", name="parent_finance")
async def parent_finance(request: Request, db: AsyncSession = Depends(get_db)):
    """الوضع المالي للفواتير والدفعات."""
    err, user, club = await _guard(request, db, UserRole.PARENT, UserRole.ATHLETE)
    if err:
        return err

    children = await people_service.get_parent_children(db, user.id)
    child_ids = [c.id for c in children]
    names = {c.id: c.full_name for c in children}

    invoices = []
    payments = []

    if child_ids:
        invoice_rows = await db.execute(
            select(Invoice)
            .where(
                Invoice.athlete_id.in_(child_ids),
                Invoice.is_active.is_(True),
            )
            .order_by(Invoice.due_date.desc())
            .limit(50)
        )
        invoices = list(invoice_rows.scalars().all())

        payment_rows = await db.execute(
            select(Payment)
            .where(Payment.athlete_id.in_(child_ids))
            .order_by(Payment.payment_date.desc())
            .limit(30)
        )
        payments = list(payment_rows.scalars().all())

    return _render(
        request,
        "parent/finance.html",
        {
            **await page_context(request, db),
            "page_title": "الوضع المالي",
            "active_nav": "finance",
            "children": children,
            "child_names": names,
            "invoices": invoices,
            "payments": payments,
        },
    )


# =====================================================================
#  لوحة مدير المنصة
# =====================================================================
admin_router = APIRouter(prefix="/admin", include_in_schema=False)


@admin_router.get("", name="admin_dashboard")
@admin_router.get("/", name="admin_dashboard_slash")
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """إحصائيات المنصة الكاملة."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    stats = await report_service.platform_dashboard(db)

    return _render(
        request,
        "admin/dashboard.html",
        {
            **await page_context(request, db),
            "page_title": "لوحة المنصة",
            "active_nav": "dashboard",
            "stats": stats,
        },
    )


@admin_router.get("/clubs", name="admin_clubs")
async def admin_clubs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = Query(""),
    search: str = Query(""),
    page: int = Query(1, ge=1),
):
    """كل الأندية."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    items, total = await club_service.search_clubs(
        db,
        search=search or None,
        status=ClubStatus(status) if status else None,
        public_only=False,
        page=page,
        per_page=25,
    )

    return _render(
        request,
        "admin/clubs.html",
        {
            **await page_context(request, db),
            "page_title": "إدارة الأندية",
            "active_nav": "clubs",
            "clubs": items,
            "total": total,
            "page": page,
            "per_page": 25,
            "filters": {"status": status, "search": search},
        },
    )


@admin_router.post("/clubs/{club_id}/activate", name="admin_club_activate")
async def admin_club_activate(
    request: Request, club_id: int, db: AsyncSession = Depends(get_db)
):
    """تفعيل نادي."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    try:
        await club_service.activate_club(db, club_id)
        await db.commit()
    except AppError as exc:
        logger.warning("فشل التفعيل: %s", exc.message)
        await db.rollback()

    return _redirect("/admin/clubs")


@admin_router.post("/clubs/{club_id}/suspend", name="admin_club_suspend")
async def admin_club_suspend(
    request: Request, club_id: int, db: AsyncSession = Depends(get_db)
):
    """إيقاف نادي."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    form = await request.form()
    try:
        await club_service.suspend_club(db, club_id, _fv(form, "reason", "إجراء إداري"))
        await db.commit()
    except AppError as exc:
        logger.warning("فشل الإيقاف: %s", exc.message)
        await db.rollback()

    return _redirect("/admin/clubs")


@admin_router.post("/clubs/{club_id}/renew", name="admin_club_renew")
async def admin_club_renew(
    request: Request, club_id: int, db: AsyncSession = Depends(get_db)
):
    """تجديد اشتراك نادي في المنصة."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    form = await request.form()
    plan_id = _int(_fv(form, "plan_id", 0)) or None
    months = _int(_fv(form, "months", 12), 12)

    try:
        await club_service.renew_club_subscription(
            db, club_id, plan_id=plan_id, months=months
        )
        await db.commit()
    except AppError as exc:
        logger.warning("فشل التجديد: %s", exc.message)
        await db.rollback()

    return _redirect("/admin/clubs")


@admin_router.get("/plans", name="admin_plans")
async def admin_plans(request: Request, db: AsyncSession = Depends(get_db)):
    """خطط اشتراك الأندية."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    plans = list(
        (
            await db.execute(
                select(PlatformPlan).where(PlatformPlan.is_active.is_(True))
            )
        ).scalars().all()
    )

    return _render(
        request,
        "admin/plans.html",
        {
            **await page_context(request, db),
            "page_title": "خطط الاشتراك",
            "active_nav": "plans",
            "plans": plans,
        },
    )


@admin_router.get("/audit", name="admin_audit")
async def admin_audit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    action: str = Query(""),
    page: int = Query(1, ge=1),
):
    """سجل التدقيق."""
    err, user, club = await _guard(request, db, UserRole.SUPER_ADMIN)
    if err:
        return err

    conditions = []
    if action:
        conditions.append(AuditLog.action == action)

    count_stmt = select(func.count(AuditLog.id))
    stmt = select(AuditLog)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        stmt = stmt.where(*conditions)

    total = (await db.execute(count_stmt)).scalar() or 0
    logs = list(
        (
            await db.execute(
                stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * 50).limit(50)
            )
        ).scalars().all()
    )

    return _render(
        request,
        "admin/audit.html",
        {
            **await page_context(request, db),
            "page_title": "سجل التدقيق",
            "active_nav": "audit",
            "logs": logs,
            "total": total,
            "page": page,
            "filters": {"action": action},
        },
    )