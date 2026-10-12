"""
حزمة النماذج (Models Package)
=============================
تستورد كل النماذج هنا حتى يسجّلها SQLAlchemy في ``Base.metadata``.
"""

from app.models.base import (
    AuditMixin,
    Base,
    BaseModel,
    SoftDeleteMixin,
    TenantMixin,
    TenantOwned,
    TimestampMixin,
    utcnow,
)
from app.models.content import (
    AuditLog,
    ClubNews,
    DailyDigest,
    Notification,
    RegistrationRequest,
)
from app.models.enums import (
    AttendanceMethod,
    AttendanceStatus,
    AuditAction,
    ClubStatus,
    EvaluationType,
    ExpenseCategory,
    Gender,
    InvoiceStatus,
    Level,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    PaymentDirection,
    PaymentMethod,
    PaymentStatus,
    PeriodType,
    PlatformPlanPeriod,
    RegistrationStatus,
    SessionStatus,
    SportCategory,
    StaffJobTitle,
    Status,
    SubscriptionStatus,
    UserRole,
)
from app.models.finance import Expense, FeePlan, Invoice, Payment
from app.models.people import Athlete, ParentLink, StaffMember
from app.models.platform import (
    Club,
    ClubSport,
    ClubSubscription,
    PlatformPlan,
    Sport,
    User,
)
from app.models.training import (
    Attendance,
    Evaluation,
    Group,
    GroupAthlete,
    TrainingSession,
)

__all__ = [
    # الأساس
    "Base",
    "BaseModel",
    "TimestampMixin",
    "TenantMixin",
    "TenantOwned",
    "SoftDeleteMixin",
    "AuditMixin",
    # المنصة
    "User",
    "Club",
    "ClubSport",
    "ClubSubscription",
    "PlatformPlan",
    "Sport",
    # الأشخاص
    "Athlete",
    "ParentLink",
    "StaffMember",
    # التدريب
    "Group",
    "GroupAthlete",
    "TrainingSession",
    "Attendance",
    "Evaluation",
    # المالية
    "FeePlan",
    "Invoice",
    "Payment",
    "Expense",
    # المحتوى
    "ClubNews",
    "RegistrationRequest",
    "Notification",
    "AuditLog",
    "DailyDigest",
    # التعدادات
    "UserRole",
    "Status",
    "ClubStatus",
    "SportCategory",
    "Gender",
    "Level",
    "SessionStatus",
    "AttendanceStatus",
    "AttendanceMethod",
    "EvaluationType",
    "PeriodType",
    "InvoiceStatus",
    "PaymentMethod",
    "PaymentStatus",
    "ExpenseCategory",
    "PaymentDirection",
    "PlatformPlanPeriod",
    "SubscriptionStatus",
    "RegistrationStatus",
    "NotificationType",
    "NotificationChannel",
    "NotificationStatus",
    "AuditAction",
    "StaffJobTitle",
]