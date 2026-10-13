"""
النماذج المركزية للمنصة (Platform Models)
==========================================
جدول ``users`` وصفحة ``clubs`` وخطط الاشتراك -- هذه الجداول **مشتركة بين
كل الأندية** ولا تحمل ``club_id``، لأنها تمثل البنية التحتية للمنصة نفسها.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, unique_text
from app.models.enums import (
    ClubStatus,
    PlatformPlanPeriod,
    SportCategory,
    Status,
    SubscriptionStatus,
    UserRole,
)

if TYPE_CHECKING:
    from app.models.content import ClubNews, Notification, RegistrationRequest
    from app.models.finance import Expense, FeePlan, Invoice, Payment
    from app.models.people import Athlete, ParentLink, StaffMember
    from app.models.training import Attendance, Evaluation, Group, TrainingSession


# =====================================================================
#  الرياضات
# =====================================================================
class Sport(BaseModel):
    """جدول الرياضات -- مرجعي مشترك بين كل الأندية."""

    __tablename__ = "sports"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(100), nullable=False, comment="الاسم بالعربية")
    name_en: Mapped[str | None] = mapped_column(String(100), comment="الاسم بالإنجليزية")
    slug: Mapped[str] = mapped_column(
        String(120), unique=True, nullable=False, index=True, comment="معرّف للرابط"
    )
    icon: Mapped[str | None] = mapped_column(String(50), comment="أيقونة (emoji أو اسم أيقونة)")
    category: Mapped[SportCategory] = mapped_column(
        default=SportCategory.TEAM, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # علاقات
    clubs: Mapped[list["Club"]] = relationship(
        secondary="club_sports", back_populates="sports"
    )

    __table_args__ = (
        Index("ix_sports_category_active", "category", "is_active"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.name_ar


# =====================================================================
#  المستخدمون
# =====================================================================
class User(BaseModel):
    """
    جدول المستخدمين الموحّد.

    يشمل: مدير المنصة، مدير النادي، الإداري، المدرب، الولي، والرياضي.
    ``role`` يحدد الصلاحيات، و ``club_id`` (إن وُجد) يحدد النادي
    الافتراضي للمستخدم من الطاقم.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(
        String(255), comment="البريد الإلكتروني (مفتاح دخول اختياري)"
    )
    phone: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, comment="رقم الهاتف (مفتاح دخول أساسي)"
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(
        nullable=False, index=True, comment="رتبة المستخدم"
    )

    #: النادي الافتراضي (للأدوار المرتبطة بنادٍ). الولي قد يكون بلا نادي.
    club_id: Mapped[int | None] = mapped_column(
        ForeignKey("clubs.id", ondelete="SET NULL"), index=True
    )

    avatar_url: Mapped[str | None] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(10), default="ar")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    #: تتبع أمان: قفل الحساب بعد محاولات فاشلة
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # إعدادات إضافية (لون التمييز، التفضيلات، إعدادات الإشعارات)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)

    # ملاحظة: العلاقة مع الملف الرياضي في الاتجاه العاكس فقط
    # (``athletes.user_id``) لتفادي دورة مراجع Foreign Keys في القاعدة.

    # --- العلاقات ---
    club: Mapped["Club | None"] = relationship(
        "Club", back_populates="users", foreign_keys=[club_id]
    )
    staff_memberships: Mapped[list["StaffMember"]] = relationship(
        "StaffMember", back_populates="user", cascade="all, delete-orphan"
    )
    parent_links: Mapped[list["ParentLink"]] = relationship(
        "ParentLink", back_populates="parent", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # MariaDB/MySQL: the unique index is limited to the first
        # 191 characters (764 bytes utf8mb4), staying under
        # InnoDB's legacy 767-byte prefix limit. Other dialects
        # ignore the prefix and keep the full-width index.
        unique_text("uq_users_email", "email"),
        Index("ix_users_role_active", "role", "is_active"),
        Index("ix_users_club_role", "club_id", "role"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.full_name} ({self.role.value})"

    @property
    def display_name(self) -> str:
        """الاسم المعروض في الواجهة."""
        return self.full_name.strip()

    @property
    def initials(self) -> str:
        """الأحرف الأولى للعرض في الصورة الرمزية."""
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return "؟"
        if len(parts) == 1:
            return parts[0][0]
        return f"{parts[0][0]}{parts[-1][0]}"

    @property
    def is_locked(self) -> bool:
        """هل الحساب مقفل حالياً؟"""
        if self.locked_until is None:
            return False
        from app.core.db import utcnow
        return self.locked_until > utcnow()

    @property
    def is_platform_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN


# =====================================================================
#  خطط اشتراك المنصة
# =====================================================================
class PlatformPlan(BaseModel):
    """خطة اشتراك النادي في المنصة (يحددها مدير المنصة)."""

    __tablename__ = "platform_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    period: Mapped[PlatformPlanPeriod] = mapped_column(
        default=PlatformPlanPeriod.MONTHLY, nullable=False
    )
    duration_days: Mapped[int] = mapped_column(
        Integer, default=30, nullable=False, comment="مدة الخطة بالأيام"
    )

    #: حدود الاستخدام (None = بلا حد)
    max_athletes: Mapped[int | None] = mapped_column(Integer)
    max_groups: Mapped[int | None] = mapped_column(Integer)
    max_staff: Mapped[int | None] = mapped_column(Integer)

    #: الميزات المُفعّلة لهذه الخطة (قائمة نصوص)
    features: Mapped[list] = mapped_column(JSON, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    clubs: Mapped[list["Club"]] = relationship("Club", back_populates="plan")

    def __str__(self) -> str:  # pragma: no cover
        return self.name


# =====================================================================
#  الأندية
# =====================================================================
class Club(BaseModel):
    """
    النادي -- وحدة العزل (Tenant) في المنصة.

    هذا الجدول نفسه لا يحمل ``club_id`` (هو الجذر)، وكل الجداول الأخرى
    المملوكة لنادٍ تشير إليه.
    """

    __tablename__ = "clubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(
        String(220), nullable=False, comment="معرّف الرابط"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="نبذة عن النادي")

    # --- الهوية البصرية ---
    logo_url: Mapped[str | None] = mapped_column(String(255))
    cover_url: Mapped[str | None] = mapped_column(String(255), comment="صورة الغلاف")
    primary_color: Mapped[str | None] = mapped_column(
        String(20), default="#0F766E", comment="اللون الأساسي"
    )

    # --- الموقع والاتصال ---
    address: Mapped[str | None] = mapped_column(String(255))
    wilaya: Mapped[str | None] = mapped_column(String(100), index=True, comment="الولاية")
    commune: Mapped[str | None] = mapped_column(String(100), comment="البلدية")
    city: Mapped[str | None] = mapped_column(String(100), index=True, comment="المدينة")
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))

    phone: Mapped[str | None] = mapped_column(String(20))
    phone_2: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(255))
    facebook_url: Mapped[str | None] = mapped_column(String(255))
    instagram_url: Mapped[str | None] = mapped_column(String(255))

    # --- الفئات المستهدفة ---
    min_age: Mapped[int | None] = mapped_column(Integer, comment="أصغر فئة عمرية")
    max_age: Mapped[int | None] = mapped_column(Integer, comment="أكبر فئة عمرية")
    gender_mixed: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- الحالة والاشتراك ---
    status: Mapped[ClubStatus] = mapped_column(
        default=ClubStatus.PENDING, nullable=False, index=True
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_plans.id", ondelete="SET NULL"), index=True
    )
    subscription_start: Mapped[date | None] = mapped_column(Date)
    subscription_end: Mapped[date | None] = mapped_column(Date, index=True)

    # --- الفوترة داخل النادي ---
    currency: Mapped[str] = mapped_column(String(10), default="DZD")
    default_monthly_price: Mapped[float | None] = mapped_column(Numeric(10, 2))

    # --- إعدادات العرض العام ---
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, comment="هل يظهر في البوابة العامة؟"
    )
    accepts_online_registration: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="هل يقبل طلبات التسجيل عبر الموقع؟"
    )
    rating: Mapped[float] = mapped_column(
        Numeric(3, 2), default=0, comment="التقييم العام (0-5)"
    )
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    members_count: Mapped[int] = mapped_column(Integer, default=0, comment="عدد الأعضاء")

    # --- الإعدادات ---
    settings: Mapped[dict] = mapped_column(
        JSON, default=dict, comment="إعدادات النادي (ساعات العمل، التنبيهات...)"
    )
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- العلاقات ---
    plan: Mapped["PlatformPlan | None"] = relationship(
        "PlatformPlan", back_populates="clubs"
    )
    sports: Mapped[list["Sport"]] = relationship(
        "Sport", secondary="club_sports", back_populates="clubs", lazy="selectin"
    )
    users: Mapped[list["User"]] = relationship(
        "User", back_populates="club", foreign_keys="User.club_id"
    )
    staff: Mapped[list["StaffMember"]] = relationship(
        "StaffMember", back_populates="club", cascade="all, delete-orphan"
    )
    athletes: Mapped[list["Athlete"]] = relationship(
        "Athlete", back_populates="club", cascade="all, delete-orphan"
    )
    groups: Mapped[list["Group"]] = relationship(
        "Group", back_populates="club", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["TrainingSession"]] = relationship(
        "TrainingSession", back_populates="club", cascade="all, delete-orphan"
    )
    fees: Mapped[list["FeePlan"]] = relationship(
        "FeePlan", back_populates="club", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["ClubSubscription"]] = relationship(
        "ClubSubscription", back_populates="club", cascade="all, delete-orphan"
    )
    news: Mapped[list["ClubNews"]] = relationship(
        "ClubNews", back_populates="club", cascade="all, delete-orphan"
    )
    registration_requests: Mapped[list["RegistrationRequest"]] = relationship(
        "RegistrationRequest", back_populates="club", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # MariaDB/MySQL: the unique index is limited to the first
        # 191 characters (764 bytes utf8mb4), staying under
        # InnoDB's legacy 767-byte prefix limit. Other dialects
        # ignore the prefix and keep the full-width index.
        unique_text("uq_clubs_slug", "slug"),
        Index("ix_clubs_status_public", "status", "is_public"),
        Index("ix_clubs_location", "wilaya", "city"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    # -----------------------------------------------------------------
    @property
    def subscription_days_left(self) -> int | None:
        """عدد الأيام المتبقية على الاشتراك (``None`` إذا كان غير محدد)."""
        if self.subscription_end is None:
            return None
        from datetime import date as _date

        return (self.subscription_end - _date.today()).days

    @property
    def is_subscription_expired(self) -> bool:
        if self.subscription_end is None:
            return False
        from datetime import date as _date

        return self.subscription_end < _date.today()

    @property
    def is_subscription_expiring(self) -> bool:
        """هل الاشتراك على وشك الانتهاء (خلال مدة التنبيه)؟"""
        from app.core.config import settings

        days = self.subscription_days_left
        return days is not None and 0 <= days <= settings.RENEWAL_ALERT_DAYS

    @property
    def is_operational(self) -> bool:
        """هل النادي مُفعّل ويقبل العمليات؟"""
        return self.status == ClubStatus.ACTIVE and not self.maintenance_mode

    @property
    def location_label(self) -> str:
        """وصف الموقع للعرض."""
        parts = [p for p in (self.commune, self.city, self.wilaya) if p]
        return " - ".join(parts)


# =====================================================================
#  جدول الربط بين النادي والرياضة
# =====================================================================
class ClubSport(BaseModel):
    """جدول ربط متعدد-متعدد بين الأندية والرياضات."""

    __tablename__ = "club_sports"

    club_id: Mapped[int] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"), primary_key=True
    )
    sport_id: Mapped[int] = mapped_column(
        ForeignKey("sports.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="الرياضة الأساسية للنادي"
    )
    price_from: Mapped[float | None] = mapped_column(Numeric(10, 2))

    __table_args__ = (Index("ix_club_sports_sport", "sport_id"),)


# =====================================================================
#  اشتراك النادي في المنصة
# =====================================================================
class ClubSubscription(BaseModel):
    """سجل اشتراك النادي في المنصة (الفوترة المركزية)."""

    __tablename__ = "club_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    club_id: Mapped[int] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_plans.id", ondelete="SET NULL")
    )

    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        default=SubscriptionStatus.PENDING, nullable=False, index=True
    )

    payment_method: Mapped[str | None] = mapped_column(String(30))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    invoice_number: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)

    club: Mapped["Club"] = relationship("Club", back_populates="subscriptions")
    plan: Mapped["PlatformPlan | None"] = relationship("PlatformPlan")

    __table_args__ = (
        UniqueConstraint("club_id", "period_start", name="uq_club_sub_period"),
        Index("ix_club_sub_status_end", "status", "period_end"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"اشتراك {self.club_id}: {self.period_start} -> {self.period_end}"