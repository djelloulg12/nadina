"""
أدوات المزامنة بين المخطط والترحيلات
===================================

يضمن هذا الملف **مصدراً واحداً للحقيقة** للمخطط: ملفات Alembic لا
``Base.metadata.create_all``.

الفرق ليس شكلياً -- حين يُنشأ المخطط من ``create_all`` لا يعرف Alembic
حالة قاعدة البيانات، فيحكم ``alembic check`` بأنها «ليست محدَّثة» ويقترح
ترحيلات فارغة تخدع المطور. الاعتماد على الترحيلات يجعل المخطط المُنشأ
والـ migration المرجعي متطابقين دائماً، ويجعل ``alembic check`` فحوصاً
حقيقياً لانحراف المخطط.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.db import Base, engine

logger = logging.getLogger(__name__)

#: جذر المشروع: نبحث عن ``alembic.ini`` صعوداً بدل الاعتماد على عدد
#: المستويات، حتى لا ينكسر المسار عند نقل ملف.
ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "alembic.ini").exists()),
    Path(__file__).resolve().parent.parent,
)

#: جدول Alembic الذي يتتبّع رقم الترحيل المطبَّق
VERSION_TABLE = "alembic_version"


async def has_version_table() -> bool:
    """هل قاعدة البيانات مرتبطة بـ Alembic؟"""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": VERSION_TABLE},
        )
        if result.first():
            return True

        # قواعد أخرى غير SQLite
        try:
            await conn.execute(text(f"SELECT 1 FROM {VERSION_TABLE} LIMIT 1"))
        except Exception:
            return False
        return True


async def current_revision() -> str | None:
    """رقم الترحيل المطبَّق حالياً، أو ``None`` إن لم تكن القاعدة مُهيّأة."""
    if not await has_version_table():
        return None

    async with engine.connect() as conn:
        result = await conn.execute(text(f"SELECT version_num FROM {VERSION_TABLE} LIMIT 1"))
        row = result.first()
        return row[0] if row else None


async def drop_everything() -> None:
    """
    حذف كل الجداول بما فيها جدول التتبّع.

    نستخدم ``checkfirst`` مع ``DROP ... CASCADE`` للجداول حتى نتخلّص من
    قيود المفاتيح الأجنبية التي تمنع الحذف المباشر على SQLite.
    """
    from app import models  # noqa: F401  (يسجّل كل النماذج)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, checkfirst=True)

        # جدول التتبّع ليس جزءاً من Metadata، فنحذفه صراحةً
        await conn.execute(text(f"DROP TABLE IF EXISTS {VERSION_TABLE}"))

    logger.warning("تم حذف جميع الجداول.")


async def _run_alembic(*args: str) -> int:
    """تشغيل أمر Alembic في عملية منفصلة (متزامنة)."""
    cmd = [sys.executable, "-m", "alembic", *args]
    logger.info("تشغيل: %s", " ".join(cmd))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()

    if process.returncode != 0:
        output = (stdout or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"فشل أمر Alembic: {' '.join(args)}\n{output}")

    return process.returncode or 0


async def upgrade(target: str = "head") -> None:
    """تطبيق الترحيلات حتى ``target`` (افتراضياً: أحدث نسخة)."""
    await _run_alembic("upgrade", target)


async def head_revision() -> str | None:
    """قراءة رقم أحدث ترحيل من مجلد الترحيلات (بدون اتصال)."""
    return await _read_head()


async def stamp(target: str = "head") -> None:
    """
    وسم القاعدة برقم ترحيل **دون** تشغيله.

    يُستخدم فقط عند الترحيل من ``create_all`` إلى الترحيلات على قاعدة
    بيانات قائمة تحتوي فعلاً على المخطط الصحيح.
    """
    await _run_alembic("stamp", target)


async def sync_schema(reset: bool = False, quiet: bool = True) -> str:
    """
    توحيد المخطط مع الترحيلات.

    * ``reset=False``: يطبّق الترحيلات الناقصة فقط (آمن للتشغيل المتكرر).
    * ``reset=True``:  يحذف كل شيء ثم يطبّق الترحيلات من الصفر.

    نُغلق المحرك قبل وبعد لأن Alembic يعمل في عملية منفصلة، ولأن
    SQLite يقفل الملف أثناء الكتابة.
    """
    from app.core.db import dispose_engine

    if reset:
        await drop_everything()

    await dispose_engine()

    if not quiet:
        revision = await _read_head()
        current = await current_revision()
        if reset:
            print(f"🗑️  إعادة بناء المخطط حتى {revision[:8] if revision else '—'}...")
        elif current != revision:
            print(f"⬆️  تطبيق ترحيلات جديدة ({current or '—'} ← {revision or '—'})...")
        else:
            print("✓ المخطط محدَّث — لا ترحيلات جديدة.")

    try:
        await upgrade("head")
    finally:
        # نُعيد المحرك للاستخدام بعد كتابة Alembic
        await dispose_engine()

    return await current_revision() or ""


async def _read_head() -> str | None:
    """قراءة رقم أحدث ترحيل من مجلد الترحيلات (بدون اتصال)."""
    script = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "heads",
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await script.communicate()
    if script.returncode != 0 or not stdout:
        return None

    lines = (stdout or b"").decode("utf-8", errors="replace").strip().splitlines()
    return lines[0].strip() if lines else None