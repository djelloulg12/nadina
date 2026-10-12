"""
خدمة الإشعارات (Notification Service)
====================================
إنشاء الإشعارات داخل المنصة، وإرسالها عبر القنوات المتاحة
(داخل المنصة، البريد، الرسائل النصية، الإشعارات الفورية).

**استراتيجية التنفيذ**: قنوات البريد والرسائل والمتصفح معطّلة افتراضياً،
ولا تُرسل شيئاً فعلياً في وضع التطوير -- تُسجَّل الرسائل فقط في السجل.
هذا يجعل النظام آمناً للتجربة قبل ربط مزوّد الخدمة.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.models import (
    Athlete,
    Evaluation,
    GroupAthlete,
    Invoice,
    InvoiceStatus,
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    ParentLink,
    User,
)
from app.core.db import utcnow

logger = logging.getLogger(__name__)


# =====================================================================
#  إنشاء الإشعارات
# =====================================================================
def _dedupe_key(kind: str, *parts: Any) -> str:
    """
    مفتاح منع تكرار الإشعار.

    بدونه، كل تشغيل للمجدول (كل ساعة) سينشئ إشعار "فواتير متأخرة" جديداً
    لنفس الفاتورة -- فيغرق الولي في نسخ متطابقة.
    """
    raw = f"{kind}:" + ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


async def notify(
    db: AsyncSession,
    *,
    user_id: int | None,
    type: NotificationType,
    title: str,
    body: str | None = None,
    link: str | None = None,
    club_id: int | None = None,
    priority: str = "normal",
    data: dict | None = None,
    dedupe: bool = False,
    channels: list[NotificationChannel] | None = None,
) -> Notification | None:
    """
    إنشاء إشعار لمستخدم.

    يُرجع ``None`` إذا كان الإشعار مكرراً وتم تفعيل منع التكرار.
    """
    dedupe_key = None
    if dedupe:
        dedupe_key = _dedupe_key(type.value, user_id, title, link)
        existing = await db.execute(
            select(Notification.id).where(Notification.dedupe_key == dedupe_key).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug("تم تخطي إشعار مكرر: %s", dedupe_key)
            return None

    notification = Notification(
        club_id=club_id,
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        link=link,
        data=data or {},
        priority=priority,
        is_read=False,
        delivery_status=NotificationStatus.PENDING,
        dedupe_key=dedupe_key,
    )
    db.add(notification)
    await db.flush()

    # --- الإرسال عبر القنوات الخارجية ---
    for channel in channels or [NotificationChannel.IN_APP]:
        await _dispatch(db, notification, user_id, channel)

    notification.sent_at = utcnow()
    notification.delivery_status = NotificationStatus.SENT
    await db.flush()
    return notification


async def notify_many(
    db: AsyncSession,
    user_ids: list[int],
    *,
    type: NotificationType,
    title: str,
    body: str | None = None,
    link: str | None = None,
    club_id: int | None = None,
    dedupe: bool = True,
) -> int:
    """إرسال نفس الإشعار لعدة مستخدمين."""
    count = 0
    for user_id in set(user_ids):
        result = await notify(
            db,
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            link=link,
            club_id=club_id,
            dedupe=dedupe,
        )
        if result is not None:
            count += 1
    return count


async def _dispatch(
    db: AsyncSession,
    notification: Notification,
    user_id: int | None,
    channel: NotificationChannel,
) -> None:
    """
    إرسال إشعار عبر قناة خارجية.

    في وضع التطوير، القنوات معطّلة ولا نفعل شيئاً سوى التسجيل. عند ربط
    مزوّد خدمة حقيقي، تُستبدل bodies أدناه باستدعاءات المزوّد.
    """
    if channel == NotificationChannel.IN_APP:
        notification.channel = NotificationChannel.IN_APP
        return

    if not user_id:
        return

    user = await db.get(User, user_id)
    if user is None:
        return

    try:
        if channel == NotificationChannel.EMAIL:
            if not settings.EMAIL_ENABLED or not user.email:
                logger.debug("البريد معطّل أو غير متاح لـ %s", user_id)
                return
            # TODO: استدعاء مزوّد البريد
            logger.info("[EMAIL → %s] %s", user.email, notification.title)

        elif channel == NotificationChannel.SMS:
            if not settings.SMS_ENABLED or not user.phone:
                logger.debug("الرسائل معطّلة أو غير متاحة لـ %s", user_id)
                return
            # TODO: استدعاء مزوّد الرسائل
            logger.info("[SMS → %s] %s", user.phone, notification.title)

        elif channel == NotificationChannel.PUSH:
            if not settings.PUSH_ENABLED:
                logger.debug("الإشعارات الفورية معطّلة")
                return
            logger.info("[PUSH → %s] %s", user_id, notification.title)

    except Exception as exc:  # pragma: no cover
        logger.error("فشل إرسال إشعار عبر %s: %s", channel.value, exc)
        notification.delivery_status = NotificationStatus.FAILED
        notification.error_message = str(exc)[:500]


# =====================================================================
#  إشعارات خاصة بالأحداث
# =====================================================================
async def notify_absence(
    db: AsyncSession, athlete: Athlete, session_date: dt.date, coach_name: str | None = None
) -> int:
    """
    إشعار أولياء الرياضي بغيابه عن حصة.

    يُرسل فقط لمن وُضع كـ "ولي مسؤول" لتجنب إغراق ولي ثانٍ بنفس الخبر.
    """
    rows = await db.execute(
        select(ParentLink, User)
        .join(User, User.id == ParentLink.parent_id)
        .where(
            ParentLink.athlete_id == athlete.id,
            ParentLink.is_payer.is_(True),
            User.is_active.is_(True),
        )
    )
    links = rows.all()
    if not links:
        return 0

    when = session_date.strftime("%Y/%m/%d")
    count = 0
    for link, parent in links:
        result = await notify(
            db,
            user_id=parent.id,
            type=NotificationType.ABSENCE,
            title=f"غياب {athlete.full_name} عن الحصة",
            body=(
                f"تم تسجيل غياب {athlete.full_name} عن حصة يوم {when}"
                + (f" ({coach_name})" if coach_name else "")
                + ". يرجى الاطمئنان على سبب الغياب."
            ),
            link=f"/parent/attendance/{athlete.id}",
            club_id=athlete.club_id,
            priority="high",
            data={"athlete_id": athlete.id, "session_date": str(session_date)},
            dedupe=True,
        )
        if result is not None:
            count += 1
    return count


async def notify_invoice_due(
    db: AsyncSession, invoice: Invoice, days_before: int = 3
) -> int:
    """تنبيه الولي قرب استحقاق اشتراك ابنه."""
    rows = await db.execute(
        select(ParentLink, User)
        .join(User, User.id == ParentLink.parent_id)
        .where(
            ParentLink.athlete_id == invoice.athlete_id,
            ParentLink.is_payer.is_(True),
            User.is_active.is_(True),
        )
    )
    links = rows.all()
    if not links:
        return 0

    athlete = await db.get(Athlete, invoice.athlete_id)
    name = athlete.full_name if athlete else "الرياضي"

    count = 0
    for link, parent in links:
        result = await notify(
            db,
            user_id=parent.id,
            type=NotificationType.PAYMENT_DUE,
            title=f"اشتراك {name} يستحق خلال {days_before} أيام",
            body=(
                f"الفاتورة رقم {invoice.invoice_number} بمبلغ "
                f"{float(invoice.amount_due):,.0f} دج مستحقة بتاريخ "
                f"{invoice.due_date.strftime('%Y/%m/%d')}."
            ),
            link=f"/parent/invoices",
            club_id=invoice.club_id,
            priority="high",
            data={"invoice_id": invoice.id, "amount": float(invoice.amount_due)},
            dedupe=True,
        )
        if result is not None:
            count += 1
    return count


async def notify_evaluation(
    db: AsyncSession, evaluation: Evaluation
) -> int:
    """إشعار الولي بتقييم جديد للرياضي."""
    athlete = await db.get(Athlete, evaluation.athlete_id)
    if athlete is None:
        return 0

    rows = await db.execute(
        select(ParentLink, User)
        .join(User, User.id == ParentLink.parent_id)
        .where(
            ParentLink.athlete_id == athlete.id,
            User.is_active.is_(True),
        )
    )

    grade = evaluation.grade
    count = 0
    for link, parent in rows.all():
        result = await notify(
            db,
            user_id=parent.id,
            type=NotificationType.EVALUATION,
            title=f"تقييم جديد لـ {athlete.full_name}: {grade}",
            body=(
                f"قيّم المدرب {athlete.full_name} بتاريخ "
                f"{evaluation.eval_date.strftime('%Y/%m/%d')} بتقدير {grade}."
            ),
            link=f"/parent/evaluations/{athlete.id}",
            club_id=athlete.club_id,
            data={"evaluation_id": evaluation.id},
            dedupe=True,
        )
        if result is not None:
            count += 1
    return count


async def notify_payment_received(
    db: AsyncSession, payment, *, notify_parent: bool = True
) -> int:
    """إشعار الولي باستلام دفعة."""
    if not notify_parent:
        return 0

    rows = await db.execute(
        select(ParentLink, User)
        .join(User, User.id == ParentLink.parent_id)
        .where(
            ParentLink.athlete_id == payment.athlete_id,
            ParentLink.is_payer.is_(True),
            User.is_active.is_(True),
        )
    )
    athlete = await db.get(Athlete, payment.athlete_id)
    name = athlete.full_name if athlete else "الرياضي"

    count = 0
    for link, parent in rows.all():
        result = await notify(
            db,
            user_id=parent.id,
            type=NotificationType.PAYMENT_RECEIVED,
            title=f"تم استلام دفعة عن {name}",
            body=(
                f"تم تسجيل دفعة بمبلغ {float(payment.amount):,.0f} دج "
                f"بتاريخ {payment.payment_date.strftime('%Y/%m/%d')}. "
                f"رقم الوصل: {payment.receipt_number}"
            ),
            link="/parent/payments",
            club_id=payment.club_id,
            data={"payment_id": payment.id, "receipt": payment.receipt_number},
            dedupe=True,
        )
        if result is not None:
            count += 1
    return count


async def notify_absence_bulk(
    db: AsyncSession, attendance_records: list
) -> int:
    """
    إشعار أولياء مجموعة من الغائبين في حصة واحدة.

    نجمع الغائبين ونرسل لكل ولي إشعاراً واحداً بأسماء أبنائه -- بدل
    إشعار منفصل لكل غياب، وهو أسوأ حالة لمن لديه ولدين في نفس الفوج.
    """
    from app.models import TrainingSession

    # تجميع الغائبين حسب الحصة
    by_session: dict[int, list[int]] = {}
    for record in attendance_records:
        if record.status not in ("absent",):
            continue
        by_session.setdefault(record.session_id, []).append(record.athlete_id)

    total = 0
    for session_id, athlete_ids in by_session.items():
        session = await db.get(TrainingSession, session_id)
        if session is None:
            continue

        rows = await db.execute(
            select(ParentLink, User)
            .join(User, User.id == ParentLink.parent_id)
            .where(
                ParentLink.athlete_id.in_(athlete_ids),
                ParentLink.is_payer.is_(True),
                User.is_active.is_(True),
            )
        )
        links = rows.all()
        if not links:
            continue

        athletes = await db.execute(
            select(Athlete.id, Athlete.full_name).where(Athlete.id.in_(athlete_ids))
        )
        names = dict(athletes.all())

        # تجميع حسب الولي: قد يكون للولي أكثر من athlete في نفس الحصة
        per_parent: dict[int, list[str]] = {}
        for link, parent in links:
            name = names.get(link.athlete_id)
            if name:
                per_parent.setdefault(parent.id, []).append(name)

        when = session.session_date.strftime("%Y/%m/%d")
        for parent_id, child_names in per_parent.items():
            children = "، ".join(child_names)
            result = await notify(
                db,
                user_id=parent_id,
                type=NotificationType.ABSENCE,
                title=(
                    f"تسجيل غياب {len(child_names)} من أبنائك"
                    if len(child_names) > 1
                    else f"غياب {children}"
                ),
                body=f"تم تسجيل غياب: {children} — حصة يوم {when}.",
                link="/parent/attendance",
                club_id=session.club_id,
                priority="high",
                data={"session_id": session_id, "athlete_ids": athlete_ids},
                dedupe=True,
            )
            if result is not None:
                total += 1

    return total


# =====================================================================
#  إدارة الإشعارات
# =====================================================================
async def list_notifications(
    db: AsyncSession,
    user_id: int,
    *,
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[Notification]:
    """قائمة إشعارات المستخدم."""
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def unread_count(db: AsyncSession, user_id: int) -> int:
    """عدد الإشعارات غير المقروءة."""
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
    )
    return result.scalar() or 0


async def mark_read(db: AsyncSession, notification_id: int, user_id: int) -> None:
    """تعليم إشعار كمقروء (يتحقق من الملكية)."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise NotFoundError("الإشعار غير موجود")

    notification.is_read = True
    notification.read_at = utcnow()
    notification.delivery_status = NotificationStatus.READ
    await db.flush()


async def mark_all_read(db: AsyncSession, user_id: int) -> int:
    """تعليم كل إشعارات المستخدم كمقروءة."""
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
    )
    count = result.scalar() or 0

    await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        .values(is_read=True, read_at=utcnow())
    )
    await db.flush()
    return count


async def notify_club_staff(
    db: AsyncSession,
    club_id: int,
    *,
    type: NotificationType,
    title: str,
    body: str | None = None,
    link: str | None = None,
    roles: list[str] | None = None,
) -> int:
    """
    إشعار طاقم النادي كله أو فئة منه (مدير، إداري).

    يُستخدم للتنبيهات التشغيلية: تسجيل طلب جديد، اقتراب انتهاء الاشتراك،
    فواتير متأخرة بشكل عام.
    """
    from app.models import StaffMember

    stmt = select(StaffMember.user_id).where(
        StaffMember.club_id == club_id,
        StaffMember.is_active.is_(True),
    )
    if roles:
        stmt = stmt.where(StaffMember.role.in_(roles))

    result = await db.execute(stmt)
    user_ids = [r[0] for r in result.all()]

    if not user_ids:
        return 0

    return await notify_many(
        db,
        user_ids,
        type=type,
        title=title,
        body=body,
        link=link,
        club_id=club_id,
    )