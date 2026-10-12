"""
مسارات لوحات التحكم (Club / Coach / Parent / Admin)
====================================================
صفحات العمل اليومية لكل رتبة. كلها تقرأ عبر طبقة الخدمات، فتتبع نفس
قواعد العزل والصلاحيات التي تتبعها الـ API.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.exceptions import AppError, ValidationError
from app.core.security import validate_password_strength
from app.models import (
    Attendance,
    AttendanceStatus,
    Athlete,
    AuditLog,
    Club,
    ClubNews,
    ClubStatus,
    Evaluation,
    FeePlan,
    Group,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    Payment,
    PlatformPlan,
    RegistrationRequest,
    SessionStatus,
    Sport,
    StaffMember,
    TrainingSession,
    User,
    UserRole,
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
from app.web.helpers import current_user_or_redirect, ensure_role, page_context
from app.web.templates_setup import templates

logger = logging.getLogger(__name__)

CLUB_ROLES = (UserRole.CLUB_ADMIN, UserRole.RECEPTIONIST)
ADMIN_ROLES = (UserRole.CLUB_ADMIN,)


# =====================================================================
#  أدوات مشتركة
# =====================================================================
def _render(request: Request, template: str, ctx: dict, code: int = 200):
    return templates.TemplateResponse(
        request=request, name=template, context=ctx, status_code=code
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


async def _bind(db: AsyncSession, user: User):
    """تثبيت سياق العزل للصفحة (انظر app/web/routes.py)."""
    from app.api.deps import establish_tenant_context

    return await establish_tenant_context(db, user)


async def _guard(request: Request, db: AsyncSession, *roles: UserRole):
    """
    حارس موحّد لكل صفحات اللوحات.

    يُعيد ``(error_response, user, club)``؛ إن كان ``error_response`` غير
    ``None`` يجب على المسار إعادته فوراً.
    """
    user = await current_user_or_redirect(db, request)
    if isinstance(user, RedirectResponse):
        return user, None, None

    error = ensure_role(user, *roles)
    if error:
        return (
            _render(
                request,
                "errors/403.html",
                {**await page_context(request, db), "message": error},
                403,
            ),
            None,
            None,
        )

    club = await db.get(Club, user.club_id) if user.club_id else None
    await _bind(db, user)
    return None, user, club


def _fv(form: Any, key: str, default: Any = None) -> Any:
    value = form.get(key, default)
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return value


def _date(value: Any) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def _time(value: Any) -> dt.time | None:
    if not value:
        return None
    if isinstance(value, dt.time):
        return value
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(str(value), fmt).time()
        except ValueError:
            continue
    return None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# =====================================================================
#  لوحة تحكم النادي
# =====================================================================
club_router = APIRouter(prefix="/club", include_in_schema=False)


@club_router.get("", name="club_dashboard")
@club_router.get("/", name="club_dashboard_slash")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """لوحة تحكم النادي: المؤشرات، جدول اليوم، والغيابات."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    summary = await report_service.club_dashboard(db)
    schedule = await report_service.today_schedule(db)
    absences = await report_service.recent_absences(db)
    chart = await report_service.attendance_chart(db, days=14)

    for item in schedule:
        item["time"] = str(item["time"])
        item["end_time"] = str(item["end_time"])

    return _render(
        request,
        "club/dashboard.html",
        {
            **await page_context(request, db),
            "page_title": "لوحة التحكم",
            "active_nav": "dashboard",
            "club": club,
            "summary": summary,
            "schedule": schedule,
            "absences": absences,
            "chart": chart,
        },
    )


# ---------------------------------------------------------------------
#  الرياضيين
# ---------------------------------------------------------------------
@club_router.get("/athletes", name="club_athletes")
async def athletes_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query(""),
    group_id: int | None = Query(None),
    status: str = Query(""),
    has_debt: str = Query(""),
    page: int = Query(1, ge=1),
):
    """قائمة الرياضيين مع البحث والتصفية."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    items, total = await people_service.search_athletes(
        db,
        search=search or None,
        group_id=group_id,
        status=status or None,
        has_debt=(has_debt == "1") if has_debt else None,
        page=page,
        per_page=20,
    )
    groups = await training_service.list_groups(db)

    return _render(
        request,
        "club/athletes.html",
        {
            **await page_context(request, db),
            "page_title": "الرياضيون",
            "active_nav": "athletes",
            "club": club,
            "athletes": items,
            "groups": groups,
            "total": total,
            "page": page,
            "per_page": 20,
            "filters": {
                "search": search, "group_id": group_id,
                "status": status, "has_debt": has_debt,
            },
        },
    )


@club_router.get("/athletes/new", name="club_athlete_new")
async def athlete_new(request: Request, db: AsyncSession = Depends(get_db)):
    """نموذج إضافة رياضي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())
    return _render(
        request,
        "club/athlete_form.html",
        {
            **await page_context(request, db),
            "page_title": "إضافة رياضي",
            "active_nav": "athletes",
            "club": club,
            "athlete": None,
            "groups": await training_service.list_groups(db),
            "sports": sports,
        },
    )


@club_router.post("/athletes", name="club_athlete_create")
async def athlete_create(request: Request, db: AsyncSession = Depends(get_db)):
    """معالجة إضافة رياضي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())

    group_ids = [int(g) for g in form.getlist("group_ids") if str(g).isdigit()]
    payload = {
        "first_name": _fv(form, "first_name"),
        "last_name": _fv(form, "last_name"),
        "birth_date": _date(_fv(form, "birth_date")),
        "gender": _fv(form, "gender"),
        "nationality": _fv(form, "nationality"),
        "national_id": _fv(form, "national_id"),
        "blood_type": _fv(form, "blood_type"),
        "phone": _fv(form, "phone"),
        "email": _fv(form, "email"),
        "address": _fv(form, "address"),
        "wilaya": _fv(form, "wilaya"),
        "guardian_name": _fv(form, "guardian_name"),
        "guardian_phone": _fv(form, "guardian_phone"),
        "guardian_email": _fv(form, "guardian_email"),
        "emergency_contact_name": _fv(form, "emergency_contact_name"),
        "emergency_contact_phone": _fv(form, "emergency_contact_phone"),
        "medical_notes": _fv(form, "medical_notes"),
        "allergies": _fv(form, "allergies"),
        "chronic_conditions": _fv(form, "chronic_conditions"),
        "notes": _fv(form, "notes"),
        "sport_id": int(_fv(form, "sport_id", 0) or 0) or None,
        "level": _fv(form, "level", "beginner"),
        "jersey_number": int(_fv(form, "jersey_number", 0) or 0) or None,
        "group_ids": group_ids,
        "club_id": user.club_id,
    }

    try:
        athlete = await people_service.create_athlete(db, payload)
        await auth_service.log_audit(
            db, user=user, action="create", entity_type="Athlete",
            entity_id=athlete.id, description=f"إضافة رياضي: {athlete.full_name}",
        )
        await club_service.update_club_counts(db, user.club_id)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        return _render(
            request,
            "club/athlete_form.html",
            {
                **await page_context(request, db),
                "page_title": "إضافة رياضي",
                "active_nav": "athletes",
                "club": club,
                "athlete": None,
                "form": form,
                "error": exc.message,
                "groups": await training_service.list_groups(db),
                "sports": sports,
            },
            exc.status_code,
        )

    return _redirect(f"/club/athletes/{athlete.id}")


@club_router.get("/athletes/{athlete_id}", name="club_athlete_detail")
async def athlete_detail(request: Request, athlete_id: int, db: AsyncSession = Depends(get_db)):
    """الملف الكامل للرياضي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    try:
        profile = await people_service.get_athlete_profile(db, athlete_id)
    except AppError as exc:
        return _render(
            request,
            "errors/404.html",
            {**await page_context(request, db), "message": exc.message},
            exc.status_code,
        )

    recent = await training_service.get_athlete_attendance_history(
        db, athlete_id, limit=10
    )

    return _render(
        request,
        "club/athlete_detail.html",
        {
            **await page_context(request, db),
            "page_title": profile["athlete"].full_name,
            "active_nav": "athletes",
            "club": club,
            # نمرّر ``profile`` كقاموس كامل *وكذلك* مفاتيحه مباشرة، لأن القالب
            # يستخدم ``profile.x`` في بعض المواضع و ``athlete`` في غيرها.
            "profile": profile,
            **profile,
            "recent_attendance": recent,
        },
    )


@club_router.get("/athletes/{athlete_id}/edit", name="club_athlete_edit")
async def athlete_edit(request: Request, athlete_id: int, db: AsyncSession = Depends(get_db)):
    """نموذج تعديل رياضي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    athlete = await people_service.get_athlete(db, athlete_id)
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())

    return _render(
        request,
        "club/athlete_form.html",
        {
            **await page_context(request, db),
            "page_title": f"تعديل {athlete.full_name}",
            "active_nav": "athletes",
            "club": club,
            "athlete": athlete,
            "athlete_groups": await people_service.get_athlete_groups(db, athlete_id),
            "groups": await training_service.list_groups(db),
            "sports": sports,
        },
    )


@club_router.post("/athletes/{athlete_id}/edit", name="club_athlete_update")
async def athlete_update(
    request: Request, athlete_id: int, db: AsyncSession = Depends(get_db)
):
    """معالجة تعديل رياضي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    group_ids = [int(g) for g in form.getlist("group_ids") if str(g).isdigit()]

    payload = {
        "first_name": _fv(form, "first_name"),
        "last_name": _fv(form, "last_name"),
        "birth_date": _date(_fv(form, "birth_date")),
        "gender": _fv(form, "gender"),
        "nationality": _fv(form, "nationality"),
        "national_id": _fv(form, "national_id"),
        "blood_type": _fv(form, "blood_type"),
        "phone": _fv(form, "phone"),
        "email": _fv(form, "email"),
        "address": _fv(form, "address"),
        "wilaya": _fv(form, "wilaya"),
        "guardian_name": _fv(form, "guardian_name"),
        "guardian_phone": _fv(form, "guardian_phone"),
        "guardian_email": _fv(form, "guardian_email"),
        "emergency_contact_name": _fv(form, "emergency_contact_name"),
        "emergency_contact_phone": _fv(form, "emergency_contact_phone"),
        "medical_notes": _fv(form, "medical_notes"),
        "allergies": _fv(form, "allergies"),
        "chronic_conditions": _fv(form, "chronic_conditions"),
        "notes": _fv(form, "notes"),
        "sport_id": int(_fv(form, "sport_id", 0) or 0) or None,
        "level": _fv(form, "level"),
        "status": _fv(form, "status"),
        "group_ids": group_ids,
    }

    try:
        await people_service.update_athlete(db, athlete_id, payload)
        await auth_service.log_audit(
            db, user=user, action="update", entity_type="Athlete",
            entity_id=athlete_id, description="تعديل بيانات رياضي",
        )
        await db.commit()
    except AppError as exc:
        await db.rollback()
        athlete = await people_service.get_athlete(db, athlete_id)
        sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())
        return _render(
            request,
            "club/athlete_form.html",
            {
                **await page_context(request, db),
                "page_title": "تعديل رياضي",
                "active_nav": "athletes",
                "club": club,
                "athlete": athlete,
                "form": form,
                "error": exc.message,
                "groups": await training_service.list_groups(db),
                "sports": sports,
            },
            exc.status_code,
        )

    return _redirect(f"/club/athletes/{athlete_id}")


@club_router.post("/athletes/{athlete_id}/delete", name="club_athlete_delete")
async def athlete_delete(
    request: Request, athlete_id: int, db: AsyncSession = Depends(get_db)
):
    """تعطيل رياضي (مع فحص الديون)."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    permanent = _fv(form, "permanent") == "1"

    try:
        await people_service.delete_athlete(db, athlete_id, soft=not permanent)
        await auth_service.log_audit(
            db, user=user, action="delete", entity_type="Athlete",
            entity_id=athlete_id, description="حذف رياضي",
        )
        await club_service.update_club_counts(db, user.club_id)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        return _render(
            request,
            "club/athlete_detail.html",
            {
                **await page_context(request, db),
                "page_title": "خطأ",
                "active_nav": "athletes",
                "club": club,
                "error": exc.message,
                **await people_service.get_athlete_profile(db, athlete_id),
            },
            exc.status_code,
        )

    return _redirect("/club/athletes")


@club_router.get("/athletes/{athlete_id}/card", name="club_athlete_card")
async def athlete_card(request: Request, athlete_id: int, db: AsyncSession = Depends(get_db)):
    """البطاقة الرياضية للرياضي (للطباعة)."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    content = await card_service.generate_athlete_card(db, athlete_id)
    media = "application/pdf" if content[:4] == b"%PDF" else "text/html; charset=utf-8"
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media,
        headers={"Content-Disposition": f"inline; filename=\"card-{athlete_id}\""},
    )


# ---------------------------------------------------------------------
#  الأفواج
# ---------------------------------------------------------------------
@club_router.get("/groups", name="club_groups")
async def groups_list(request: Request, db: AsyncSession = Depends(get_db)):
    """قائمة الأفواج مع نسب الإشغال."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    groups = await training_service.list_groups(db)
    occupancy = await report_service.groups_occupancy(db)
    occupancy_map = {o["group_id"]: o for o in occupancy}

    staff_rows = await db.execute(
        select(StaffMember).where(StaffMember.is_active.is_(True))
    )

    return _render(
        request,
        "club/groups.html",
        {
            **await page_context(request, db),
            "page_title": "الأفواج",
            "active_nav": "groups",
            "club": club,
            "groups": groups,
            "occupancy": occupancy_map,
            "staff": list(staff_rows.scalars().all()),
        },
    )


@club_router.get("/groups/new", name="club_group_new")
async def group_new(request: Request, db: AsyncSession = Depends(get_db)):
    """نموذج إنشاء فوج."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    staff_rows = await db.execute(
        select(StaffMember).where(
            StaffMember.is_active.is_(True), StaffMember.role == UserRole.COACH
        )
    )
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())

    return _render(
        request,
        "club/group_form.html",
        {
            **await page_context(request, db),
            "page_title": "إنشاء فوج",
            "active_nav": "groups",
            "club": club,
            "group": None,
            "coaches": list(staff_rows.scalars().all()),
            "sports": sports,
        },
    )


@club_router.post("/groups", name="club_group_create")
async def group_create(request: Request, db: AsyncSession = Depends(get_db)):
    """معالجة إنشاء فوج."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    staff_rows = await db.execute(
        select(StaffMember).where(
            StaffMember.is_active.is_(True), StaffMember.role == UserRole.COACH
        )
    )
    coaches = list(staff_rows.scalars().all())
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())

    # أيام التدريب تأتي كقائمة حقول متكررة، تُدمج في نص واحد للتخزين
    days = [d for d in form.getlist("schedule_days") if d]
    schedule = ", ".join(days) if days else None

    payload = {
        "name": _fv(form, "name"),
        "code": _fv(form, "code"),
        "sport_id": int(_fv(form, "sport_id", 0) or 0) or None,
        "level": _fv(form, "level", "beginner"),
        "min_age": int(_fv(form, "min_age", 0) or 0) or None,
        "max_age": int(_fv(form, "max_age", 0) or 0) or None,
        "gender": _fv(form, "gender"),
        "coach_id": int(_fv(form, "coach_id", 0) or 0) or None,
        "capacity": int(_fv(form, "capacity", 20) or 20),
        "color": _fv(form, "color"),
        "location": _fv(form, "location"),
        "schedule_days": schedule,
        "start_time": _time(_fv(form, "start_time")),
        "end_time": _time(_fv(form, "end_time")),
        "monthly_fee": _float(_fv(form, "monthly_fee")),
        "description": _fv(form, "description"),
        "club_id": user.club_id,
    }

    try:
        group = await training_service.create_group(db, payload)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        return _render(
            request,
            "club/group_form.html",
            {
                **await page_context(request, db),
                "page_title": "إنشاء فوج",
                "active_nav": "groups",
                "club": club,
                "group": None,
                "form": form,
                "error": exc.message,
                "coaches": coaches,
                "sports": sports,
            },
            exc.status_code,
        )

    return _redirect(f"/club/groups/{group.id}")


@club_router.post(
    "/athletes/{athlete_id}/groups/{group_id}/remove",
    name="club_group_remove_athlete",
)
async def group_remove_athlete(
    request: Request, athlete_id: int, group_id: int, db: AsyncSession = Depends(get_db)
):
    """إخراج رياضي من فوج."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    try:
        await people_service.remove_athlete_from_group(db, athlete_id, group_id)
        await db.commit()
    except AppError as exc:
        logger.warning("فشل إخراج الرياضي من الفوج: %s", exc.message)

    return _redirect(f"/club/groups/{group_id}")


@club_router.get("/groups/{group_id}", name="club_group_detail")
async def group_detail(request: Request, group_id: int, db: AsyncSession = Depends(get_db)):
    """تفاصيل الفوج ورياضيوه."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    try:
        group = await training_service.get_group(db, group_id)
    except AppError as exc:
        return _render(
            request, "errors/404.html",
            {**await page_context(request, db), "message": exc.message},
            exc.status_code,
        )

    athletes = await training_service.get_group_athletes(db, group_id)
    sessions = await training_service.list_sessions(db, group_id=group_id)

    return _render(
        request,
        "club/group_detail.html",
        {
            **await page_context(request, db),
            "page_title": group.name,
            "active_nav": "groups",
            "club": club,
            "group": group,
            "athletes": athletes,
            "sessions": sessions[:10],
        },
    )


@club_router.get("/groups/{group_id}/edit", name="club_group_edit")
async def group_edit(request: Request, group_id: int, db: AsyncSession = Depends(get_db)):
    """نموذج تعديل فوج."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    group = await training_service.get_group(db, group_id)
    staff_rows = await db.execute(
        select(StaffMember).where(
            StaffMember.is_active.is_(True), StaffMember.role == UserRole.COACH
        )
    )
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())

    return _render(
        request,
        "club/group_form.html",
        {
            **await page_context(request, db),
            "page_title": f"تعديل {group.name}",
            "active_nav": "groups",
            "club": club,
            "group": group,
            "coaches": list(staff_rows.scalars().all()),
            "sports": sports,
        },
    )


@club_router.post("/groups/{group_id}/edit", name="club_group_update")
async def group_update(request: Request, group_id: int, db: AsyncSession = Depends(get_db)):
    """معالجة تعديل فوج."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    staff_rows = await db.execute(
        select(StaffMember).where(
            StaffMember.is_active.is_(True), StaffMember.role == UserRole.COACH
        )
    )
    coaches = list(staff_rows.scalars().all())
    sports = list((await db.execute(select(Sport).where(Sport.is_active.is_(True)))).scalars().all())

    # أيام التدريب تأتي كقائمة حقول متكررة، تُدمج في نص واحد للتخزين
    days = [d for d in form.getlist("schedule_days") if d]
    schedule = ", ".join(days) if days else None

    payload = {
        "name": _fv(form, "name"),
        "code": _fv(form, "code"),
        "sport_id": int(_fv(form, "sport_id", 0) or 0) or None,
        "level": _fv(form, "level", "beginner"),
        "min_age": int(_fv(form, "min_age", 0) or 0) or None,
        "max_age": int(_fv(form, "max_age", 0) or 0) or None,
        "gender": _fv(form, "gender"),
        "coach_id": int(_fv(form, "coach_id", 0) or 0) or None,
        "capacity": int(_fv(form, "capacity", 20) or 20),
        "color": _fv(form, "color"),
        "location": _fv(form, "location"),
        "schedule_days": schedule,
        "start_time": _time(_fv(form, "start_time")),
        "end_time": _time(_fv(form, "end_time")),
        "monthly_fee": _float(_fv(form, "monthly_fee")),
        "description": _fv(form, "description"),
    }

    try:
        await training_service.update_group(db, group_id, payload)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        group = await training_service.get_group(db, group_id)
        return _render(
            request,
            "club/group_form.html",
            {
                **await page_context(request, db),
                "page_title": "تعديل فوج",
                "active_nav": "groups",
                "club": club,
                "group": group,
                "form": form,
                "error": exc.message,
                "coaches": coaches,
                "sports": sports,
            },
            exc.status_code,
        )

    return _redirect(f"/club/groups/{group_id}")


@club_router.post("/groups/{group_id}/delete", name="club_group_delete")
async def group_delete(request: Request, group_id: int, db: AsyncSession = Depends(get_db)):
    """حذف فوج."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    try:
        await training_service.delete_group(db, group_id)
        await db.commit()
    except AppError as exc:
        return _render(
            request, "errors/400.html",
            {**await page_context(request, db), "message": exc.message},
            exc.status_code,
        )

    return _redirect("/club/groups")


# ---------------------------------------------------------------------
#  الحصص والحضور
# ---------------------------------------------------------------------
@club_router.get("/sessions", name="club_sessions")
async def sessions_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start_date: str = Query(""),
    group_id: int | None = Query(None),
    status: str = Query(""),
):
    """قائمة الحصص."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    start = _date(start_date)
    sessions = await training_service.list_sessions(
        db,
        start_date=start,
        end_date=start,
        group_id=group_id,
        status=status or None,
    )
    if not start:
        sessions = await training_service.list_sessions(
            db, group_id=group_id, status=status or None
        )

    return _render(
        request,
        "club/sessions.html",
        {
            **await page_context(request, db),
            "page_title": "الحصص التدريبية",
            "active_nav": "sessions",
            "club": club,
            "sessions": sessions,
            "groups": await training_service.list_groups(db),
            "filters": {"start_date": start_date, "group_id": group_id, "status": status},
        },
    )


@club_router.post("/sessions", name="club_session_create")
async def session_create(request: Request, db: AsyncSession = Depends(get_db)):
    """برمجة حصة جديدة."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    payload = {
        "group_id": int(_fv(form, "group_id", 0) or 0) or None,
        "coach_id": int(_fv(form, "coach_id", 0) or 0) or None,
        "title": _fv(form, "title"),
        "session_date": _date(_fv(form, "session_date")),
        "start_time": _time(_fv(form, "start_time")),
        "end_time": _time(_fv(form, "end_time")),
        "location": _fv(form, "location"),
        "session_type": _fv(form, "session_type"),
        "objectives": _fv(form, "objectives"),
        "club_id": user.club_id,
    }

    try:
        session = await training_service.create_session(db, payload)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        return _render(
            request,
            "club/sessions.html",
            {
                **await page_context(request, db),
                "page_title": "الحصص التدريبية",
                "active_nav": "sessions",
                "club": club,
                "sessions": await training_service.list_sessions(db),
                "groups": await training_service.list_groups(db),
                "error": exc.message,
                "filters": {},
            },
            exc.status_code,
        )

    return _redirect(f"/club/sessions/{session.id}")


@club_router.get("/sessions/{session_id}", name="club_session_detail")
async def session_detail(request: Request, session_id: int, db: AsyncSession = Depends(get_db)):
    """ورقة حضور الحصة."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
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

    athletes = (
        await training_service.get_group_athletes(db, session.group_id)
        if session.group_id else []
    )
    records = await training_service.get_session_attendance(db, session_id)
    status_map = {r[0].athlete_id: r[0] for r in records}

    return _render(
        request,
        "club/session_detail.html",
        {
            **await page_context(request, db),
            "page_title": f"حصة {session.session_date}",
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


@club_router.post("/sessions/{session_id}/attendance", name="club_attendance_record")
async def attendance_record(
    request: Request, session_id: int, db: AsyncSession = Depends(get_db)
):
    """تسجيل حضور (من ورقة الحضور في صفحة الإدارة)."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    athlete_id = int(_fv(form, "athlete_id", 0) or 0)
    status = _fv(form, "status", "present")

    if athlete_id:
        try:
            await training_service.record_attendance(
                db,
                session_id=session_id,
                athlete_id=athlete_id,
                status=status,
                method="manual",
                notes=_fv(form, "notes"),
                recorded_by_id=user.id,
            )
            await db.commit()
        except AppError as exc:
            logger.warning("فشل تسجيل الحضور: %s", exc.message)

    return _redirect(f"/club/sessions/{session_id}")


@club_router.post(
    "/sessions/{session_id}/mark-absent",
    name="club_session_mark_absent",
)
async def session_mark_absent(
    request: Request, session_id: int, db: AsyncSession = Depends(get_db)
):
    """تحديد المتبقين كغياب + إشعار الأولياء."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    try:
        count = await training_service.mark_absent_for_remaining(
            db, session_id=session_id, recorded_by_id=user.id
        )
        records = await training_service.get_session_attendance(db, session_id)
        await notification_service.notify_absence_bulk(db, [r[0] for r in records])
        await db.commit()
    except AppError as exc:
        logger.warning("فشل تحديد الغائبين: %s", exc.message)

    return _redirect(f"/club/sessions/{session_id}")


# ---------------------------------------------------------------------
#  المالية
# ---------------------------------------------------------------------
@club_router.get("/finance", name="club_finance")
async def finance_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start_date: str = Query(""),
    end_date: str = Query(""),
):
    """لوحة finances: الإيرادات والمصاريف والديون."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    summary = await finance_service.financial_summary(
        db, start_date=_date(start_date), end_date=_date(end_date)
    )
    chart = await finance_service.revenue_by_month(db, months=12)
    debtors = await finance_service.debtors_report(db, limit=10)
    categories = await finance_service.revenue_by_category(db)

    return _render(
        request,
        "club/finance.html",
        {
            **await page_context(request, db),
            "page_title": "التسيير المالي",
            "active_nav": "finance",
            "club": club,
            "summary": summary,
            "chart": chart,
            "debtors": debtors,
            "categories": categories,
            "filters": {"start_date": start_date, "end_date": end_date},
        },
    )


@club_router.get("/finance/invoices", name="club_invoices")
async def invoices_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = Query(""),
    overdue: str = Query(""),
    page: int = Query(1, ge=1),
):
    """قائمة الفواتير."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    items, total = await finance_service.list_invoices(
        db,
        status=status or None,
        overdue_only=(overdue == "1") if overdue else False,
        page=page,
        per_page=25,
    )
    plans = await finance_service.list_fee_plans(db)

    return _render(
        request,
        "club/invoices.html",
        {
            **await page_context(request, db),
            "page_title": "الفواتير",
            "active_nav": "finance",
            "club": club,
            "invoices": items,
            "plans": plans,
            "total": total,
            "page": page,
            "per_page": 25,
            "filters": {"status": status, "overdue": overdue},
        },
    )


@club_router.post("/finance/invoices", name="club_invoice_create")
async def invoice_create(request: Request, db: AsyncSession = Depends(get_db)):
    """إصدار فاتورة جديدة."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    athlete_id = int(_fv(form, "athlete_id", 0) or 0)
    fee_plan_id = int(_fv(form, "fee_plan_id", 0) or 0) or None

    try:
        invoice = await finance_service.create_invoice(
            db,
            athlete_id=athlete_id,
            fee_plan_id=fee_plan_id,
            amount=_float(_fv(form, "amount")),
            discount=_float(_fv(form, "discount")) or 0,
            notes=_fv(form, "notes"),
        )
        await db.commit()
        return _redirect(f"/club/finance/invoices/{invoice.id}")
    except AppError as exc:
        await db.rollback()
        items, total = await finance_service.list_invoices(db, page=1, per_page=25)
        return _render(
            request,
            "club/invoices.html",
            {
                **await page_context(request, db),
                "page_title": "الفواتير",
                "active_nav": "finance",
                "club": club,
                "invoices": items,
                "plans": await finance_service.list_fee_plans(db),
                "total": total,
                "page": 1,
                "per_page": 25,
                "filters": {},
                "error": exc.message,
            },
            exc.status_code,
        )


@club_router.get("/finance/invoices/{invoice_id}", name="club_invoice_detail")
async def invoice_detail(
    request: Request, invoice_id: int, db: AsyncSession = Depends(get_db)
):
    """الفاتورة جاهزة للطباعة."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    try:
        invoice = await finance_service.get_invoice(db, invoice_id)
    except AppError as exc:
        return _render(
            request, "errors/404.html",
            {**await page_context(request, db), "message": exc.message},
            exc.status_code,
        )

    payments_result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == invoice_id)
        .order_by(Payment.payment_date.desc())
    )
    payments = list(payments_result.scalars().all())

    return _render(
        request,
        "club/invoice_detail.html",
        {
            **await page_context(request, db),
            "page_title": f"فاتورة {invoice.invoice_number}",
            "active_nav": "finance",
            "club": club,
            "invoice": invoice,
            "payments": payments,
        },
    )


@club_router.post("/finance/payments", name="club_payment_create")
async def payment_create(request: Request, db: AsyncSession = Depends(get_db)):
    """تسجيل دفعة."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    invoice_id = int(_fv(form, "invoice_id", 0) or 0) or None
    athlete_id = int(_fv(form, "athlete_id", 0) or 0) or None

    try:
        payment = await finance_service.record_payment(
            db,
            invoice_id=invoice_id,
            athlete_id=athlete_id,
            amount=_float(_fv(form, "amount")) or 0,
            method=_fv(form, "method", "cash"),
            reference=_fv(form, "reference"),
            notes=_fv(form, "notes"),
            recorded_by_id=user.id,
        )
        await notification_service.notify_payment_received(db, payment)
        await db.commit()

        if invoice_id:
            return _redirect(f"/club/finance/invoices/{invoice_id}")
    except AppError as exc:
        await db.rollback()
        items, total = await finance_service.list_invoices(db, page=1, per_page=25)
        return _render(
            request,
            "club/invoices.html",
            {
                **await page_context(request, db),
                "page_title": "الفواتير",
                "active_nav": "finance",
                "club": club,
                "invoices": items,
                "plans": await finance_service.list_fee_plans(db),
                "total": total,
                "page": 1,
                "per_page": 25,
                "filters": {},
                "error": exc.message,
            },
            exc.status_code,
        )

    return _redirect("/club/finance/invoices")


@club_router.post("/finance/fee-plans", name="club_fee_plan_create")
async def fee_plan_create(request: Request, db: AsyncSession = Depends(get_db)):
    """إنشاء خطة اشتراك."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    payload = {
        "name": _fv(form, "name"),
        "description": _fv(form, "description"),
        "amount": _float(_fv(form, "amount")) or 0,
        "period": _fv(form, "period", "monthly"),
        "installments": int(_fv(form, "installments", 1) or 1),
        "sport_id": int(_fv(form, "sport_id", 0) or 0) or None,
        "age_group": _fv(form, "age_group"),
        "discount_percent": _float(_fv(form, "discount_percent")) or 0,
        "club_id": user.club_id,
    }

    try:
        await finance_service.create_fee_plan(db, payload)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        logger.warning("فشل إنشاء خطة: %s", exc.message)

    return _redirect("/club/finance")


@club_router.post("/finance/expenses", name="club_expense_create")
async def expense_create(request: Request, db: AsyncSession = Depends(get_db)):
    """تسجيل مصروف."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    payload = {
        "title": _fv(form, "title"),
        "description": _fv(form, "description"),
        "category": _fv(form, "category", "other"),
        "amount": _float(_fv(form, "amount")) or 0,
        "expense_date": _date(_fv(form, "expense_date")),
        "vendor": _fv(form, "vendor"),
        "notes": _fv(form, "notes"),
        "club_id": user.club_id,
        "recorded_by_id": user.id,
        "approved_by_id": user.id,
    }

    try:
        await finance_service.create_expense(db, payload)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        logger.warning("فشل تسجيل المصروف: %s", exc.message)

    return _redirect("/club/finance")


# ---------------------------------------------------------------------
#  الطاقم
# ---------------------------------------------------------------------
@club_router.get("/staff", name="club_staff")
async def staff_list(request: Request, db: AsyncSession = Depends(get_db)):
    """طاقم النادي."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    members = await people_service.list_staff(db)

    return _render(
        request,
        "club/staff.html",
        {
            **await page_context(request, db),
            "page_title": "طاقم النادي",
            "active_nav": "staff",
            "club": club,
            "members": members,
        },
    )


@club_router.post("/staff", name="club_staff_create")
async def staff_create(request: Request, db: AsyncSession = Depends(get_db)):
    """إضافة عضو للطاقم."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    email = _fv(form, "email")
    phone = _fv(form, "phone")
    password = _fv(form, "password", "")

    try:
        if not password:
            from app.core.security import generate_token

            password = generate_token(12) + "Aa1"

        validate_password_strength(password)

        new_user = await auth_service.create_user(
            db,
            identifier=email or phone,
            password=password,
            full_name=_fv(form, "full_name"),
            role=UserRole(_fv(form, "role", "coach")),
            club_id=user.club_id,
            phone=phone,
            email=email,
        )
        await people_service.create_staff_member(
            db,
            user_id=new_user.id,
            job_title=_fv(form, "job_title", "other"),
            role=UserRole(_fv(form, "role", "coach")),
            salary=_float(_fv(form, "salary")),
            specialty=_fv(form, "specialty"),
            hire_date=_date(_fv(form, "hire_date")),
            club_id=user.club_id,
        )
        await db.commit()
    except AppError as exc:
        await db.rollback()
        members = await people_service.list_staff(db)
        return _render(
            request,
            "club/staff.html",
            {
                **await page_context(request, db),
                "page_title": "طاقم النادي",
                "active_nav": "staff",
                "club": club,
                "members": members,
                "error": exc.message,
                "form": form,
            },
            exc.status_code,
        )

    return _redirect("/club/staff")


# ---------------------------------------------------------------------
#  الأخبار وطلبات التسجيل
# ---------------------------------------------------------------------
@club_router.get("/news", name="club_news")
async def club_news_page(request: Request, db: AsyncSession = Depends(get_db)):
    """إدارة أخبار النادي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    items, _ = await club_service.list_news(db, published_only=False, per_page=30)

    return _render(
        request,
        "club/news.html",
        {
            **await page_context(request, db),
            "page_title": "الأخبار والإعلانات",
            "active_nav": "news",
            "club": club,
            "news": items,
        },
    )


@club_router.post("/news", name="club_news_create")
async def club_news_create(request: Request, db: AsyncSession = Depends(get_db)):
    """نشر خبر أو إعلان."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    payload = {
        "title": _fv(form, "title"),
        "content": _fv(form, "content"),
        "kind": _fv(form, "kind", "news"),
        "is_pinned": _fv(form, "is_pinned") == "on",
        "event_date": _date(_fv(form, "event_date")),
        "location": _fv(form, "location"),
        "author_id": user.id,
    }

    try:
        await club_service.create_news(db, payload, club_id=user.club_id)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        logger.warning("فشل نشر الخبر: %s", exc.message)

    return _redirect("/club/news")


@club_router.get("/registrations", name="club_registrations")
async def registrations_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = Query("pending"),
):
    """طلبات التسجيل الواردة."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    items, total = await club_service.list_registration_requests(
        db, status=status or None, per_page=50
    )

    return _render(
        request,
        "club/registrations.html",
        {
            **await page_context(request, db),
            "page_title": "طلبات التسجيل",
            "active_nav": "registrations",
            "club": club,
            "requests": items,
            "total": total,
            "filters": {"status": status},
        },
    )


@club_router.post(
    "/registrations/{request_id}/approve", name="club_registration_approve"
)
async def registration_approve(
    request: Request, request_id: int, db: AsyncSession = Depends(get_db)
):
    """الموافقة على طلب التسجيل وإنشاء ملف الرياضي."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    try:
        result = await club_service.approve_registration(db, request_id, user.id)
        await notification_service.notify_club_staff(
            db,
            club_id=user.club_id,
            type="registration",
            title="تمت الموافقة على طلب تسجيل",
            body=f"تم إنشاء ملف {result['athlete'].full_name}",
        )
        await club_service.update_club_counts(db, user.club_id)
        await db.commit()
    except AppError as exc:
        await db.rollback()
        logger.warning("فشل الموافقة: %s", exc.message)

    return _redirect("/club/registrations")


@club_router.post(
    "/registrations/{request_id}/reject", name="club_registration_reject"
)
async def registration_reject(
    request: Request, request_id: int, db: AsyncSession = Depends(get_db)
):
    """رفض طلب تسجيل."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    form = await request.form()
    try:
        await club_service.reject_registration(
            db, request_id, _fv(form, "reason", "لم يذكر السبب"), user.id
        )
        await db.commit()
    except AppError as exc:
        await db.rollback()
        logger.warning("فشل الرفض: %s", exc.message)

    return _redirect("/club/registrations")


# ---------------------------------------------------------------------
#  الاستيراد والتصدير
# ---------------------------------------------------------------------
@club_router.get("/import", name="club_import")
async def import_page(request: Request, db: AsyncSession = Depends(get_db)):
    """صفحة الاستيراد والتصدير."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    return _render(
        request,
        "club/import.html",
        {
            **await page_context(request, db),
            "page_title": "الاستيراد والتصدير",
            "active_nav": "import",
            "club": club,
        },
    )


@club_router.post("/import", name="club_import_athletes")
async def import_athletes(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """استيراد رياضيين من ملف Excel."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    form = await request.form()
    dry_run = _fv(form, "dry_run") == "1"

    content = await file.read()
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

    return _render(
        request,
        "club/import.html",
        {
            **await page_context(request, db),
            "page_title": "الاستيراد والتصدير",
            "active_nav": "import",
            "club": club,
            "result": result,
        },
    )


@club_router.get("/export/athletes", name="club_export_athletes")
async def export_athletes(request: Request, db: AsyncSession = Depends(get_db)):
    """تصدير الرياضيين إلى Excel."""
    err, user, club = await _guard(request, db, *ADMIN_ROLES)
    if err:
        return err

    content = await excel_service.export_athletes(db)
    await db.commit()
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="athletes.xlsx"'},
    )


# ---------------------------------------------------------------------
#  الإشعارات والملف الشخصي
# ---------------------------------------------------------------------
@club_router.get("/notifications", name="club_notifications")
async def notifications_page(request: Request, db: AsyncSession = Depends(get_db)):
    """كل الإشعارات."""
    err, user, club = await _guard(request, db, *CLUB_ROLES)
    if err:
        return err

    items = await notification_service.list_notifications(db, user.id, limit=50)

    return _render(
        request,
        "shared/notifications.html",
        {
            **await page_context(request, db),
            "page_title": "الإشعارات",
            "notifications": items,
        },
    )


profile_router = APIRouter(include_in_schema=False)


@profile_router.get("/profile", name="profile")
async def profile_page(request: Request, db: AsyncSession = Depends(get_db)):
    """الملف الشخصي وإعدادات الحساب."""
    err, user, club = await _guard(request, db, *(
        UserRole.SUPER_ADMIN, UserRole.CLUB_ADMIN, UserRole.RECEPTIONIST,
        UserRole.COACH, UserRole.PARENT, UserRole.ATHLETE,
    ))
    if err:
        return err

    return _render(
        request,
        "shared/profile.html",
        {
            **await page_context(request, db),
            "page_title": "الملف الشخصي",
            "profile_user": user,
        },
    )


@profile_router.post("/profile", name="profile_update")
async def profile_update(request: Request, db: AsyncSession = Depends(get_db)):
    """تحديث بيانات الملف الشخصي."""
    user = await current_user_or_redirect(db, request)
    if isinstance(user, RedirectResponse):
        return user

    form = await request.form()
    try:
        await auth_service.update_profile(
            db,
            user,
            full_name=_fv(form, "full_name"),
            email=_fv(form, "email"),
            phone=_fv(form, "phone"),
        )
        new_password = _fv(form, "new_password")
        if new_password:
            if _fv(form, "confirm_password") != new_password:
                raise ValidationError("كلمتا المرور غير متطابقتين")
            await auth_service.change_password(
                db, user, _fv(form, "current_password"), new_password
            )
        await db.commit()
    except AppError as exc:
        await db.rollback()
        return _render(
            request,
            "shared/profile.html",
            {
                **await page_context(request, db),
                "page_title": "الملف الشخصي",
                "profile_user": user,
                "error": exc.message,
            },
            exc.status_code,
        )

    return _redirect("/profile")