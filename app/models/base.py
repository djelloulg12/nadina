"""
النماذج الأساسية المشتركة (Base Models & Mixins)
=================================================
تعريف الـ Base الذي ترث منه كل النماذج، الـ Mixins المشتركة:
توثيق الحالة (timestamps)، العلامة النشطة (soft delete)، وملكية المستأجر.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow  # noqa: F401  (يُعاد تصديره للنماذج)


# =====================================================================
#  أنواع أعمدة التعدادات
# =====================================================================
def _enum_values(enum_cls: type) -> list[str]:
    """قائمة القيم النصية للتعداد (بدل أسماء الأعضاء)."""
    return [member.value for member in enum_cls]  # type: ignore[attr-defined]

#: Maximum safe index length on ``utf8mb4`` in MySQL/MariaDB.
#: 4 bytes per char x 191 chars = 764 bytes, which is below InnoDB's
#: legacy limit (767) and its modern limit (3072) -- so it works on
#: every server version without exceptions.
MYSQL_INDEX_PREFIX = 191


def unique_text(name: str, column: str, length: int = MYSQL_INDEX_PREFIX) -> Index:
    """
    Unique index that is safe on every database dialect.

    The problem: ``VARCHAR(255)`` under ``utf8mb4`` occupies 1020
    bytes, which exceeds InnoDB's legacy index prefix limit (767
    bytes). On an older MySQL/MariaDB the ``CREATE TABLE`` then fails
    outright and the application cannot start.

    The fix: keep the stored column at its full width (no data
    truncation) but index only the first 191 characters. Other
    dialects ignore ``mysql_length``, so their indexes stay complete.

    Usage::

        __table_args__ = (unique_text("uq_users_email", "email"),)
    """
    return Index(
        name,
        column,
        unique=True,
        mysql_length=min(length, MYSQL_INDEX_PREFIX),
    )



class StrEnumType(SAEnum):
    """
    عمود تعداد يُخزَّن كنص ``VARCHAR`` مع تحويل تلقائي في الاتجاهين.

    لماذا لا ``Enum`` أصلي (``native_enum=True``)؟
    لأن النوع الأصلي في PostgreSQL يحتاج صلاحيات ``CREATE TYPE``، ويزيل
    القيم عملياً، ويُخالف معيار ``CHECK`` في SQLite. التخزين
    كنص مع قيد ``CHECK`` ضمني يمنح نفس الأمان وقابلية النقل الكاملة.

    ولأن ``type_annotation_map`` في :class:`Base` يربط كل تعداد بهذا
    النوع، نكتب في النماذج ``Mapped[UserRole] = mapped_column()`` فقط
    دون تكرار النوع في كل حقل.
    """

    def __init__(self, enum_cls: type, **kwargs) -> None:
        kwargs.setdefault("native_enum", False)
        kwargs.setdefault("length", 40)
        kwargs.setdefault("create_constraint", True)
        kwargs.setdefault("validate_strings", True)
        kwargs["values_callable"] = _enum_values
        super().__init__(enum_cls, **kwargs)


def _build_enum_map() -> dict:
    """
    خريطة من كل تعداد في :mod:`app.models.enums` إلى نوع العمود.

    تُبنى ديناميكياً حتى لا يُنسى تعداد جديد عند إضافته.
    """
    import importlib

    enums_module = importlib.import_module("app.models.enums")

    result: dict = {}
    for name in dir(enums_module):
        obj = getattr(enums_module, name)
        if isinstance(obj, type) and issubclass(obj, enum.Enum):
            result[obj] = StrEnumType(obj)
    return result


#: ربط كل تعداد بنوع العمود المناسب (VARCHAR مع تحويل تلقائي).
#: نحدّث خريطة الصنف **وخريطة السجل** معاً، لأن ``DeclarativeBase`` تنسخ
#: الأولى إلى الثانية عند إنشاء الصنف، فتحديث إحداهما وحدها لا يكفي.
_ENUM_MAP = _build_enum_map()
Base.type_annotation_map.update(_ENUM_MAP)
Base.registry.type_annotation_map.update(_ENUM_MAP)


class TimestampMixin:
    """يضيف أعمدة ``created_at`` و ``updated_at`` تلقائياً."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=func.now(),
        index=True,
        comment="تاريخ الإنشاء",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="تاريخ آخر تعديل",
    )


class TenantOwned:
    """
    علامة (marker) للنماذج المملوكة لنادٍ.

    وجود هذه الفئة في ``__mro__`` يجعل نموذج العزل
    (:mod:`app.core.tenancy`) يتعرف على النموذج ويحقن شرط ``club_id``
    تلقائياً في كل استعلام.
    """

    __tenant_owned__: bool = True


class TenantMixin(TenantOwned):
    """
    يضيف عمود ``club_id`` إلى أي نموذج مملوك لنادٍ.

    كل النماذج التي ترثه تُحقن تلقائياً بشرط العزل في
    :mod:`app.core.tenancy`، فتنتهي أي محاولات لتجاوز العزل.
    """

    __abstract__ = True

    club_id: Mapped[int] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="معرّف النادي المالك (مفتاح العزل)",
    )


class AuditMixin:
    """أعمدة تدقيق: من أنشأ السجل ومن عدّله."""

    created_by_id: Mapped[int | None] = mapped_column(
        nullable=True, index=True, comment="معرّف المستخدم المنشئ"
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        nullable=True, comment="معرّف المستخدم آخر من عدّل"
    )


class SoftDeleteMixin:
    """حذف منطقي عبر عمود ``is_active`` بدلاً من ``DELETE`` الفعلي.

    يضمن حفظ السجلات المالية والتاريخية (مثل الفواتير المدفوعة) حسب
    متطلبات المحاسبة والتدقيق.
    """

    is_active: Mapped[bool] = mapped_column(
        default=True, nullable=False, index=True, comment="هل السجل نشط؟"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="تاريخ الحذف المنطقي"
    )


class BaseModel(Base, TimestampMixin):
    """
    النموذج الأساسي المُصدَّر.

    يرث ``Base`` و ``TimestampMixin`` معاً. كل النماذج في المنصة ترث منه.
    """

    __abstract__ = True

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """
        تحويل النموذج إلى قاموس (بدون تحميل العلاقات).

        مفيد للطباعة في السجلات وتصدير JSON.
        """
        exclude = exclude or set()
        result: dict[str, Any] = {}
        for column in self.__table__.columns:
            name = column.name
            if name in exclude:
                continue
            value = getattr(self, name, None)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[name] = value
        return result

    def __repr__(self) -> str:  # pragma: no cover
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"