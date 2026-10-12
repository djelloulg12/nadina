"""
مخططات التحقق (Pydantic Schemas)
================================
كل ما يدخل من الـ API يمرّ عبر هذه المخططات. الهدف مزدوج:
  1. التحقق من صحة البيانات برسائل عربية واضحة.
  2. منع الحقول غير المرغوبة من التسرب إلى طبقة الخدمات
     (``extra="forbid"``) -- وهو خط دفاع إضافي بعد نظام العزل.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

T = TypeVar("T")


# =====================================================================
#  أساس
# =====================================================================
class BaseSchema(BaseModel):
    """الأساس المشترك لكل المخططات."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class ORMResponse(BaseModel):
    """أساس مخططات الاستجابة (يسمح فقط بقراءة حقول النموذج)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")


# =====================================================================
#  المصادقة
# =====================================================================
PHONE_PATTERN = re.compile(r"^(?:\+?213|0)(?:5|6|7)[0-9]{8}$")


def normalise_phone(value: str) -> str:
    """
    توحيد صيغة رقم الهاتف الجزائري.

    يقبل: 0555123456، +213555123456، 555123456
    يُرجع: 0555123456
    """
    if not value:
        return value
    digits = re.sub(r"[^\d]", "", value)
    if digits.startswith("213"):
        digits = "0" + digits[3:]
    elif len(digits) == 9 and digits[0] in "56789":
        digits = "0" + digits
    return digits


class LoginRequest(BaseSchema):
    """طلب تسجيل الدخول."""

    identifier: str = Field(
        ..., min_length=3, max_length=120,
        description="البريد الإلكتروني أو رقم الهاتف",
    )
    password: str = Field(..., min_length=1, max_length=128)
    remember_me: bool = Field(default=False)

    @field_validator("identifier")
    @classmethod
    def _clean(cls, v: str) -> str:
        return v.strip().lower()


class TokenResponse(ORMResponse):
    """استجابة تسجيل الدخول مع الرموز."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

    user: "UserResponse"
    club: "ClubSummary | None" = None


class RefreshRequest(BaseSchema):
    refresh_token: str = Field(..., min_length=10)


class PasswordChangeRequest(BaseSchema):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        if not any(c.islower() for c in v):
            raise ValueError("كلمة المرور يجب أن تحتوي على حرف صغير")
        if not any(c.isupper() for c in v):
            raise ValueError("كلمة المرور يجب أن تحتوي على حرف كبير")
        if not any(c.isdigit() for c in v):
            raise ValueError("كلمة المرور يجب أن تحتوي على رقم")
        return v

    def validate_match(self) -> None:
        """التأكد من تطابق التأكيد (تُستدعى يدوياً في المسار)."""
        if self.new_password != self.confirm_password:
            raise ValueError("كلمتا المرور غير متطابقتين")


class ProfileUpdateRequest(BaseSchema):
    """تحديث الملف الشخصي."""

    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)
    avatar_url: str | None = Field(default=None, max_length=255)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalise_phone(v) if v else v


# =====================================================================
#  المستخدمون
# =====================================================================
class UserResponse(ORMResponse):
    id: int
    full_name: str
    email: str | None
    phone: str | None
    role: str
    club_id: int | None
    avatar_url: str | None
    is_active: bool
    last_login_at: dt.datetime | None

    @property
    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return "؟"
        return parts[0][0] if len(parts) == 1 else f"{parts[0][0]}{parts[-1][0]}"


class UserCreateRequest(BaseSchema):
    """إنشاء مستخدم جديد (من طاقم النادي)."""

    full_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(...)
    job_title: str | None = None
    salary: float | None = Field(default=None, ge=0)
    hire_date: dt.date | None = None
    specialty: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalise_phone(v) if v else v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        allowed = {"club_admin", "receptionist", "coach", "parent"}
        if v not in allowed:
            raise ValueError(f"الرتبة غير صالحة. المسموح: {', '.join(allowed)}")
        return v


# =====================================================================
#  الأندية
# =====================================================================
class ClubSummary(ORMResponse):
    """ملخص نادي (يُستخدم في القوائم والردود السريعة)."""

    id: int
    name: str
    slug: str
    logo_url: str | None
    city: str | None
    wilaya: str | None
    primary_color: str | None


class ClubResponse(ClubSummary):
    description: str | None
    cover_url: str | None
    address: str | None
    commune: str | None
    phone: str | None
    email: str | None
    website: str | None
    facebook_url: str | None
    instagram_url: str | None
    min_age: int | None
    max_age: int | None
    status: str
    is_public: bool
    rating: float
    members_count: int
    accepts_online_registration: bool
    sports: list[dict[str, Any]] = []
    subscription_end: dt.date | None = None

    @field_validator("sports", mode="before")
    @classmethod
    def _serialise_sports(cls, value: Any) -> Any:
        """
        تحويل كائنات ``Sport`` إلى قاموس.

        العلاقة ``Club.sports`` تُرجع نماذج ORM، و Pydantic لا يحوّلها
        تلقائياً إلى ``dict`` مع الحقول المختارة.
        """
        if not value or not isinstance(value, (list, tuple)):
            return []
        return [
            {
                "id": sport.id,
                "name": sport.name_ar,
                "name_en": sport.name_en,
                "slug": sport.slug,
                "icon": sport.icon,
                "category": sport.category,
            }
            for sport in value
        ]


class ClubCreateRequest(BaseSchema):
    """إنشاء نادي (مدير المنصة)."""

    name: str = Field(..., min_length=2, max_length=200)
    slug: str | None = Field(default=None, max_length=220)
    description: str | None = Field(default=None, max_length=2000)
    logo_url: str | None = None
    cover_url: str | None = None
    address: str | None = None
    wilaya: str | None = None
    commune: str | None = None
    city: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    website: str | None = None
    min_age: int | None = Field(default=None, ge=0, le=100)
    max_age: int | None = Field(default=None, ge=0, le=100)
    plan_id: int | None = None
    sport_ids: list[int] = Field(default_factory=list)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalise_phone(v) if v else v


class ClubUpdateRequest(BaseSchema):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    logo_url: str | None = None
    cover_url: str | None = None
    address: str | None = None
    wilaya: str | None = None
    commune: str | None = None
    city: str | None = None
    phone: str | None = None
    phone_2: str | None = None
    email: EmailStr | None = None
    website: str | None = None
    facebook_url: str | None = None
    instagram_url: str | None = None
    min_age: int | None = Field(default=None, ge=0, le=100)
    max_age: int | None = Field(default=None, ge=0, le=100)
    sport_ids: list[int] | None = None
    is_public: bool | None = None
    accepts_online_registration: bool | None = None
    default_monthly_price: float | None = Field(default=None, ge=0)


# =====================================================================
#  الرياضيين
# =====================================================================
class AthleteResponse(ORMResponse):
    id: int
    card_code: str
    first_name: str
    last_name: str
    full_name: str
    birth_date: dt.date | None
    gender: str | None
    age: int | None = None
    photo_url: str | None
    phone: str | None
    guardian_name: str | None
    guardian_phone: str | None
    sport_id: int | None
    level: str
    status: str
    is_active: bool
    joined_at: dt.date | None
    attendance_rate: float | None = None


class AthleteCreateRequest(BaseSchema):
    """إنشاء رياضي."""

    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    birth_date: dt.date | None = None
    gender: str | None = Field(default=None, pattern="^(male|female)$")
    nationality: str | None = None
    national_id: str | None = None
    blood_type: str | None = Field(default=None, max_length=5)
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    wilaya: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None
    guardian_email: EmailStr | None = None
    guardian_relation: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    medical_notes: str | None = None
    allergies: str | None = None
    chronic_conditions: str | None = None
    injuries: str | None = None
    has_medical_clearance: bool = False
    photo_url: str | None = None
    notes: str | None = None
    sport_id: int | None = None
    preferred_position: str | None = None
    level: str = "beginner"
    jersey_number: int | None = Field(default=None, ge=0, le=99)
    group_ids: list[int] = Field(default_factory=list)

    @field_validator("phone", "guardian_phone", "emergency_contact_phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalise_phone(v) if v else v


class AthleteUpdateRequest(BaseSchema):
    """تحديث رياضي."""

    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    birth_date: dt.date | None = None
    gender: str | None = Field(default=None, pattern="^(male|female)$")
    nationality: str | None = None
    national_id: str | None = None
    blood_type: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    wilaya: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None
    guardian_email: EmailStr | None = None
    guardian_relation: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    medical_notes: str | None = None
    allergies: str | None = None
    chronic_conditions: str | None = None
    injuries: str | None = None
    has_medical_clearance: bool | None = None
    photo_url: str | None = None
    notes: str | None = None
    sport_id: int | None = None
    preferred_position: str | None = None
    level: str | None = None
    jersey_number: int | None = Field(default=None, ge=0, le=99)
    status: str | None = None
    group_ids: list[int] | None = None


class AthleteSearchQuery(BaseSchema):
    """معايير البحث في الرياضيين."""

    search: str | None = None
    status: str | None = None
    group_id: int | None = None
    sport_id: int | None = None
    level: str | None = None
    has_debt: bool | None = None
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


# =====================================================================
#  الأفواج
# =====================================================================
class GroupResponse(ORMResponse):
    id: int
    name: str
    code: str | None
    sport_id: int | None
    level: str
    coach_id: int | None
    coach_name: str | None = None
    capacity: int
    current_count: int
    available_slots: int = 0
    color: str | None
    location: str | None
    schedule_days: str | None
    time_label: str | None = None
    monthly_fee: float | None
    status: str
    is_active: bool


class GroupCreateRequest(BaseSchema):
    name: str = Field(..., min_length=2, max_length=150)
    code: str | None = Field(default=None, max_length=30)
    sport_id: int | None = None
    level: str = "beginner"
    min_age: int | None = Field(default=None, ge=0, le=100)
    max_age: int | None = Field(default=None, ge=0, le=100)
    gender: str | None = Field(default=None, pattern="^(male|female)$")
    coach_id: int | None = None
    capacity: int = Field(default=20, ge=1, le=500)
    color: str | None = Field(default=None, max_length=20)
    location: str | None = None
    schedule_days: str | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    monthly_fee: float | None = Field(default=None, ge=0)
    description: str | None = None


class GroupUpdateRequest(BaseSchema):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    code: str | None = None
    sport_id: int | None = None
    level: str | None = None
    min_age: int | None = None
    max_age: int | None = None
    gender: str | None = None
    coach_id: int | None = None
    capacity: int | None = Field(default=None, ge=1, le=500)
    color: str | None = None
    location: str | None = None
    schedule_days: str | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    monthly_fee: float | None = None
    description: str | None = None
    is_active: bool | None = None


# =====================================================================
#  الحصص والحضور
# =====================================================================
class SessionResponse(ORMResponse):
    id: int
    group_id: int | None
    group_name: str | None = None
    coach_id: int | None
    title: str | None
    session_date: dt.date
    start_time: dt.time
    end_time: dt.time
    time_label: str | None = None
    location: str | None
    status: str
    expected_count: int
    present_count: int
    absent_count: int
    attendance_rate: float | None = None


class SessionCreateRequest(BaseSchema):
    group_id: int | None = None
    coach_id: int | None = None
    title: str | None = Field(default=None, max_length=200)
    session_date: dt.date
    start_time: dt.time
    end_time: dt.time
    location: str | None = None
    session_type: str | None = None
    objectives: str | None = None
    notes: str | None = None

    @field_validator("end_time")
    @classmethod
    def _order(cls, v: dt.time, info) -> dt.time:
        start = info.data.get("start_time")
        if start and v <= start:
            raise ValueError("وقت النهاية يجب أن يكون بعد وقت البداية")
        return v


class SessionUpdateRequest(BaseSchema):
    group_id: int | None = None
    coach_id: int | None = None
    title: str | None = None
    session_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    location: str | None = None
    session_type: str | None = None
    objectives: str | None = None
    notes: str | None = None


class AttendanceRecordRequest(BaseSchema):
    """تسجيل حضور رياضي واحد."""

    athlete_id: int
    status: str = Field(default="present", pattern="^(present|absent|late|excused)$")
    notes: str | None = None
    minutes_late: int = Field(default=0, ge=0, le=600)


class AttendanceScanRequest(BaseSchema):
    """تسجيل حضور بمسح QR."""

    card_code: str = Field(..., min_length=3, max_length=60)
    status: str = Field(default="present", pattern="^(present|absent|late|excused)$")


class AttendanceBulkRequest(BaseSchema):
    """تسجيل حضور دفعة واحدة."""

    records: list[AttendanceRecordRequest] = Field(..., min_length=1, max_length=200)


# =====================================================================
#  التقييمات
# =====================================================================
class EvaluationResponse(ORMResponse):
    id: int
    athlete_id: int
    group_id: int | None
    coach_id: int | None
    eval_date: dt.date
    type: str
    physical_score: int | None
    technique_score: int | None
    discipline_score: int | None
    teamwork_score: int | None
    overall_score: float | None
    grade: str | None = None
    strengths: str | None
    weaknesses: str | None
    notes: str | None
    recommendations: str | None


class EvaluationCreateRequest(BaseSchema):
    athlete_id: int
    group_id: int | None = None
    session_id: int | None = None
    eval_date: dt.date | None = None
    type: str = Field(default="periodic", pattern="^(periodic|technical|behavior|match)$")
    physical_score: int | None = Field(default=None, ge=1, le=10)
    technique_score: int | None = Field(default=None, ge=1, le=10)
    discipline_score: int | None = Field(default=None, ge=1, le=10)
    teamwork_score: int | None = Field(default=None, ge=1, le=10)
    strengths: str | None = None
    weaknesses: str | None = None
    notes: str | None = None
    recommendations: str | None = None

    @field_validator(
        "physical_score", "technique_score",
        "discipline_score", "teamwork_score",
    )
    @classmethod
    def _no_all_none(cls, v, info):
        """التقييم يحتاج درجة واحدة على الأقل (يتحقق الخدمات أيضاً)."""
        return v


# =====================================================================
#  المالية
# =====================================================================
class FeePlanResponse(ORMResponse):
    id: int
    name: str
    description: str | None
    amount: float
    period: str
    period_label: str | None = None
    duration_days: int
    installments: int
    discount_percent: float
    is_active: bool


class FeePlanCreateRequest(BaseSchema):
    name: str = Field(..., min_length=2, max_length=150)
    description: str | None = None
    amount: float = Field(..., ge=0)
    period: str = Field(default="monthly",
                        pattern="^(monthly|quarterly|semi_annual|annual|one_time)$")
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    installments: int = Field(default=1, ge=1, le=24)
    sport_id: int | None = None
    group_id: int | None = None
    age_group: str | None = None
    gender: str | None = None
    discount_percent: float = Field(default=0, ge=0, le=100)
    sibling_discount: float = Field(default=0, ge=0, le=100)
    registration_fee: float | None = Field(default=None, ge=0)
    sort_order: int = 0


class FeePlanUpdateRequest(BaseSchema):
    name: str | None = None
    description: str | None = None
    amount: float | None = Field(default=None, ge=0)
    period: str | None = None
    duration_days: int | None = None
    installments: int | None = None
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    sibling_discount: float | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class InvoiceResponse(ORMResponse):
    id: int
    invoice_number: str
    athlete_id: int
    athlete_name: str | None = None
    fee_plan_id: int | None
    period_start: dt.date
    period_end: dt.date
    issue_date: dt.date
    due_date: dt.date
    subtotal: float
    discount: float
    total: float
    paid: float
    amount_due: float = 0
    status: str
    status_label: str | None = None
    is_overdue: bool = False
    days_overdue: int = 0


class InvoiceCreateRequest(BaseSchema):
    athlete_id: int
    fee_plan_id: int | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    due_date: dt.date | None = None
    amount: float | None = Field(default=None, ge=0)
    discount: float = Field(default=0, ge=0)
    notes: str | None = None


class PaymentCreateRequest(BaseSchema):
    invoice_id: int | None = None
    athlete_id: int | None = None
    amount: float = Field(..., gt=0)
    method: str = Field(default="cash",
                        pattern="^(cash|bank|card|check|online|waiver)$")
    reference: str | None = None
    notes: str | None = None

    def validate_target(self) -> None:
        """يجب تحديد الفاتورة أو الرياضي."""
        if self.invoice_id is None and self.athlete_id is None:
            raise ValueError("يجب تحديد الفاتورة أو الرياضي")


class ExpenseCreateRequest(BaseSchema):
    title: str = Field(..., min_length=2, max_length=200)
    description: str | None = None
    category: str = Field(
        default="other",
        pattern="^(rent|salary|equipment|maintenance|utilities|marketing|transport|medical|other)$",
    )
    amount: float = Field(..., gt=0)
    expense_date: dt.date | None = None
    staff_id: int | None = None
    group_id: int | None = None
    vendor: str | None = None
    reference: str | None = None
    receipt_url: str | None = None
    notes: str | None = None


# =====================================================================
#  المحتوى والتسجيل
# =====================================================================
class NewsResponse(ORMResponse):
    id: int
    club_id: int | None
    club_name: str | None = None
    title: str
    excerpt: str | None
    kind: str
    cover_url: str | None
    is_pinned: bool
    published_at: dt.datetime | None
    event_date: dt.date | None
    views_count: int


class NewsCreateRequest(BaseSchema):
    title: str = Field(..., min_length=3, max_length=250)
    content: str = Field(..., min_length=10)
    excerpt: str | None = Field(default=None, max_length=400)
    kind: str = Field(default="news", pattern="^(news|announcement|result|event|tournament)$")
    category: str | None = None
    cover_url: str | None = None
    is_pinned: bool = False
    event_date: dt.date | None = None
    event_end_date: dt.date | None = None
    location: str | None = None
    sport_id: int | None = None


class RegistrationRequestCreate(BaseSchema):
    """طلب تسجيل من زائر (لا يحتاج مصادقة)."""

    full_name: str = Field(..., min_length=3, max_length=160)
    birth_date: dt.date | None = None
    gender: str | None = Field(default=None, pattern="^(male|female)$")
    parent_name: str | None = Field(default=None, max_length=160)
    phone: str = Field(..., min_length=8, max_length=20)
    email: EmailStr | None = None
    address: str | None = None
    wilaya: str | None = None
    sport_id: int | None = None
    preferred_group_id: int | None = None
    level: str | None = None
    message: str | None = Field(default=None, max_length=1000)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)


class RegistrationDecisionRequest(BaseSchema):
    """قرار بشأن طلب التسجيل."""

    reason: str | None = Field(default=None, max_length=500)


# =====================================================================
#  استجابات عامة
# =====================================================================
class MessageResponse(BaseSchema):
    """رسالة نجاح بسيطة."""

    success: bool = True
    message: str
    data: dict[str, Any] | None = None


class PaginatedResponse(BaseSchema, Generic[T]):
    """استجابة مُقسّمة إلى صفحات."""

    items: list[T]
    total: int
    page: int
    per_page: int
    pages: int

    @classmethod
    def create(cls, items: list[T], total: int, page: int, per_page: int) -> "PaginatedResponse[T]":
        pages = (total + per_page - 1) // per_page if per_page else 1
        return cls(items=items, total=total, page=page, per_page=per_page, pages=pages)


# تحديث المراجع الأمامية (لتفادي التحقق المتبادل غير الضروري)
TokenResponse.model_rebuild()