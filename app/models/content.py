"""
نماذج المحتوى والتواصل (Content Models)
=======================================
الأخبار والإعلانات، طلبات التسجيل، الإشعارات، وسجل التدقيق.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, SoftDeleteMixin, TenantMixin, utcnow
from app.models.enums import (
    AuditAction,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    RegistrationStatus,
)

if TYPE_CHECKING:
    from app.models.people import Athlete
    from app.models.platform import Club, Sport, User


# =====================================================================
#  الأخبار والإعلانات
# =====================================================================
class ClubNews(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    خبر أو إعلان ينشره النادي.

    ``club_id`` nullable: إذا كان ``None`` فالمحتوى إعلان عام على مستوى
    المنصة يظهر لكل الأندية (يقرأه الجميع عبر العقل
    ``__tenant_global_read__``).
    """

    __tablename__ = "club_news"

    __tenant_global_read__: bool = True
    __parent_scope__ = "club_id:clubs"

    #: يُسمح بـ NULL = إعلان عام على مستوى المنصة (يراه كل مستخدم)
    club_id: Mapped[int | None] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="معرّف النادي (NULL = إعلان عام للمنصة)",
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    excerpt: Mapped[str | None] = mapped_column(
        String(400), comment="ملخص قصير لقائمة الأخبار"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="المحتوى الكامل")

    #: نوع المنشور
    kind: Mapped[str] = mapped_column(
        String(30),
        default="news",
        nullable=False,
        index=True,
        comment="news / announcement / result / event",
    )

    #: تصنيف إضافي (مثلاً نتائج بطولات)
    category: Mapped[str | None] = mapped_column(String(60), index=True)

    cover_url: Mapped[str | None] = mapped_column(String(255))
    #: صور إضافية (JSON list)
    gallery: Mapped[list] = mapped_column(JSON, default=list)

    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime, index=True, comment="تاريخ النشر"
    )

    #: فعالية التاريخ (لأخبار التسجيل والفعاليات)
    event_date: Mapped[dt.date | None] = mapped_column(Date)
    event_end_date: Mapped[dt.date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(String(200))

    views_count: Mapped[int] = mapped_column(Integer, default=0)
    likes_count: Mapped[int] = mapped_column(Integer, default=0)

    #: sport_id للتصفية حسب الرياضة
    sport_id: Mapped[int | None] = mapped_column(
        ForeignKey("sports.id", ondelete="SET NULL"), index=True
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    club: Mapped["Club | None"] = relationship(
        "Club", back_populates="news", foreign_keys=[club_id]
    )
    author: Mapped["User | None"] = relationship("User", foreign_keys=[author_id])

    __table_args__ = (
        Index("ix_news_published", "is_published", "published_at"),
        Index("ix_news_club_kind", "club_id", "kind"),
        Index("ix_feed_order", "is_pinned", "published_at"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.title

    @property
    def kind_label(self) -> str:
        return {
            "news": "خبر",
            "announcement": "إعلان",
            "result": "نتيجة",
            "event": "فعالية",
            "tournament": "بطولة",
        }.get(self.kind, self.kind)

    @property
    def is_event(self) -> bool:
        return self.kind in ("event", "tournament") and self.event_date is not None

    @property
    def is_upcoming_event(self) -> bool:
        return self.is_event and self.event_date >= dt.date.today()


# =====================================================================
#  طلبات التسجيل
# =====================================================================
class RegistrationRequest(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    طلب تسجيل مبدئي يرسله زائر من البوابة العامة.

    يمر بحالات: ``pending`` -> ``approved`` / ``rejected``.
    عند الموافقة، تتحوّل البيانات إلى سجل رياضي فعلي.
    """

    __tablename__ = "registration_requests"

    __parent_scope__ = "club_id:clubs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- بيانات المتقدّم ---
    full_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    birth_date: Mapped[dt.date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(10))

    parent_name: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    address: Mapped[str | None] = mapped_column(String(255))
    wilaya: Mapped[str | None] = mapped_column(String(100))

    sport_id: Mapped[int | None] = mapped_column(
        ForeignKey("sports.id", ondelete="SET NULL"), index=True
    )
    preferred_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL")
    )
    level: Mapped[str | None] = mapped_column(String(30))

    message: Mapped[str | None] = mapped_column(Text, comment="ملاحظات المتقدّم")
    documents_url: Mapped[str | None] = mapped_column(
        String(255), comment="صورة شهادة الميلاد أو طبيعة"
    )

    # --- الحالة ---
    status: Mapped[RegistrationStatus] = mapped_column(
        default=RegistrationStatus.PENDING, nullable=False, index=True
    )
    submitted_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    processed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    #: عند الموافقة: معرّف الرياضي الذي أُنشئ
    created_athlete_id: Mapped[int | None] = mapped_column(
        ForeignKey("athletes.id", ondelete="SET NULL")
    )

    club: Mapped["Club"] = relationship("Club", back_populates="registration_requests")
    sport: Mapped["Sport | None"] = relationship("Sport")
    created_athlete: Mapped["Athlete | None"] = relationship("Athlete")

    __table_args__ = (
        Index("ix_reg_requests_club_status", "club_id", "status"),
        Index("ix_reg_requests_pending", "status", "submitted_at"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"طلب {self.full_name}"

    @property
    def status_label(self) -> str:
        return {
            "pending": "قيد المراجعة",
            "approved": "مقبول",
            "rejected": "مرفوض",
            "cancelled": "ملغى",
        }.get(self.status, self.status)

    @property
    def waiting_days(self) -> int:
        """كم يوماً ينتظر الطلب المراجعة؟"""
        return (utcnow() - self.submitted_at).days


# =====================================================================
#  الإشعارات
# =====================================================================
class Notification(TenantMixin, BaseModel):
    """
    إشعار داخل المنصة (يُعرض في جرس الإشعارات).

    ``user_id`` هو المستقبِل. الإشعارات على مستوى النادي (``user_id=None``)
    تظهر لكل طاقم النادي.
    """

    __tablename__ = "notifications"

    __parent_scope__ = "user_id:own"

    #: يُسمح بـ NULL: حساب الولي قد يمتد عبر عدة أندية، فلا نادي واحد
    #: يصف إشعاره. عزل الولي يعتمد على ``user_id`` لا على ``club_id``.
    club_id: Mapped[int | None] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="معرّف النادي (NULL = إشعار عابر للأندية)",
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    type: Mapped[NotificationType] = mapped_column(
        default=NotificationType.SYSTEM, nullable=False, index=True
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        default=NotificationChannel.IN_APP, nullable=False
    )

    title: Mapped[str] = mapped_column(String(250), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)

    #: رابط داخل المنصة عند النقر
    link: Mapped[str | None] = mapped_column(String(300))

    #: بيانات إضافية للعرض المرن
    data: Mapped[dict] = mapped_column(JSON, default=dict)

    priority: Mapped[str] = mapped_column(
        String(10), default="normal", index=True, comment="normal / high / urgent"
    )

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, index=True)
    delivery_status: Mapped[NotificationStatus] = mapped_column(
        default=NotificationStatus.PENDING, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    #: محاولة تجديد فريدة لتفادي تكرار نفس الإشعار
    dedupe_key: Mapped[str | None] = mapped_column(
        String(150), index=True, comment="مفتاح منع التكرار"
    )

    user: Mapped["User | None"] = relationship(
        "User", back_populates="notifications", foreign_keys=[user_id]
    )

    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_club_type", "club_id", "type"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.title

    @property
    def type_label(self) -> str:
        return {
            "absence": "غياب",
            "payment_due": "اشتراك مستحق",
            "payment_received": "دفعة مسجلة",
            "invoice_overdue": "فاتورة متأخرة",
            "evaluation": "تقييم جديد",
            "announcement": "إعلان",
            "registration": "طلب تسجيل",
            "renewal": "تجديد اشتراك",
            "system": "إشعار نظامي",
        }.get(self.type, self.type)

    @property
    def icon(self) -> str:
        return {
            "absence": "fa-user-slash",
            "payment_due": "fa-money-bill",
            "payment_received": "fa-check-circle",
            "invoice_overdue": "fa-exclamation-triangle",
            "evaluation": "fa-star",
            "announcement": "fa-bullhorn",
            "registration": "fa-user-plus",
            "renewal": "fa-calendar-check",
            "system": "fa-info-circle",
        }.get(self.type, "fa-info-circle")


# =====================================================================
#  سجل التدقيق (Audit Log)
# =====================================================================
class AuditLog(TenantMixin, BaseModel):
    """
    سجل تدقيق لكل عملية حساسة.

    لا يُعدَّل ولا يُحذف أبداً -- يوفّر مسار مراجعة كامل عند النزاعات.
    """

    __tablename__ = "audit_logs"

    __parent_scope__ = "user_id:own"

    #: يُسمح بـ NULL: بعض الأحداث على مستوى المنصة لا تتبع نادياً --
    #: محاولات الدخول الفاشلة، وإجراءات مدير المنصة على نوادٍ متعددة.
    club_id: Mapped[int | None] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="معرّف النادي (NULL = حدث على مستوى المنصة)",
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    user_role: Mapped[str | None] = mapped_column(String(30))
    user_name: Mapped[str | None] = mapped_column(String(150))

    action: Mapped[AuditAction] = mapped_column(
        nullable=False, index=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(80), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)

    #: وصف بشري للعملية
    description: Mapped[str | None] = mapped_column(Text)

    #: تفاصيل إضافية (قيم قبل/بعد، معطيات الطلب)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)

    ip_address: Mapped[str | None] = mapped_column(String(45), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    endpoint: Mapped[str | None] = mapped_column(String(300))

    #: هل نجحت العملية؟
    success: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("ix_audit_club_created", "club_id", "created_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_user_action", "user_id", "action"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.action} على {self.entity_type}#{self.entity_id}"


# =====================================================================
#  الغلاف اليومي (Digest)
# =====================================================================
class DailyDigest(TenantMixin, BaseModel):
    """ملخص يومي مجمّع يُرسل للإدارة (-performance مُجمَّع مرة واحدة)."""

    __tablename__ = "daily_digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)

    total_athletes: Mapped[int] = mapped_column(Integer, default=0)
    present_today: Mapped[int] = mapped_column(Integer, default=0)
    absent_today: Mapped[int] = mapped_column(Integer, default=0)
    new_registrations: Mapped[int] = mapped_column(Integer, default=0)
    income_today: Mapped[int] = mapped_column(Integer, default=0)
    outstanding_amount: Mapped[int] = mapped_column(Integer, default=0)

    summary: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_digest_club_date", "club_id", "digest_date"),
    )