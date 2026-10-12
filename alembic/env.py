"""
بيئة Alembic (الهجرات)
======================
تقرأ رابط قاعدة البيانات من إعدادات التطبيق (:mod:`app.core.config`)
بدل تثبيته هنا، فيبقى ملف ``.env`` المصدر الوحيد للحقيقة -- فلا
تنتج بيئة تطوير عناوين اتصال لا تطابق الإنتاج.

لاحظ استخدام الرابط **المتزامن**: Alembic يعمل بشكل متزامن، بينما
يعمل التطبيق بشكل غير متزامن.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# نضيف جذر المشروع إلى المسار حتى تعمل الاستيرادات
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models  # noqa: E402,F401  (يسجّل كل النماذج في Metadata)

config = context.config

# نتجاهل إعداد ``sqlalchemy.url`` في alembic.ini
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: Metadata الذي يعرفه Alembic
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    تشغيل الترحيلات دون اتصال بقاعدة بيانات (توليد SQL فقط).

    الاستخدام::

        alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,      # ضروري لـ SQLite
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """تشغيل الترحيلات مع الاتصال بقاعدة البيانات."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,      # ضروري لتعديل الأعمدة على SQLite
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()