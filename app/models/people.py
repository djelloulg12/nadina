"""
نماذج الأشخاص (People Models)
=============================
الرياضيون، الأولياء، وطاقم النادي (مدربون وإداريون).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
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

from app.models.base import BaseModel, SoftDeleteMixin, TenantMixin
from app.models.enums import Gender, Level, StaffJobTitle, Status, UserRole

if TYPE_CHECKING:
    from app.models.finance import FeePlan, Invoice, Payment
    from app.models.platform import Club, Sport, User
    from app.models.training import (
        Attendance,
        Evaluation,
        Group,
        GroupAthlete,
        TrainingSession,
    )


# =====================================================================
#  الرياضيون
# =====================================================================
class Athlete(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    الرياضي (المنخرط في النادي).

    كل رياضي ينتمي لنادٍ واحد، ويمكن ربطه بولي أو أكثر.
    """

    __tablename__ = "athletes"

    #: للوالين: الرياضيون هم أنفسهم
    __parent_scope__ = "id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: كود فريد للبطاقة QR
    card_code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True, comment="كود البطاقة"
    )

    # --- البيانات الشخصية ---
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    full_name: Mapped[str] = mapped_column(
        String(160), nullable=False, index=True, comment="الاسم الكامل للبحث"
    )

    birth_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    gender: Mapped[Gender | None] = mapped_column(index=True)
    nationality: Mapped[str | None] = mapped_column(String(80))
    national_id: Mapped[str | None] = mapped_column(String(30), comment="رقم الهوية")
    blood_type: Mapped[str | None] = mapped_column(String(5))

    # --- الاتصال ---
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(255))
    wilaya: Mapped[str | None] = mapped_column(String(100))

    # --- بيانات ولي الأمر ---
    guardian_name: Mapped[str | None] = mapped_column(String(160))
    guardian_phone: Mapped[str | None] = mapped_column(String(20), index=True)
    guardian_email: Mapped[str | None] = mapped_column(String(255))
    guardian_relation: Mapped[str | None] = mapped_column(
        String(30), comment="صلة القرابة بالأب/الأم"
    )

    emergency_contact_name: Mapped[str | None] = mapped_column(String(160))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))

    # --- الصورة والسيرة ---
    photo_url: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    # --- البيانات الطبية (مشفّرة منطقياً، لا تظهر للمدرب إلا بصلاحية) ---
    medical_notes: Mapped[str | None] = mapped_column(Text, comment="ملاحظات طبية")
    allergies: Mapped[str | None] = mapped_column(Text, comment="الحساسية")
    chronic_conditions: Mapped[str | None] = mapped_column(Text, comment="أمراض مزمنة")
    injuries: Mapped[str | None] = mapped_column(Text, comment="الإصابات السابقة")
    medical_doc_url: Mapped[str | None] = mapped_column(String(255))
    has_medical_clearance: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="هل لديه شهادة لياقة طبية؟"
    )

    # --- الرياضي ---
    sport_id: Mapped[int | None] = mapped_column(
        ForeignKey("sports.id", ondelete="SET NULL"), index=True
    )
    preferred_position: Mapped[str | None] = mapped_column(String(50))
    level: Mapped[Level] = mapped_column(default=Level.BEGINNER, index=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer)

    # --- الحالة ---
    status: Mapped[Status] = mapped_column(
        default=Status.ACTIVE, nullable=False, index=True
    )
    joined_at: Mapped[dt.date | None] = mapped_column(Date, comment="تاريخ التسجيل")
    left_at: Mapped[dt.date | None] = mapped_column(Date, comment="تاريخ المغادرة")

    #: رابط المستخدم إن كان الرياضي لديه حساب دخول
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )

    #: نسبة حضور آخر 30 يوماً (تُحسب دورياً)
    attendance_rate: Mapped[float] = mapped_column(
        Numeric(5, 2), default=Decimal("0.00"), comment="نسبة الحضور %"
    )

    # --- العلاقات ---
    club: Mapped["Club"] = relationship("Club", back_populates="athletes", lazy="selectin")
    sport: Mapped["Sport | None"] = relationship("Sport", lazy="selectin")
    groups: Mapped[list["GroupAthlete"]] = relationship(
        "GroupAthlete", back_populates="athlete", cascade="all, delete-orphan",
        lazy="selectin",
    )
    parents: Mapped[list["ParentLink"]] = relationship(
        "ParentLink", back_populates="athlete", cascade="all, delete-orphan"
    )
    attendance_records: Mapped[list["Attendance"]] = relationship(
        "Attendance", back_populates="athlete", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        "Evaluation", back_populates="athlete", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["Invoice"]] = relationship(
        "Invoice", back_populates="athlete", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="athlete", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_athletes_club_status", "club_id", "status"),
        Index("ix_athletes_club_name", "club_id", "full_name"),
        Index("ix_athletes_name_search", "full_name"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.full_name

    # -----------------------------------------------------------------
    @property
    def age(self) -> int | None:
        """العمر بالسنوات (محسوب من تاريخ الميلاد)."""
        if self.birth_date is None:
            return None
        today = dt.date.today()
        return (
            today.year
            - self.birth_date.year
            - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
        )

    @property
    def age_group(self) -> str:
        """الفئة العمرية للعرض."""
        years = self.age
        if years is None:
            return "غير محدد"
        if years <= 7:
            return "أقل من 8 سنوات"
        if years <= 10:
            return "8 - 10 سنوات"
        if years <= 13:
            return "11 - 13 سنة"
        if years <= 16:
            return "14 - 16 سنة"
        if years <= 19:
            return "17 - 19 سنة"
        return "فوق 20 سنة"

    @property
    def gender_label(self) -> str:
        return {"male": "ذكر", "female": "أنثى"}.get(self.gender or "", "غير محدد")

    @property
    def has_unpaid_invoices(self) -> bool:
        """هل عليه فواتير غير مسددة؟"""
        return any(
            inv.status in ("unpaid", "overdue", "partial") for inv in self.invoices
        )

    @property
    def debt_amount(self) -> float:
        """إجمالي الديون المستحقة."""
        return float(
            sum(
                (inv.amount_due or Decimal("0"))
                for inv in self.invoices
                if inv.status in ("unpaid", "overdue", "partial")
            )
        )


# =====================================================================
#  ربط الولي بأبنائه
# =====================================================================
class ParentLink(TenantMixin, BaseModel):
    """
    رابط بين حساب الولي ورياضي.

    ``club_id`` هنا يسمح بربط الولي بأبنائه في نفس النادي أولاً، بينما
    تبقى علاقة الولي بأبنائه في أندية أخرى عبر روابط منفصلة -- وهذا يحقق
    "حساب الولي الموحد" عبر الأندية.
    """

    __tablename__ = "parent_links"

    #: للوالين: مرتبطون عبر الرياضي المرتبط
    __parent_scope__ = "athlete_id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="معرّف حساب الولي",
    )
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="معرّف الابن/الابنة",
    )

    relationship_type: Mapped[str | None] = mapped_column(
        String(30), comment="الأب / الأم / الأخ الأكبر..."
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="هل هو الولي الأساسي؟"
    )
    is_payer: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="هل مسؤول عن الدفع؟"
    )
    can_pickup: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="مسموح بالاستلام من المدرب؟"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    #: هل تم إرسال دعوة الحساب لهذا الولي؟
    invite_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    invite_accepted: Mapped[bool] = mapped_column(Boolean, default=False)

    parent: Mapped["User"] = relationship(
        "User", back_populates="parent_links", lazy="selectin"
    )
    athlete: Mapped["Athlete"] = relationship("Athlete", back_populates="parents")

    __table_args__ = (
        UniqueConstraint("parent_id", "athlete_id", name="uq_parent_athlete"),
        Index("ix_parent_links_parent_athlete", "parent_id", "athlete_id"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"ولي {self.parent_id} -> رياضي {self.athlete_id}"


# =====================================================================
#  طاقم النادي (الموارد البشرية)
# =====================================================================
class StaffMember(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    عضو في طاقم النادي (مدرب، إداري، إداري استقبال).

    يرتبط بمستخدم (``User``) بعلاقة واحد-لواحد، لكنMembership مستقلة
    لأن نفس المدرب يمكن أن يكون عضواً في أكثر من نادي.
    """

    __tablename__ = "staff_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="معرّف حساب المستخدم",
    )

    # --- بيانات الوظيفة ---
    job_title: Mapped[StaffJobTitle] = mapped_column(
        default=StaffJobTitle.OTHER, nullable=False, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        nullable=False,
        index=True,
        comment="الرتبة (admin / coach / receptionist)",
    )

    specialty: Mapped[str | None] = mapped_column(String(150), comment="التخصص")
    qualifications: Mapped[str | None] = mapped_column(Text, comment="الشهادات")
    bio: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(String(255))

    sport_id: Mapped[int | None] = mapped_column(
        ForeignKey("sports.id", ondelete="SET NULL"), index=True
    )

    # --- التوظيف ---
    hire_date: Mapped[dt.date | None] = mapped_column(Date)
    contract_end: Mapped[dt.date | None] = mapped_column(Date)
    employment_type: Mapped[str | None] = mapped_column(
        String(20), comment="دوام كامل / جزئي"
    )
    salary: Mapped[float | None] = mapped_column(
        Numeric(10, 2), comment="الراتب الشهري"
    )
    bonus: Mapped[float | None] = mapped_column(Numeric(10, 2), comment="علاوة")

    #: صلاحية إضافية على مستوى النادي (مثلاً: مدير مالي)
    permissions_json: Mapped[dict] = mapped_column(
        JSON, default=dict, comment="صلاحيات إضافية"
    )

    status: Mapped[Status] = mapped_column(
        default=Status.ACTIVE, nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    # --- العلاقات ---
    club: Mapped["Club"] = relationship("Club", back_populates="staff", lazy="selectin")
    #: نُحمّل المستخدم تلقائياً لأن ``StaffMember.name`` تعبر إليه دائماً،
    #: والتحميل الكسول في سياق async يرمي ``MissingGreenlet``.
    user: Mapped["User"] = relationship(
        "User", back_populates="staff_memberships", lazy="selectin"
    )
    sport: Mapped["Sport | None"] = relationship("Sport", lazy="selectin")

    groups_coached: Mapped[list["Group"]] = relationship(
        "Group", back_populates="coach"
    )

    __table_args__ = (
        UniqueConstraint("club_id", "user_id", name="uq_staff_club_user"),
        Index("ix_staff_club_role", "club_id", "role"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"عضو طاقم {self.id} ({self.job_title})"

    @property
    def name(self) -> str:
        """اسم العضو من حساب المستخدم المرتبط."""
        return self.user.full_name if self.user else f"عضو-{self.id}"