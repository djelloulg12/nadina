"""
إعدادات pytest العامة.

**مهم**: متغيرات البيئة تُضبط قبل أي استيراد لحزم التطبيق، حتى تقرأها
:mod:`app.core.config` عند تحميلها لأول مرة.
"""

from __future__ import annotations

import os

# --- قاعدة بيانات معزولة للاختبارات (ذاكرة فقط) ---
os.environ["TESTING"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["DB_DRIVER"] = "sqlite"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-for-automated-tests-only-0123456789"
os.environ["DEBUG"] = "false"
os.environ["BCRYPT_ROUNDS"] = "4"          # تسريع الاختبارات
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SMS_ENABLED"] = "false"
os.environ["EMAIL_ENABLED"] = "false"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """معاير asyncio لـ pytest."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_tenant_context():
    """ضمان عدم تسرّب سياق مستأجر بين الاختبارات."""
    from app.core.tenancy import clear_tenant_context

    clear_tenant_context()
    yield
    clear_tenant_context()