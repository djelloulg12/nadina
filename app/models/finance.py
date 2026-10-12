"""
النماذج المالية (Finance Models)
===============================
خطط الاشتراك، الفواتير، الدفعات، والمصاريف -- قلب نظام تسيير النادي.
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

from app.models.base import BaseModel, SoftDeleteMixin, TenantMixin, utcnow
from app.models.enums import (
    ExpenseCategory,
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    PeriodType,
    Status,
)

if TYPE_CHECKING:
    from app.models.people import Athlete, StaffMember
    from app.models.platform import Club, Sport, User


# =====================================================================
#  خطط الاشتراك
# =====================================================================
class FeePlan(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    خطة اشتراك يحددها النادي لرياضيه أو لفوج.

    أمثلة: "اشتراك شهري -Classes كرة القدم - فئة 8-10 سنوات - 2500 دج".
    """

    __tablename__ = "fee_plans"

    __parent_scope__ = "club_id:clubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # --- السعر والدورية ---
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    period: Mapped[PeriodType] = mapped_column(
        default=PeriodType.MONTHLY, nullable=False, index=True
    )
    duration_days: Mapped[int] = mapped_column(
        Integer, default=30, nullable=False, comment="مدة الاشتراك بالأيام"
    )
    #: عدد الفواتير التي تُصدر تلقائياً عند الاشتراك
    installments: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="عدد الأقساط"
    )

    # --- الفئة المستهدفة ---
    sport_id: Mapped[int | None] = mapped_column(
        ForeignKey("sports.id", ondelete="SET NULL"), index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), index=True
    )
    age_group: Mapped[str | None] = mapped_column(String(50), index=True)
    gender: Mapped[str | None] = mapped_column(String(10))

    # --- خصومات ---
    discount_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), comment="خصم دائم %"
    )
    sibling_discount: Mapped[float] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), comment="خصم الإخوة %"
    )

    #: هل الخطة تتطلب دفعة أولى (تسجيل)؟
    registration_fee: Mapped[float | None] = mapped_column(
        Numeric(10, 2), comment="رسوم التسجيل"
    )

    status: Mapped[Status] = mapped_column(
        default=Status.ACTIVE, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    #: ترتيب العرض في القوائم
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    club: Mapped["Club"] = relationship("Club", back_populates="fees")
    sport: Mapped["Sport | None"] = relationship("Sport")
    group: Mapped["Group | None"] = relationship("Group")

    __table_args__ = (
        Index("ix_fee_plans_club_active", "club_id", "is_active"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    # -----------------------------------------------------------------
    @property
    def period_label(self) -> str:
        return {
            "monthly": "شهري",
            "quarterly": "ثلاثي",
            "semi_annual": "نصف سنوي",
            "annual": "سنوي",
            "one_time": "دفعة واحدة",
        }.get(self.period, self.period)

    @property
    def price_label(self) -> str:
        """السعر بصيغة عرض مع العملة."""
        from app.core.config import settings

        return f"{float(self.amount):,.0f} دج"

    @property
    def installments_amount(self) -> float:
        """قيمة القسط الواحد."""
        if self.installments <= 1:
            return float(self.amount)
        return round(float(self.amount) / self.installments, 2)


# =====================================================================
#  الفواتير
# =====================================================================
class Invoice(SoftDeleteMixin, TenantMixin, BaseModel):
    """
    فاتورة اشتراك رياضي.

    الفاتورة هي المرجع المالي الرسمي: كل دفعة تُسجَّل مقابل فاتورة، وحالة
    الفاتورة تُحسب تلقائياً من مجموع الدفعات.
    """

    __tablename__ = "invoices"

    __parent_scope__ = "athlete_id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="رقم الفاتورة"
    )

    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("fee_plans.id", ondelete="SET NULL"), index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), index=True
    )

    # --- الفترة ---
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    issue_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    due_date: Mapped[dt.date] = mapped_column(
        Date, nullable=False, comment="تاريخ الاستحقاق"
    )

    # --- المبالغ ---
    subtotal: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="المجموع قبل الخصم"
    )
    discount: Mapped[float] = mapped_column(
        Numeric(10, 2), default=Decimal("0"), nullable=False
    )
    total: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="المبلغ الإجمالي"
    )
    paid: Mapped[float] = mapped_column(
        Numeric(10, 2), default=Decimal("0"), nullable=False, comment="المبلغ المدفوع"
    )

    installment_no: Mapped[int | None] = mapped_column(
        Integer, comment="رقم القسط (1 من 3 مثلاً)"
    )
    is_installment: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[InvoiceStatus] = mapped_column(
        default=InvoiceStatus.UNPAID, nullable=False, index=True
    )

    notes: Mapped[str | None] = mapped_column(Text)
    #: سبب الإلغاء أو الإعفاء
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    #: آخر تنبيه تم إرساله
    last_reminder_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # --- العلاقات ---
    athlete: Mapped["Athlete"] = relationship(
        "Athlete", back_populates="invoices", lazy="selectin"
    )
    fee_plan: Mapped["FeePlan | None"] = relationship("FeePlan", lazy="selectin")
    group: Mapped["Group | None"] = relationship("Group")
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="invoice", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_invoices_club_status", "club_id", "status"),
        Index("ix_invoices_athlete_status", "athlete_id", "status"),
        Index("ix_invoices_due_date", "due_date", "status"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"فاتورة {self.invoice_number}"

    # -----------------------------------------------------------------
    @property
    def amount_due(self) -> Decimal:
        """المبلغ المتبقي غير المدفوع."""
        return max(Decimal("0"), Decimal(str(self.total)) - Decimal(str(self.paid)))

    @property
    def is_paid(self) -> bool:
        return self.status == InvoiceStatus.PAID

    @property
    def is_overdue(self) -> bool:
        """هل تجاوزت الفاتورة تاريخ الاستحقاق وهي غير مدفوعة؟"""
        if self.is_paid or self.status == InvoiceStatus.CANCELLED:
            return False
        return self.due_date < dt.date.today()

    @property
    def days_overdue(self) -> int:
        """عدد أيام التأخير (0 إن لم تتأخر)."""
        if not self.is_overdue:
            return 0
        return (dt.date.today() - self.due_date).days

    @property
    def status_label(self) -> str:
        return {
            "unpaid": "غير مدفوعة",
            "partial": "مدفوعة جزئياً",
            "paid": "مدفوعة",
            "overdue": "متأخرة",
            "cancelled": "ملغاة",
        }.get(self.status, self.status)

    @property
    def period_label(self) -> str:
        return f"{self.period_start.strftime('%Y/%m/%d')} - {self.period_end.strftime('%Y/%m/%d')}"

    def recalculate_status(self) -> InvoiceStatus:
        """
        إعادة حساب حالة الفاتورة من المبالغ والتاريخ.

        تُستدعى بعد كل إضافة دفعة أو تعديل مبلغ.
        """
        total = Decimal(str(self.total))
        paid = Decimal(str(self.paid))

        if total <= 0 or paid >= total:
            self.status = InvoiceStatus.PAID
        elif paid > 0:
            self.status = InvoiceStatus.PARTIAL
        elif self.due_date < dt.date.today():
            self.status = InvoiceStatus.OVERDUE
        else:
            self.status = InvoiceStatus.UNPAID

        return self.status


# =====================================================================
#  الدفعات
# =====================================================================
class Payment(TenantMixin, BaseModel):
    """دفعة مالية مسددة ضد فاتورة."""

    __tablename__ = "payments"

    __parent_scope__ = "athlete_id:athlete"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="رقم الوصل"
    )

    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), index=True
    )
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    paid_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    #: تاريخ الدفع الفعلي (قد يختلف عن paid_at لأجل التقارير)
    payment_date: Mapped[dt.date] = mapped_column(
        Date, default=dt.date.today, index=True
    )

    method: Mapped[PaymentMethod] = mapped_column(
        default=PaymentMethod.CASH, nullable=False, index=True
    )
    status: Mapped[PaymentStatus] = mapped_column(
        default=PaymentStatus.COMPLETED, nullable=False
    )
    reference: Mapped[str | None] = mapped_column(
        String(100), comment="رقم الشيك / المرجع البنكي"
    )

    #: هل تم إرسال الوصل للولي؟
    receipt_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    invoice: Mapped["Invoice | None"] = relationship(
        "Invoice", back_populates="payments"
    )
    athlete: Mapped["Athlete"] = relationship(
        "Athlete", back_populates="payments", lazy="selectin"
    )
    recorded_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[recorded_by_id], lazy="selectin"
    )

    __table_args__ = (
        Index("ix_payments_club_date", "club_id", "payment_date"),
        Index("ix_payments_athlete_date", "athlete_id", "payment_date"),
        Index("ix_payments_status", "club_id", "status"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"دفعة {self.receipt_number}: {self.amount}"

    @property
    def method_label(self) -> str:
        return {
            "cash": "نقداً",
            "bank": "تحويل بنكي",
            "card": "بطاقة",
            "check": "شيك",
            "online": "دفع إلكتروني",
            "waiver": "إعفاء",
        }.get(self.method, self.method)

    @property
    def status_label(self) -> str:
        return {
            "pending": "قيد الانتظار",
            "completed": "مكتملة",
            "failed": "فشلت",
            "refunded": "مستردة",
        }.get(self.status, self.status)


# =====================================================================
#  المصاريف
# =====================================================================
class Expense(TenantMixin, BaseModel):
    """مصروف صرفه النادي."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[ExpenseCategory] = mapped_column(
        default=ExpenseCategory.OTHER, nullable=False, index=True
    )

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    expense_date: Mapped[dt.date] = mapped_column(
        Date, default=dt.date.today, nullable=False, index=True
    )

    #: المصروف مرتبط بعضو من الطاقم (مثل راتب)
    staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_members.id", ondelete="SET NULL"), index=True
    )
    #: المصروف مرتبط بفوج (مثل تجهيزات)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), index=True
    )

    #: مستند إثبات (فاتورة، وصل)
    receipt_url: Mapped[str | None] = mapped_column(String(255))
    vendor: Mapped[str | None] = mapped_column(String(150), comment="المورّد / الجهة")
    reference: Mapped[str | None] = mapped_column(String(100))

    #: هل تمت الموافقة على المصروف؟
    approved: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    staff: Mapped["StaffMember | None"] = relationship("StaffMember")
    recorded_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[recorded_by_id]
    )

    __table_args__ = (
        Index("ix_expenses_club_date", "club_id", "expense_date"),
        Index("ix_expenses_club_category", "club_id", "category"),
    )

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.title}: {self.amount}"

    @property
    def category_label(self) -> str:
        return {
            "rent": "إيجار",
            "salary": "رواتب",
            "equipment": "تجهيزات",
            "maintenance": "صيانة",
            "utilities": "مرافق",
            "marketing": "تسويق",
            "transport": "نقل",
            "medical": "طبي",
            "other": "أخرى",
        }.get(self.category, self.category)