"""
نقطة انطلاق الإنتاج
===================

المنصّات المجانية (Render، Railway، Fly) تشغّل أمر واحداً. هذا السكربت
يجمع ما يجب أن يحدث بالترتيب الصحيح قبل أن يستقبل التطبيق أول طلب:

1. **الترحيلات** — ``alembic upgrade head``. ينشئ الجداول أو يرقّيها.
   تشغيلها هنا بدل ``create_all`` يحافظ على مصدر واحد للحقيقة.
2. **البذر** — *اختياري* ومراقب بمتغيّر بيئة. لا نزرع البيانات التجريبية
   تلقائياً في الإنتاج: زرعها على قاعدة بيانات فيها مستخدمون حقيقيون
   خطأ صامت. نزرعها فقط إذا كان الجدول فارغاً تماماً *و* طُلب ذلك.

ثم نسلّم التنفيذ لـ Uvicorn في المنفذ الذي تختاره المنصّة.

ملاحظة عن ``exec``: نستخدمه ليحلّ محل Uvicorn العملية الحالية، فيستقبل
الإشارات (SIGTERM) ويُنظَّف بشكل صحيح عند إيقاف المنصّة.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# نضع جذر المشروع في مسار الاستيراد *قبل* أي استيراد آخر، ونزيل
# مجلد السكربتات نفسه. السبب: عند تشغيل ``python scripts/startup.py``
# يضيف Python مجلد ``scripts/`` تلقائيًا لمسار البحث.
# المجلد على ``app.py`` أو تسبّب في تظليل الحزمة، الاستيراد يُحمَّل ``app``
# كحزمة بلا وحدات (namespace package) فيفشل كل استيراد لاحق بـ
# ``cannot import name ... (unknown location)``.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")

sys.path[:] = [
    p for p in sys.path
    if os.path.abspath(p or ".") not in (_SCRIPTS,)
]
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core.config import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("mashia.startup")


async def run_migrations() -> None:
    """تطبيق ترحيلات Alembic حتى أحدث نسخة."""
    from app.core import schema

    logger.info("▸ تطبيق الترحيلات...")

    current = await schema.current_revision()
    head = await schema.head_revision()

    if current == head:
        logger.info("  المخطط محدَّث (لا ترحيلات جديدة).")
        return

    logger.info("  من %s ← إلى %s", current or "—", head or "—")
    await schema.upgrade("head")
    logger.info("  تمّت الترحيلات.")


async def maybe_seed() -> None:
    """
    بذر البيانات التجريبية — بشروط صارمة.

    نزرع فقط إذا طلب ذلك صراحةً عبر ``SEED_ON_EMPTY=true`` **و** كانت
    قاعدة البيانات فارغة. الشرط الثاني يمنع زرع بيانات تجريبية فوق
    بيانات حقيقية لو نُسخ السكربت إلى بيئة إنتاج بالخطأ.
    """
    if os.getenv("SEED_ON_EMPTY", "").lower() not in ("1", "true", "yes"):
        return

    from sqlalchemy import func, select

    from app.core.db import SessionLocal, dispose_engine
    from app.models import Club

    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(Club.id)))).scalar() or 0

    if count:
        logger.info("▹ تخطّي البذر: القاعدة تحتوي %d نادياً بالفعل.", count)
        await dispose_engine()
        return

    logger.info("▸ القاعدة فارغة — بذر البيانات التجريبية...")
    from scripts.seed import main as seed_main

    await seed_main()
    logger.info("  اكتمل البذر.")


async def main() -> int:
    logger.info("=" * 62)
    logger.info("  %s — بدء الإنتاج", settings.APP_NAME)
    logger.info("  البيئة: %s | السائق: %s", settings.APP_ENV, settings.DB_DRIVER)
    logger.info("=" * 62)

    try:
        await run_migrations()
        await maybe_seed()
    except Exception:
        logger.exception("فشل التحضير قبل الإقلاع")
        return 1

    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    workers = int(os.getenv("WEB_CONCURRENCY", "1"))

    logger.info("▸ التشغيل على %s:%d (%d عامل)", host, port, workers)

    # نسلّم التنفيذ لـ uvicorn داخل نفس العملية: ``server.run`` يستقبل
    # الإشارات ويخرج برمز حالة صحيح، بخلاف استدعاء فرعي منفصل.
    # عن ``proxy_headers``: لا نمرّره هنا إطلاقاً. التطبيق نفسه يركّب
    # ProxyHeadersMiddleware مع قائمة ``TRUSTED_PROXIES`` محدّدة (في
    # main.py). تمرير ``forwarded_allow_ips="*"`` من uvicorn كان يُلغي
    # تلك القائمة بصمت — لأن وسيط uvicorn يعمل خارج التطبيق فيسبقها،
    # ويجعل ``X-Forwarded-For`` قابلة للتزوير من أي عميل.
    # قائمة واحدة موثوقة خير من قائمتين إحداهما مفتوحة.
    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        workers=workers if workers > 1 else None,
        timeout_keep_alive=65,   # أقل من مهلة المنصّة (100 ثانية)
    )
    server = uvicorn.Server(config)
    await server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
