"""
نظام الصلاحيات (Role-Based Access Control)
==========================================
مصفوفة صلاحيات صريحة (Explicit permission matrix) بدل الصلاحيات الحرة
(Free-form ACL)، وهذا يجعل مراجعة الأمان سهلة ومتوقعة:

* كل صلاحية لها رمز نصي ثابت بصيغة ``<المورد>.<العملية>``.
* كل دور يملك مجموعة محددة من الرموز.
* ``super_admin`` يملك كل شيء تلقائياً عبر البادئة ``*``.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.enums import UserRole


# =====================================================================
# 1) الموارد والعمليات
# =====================================================================
class Resource:
    """الموارد المحمية في المنصة."""

    CLUBS = "clubs"              # النوادي
    USERS = "users"              # المستخدمون
    ATHLETES = "athletes"        # الرياضيين
    GROUPS = "groups"            # الأفواج
    SESSIONS = "sessions"        # الحصص التدريبية
    ATTENDANCE = "attendance"    # الحضور
    EVALUATIONS = "evaluations"  # التقييمات
    STAFF = "staff"              # طاقم النادي
    FINANCE = "finance"          # المالية
    INVOICES = "invoices"        # الفواتير
    PAYMENTS = "payments"        # الدفعات
    EXPENSES = "expenses"        # المصاريف
    REPORTS = "reports"          # التقارير
    NEWS = "news"                # الأخبار والإعلانات
    REGISTRATIONS = "registrations"  # طلبات التسجيل
    NOTIFICATIONS = "notifications"  # الإشعارات
    IMPORT_EXPORT = "import_export"  # الاستيراد والتصدير
    PLATFORM = "platform"        # إعدادات المنصة
    CARDS = "cards"              # البطاقات
    PARENTS = "parents"          # أولياء الأمور
    SETTINGS = "settings"        # إعدادات النادي


class Action:
    """العمليات الممكنة على كل مورد."""

    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MANAGE = "manage"    # صلاحيات إدارية كاملة


READ_ONLY = Action.READ


def _p(resource: str, action: str) -> str:
    """اختصار لبناء رمز الصلاحية."""
    return f"{resource}.{action}"


# =====================================================================
# 2) مصفوفة الصلاحيات
# =====================================================================
#: الصلاحيات المشتركة بين كل إدارة النادي
CLUB_ADMIN_PERMISSIONS: frozenset[str] = frozenset(
    {
        # الرياضيين
        _p(Resource.ATHLETES, Action.READ),
        _p(Resource.ATHLETES, Action.CREATE),
        _p(Resource.ATHLETES, Action.UPDATE),
        _p(Resource.ATHLETES, Action.DELETE),
        # الأفواج
        _p(Resource.GROUPS, Action.READ),
        _p(Resource.GROUPS, Action.CREATE),
        _p(Resource.GROUPS, Action.UPDATE),
        _p(Resource.GROUPS, Action.DELETE),
        # الحصص
        _p(Resource.SESSIONS, Action.READ),
        _p(Resource.SESSIONS, Action.CREATE),
        _p(Resource.SESSIONS, Action.UPDATE),
        _p(Resource.SESSIONS, Action.DELETE),
        # الحضور
        _p(Resource.ATTENDANCE, Action.READ),
        _p(Resource.ATTENDANCE, Action.CREATE),
        _p(Resource.ATTENDANCE, Action.UPDATE),
        _p(Resource.ATTENDANCE, Action.DELETE),
        # التقييمات
        _p(Resource.EVALUATIONS, Action.READ),
        _p(Resource.EVALUATIONS, Action.CREATE),
        _p(Resource.EVALUATIONS, Action.UPDATE),
        _p(Resource.EVALUATIONS, Action.DELETE),
        # الطاقم
        _p(Resource.STAFF, Action.READ),
        _p(Resource.STAFF, Action.CREATE),
        _p(Resource.STAFF, Action.UPDATE),
        _p(Resource.STAFF, Action.DELETE),
        # المالية
        _p(Resource.FINANCE, Action.READ),
        _p(Resource.INVOICES, Action.READ),
        _p(Resource.INVOICES, Action.CREATE),
        _p(Resource.INVOICES, Action.UPDATE),
        _p(Resource.INVOICES, Action.DELETE),
        _p(Resource.PAYMENTS, Action.READ),
        _p(Resource.PAYMENTS, Action.CREATE),
        _p(Resource.PAYMENTS, Action.DELETE),
        _p(Resource.EXPENSES, Action.READ),
        _p(Resource.EXPENSES, Action.CREATE),
        _p(Resource.EXPENSES, Action.UPDATE),
        _p(Resource.EXPENSES, Action.DELETE),
        # المحتوى
        _p(Resource.NEWS, Action.READ),
        _p(Resource.NEWS, Action.CREATE),
        _p(Resource.NEWS, Action.UPDATE),
        _p(Resource.NEWS, Action.DELETE),
        _p(Resource.REGISTRATIONS, Action.READ),
        _p(Resource.REGISTRATIONS, Action.CREATE),
        _p(Resource.REGISTRATIONS, Action.UPDATE),
        _p(Resource.REGISTRATIONS, Action.DELETE),
        # البطاقات والتقارير
        _p(Resource.CARDS, Action.READ),
        _p(Resource.CARDS, Action.CREATE),
        _p(Resource.REPORTS, Action.READ),
        _p(Resource.REPORTS, Action.MANAGE),
        _p(Resource.IMPORT_EXPORT, Action.READ),
        _p(Resource.IMPORT_EXPORT, Action.CREATE),
        # الأولياء والإشعارات
        _p(Resource.PARENTS, Action.READ),
        _p(Resource.PARENTS, Action.CREATE),
        _p(Resource.PARENTS, Action.UPDATE),
        _p(Resource.NOTIFICATIONS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.CREATE),
        # المستخدمون
        _p(Resource.USERS, Action.READ),
        _p(Resource.USERS, Action.CREATE),
        _p(Resource.USERS, Action.UPDATE),
        # إعدادات النادي
        _p(Resource.SETTINGS, Action.READ),
        _p(Resource.SETTINGS, Action.UPDATE),
    }
)


#: صلاحيات الإداري / السكرتير -- مالية وإدارية محدودة، بلا حذف destructive
RECEPTIONIST_PERMISSIONS: frozenset[str] = frozenset(
    {
        # الرياضيين: إضافة وتعديل فقط (لا حذف)
        _p(Resource.ATHLETES, Action.READ),
        _p(Resource.ATHLETES, Action.CREATE),
        _p(Resource.ATHLETES, Action.UPDATE),
        # الأفواج: عرض فقط
        _p(Resource.GROUPS, Action.READ),
        # الحصص: عرض
        _p(Resource.SESSIONS, Action.READ),
        # الحضور: عرض وتسجيل
        _p(Resource.ATTENDANCE, Action.READ),
        _p(Resource.ATTENDANCE, Action.CREATE),
        # المالية: قراءة + تسجيل دفعات
        _p(Resource.FINANCE, Action.READ),
        _p(Resource.INVOICES, Action.READ),
        _p(Resource.INVOICES, Action.CREATE),
        _p(Resource.PAYMENTS, Action.READ),
        _p(Resource.PAYMENTS, Action.CREATE),
        # البطاقات والقوائم
        _p(Resource.CARDS, Action.READ),
        _p(Resource.CARDS, Action.CREATE),
        _p(Resource.REPORTS, Action.READ),
        # طلبات التسجيل
        _p(Resource.REGISTRATIONS, Action.READ),
        _p(Resource.REGISTRATIONS, Action.UPDATE),
        # الإشعارات
        _p(Resource.NOTIFICATIONS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.CREATE),
    }
)


#: صلاحيات المدرب -- محصورة في أفواجه ورياضييها
COACH_PERMISSIONS: frozenset[str] = frozenset(
    {
        _p(Resource.GROUPS, Action.READ),
        _p(Resource.SESSIONS, Action.READ),
        _p(Resource.SESSIONS, Action.CREATE),
        _p(Resource.SESSIONS, Action.UPDATE),
        _p(Resource.ATHLETES, Action.READ),
        _p(Resource.ATTENDANCE, Action.READ),
        _p(Resource.ATTENDANCE, Action.CREATE),
        _p(Resource.ATTENDANCE, Action.UPDATE),
        _p(Resource.EVALUATIONS, Action.READ),
        _p(Resource.EVALUATIONS, Action.CREATE),
        _p(Resource.EVALUATIONS, Action.UPDATE),
        _p(Resource.NOTIFICATIONS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.CREATE),
        _p(Resource.SESSIONS, Action.MANAGE),
    }
)


#: صلاحيات الولي -- قراءة فقط على بيانات أبنائه
PARENT_PERMISSIONS: frozenset[str] = frozenset(
    {
        _p(Resource.ATHLETES, Action.READ),
        _p(Resource.ATTENDANCE, Action.READ),
        _p(Resource.EVALUATIONS, Action.READ),
        _p(Resource.SESSIONS, Action.READ),
        _p(Resource.INVOICES, Action.READ),
        _p(Resource.FINANCE, Action.READ),
        _p(Resource.GROUPS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.CREATE),
    }
)


#: صلاحيات الرياضي -- قراءة فقط على بياناته الشخصية
ATHLETE_PERMISSIONS: frozenset[str] = frozenset(
    {
        _p(Resource.SESSIONS, Action.READ),
        _p(Resource.ATTENDANCE, Action.READ),
        _p(Resource.EVALUATIONS, Action.READ),
        _p(Resource.GROUPS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.READ),
        _p(Resource.NOTIFICATIONS, Action.CREATE),
    }
)


#: جدول توزيع الصلاحيات حسب الدور
ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.CLUB_ADMIN: CLUB_ADMIN_PERMISSIONS,
    UserRole.RECEPTIONIST: RECEPTIONIST_PERMISSIONS,
    UserRole.COACH: COACH_PERMISSIONS,
    UserRole.PARENT: PARENT_PERMISSIONS,
    UserRole.ATHLETE: ATHLETE_PERMISSIONS,
    UserRole.SUPER_ADMIN: frozenset({"*"}),   # كل الصلاحيات
}


#: الأوصاف العربية لكل دور (تُعرض في الواجهة)
ROLE_LABELS: dict[UserRole, str] = {
    UserRole.SUPER_ADMIN: "مدير المنصة",
    UserRole.CLUB_ADMIN: "مدير النادي",
    UserRole.RECEPTIONIST: "إداري / سكرتير",
    UserRole.COACH: "مدرب",
    UserRole.PARENT: "ولي الأمر",
    UserRole.ATHLETE: "رياضي",
}


# =====================================================================
# 3) دوال الفحص
# =====================================================================
def has_permission(role: UserRole | str | None, permission: str) -> bool:
    """
    هل يملك هذا الدور الصلاحية المطلوبة؟

    يدعم البادئة ``*`` كمطابقة شاملة (wildcard)، مثل ``athletes.*``.
    """
    if role is None:
        return False

    try:
        role_enum = role if isinstance(role, UserRole) else UserRole(role)
    except ValueError:
        return False

    granted = ROLE_PERMISSIONS.get(role_enum, frozenset())
    if "*" in granted:
        return True
    if permission in granted:
        return True

    # مطابقة بـ wildcard: "athletes.*" يغطي "athletes.read"
    if "*" in permission:
        prefix = permission.rsplit(".", 1)[0]
        return any(g.startswith(f"{prefix}.") for g in granted)

    return False


def has_any_permission(role: UserRole | str | None, permissions: Iterable[str]) -> bool:
    """هل يملك الدور أياً من الصلاحيات المعطاة؟"""
    return any(has_permission(role, p) for p in permissions)


def has_all_permissions(role: UserRole | str | None, permissions: Iterable[str]) -> bool:
    """هل يملك الدور كل الصلاحيات المعطاة؟"""
    return all(has_permission(role, p) for p in permissions)


def permissions_for(role: UserRole | str | None) -> list[str]:
    """كل الصلاحيات الممنوحة للدور (مرتبة)."""
    try:
        role_enum = role if isinstance(role, UserRole) else UserRole(str(role))
    except ValueError:
        return []
    return sorted(ROLE_PERMISSIONS.get(role_enum, frozenset()))


def is_platform_role(role: UserRole | str | None) -> bool:
    """هل هذا الدور على مستوى المنصة بالكامل؟"""
    return role == UserRole.SUPER_ADMIN


def is_club_role(role: UserRole | str | None) -> bool:
    """هل هذا الدور مربوط بنادٍ (يحتاج ``club_id``)؟"""
    try:
        role_enum = role if isinstance(role, UserRole) else UserRole(str(role))
    except ValueError:
        return False
    return role_enum in {
        UserRole.CLUB_ADMIN,
        UserRole.RECEPTIONIST,
        UserRole.COACH,
    }


def requires_club_scope(role: UserRole | str | None) -> bool:
    """هل يحتاج هذا الدور ``club_id`` صالحاً للعمل؟"""
    return is_club_role(role)


def role_label(role: UserRole | str | None) -> str:
    """الاسم العربي الظاهر للدور."""
    try:
        role_enum = role if isinstance(role, UserRole) else UserRole(str(role))
    except ValueError:
        return "غير معروف"
    return ROLE_LABELS.get(role_enum, role_enum.value)


def describe_matrix() -> dict[str, list[str]]:
    """توليد مصفوفة الصلاحيات (تُستخدم في التوثيق وشاشة المساعدة)."""
    return {
        role_label(role): sorted(perms)
        for role, perms in ROLE_PERMISSIONS.items()
    }