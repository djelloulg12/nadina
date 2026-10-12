"""
التعدادات (Enums)
================
كل الثوابت المعدّدة المستخدمة عبر المنصة، مكتوبة كنصوص مخزّنة في قاعدة
البيانات (``str, Enum``) حتى تبقى مقروءة في SQL وليست أرقاماً غامضة.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """نموذج نصي للتعدادات ليُخزَّن ويُقرأ كـ string."""

    def __str__(self) -> str:  # noqa: D105
        return str(self.value)

    @classmethod
    def values(cls) -> list[str]:
        """كل القيم النصية الصالحة."""
        return [member.value for member in cls]

    @classmethod
    def has(cls, value: str) -> bool:
        """هل القيمة المعطاة عضواً صالحاً في هذا التعداد؟"""
        return value in cls.values()


# =====================================================================
#  الأدوار (Roles)
# =====================================================================
class UserRole(StrEnum):
    """رتب المستخدمين على مستوى المنصة."""

    SUPER_ADMIN = "super_admin"          # مدير المنصة
    CLUB_ADMIN = "club_admin"            # مدير النادي
    RECEPTIONIST = "receptionist"        # إداري / سكرتير
    COACH = "coach"                      # مدرب
    ATHLETE = "athlete"                  # رياضي (اختياري)
    PARENT = "parent"                    # ولي


# =====================================================================
#  حالة الكيانات
# =====================================================================
class Status(StrEnum):
    """الحالة العامة للسجلات."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ClubStatus(StrEnum):
    """حالة النادي داخل المنصة."""

    PENDING = "pending"          # بانتظار موافقة مدير المنصة
    ACTIVE = "active"            # مُفعّل ومفعّل الاشتراك
    SUSPENDED = "suspended"      # موقوف
    EXPIRED = "expired"          # انتهى اشتراكه
    REJECTED = "rejected"


# =====================================================================
#  الرياضات والفئات
# =====================================================================
class SportCategory(StrEnum):
    """تصنيف عام للرياضة (فريق / فردي)."""

    TEAM = "team"
    INDIVIDUAL = "individual"


class Gender(StrEnum):
    """الجنس."""

    MALE = "male"
    FEMALE = "female"


class Level(StrEnum):
    """مستوى الرياضي."""

    BEGINNER = "beginner"
    AMATEUR = "amateur"
    ADVANCED = "advanced"
    ELITE = "elite"


# =====================================================================
#  المواعيد والحضور
# =====================================================================
class SessionStatus(StrEnum):
    """حالة الحصة التدريبية المجدولة."""

    SCHEDULED = "scheduled"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AttendanceStatus(StrEnum):
    """حالة حضور الرياضي في حصة."""

    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"          # غياب بعذر


class AttendanceMethod(StrEnum):
    """طريقة تسجيل الحضور."""

    QR = "qr"
    MANUAL = "manual"
    AUTO = "auto"                # تسجيل تلقائي من تطبيق المدرب
    IMPORT = "import"


# =====================================================================
#  التقييمات
# =====================================================================
class EvaluationType(StrEnum):
    """نوع التقييم الفني."""

    PERIODIC = "periodic"          # دوري
    TECHNICAL = "technical"        # مهارة
    BEHAVIOR = "behavior"          # سلوك / انضباط
    MATCH = "match"                # مباراة / منافسة


# =====================================================================
#  المالية
# =====================================================================
class PeriodType(StrEnum):
    """دورية الاشتراك المالي."""

    MONTHLY = "monthly"            # شهري
    QUARTERLY = "quarterly"        # ثلاثي
    SEMI_ANNUAL = "semi_annual"    # نصف سنوي
    ANNUAL = "annual"              # سنوي
    ONE_TIME = "one_time"          # دفعة واحدة


class InvoiceStatus(StrEnum):
    """حالة الفاتورة."""

    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    """وسيلة الدفع."""

    CASH = "cash"                  # نقداً
    BANK_TRANSFER = "bank"         # تحويل بنكي
    CARD = "card"                  # بطاقة
    CHECK = "check"                # شيك
    ONLINE = "online"              # دفع إلكتروني
    WAIVER = "waiver"              # إعفاء


class PaymentStatus(StrEnum):
    """حالة الدفعة."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class ExpenseCategory(StrEnum):
    """تصنيف المصاريف."""

    RENT = "rent"                  # إيجار
    SALARY = "salary"              # رواتب
    EQUIPMENT = "equipment"        # تجهيزات
    MAINTENANCE = "maintenance"    # صيانة
    UTILITIES = "utilities"        # كهرباء وماء
    MARKETING = "marketing"        # تسويق وإعلان
    TRANSPORT = "transport"        # نقل وانتقالات
    MEDICAL = "medical"            # طبي
    OTHER = "other"


class PaymentDirection(StrEnum):
    """اتجاه الحركة المالية."""

    IN = "in"        # دخل (اشتراكات)
    OUT = "out"      # مصروف


# =====================================================================
#  الاشتراكات على مستوى المنصة
# =====================================================================
class PlatformPlanPeriod(StrEnum):
    """دورية اشتراك النادي في المنصة."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"


class SubscriptionStatus(StrEnum):
    """حالة اشتراك النادي في المنصة."""

    ACTIVE = "active"
    PENDING = "pending"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"


# =====================================================================
#  التسجيلات والطلبات
# =====================================================================
class RegistrationStatus(StrEnum):
    """حالة طلب التسجيل في نادي."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# =====================================================================
#  الإشعارات
# =====================================================================
class NotificationType(StrEnum):
    """أنواع الإشعارات."""

    ABSENCE = "absence"              # غياب
    PAYMENT_DUE = "payment_due"      # اشتراك مستحق
    PAYMENT_RECEIVED = "payment_received"
    INVOICE_OVERDUE = "invoice_overdue"
    EVALUATION = "evaluation"        # تقييم جديد
    ANNOUNCEMENT = "announcement"    # إعلان
    REGISTRATION = "registration"    # طلب تسجيل جديد
    RENEWAL = "renewal"              # تجديد اشتراك النادي
    SYSTEM = "system"                # إشعار نظامي


class NotificationChannel(StrEnum):
    """قناة الإرسال."""

    IN_APP = "in_app"                # داخل المنصة
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class NotificationStatus(StrEnum):
    """حالة الإرسال."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"


# =====================================================================
#  التدقيق
# =====================================================================
class AuditAction(StrEnum):
    """أنواع إجراءات التدقيق."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    EXPORT = "export"
    IMPORT = "import"
    PAYMENT = "payment"
    APPROVE = "approve"
    REJECT = "reject"


class StaffJobTitle(StrEnum):
    """المسمى الوظيفي داخل النادي."""

    HEAD_COACH = "head_coach"        # مدرب رئيسي
    ASSISTANT_COACH = "assistant_coach"
    GOALKEEPER_COACH = "goalkeeper_coach"
    PHYSICAL_TRAINER = "physical_trainer"
    RECEPTIONIST = "receptionist"    # موظف استقبال
    MANAGER = "manager"              # إداري
    MEDICAL = "medical"              # طبي
    OTHER = "other"