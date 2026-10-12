"""
اختبار تحويل روابط قاعدة البيانات
================================

كل مزوّد استضافة يعطي الرابط بصيغة مختلفة، وخطأ في التعامل معها يعني
انهيار التطبيق عند أول استعلام. هذه الحالات محفوظة هنا كاختبارات
دائمة لا كتحقّق لمرة واحدة.

الخطأ الأشيع: ``postgresql://`` بلا سائق ← SQLAlchemy يختار psycopg2
المتزامن ← انهيار التطبيق.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, _with_async_driver


#: (الرابط المُدخل، المتوقع غير متزامن، المتوقع المتزامن)
URL_CASES = [
    # Render: يعطي postgresql:// بلا سائق
    (
        "postgresql://user:pass@host:5432/db",
        "postgresql+asyncpg://user:pass@host:5432/db",
        "postgresql+psycopg2://user:pass@host:5432/db",
    ),
    # Neon / Heroku: postgres://
    (
        "postgres://user:pass@host/db",
        "postgresql+asyncpg://user:pass@host/db",
        "postgresql+psycopg2://user:pass@host/db",
    ),
    # سائق محدّد مسبقاً — يُRespect
    (
        "postgresql+asyncpg://user:pass@host/db",
        "postgresql+asyncpg://user:pass@host/db",
        "postgresql+psycopg2://user:pass@host/db",
    ),
    (
        "postgresql+psycopg2://user:pass@host/db",
        "postgresql+psycopg2://user:pass@host/db",
        "postgresql+psycopg2://user:pass@host/db",
    ),
    # psycopg3 — قرار المستخدم، لا نتدخّل
    (
        "postgresql+psycopg://user:pass@host/db",
        "postgresql+psycopg://user:pass@host/db",
        "postgresql+psycopg://user:pass@host/db",
    ),
    # SQLite
    (
        "sqlite+aiosqlite:///./data/test.db",
        "sqlite+aiosqlite:///./data/test.db",
        "sqlite:///./data/test.db",
    ),
    # كلمة مرور فيها محارف محجوزة — يجب أن تبقى كما هي
    (
        "postgresql://user:p%40ss%3Aword@host/db",
        "postgresql+asyncpg://user:p%40ss%3Aword@host/db",
        "postgresql+psycopg2://user:p%40ss%3Aword@host/db",
    ),
]


@pytest.mark.parametrize("url,expected_async,expected_sync", URL_CASES)
def test_driver_is_normalized(url: str, expected_async: str, expected_sync: str):
    """الرابط يُرجَع بسائق غير متزامن صحيح، ونسخته المتزامنة صالحة لـ Alembic."""
    settings = Settings(DATABASE_URL=url)
    assert settings.database_url == expected_async
    assert settings.sync_database_url == expected_sync


def test_empty_url_returns_empty():
    assert _with_async_driver("") == ""


def test_postgres_scheme_is_upgraded():
    """``postgres://`` اختصار شائع ويجب ترقيةه إلى ``postgresql://``."""
    assert _with_async_driver("postgres://h/db").startswith("postgresql+asyncpg://")


def test_render_connection_string_is_async():
    """
    حالة Render الفعلية: ``fromDatabase.connectionString``.

    هذا هو الرابط الذي سيصل في الإنتاج حرفياً، فلو اختار psycopg2
    المتزامن لانهار التطبيق فور أول استعلام.
    """
    render_url = "postgresql://mashia:AbC123xYz@aws-0-eu-central-1.compute.amazonaws.com/mashia_db"
    settings = Settings(DATABASE_URL=render_url)

    assert "+asyncpg" in settings.database_url
    assert "+psycopg2" not in settings.database_url
    assert "+psycopg2" in settings.sync_database_url


def test_production_requires_secure_cookie():
    """إعدادات الإنتاج ترفض ``COOKIE_SECURE=false``."""
    with pytest.raises(ValueError):
        Settings(
            APP_ENV="production",
            DEBUG=False,
            COOKIE_SECURE=False,
            SECRET_KEY="x" * 50,
        )


def test_production_turns_debug_off():
    """``DEBUG=true`` في الإنتاج يُخفى تلقائياً."""
    settings = Settings(
        APP_ENV="production",
        DEBUG=True,
        COOKIE_SECURE=True,
        SECRET_KEY="x" * 50,
    )
    assert settings.DEBUG is False


def test_secret_key_minimum_length():
    """مفتاح أقصر من 32 حرفاً مرفوض."""
    with pytest.raises(ValueError):
        Settings(SECRET_KEY="short")


def test_sqlite_parent_dir_is_created(tmp_path):
    """
    SQLite لا يُنشئ المجلد الأصل، بل يفشل بـ ``unable to open
    database file``.

    هذا يقع في الحاوية عند أول نشر: ``data/`` مستثناة من Git، فلو لم
    تُنشئه الإعدادات لتنشأ قاعدة البيانات لتنهار الخدمة عند الإقلاع.
    """
    target = tmp_path / "nested" / "deeper" / "app.db"
    assert not target.parent.exists()

    settings = Settings(DATABASE_URL=f"sqlite+aiosqlite:///{target.as_posix()}")
    _ = settings.database_url          # الخاصية هي التي تُنشئ المجلد

    assert target.parent.exists(), "مجلد SQLite لم يُنشأ"


def test_postgres_url_creates_no_dirs(tmp_path):
    """روابط PostgreSQL لا تُنشئ مجلدات — لا شيء لتفعله."""
    target = tmp_path / "should_not_appear"
    Settings(DATABASE_URL=f"postgresql://u:p@h/{target.name}").database_url

    assert not target.exists()
