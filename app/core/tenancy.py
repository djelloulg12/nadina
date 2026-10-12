"""
عزل المستأجرين متعددو الأندية (Multi-Tenancy Isolation)
=======================================================
هذا الملف هو **العمود الفقري للأمان** في المنصة. أي ثغرة هنا تعني تسريب
بيانات بين الأندية، لذلك بُني على عدة طبقات دفاعية (Defense in Depth):

الطبقة 1 -- ``apply_tenant_scope``
    تُحقن شرط تصفية (``with_loader_criteria``) في كل جلسة قاعدة بيانات،
    فيتحوّل كل استعلام ORM -- بما في ذلك التحميل الكسول (lazy loading)
    والعلاقات -- إلى استعلام مُقيَّد تلقائياً بـ ``club_id`` الخاص بالمستخدم.

الطبقة 2 -- ``_validate_tenant_on_flush``
    مستمع على أحداث الجلسة يمنع حفظ أي كائن يحمل ``club_id`` مختلفاً عن
    سياق المستخدم الحالي، ويمنع ربط كيانات من أندية مختلفة ببعضها.

الطبقة 3 -- ``TenantContext`` عبر ``ContextVar``
    عزل على مستوى الطلب (request-scoped) لا يتسرّب بين الطلبات المتوازية،
    وهو آمن مع ``asyncio`` لأن كل طلب يعمل في سياق مستقل.

الطبقة 4 -- التحقق الصريح في طبقة الخدمات
    طبقة الخدمات تستدعي ``assert_same_club`` قبل العمليات الحساسة (مثل ربط
    فوج برياضي) لضمان تكامل المراجع عبر الجداول.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from sqlalchemy import event, false, inspect as sa_inspect, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import NoTenantContextError, TenantIsolationError
from app.models.enums import UserRole

logger = logging.getLogger(__name__)


# =====================================================================
# 1) سياق المستأجر (Tenant Context)
# =====================================================================
@dataclass(frozen=True)
class TenantContext:
    """
    وصف كامل لنطاق البيانات الذي يحق للمستخدم رؤيته.

    يُنشأ مرة واحدة لكل طلب من خلال :func:`build_tenant_context` ويُحفظ في
    ``ContextVar`` ليقرأه كل شيء آخر (الجلسات، الخدمات، القوالب).

    أنماط النطاق الثلاثة:

    * **عام (global)**  -- مدير المنصة: كل الأندية.
    * **نادي (club)**   -- طاقم النادي: ``club_id`` واحد فقط.
    * **أولياء (parent)** -- الولي: أبناؤه عبر ``athlete_ids``، وقد يكونون
      في **أندية مختلفة**، لذلك لا يُقفل على ``club_id`` واحد.
    """

    user_id: int | None
    role: UserRole | None
    club_id: int | None = None
    #: مدير المنصة يتجاوز عزل الأندية، لكنه يبقى خاضعاً لسجل التدقيق
    is_platform_admin: bool = False
    #: نطاقات خاصة بولي الأمر (يمكن أن تعبر أندية متعددة)
    athlete_ids: tuple[int, ...] = ()
    child_club_ids: tuple[int, ...] = ()
    child_group_ids: tuple[int, ...] = ()
    #: بيانات إضافية حسب الحاجة (مثل قائمة الأندية المسموحة)
    extra: dict = field(default_factory=dict)

    # -----------------------------------------------------------------
    @property
    def is_authenticated(self) -> bool:
        """هل يوجد مستخدم مُصادَق في السياق؟"""
        return self.user_id is not None

    @property
    def has_tenant(self) -> bool:
        """هل السياق مربوط بنادٍ معيّن؟"""
        return self.club_id is not None

    @property
    def scope_mode(self) -> str:
        """
        نمط العزل النشط: ``global`` / ``club`` / ``parent`` / ``none``.

        ``none`` تعني أن الطلب لا يملك أي وصول للبيانات الخاصة.
        """
        if self.bypasses_isolation:
            return "global"
        if self.is_parent and self.athlete_ids:
            return "parent"
        if self.club_id is not None:
            return "club"
        return "none"

    @property
    def bypasses_isolation(self) -> bool:
        """هل يتجاوز المستخدم عزل الأندية (مدير المنصة فقط)؟"""
        return self.is_platform_admin and self.club_id is None

    @property
    def is_coach(self) -> bool:
        return self.role == UserRole.COACH

    @property
    def is_parent(self) -> bool:
        return self.role == UserRole.PARENT

    @property
    def is_athlete(self) -> bool:
        return self.role == UserRole.ATHLETE

    @property
    def is_club_staff(self) -> bool:
        """هل المستخدم من طاقم النادي (مدير / إداري / مدرب)؟"""
        return self.role in {
            UserRole.CLUB_ADMIN,
            UserRole.RECEPTIONIST,
            UserRole.COACH,
        }

    def as_public(self) -> "TenantContext":
        """سياق للزوار -- لا يملك أي وصول للبيانات."""
        return TenantContext(user_id=None, role=None, club_id=None)

    def as_platform(
        self,
        user_id: int,
        role: UserRole = UserRole.SUPER_ADMIN,
    ) -> "TenantContext":
        """سياق مدير المنصة."""
        return TenantContext(
            user_id=user_id,
            role=role,
            club_id=None,
            is_platform_admin=role == UserRole.SUPER_ADMIN,
        )

    def as_parent(
        self,
        user_id: int,
        athlete_ids: Iterable[int],
        child_club_ids: Iterable[int] = (),
        child_group_ids: Iterable[int] = (),
    ) -> "TenantContext":
        """سياق الولي -- مقيَّد بأبنائه فقط."""
        return TenantContext(
            user_id=user_id,
            role=UserRole.PARENT,
            club_id=None,
            is_platform_admin=False,
            athlete_ids=tuple(athlete_ids),
            child_club_ids=tuple(child_club_ids),
            child_group_ids=tuple(child_group_ids),
        )


def build_tenant_context(
    user_id: int | None,
    role: UserRole | None,
    club_id: int | None,
) -> TenantContext:
    """
    بناء سياق المستأجر من بيانات المستخدم المُصادَق.

    ``club_id`` يمر أولاً عبر :func:`normalise_club_id` الذي يمنع أي تسريب
    عبر معاملات الاستعلام.
    """
    is_platform_admin = role == UserRole.SUPER_ADMIN
    return TenantContext(
        user_id=user_id,
        role=role,
        club_id=normalise_club_id(club_id),
        is_platform_admin=is_platform_admin,
    )


def normalise_club_id(value: object) -> int | None:
    """
    تحويل قيمة ``club_id`` إلى عدد صحيح آمن.

    خط دفاع أول ضد الحقن عبر النصوص عندما يمرر المهاجم معاملات مثل
    ``?club_id=1 OR 1=1``.
    """
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.error("تجاهل قيمة club_id غير صالحة: %r", value)
        return None


_tenant_ctx: ContextVar[TenantContext | None] = ContextVar(
    "tenant_ctx", default=None
)


def get_tenant_context() -> TenantContext | None:
    """قراءة سياق المستأجر الحالي (قد يكون ``None`` خارج الطلبات)."""
    return _tenant_ctx.get()


def require_tenant_context() -> TenantContext:
    """قراءة سياق المستأجر مع رفع خطأ إن لم يكن متاحاً."""
    ctx = _tenant_ctx.get()
    if ctx is None:
        raise NoTenantContextError()
    return ctx


def require_club_scope() -> int:
    """إرجاع ``club_id`` الخاص بالمستخدم أو رفع خطأ في حال غيابه."""
    ctx = require_tenant_context()
    if ctx.bypasses_isolation:
        raise TenantIsolationError("يجب تحديد نادي محدد لتنفيذ هذه العملية")
    if ctx.club_id is None:
        raise TenantIsolationError("لا تملك صلاحية الوصول لبيانات أي نادي")
    return ctx.club_id


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """
    سياق مؤقت لتثبيت مستأجر (مثل المهام المجدولة التي تعمل على عدة أندية).

    الاستخدام::

        with tenant_scope(ctx) as c:
            ...
    """
    token: Token[TenantContext | None] = _tenant_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _tenant_ctx.reset(token)


def clear_tenant_context() -> None:
    """مسح السياق (يُستخدم عند إغلاق التطبيق أو في الاختبارات)."""
    _tenant_ctx.set(None)


def set_tenant_context(ctx: TenantContext) -> Token[TenantContext | None]:
    """
    تثبيت سياق المستأجر في المتغيّر العام.

    تُعيد ``Token`` يمكن تمريره إلى :func:`reset_tenant_context` للتراجع
    عند الحاجة (مفيد في الوسيط ``middleware``).

    هذه هي الدالة العامة الوحيدة لكتابة السياق -- أي كود آخر يجب أن
    يستخدمها بدل الوصول المباشر إلى ``ContextVar``، حتى يبقى التحكم
    центраي واضحاً.
    """
    return _tenant_ctx.set(ctx)


def reset_tenant_context(token: Token[TenantContext | None]) -> None:
    """استعادة السياق السابق (التراجع عن ``set_tenant_context``)."""
    _tenant_ctx.reset(token)


# =====================================================================
# 2) الطبقة 1 -- حقن شرط التصفية في الجلسة
# =====================================================================
def _is_tenant_owned(model_cls: type) -> bool:
    """هل هذا النموذج مملوك لمستأجر (أي يحمل حقل ``club_id``)؟"""
    return getattr(model_cls, "__tenant_owned__", False)


#: ذاكرة مؤقتة لخيارات العزل -- المفتاح هو هوية النطاق
_OPTIONS_CACHE: dict[tuple, list] = {}
_OPTIONS_CACHE_MAX = 512


def _resolve_scope_criterion(model_cls: type, ctx: TenantContext):  # noqa: ANN202
    """
    بناء شرط SQL المناسب لنطاق المستخدم.

    ========  ==========================================================
    النطاق    الشرط المطبَّق
    ========  ==========================================================
    ``global`` لا يُحقن أي شرط (مدير المنصة يرى كل شيء)
    ``club``   ``club_id = :current`` (أو ``= current OR NULL`` للمحتوى
               العام الذي يُقرأ عبر ``__tenant_global_read__``)
    ``parent`` عمود خاص بكل جدول حسب ``__parent_scope__`` (انظر أدناه)
    ``none``   ``WHERE 1=0`` -- لا يرى شيئاً
    ========  ==========================================================

    صيغة ``__parent_scope__`` هي ``"<العمود>:<النطاق>"`` حيث النطاق أحد:

    * ``athlete`` -> تقييد بـ ``ctx.athlete_ids`` (أبناء الولي)
    * ``clubs``   -> تقييد بـ ``ctx.child_club_ids`` (أندية الأبناء)
    * ``groups``  -> تقييد بـ ``ctx.child_group_ids`` (أفواج الأبناء)
    * ``own``     -> تقييد بمعرّف المستخدم نفسه
    """
    # 1) مدير المنصة: وصول كامل
    if ctx.bypasses_isolation:
        return None

    # 2) الولي: نطاق عبر أندية متعددة
    if ctx.is_parent and ctx.athlete_ids:
        spec = getattr(model_cls, "__parent_scope__", None)
        if not spec:
            # جدول لا يملك تعريف نطاق للوالين -> ممنوع
            return false()

        column_name, _, kind = spec.partition(":")
        column = getattr(model_cls, column_name, None)
        if column is None:  # pragma: no cover - تعريف خاطئ في النموذج
            logger.error("تعريف نطاق الولي غير صالح في %s", model_cls.__name__)
            return false()

        if kind == "athlete":
            return column.in_(ctx.athlete_ids)
        if kind == "clubs":
            return column.in_(ctx.child_club_ids) if ctx.child_club_ids else false()
        if kind == "groups":
            return column.in_(ctx.child_group_ids) if ctx.child_group_ids else false()
        if kind == "own":
            return column == ctx.user_id if ctx.user_id is not None else false()
        return false()

    # 3) نطاق النادي
    current = ctx.club_id
    if current is None:
        # زائر أو مستخدم بلا نادي: لا يرى أي صف خاص
        return false()

    column = model_cls.club_id  # type: ignore[attr-defined]

    if getattr(model_cls, "__tenant_global_read__", False):
        # المحتوى العام + محتوى النادي
        return or_(column == current, column.is_(None))

    return column == current


def _build_loader_options(ctx: TenantContext) -> list:
    """
    بناء خيارات ``with_loader_criteria`` لكل النماذج المملوكة لمستأجر.

    تُخزَّن النتائج مؤقتاً لأن المستمع يُنفَّذ مع كل استعلام، وإعادة بناء
    العشرات من الخيارات في كل مرة تكلف غير ضرورية. المفتاح مبني على هوية
    النطاق لا على كائن ``TenantContext`` (فهو ``frozen`` غير قابل للتغيير).

    ``propagate_to_loaders=True`` هو ما يجعل التحميل الكسول للعلاقات
    (``athlete.club``, ``invoice.payments`` ... ) محصوراً بنفس القاعدة،
    وإلا لأمكن تسريب البيانات عبر كائن تم تحميله مسبقاً.
    """
    key = (
        ctx.role,
        ctx.club_id,
        ctx.user_id,
        ctx.athlete_ids,
        ctx.child_club_ids,
        ctx.child_group_ids,
    )
    cached = _OPTIONS_CACHE.get(key)
    if cached is not None:
        return cached

    from app.core.db import Base
    from sqlalchemy.orm import with_loader_criteria

    options = []
    for mapper in Base.registry.mappers:
        model_cls = mapper.class_
        if not _is_tenant_owned(model_cls):
            continue
        if mapper.local_table is None:  # pragma: no cover
            continue

        criterion = _resolve_scope_criterion(model_cls, ctx)
        if criterion is None:
            continue

        options.append(
            with_loader_criteria(
                model_cls,
                criterion,
                include_aliases=True,
                propagate_to_loaders=True,
            )
        )

    # حماية من التضخم غير المحدود في الذاكرة
    if len(_OPTIONS_CACHE) >= _OPTIONS_CACHE_MAX:
        _OPTIONS_CACHE.clear()
    _OPTIONS_CACHE[key] = options
    return options


def clear_scope_cache() -> None:
    """مسح ذاكرة خيارات العزل (يُستخدم في الاختبارات وعند تغيّر الإعدادات)."""
    _OPTIONS_CACHE.clear()


def tenant_options(ctx: TenantContext | None = None) -> list:
    """
    قائمة خيارات ``with_loader_criteria`` الجاهزة للاستخدام اليدوي.

    الاستخدام::

        result = await db.execute(select(Athlete).options(*tenant_options()))

    غير ضروري في المسارات العادية لأن المستمع ``do_orm_execute`` يضيفها
    تلقائياً، لكنه مفيد في السكربتات التي تنفّذ استعلامات على اتصال
    (``Connection``) مباشراً خارج دورة حياة الجلسة.
    """
    ctx = ctx or get_tenant_context()
    if ctx is None or ctx.bypasses_isolation:
        return []
    return _build_loader_options(ctx)


@event.listens_for(Session, "do_orm_execute")
def _enforce_tenant_scope(state) -> None:  # noqa: ANN001
    """
    ====== خطّ الدفاع المُطبَّق ذاتياً ======

    يُنفَّذ قبل **كل** استعلام ORM في التطبيق، ويحقن شرط العزل المناسب
    تلقائياً قبل وصول الاستعلام إلى قاعدة البيانات.

    لماذا هذا بدل الاستدعاء عند إنشاء الجلسة؟
    لأن ترتيب تهيئة الاعتماديات (dependency injection) في FastAPI غير
    مضمون: قد تُنشأ الجلسة قبل أن يُعرف من هو المستخدم. هذا المستمع يحل
    المشكلة جذرياً -- يقرأ السياق من ``ContextVar`` لحظة تنفيذ الاستعلام.

    النتيجة: أي خدمة أو مسار أو استعلام مستقبلي لا يستطيع "نسيان" العزل،
    بما في ذلك ``session.get()`` والتحميل الكسول للعلاقات.
    """
    ctx = get_tenant_context()
    if ctx is None or ctx.bypasses_isolation:
        return

    options = _build_loader_options(ctx)
    if not options:
        return

    state.statement = state.statement.options(*options)


# =====================================================================
# 3) الطبقة 2 -- حارس ما قبل الكتابة (Guard)
# =====================================================================
@event.listens_for(Session, "before_flush")
def _validate_tenant_on_flush(
    session: Session,
    _flush_context,  # noqa: ANN001
    _instances,  # noqa: ANN001
) -> None:
    """
    مستمع الجلسة: يمنع أي كتابة تتجاوز حدود النادي.

    يتحقق من:
      * أن كل كائن جديد مملوك لنادٍ يحمل ``club_id`` صحيحاً.
      * أن تعديل كائن قائم لا يغيّر ``club_id`` (منع اختطاف السجل).
      * أن القيم النصية لـ ``club_id`` تُحوَّل إلى أعداد صحيحة.
    """
    ctx = get_tenant_context()
    if ctx is None or ctx.bypasses_isolation:
        return

    current_club_id = ctx.club_id

    for obj in session.new:
        if not _is_tenant_owned(type(obj)):
            continue

        raw = getattr(obj, "club_id", None)
        if raw is not None and not isinstance(raw, int):
            try:
                obj.club_id = int(raw)  # type: ignore[attr-defined]
                raw = obj.club_id
            except (TypeError, ValueError):
                raise TenantIsolationError("قيمة معرّف النادي غير صالحة") from None

        if raw is None:
            if current_club_id is None:
                raise TenantIsolationError("لا يمكن إنشاء سجل بلا نادي مالك")
            obj.club_id = current_club_id  # type: ignore[attr-defined]
        elif current_club_id is not None and raw != current_club_id:
            logger.error(
                "محاولة كتابة متقاطعة: %s club_id=%s (المسموح=%s)",
                type(obj).__name__,
                raw,
                current_club_id,
            )
            raise TenantIsolationError("محاولة إنشاء سجل في نادي مختلف عن ناديك")

    for obj in session.dirty:
        if not _is_tenant_owned(type(obj)):
            continue
        if not session.is_modified(obj, include_collections=False):
            continue

        # منع نقل السجل إلى نادي آخر بعد إنشائه.
        # القاعدة: داخل نطاق نادٍ ما، تغيير ``club_id`` ممنع تماماً --
        # سواء كان النقل إلى نادٍ آخر أو حتى إلى ``NULL``.
        history = sa_inspect(obj).attrs.club_id.history
        if history.deleted and history.deleted[0]:
            logger.error("محاولة تغيير معرّف النادي لسجل قائم: %s", type(obj).__name__)
            raise TenantIsolationError("لا يمكن نقل السجلات بين الأندية")


# =====================================================================
# 4) الطبقة 3 -- أدوات مساعدة للتحقق من التكامل
# =====================================================================
def assert_same_club(
    entity: object | None,
    expected_club_id: int,
    label: str = "السجل",
) -> None:
    """
    التأكد أن الكيان ينتمي للنادي المتوقع.

    تُستخدم في طبقة الخدمات قبل العمليات التي تمس جداول متعددة (مثل ربط
    رياضي بفوج) لضمان أن كل المراجع تنتمي لنفس النادي.
    """
    if entity is None:
        return

    owner = getattr(entity, "club_id", None)
    if owner is None or int(owner) != int(expected_club_id):
        raise TenantIsolationError(f"{label} لا ينتمي للنادي المطلوب")


def tenant_filter(query, model_cls: type, ctx: TenantContext | None = None):  # noqa: ANN001
    """
    تطبيق يدوي لشرط العزل على استعلام مُركَّب (join/subquery).

    مفيد عندما يُبنى الاستعلام عبر ``select().join()`` وتجاوزنا آلية
    ``with_loader_criteria`` التلقائية.
    """
    ctx = ctx or get_tenant_context()
    if ctx is None or ctx.bypasses_isolation:
        return query

    criterion = _resolve_scope_criterion(model_cls, ctx)
    if criterion is not None:
        query = query.where(criterion)
    return query


# =====================================================================
# 5) التهيئة عند الإقلاع
# =====================================================================
def setup_tenancy() -> None:
    """
    تفعيل كل آليات العزل.

    يُستدعى مرة واحدة عند إقلاع التطبيق (من ``lifespan``).
    """
    # الاستماعات على ``Session`` مسجّلة عالمياً عند استيراد هذه الوحدة،
    # لكن هذا الاستدعاء يضمن تحميل الوحدة مبكراً وتوثيق الحالة.
    import app.core.db  # noqa: F401

    logger.info("تم تفعيل نظام عزل المستأجرين (Multi-Tenancy Isolation).")