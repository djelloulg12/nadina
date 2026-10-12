"""
استثناءات المنصة (Application Exceptions)
=========================================
كل استثناءات الأعمال معرّفة هنا، ومُربَطة بمعالجات موحّدة في
:mod:`app.core.errors` التي تحوّلها إلى استجابات HTTP عربية واضحة.
"""

from __future__ import annotations


class AppError(Exception):
    """الاستثناء الجذر لكل أخطاء المنصة."""

    status_code: int = 500
    message: str = "حدث خطأ غير متوقع"
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """تحويل الاستثناء إلى بنية JSON موحّدة."""
        payload: dict = {
            "error": True,
            "code": self.code,
            "message": self.message,
            "status": self.status_code,
        }
        if self.details:
            payload["details"] = self.details
        return payload


# ---------------------------------------------------------------------
#  أخطاء المصادقة والصلاحيات
# ---------------------------------------------------------------------
class AuthenticationError(AppError):
    """فشل التحقق من هوية المستخدم."""

    status_code = 401
    message = "بيانات الدخول غير صحيحة"
    code = "authentication_failed"


class InvalidCredentialsError(AuthenticationError):
    """اسم المستخدم أو كلمة المرور غير صحيحة."""

    message = "البريد الإلكتروني أو كلمة المرور غير صحيحة"
    code = "invalid_credentials"


class AccountLockedError(AuthenticationError):
    """الحساب مقفل مؤقتاً بعد محاولات فاشلة متكررة."""

    status_code = 423
    message = "تم قفل الحساب مؤقتاً بسبب محاولات دخول فاشلة متكررة"
    code = "account_locked"


class AccountInactiveError(AuthenticationError):
    """الحساب غير مُفعّل."""

    status_code = 403
    message = "هذا الحساب غير مُفعّل. يرجى التواصل مع إدارة النادي"
    code = "account_inactive"


class PermissionDeniedError(AppError):
    """المستخدم لا يملك الصلاحية المطلوبة."""

    status_code = 403
    message = "ليس لديك صلاحية للقيام بهذا الإجراء"
    code = "permission_denied"


class NotFoundError(AppError):
    """السجل المطلوب غير موجود (أو لا يظهر ضمن نطاق المستخدم)."""

    status_code = 404
    message = "العنصر المطلوب غير موجود"
    code = "not_found"


# ---------------------------------------------------------------------
#  أخطاء عزل المستأجرين
# ---------------------------------------------------------------------
class TenantIsolationError(PermissionDeniedError):
    """محاولة الوصول إلى بيانات نادي آخر — تُسجَّل كحدث أمني."""

    status_code = 403
    message = "لا يمكن الوصول إلى بيانات نادي آخر"
    code = "tenant_violation"


class NoTenantContextError(AppError):
    """لم يتم-establish سياق المستأجر قبل تنفيذ عملية حساسة."""

    status_code = 500
    message = "خطأ داخلي: سياق النادي غير متاح"
    code = "no_tenant_context"


# ---------------------------------------------------------------------
#  أخطاء التحقق من المدخلات
# ---------------------------------------------------------------------
class ValidationError(AppError):
    """بيانات الإدخال غير صالحة."""

    status_code = 422
    message = "البيانات المُدخلة غير صالحة"
    code = "validation_error"


class ConflictError(AppError):
    """تعارض في البيانات (مثل تكرار بريد إلكتروني)."""

    status_code = 409
    message = "تعارض في البيانات: هذه المعلومة مستخدمة مسبقاً"
    code = "conflict"


class BusinessRuleError(AppError):
    """مخالفة لقاعدة عمل (مثل: لا يمكن حذف فوج يحتوي على رياضيين)."""

    status_code = 400
    message = "لا يمكن إتمام العملية بسبب مخالفة لقاعدة عمل"
    code = "business_rule_violation"


class SubscriptionRequiredError(AppError):
    """اشتراك النادي في المنصة منتهٍ أو غير مُفعّل."""

    status_code = 402
    message = "انتهى اشتراك النادي في المنصة. يرجى التجديد للمتابعة"
    code = "subscription_required"


class ClubSuspendedError(AppError):
    """حساب النادي موقوف."""

    status_code = 403
    message = "حساب النادي موقوف حالياً. يرجى التواصل مع دعم المنصة"
    code = "club_suspended"


# ---------------------------------------------------------------------
#  أخطاء الملفات
# ---------------------------------------------------------------------
class FileTooLargeError(AppError):
    """حجم الملف يتجاوز الحد المسموح."""

    status_code = 413
    message = "حجم الملف أكبر من الحد المسموح"
    code = "file_too_large"


class UnsupportedFileTypeError(AppError):
    """نوع الملف غير مدعوم."""

    status_code = 415
    message = "نوع الملف غير مدعوم"
    code = "unsupported_file_type"