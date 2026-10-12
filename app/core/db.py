"""
طبقة قاعدة البيانات (Database Layer)
====================================
المحرك، الجلسة، الـ Metadata، وكل أدوات الاتصال.
يعمل التطبيق بشكل غير متزامن (async) باستخدام SQLAlchemy 2.0.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# 1) الـ Base — الجذر لجميع النماذج
# ---------------------------------------------------------------------
class Base(DeclarativeBase):
    """الجذر المشترك لجميع نماذج ORM."""

    type_annotation_map: dict[Any, Any] = {}


# ---------------------------------------------------------------------
# 2) المحرك (Engine)
# ---------------------------------------------------------------------
def _engine_kwargs() -> dict[str, Any]:
    """بناء معاملات المحرك حسب نوع قاعدة البيانات."""
    common: dict[str, Any] = {
        "echo": settings.DB_ECHO,
        "pool_pre_ping": True,          # حماية من الاتصالات المقطوعة
        "future": True,
    }

    if settings.DB_DRIVER == "postgresql":
        common.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=settings.DB_POOL_RECYCLE,
        )
    elif ":memory:" in settings.database_url or settings.is_testing:
        # SQLite في الذاكرة يتطلب اتصالاً واحداً مشتركاً
        common["poolclass"] = StaticPool
        common.pop("pool_pre_ping", None)
    else:
        common["connect_args"] = {"check_same_thread": False, "timeout": 30}

    return common


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    **_engine_kwargs(),
)


# ---------------------------------------------------------------------
# 3) مُصنع الجلسات
# ---------------------------------------------------------------------
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,      # نُبقي الكائنات متاحة بعد commit
    autoflush=False,
)


# ---------------------------------------------------------------------
# 4) إعداد SQLite لمتطلبات WAL والمفاتيح الأجنبية
# ---------------------------------------------------------------------
def _sqlite_regexp(pattern: str, value: str | None) -> int:
    """محاكاة عامل ``REGEXP`` في SQLite باستخدام مكتبة re."""
    if value is None:
        return 0
    try:
        return 1 if re.search(pattern, value) else 0
    except re.error:
        return 0


if settings.DB_DRIVER == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """تفعيل WAL والمفاتيح الأجنبية ووضع busy_timeout على SQLite."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()
        cursor = None

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_regexp_fn(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """تسجيل دالة REGEXP للاستخدام داخل استعلامات SQL."""
        dbapi_connection.create_function("REGEXP", 2, _sqlite_regexp)


# ---------------------------------------------------------------------
# 5) دورة حياة الجلسة
# ---------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    اعتماد FastAPI الذي يوفّر جلسة قاعدة بيانات لكل طلب.

    يضمن الإغلاق دائماً حتى عند حدوث خطأ.

    **ملاحظة مهمة**: لا نحقن شرط العزل هنا. حقنه يعتمد على معرفة هوية
    المستخدم، وقد لا تكون قد حُلّت بعد عند إنشاء الجلسة. لذلك يتولّى
    المستمع ``do_orm_execute`` في :mod:`app.core.tenancy` تطبيق العزل
    لحظة تنفيذ كل استعلام، وهو ما يضمن الحماية بغضّ النظر عن ترتيب
    الاعتماديات.
    """
    db = AsyncSession(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield db

        if db.in_transaction():
            await db.commit()
    except Exception:
        if db.in_transaction():
            await db.rollback()
        raise
    finally:
        await db.close()


async def get_readonly_db() -> AsyncSession:
    """جلسة للقراءة فقط (للتقارير والتصدير)."""
    return AsyncSession(bind=engine, expire_on_commit=False, autoflush=False)


# ---------------------------------------------------------------------
# 6) أدوات مساعدة
# ---------------------------------------------------------------------
async def create_all() -> None:
    """إنشاء جميع الجداول مباشرة (يُستخدم في الاختبارات والتطوير)."""
    from app import models  # noqa: F401  (يسجّل كل النماذج)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("تم إنشاء جميع الجداول بنجاح.")


async def drop_all() -> None:
    """حذف جميع الجداول (للاختبارات فقط)."""
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.warning("تم حذف جميع الجداول.")


async def healthcheck() -> bool:
    """فحص سريع لاتصال قاعدة البيانات."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover
        logger.error("فشل فحص قاعدة البيانات: %s", exc)
        return False


async def dispose_engine() -> None:
    """إغلاق المحرك عند إيقاف التطبيق."""
    await engine.dispose()


def utcnow() -> datetime:
    """
    الوقت الحالي بدون منطقة زمنية (UTC) — نقطة مرجعية موحدة.

    نُرجع ``naive`` عمداً لأن أعمدة ``DateTime`` في SQLite غير مدركة
    للمنطقة الزمنية، وخلط القيمتين (واعية بلا واعية) يسبّب أخطاء
    مقارنة صامتة.

    (``datetime.utcnow()`` مهجور في Python 3.12+، فهذه بديله الآمن.)

    هذا هو **المصدر الوحيد** للوقت في المشروع. أي استدعاء مباشر لـ
    ``datetime.now(timezone.utc)`` يُعيد قيمة واعية وتسبّب انفجاراً عند
    أول طرح — راجع ``scripts/unify_utcnow.py``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def safe_get(
    db: AsyncSession,
    model_cls: type,
    pk: Any,
) -> Any | None:
    """
    جلب سجل بالمعرّف مع تحقّق إضافي من ملكيته للنادي الحالي.

    لماذا نحتاجها رغم أن ``session.get`` محميّ أصلاً؟
    لأن ``session.get`` يستجيب من *خريطة الهوية* (identity map) قبل
    الوصول لقاعدة البيانات، فيعيد كائناً حُمِّل سابقاً دون استعلام.

    في الاستخدام الطبيعي هذا آمن -- كل طلب له جلسته الخاصة ولا يتغيّر
    سياق المستأجر داخلها. لكن ``safe_get`` يضيف طبقة تحقق دفاعية تضمن
    الرجوع ``None`` بدل تسريب كائن مخزّن، وتوحّد السلوك في كل طبقة الخدمات.

    تُرجع ``None`` إذا لم يوجد السجل أو لم يكن ضمن نطاق المستخدم (ما
    يجعل استجابات الـ API لا تكشف وجود سجلات في أندية أخرى).
    """
    obj = await db.get(model_cls, pk)
    if obj is None:
        return None

    from app.core.tenancy import assert_same_club, get_tenant_context

    ctx = get_tenant_context()
    if ctx is None or ctx.bypasses_isolation:
        return obj

    owner = getattr(obj, "club_id", None)
    if owner is None:
        return obj

    if ctx.scope_mode == "parent":
        if getattr(model_cls, "__parent_scope__", None) == "id:athlete":
            if int(owner) and int(pk) in ctx.athlete_ids:
                return obj
            return None
        # بقية الجداول تُفلتر على مستوى الاستعلام نفسه
        return obj

    if ctx.club_id is not None and int(owner) != int(ctx.club_id):
        logger.warning(
            "safe_get حجب سجلاً خارج النطاق: %s#%s", model_cls.__name__, pk
        )
        return None

    assert_same_club(obj, ctx.club_id, model_cls.__name__)
    return obj