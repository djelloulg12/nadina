"""
إعدادات التطبيق (Application Settings)
======================================
تُقرأ جميع الإعدادات من متغيرات البيئة (env vars) أو من ملف ``.env``.
تُستخدم ``pydantic-settings`` لضمان التحقق من صحة القيم عند الإقلاع.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# مسار الجذر للمشروع
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# =====================================================================
#  تطبيع روابط قاعدة البيانات
# =====================================================================
def _with_async_driver(url: str) -> str:
    """
    ضمان استخدام السائق غير المتزامن الصحيح.

    مزوّدو الاستضافة (Render، Railway، Neon، Supabase) يعطون الرابط
    بصيغتين مختلفتين:

    * ``postgresql://user:pass@host/db``  — بلا سائق (Render)
    * ``postgresql://…`` أو ``postgres://…`` — بلا سائق (Neon, Heroku)

    بلا معالجة، يختار SQLAlchemy ``psycopg2`` المتزامن، فينهار التطبيق
    عند أول استعلام (``MissingGreenlet``). هنا نضيف السائق بأنفسنا.

    الرابط الذي يحدّد سائقه صراحةً يُترك كما هو.
    """
    if not url:
        return url

    # ``postgres://`` اختصار شائع لـ ``postgresql://``
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    # بلا سائق: نضيف asyncpg
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # MariaDB/MySQL: لاسياق الخوادم المشتركة التي تعطي
    # mysql:// أو mariadb:// بلا سائق. aiomysql هو السائق غير المتزامن.
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+aiomysql://", 1)
    if url.startswith("mariadb://"):
        return url.replace("mariadb://", "mysql+aiomysql://", 1)

    return url


def _ensure_sqlite_dir(url: str) -> None:
    """
    إنشاء مجلد ملف SQLite إن لم يكن موجوداً.

    SQLite لا يُنشئ المجلد الأصل — بل يفشل بـ ``unable to open
    database file``. وهذا يقع في صورة الحاوية Fresh checkout، لأن
    ``data/`` مستثناة من Git، ولأن ``Dockerfile`` ينشئها لحساب
    ``mashia`` بينما ``DATABASE_URL`` قد يشير إلى مسار نسبي.

    لا نفعل شيئاً لروابط PostgreSQL ولا ل``:memory:``.
    """
    if not url.startswith("sqlite"):
        return

    # فصل مسار الملف عن معاملات الرابط
    _, _, rest = url.partition(":///")
    if not rest:
        return

    path_text = rest.split("?", 1)[0]
    if not path_text or path_text == ":memory:":
        return

    path = Path(path_text)
    if not path.is_absolute():
        path = BASE_DIR / path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # نظام ملفات للقراءة فقط: نترك SQLException يبلّغ بوضوح
        pass


class Settings(BaseSettings):
    """كل إعدادات المنصة في مكان واحد."""

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # 1) بيانات التطبيق الأساسية
    # ------------------------------------------------------------------
    APP_NAME: str = "المنصة الشاملة"
    APP_TAGLINE: str = "الإدارة الشاملة للنوادي الرياضية"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    APP_URL: str = "http://localhost:8000"
    APP_TIMEZONE: str = "Africa/Algiers"
    APP_LOCALE: str = "ar"
    DEBUG: bool = True

    # ------------------------------------------------------------------
    # 2) الأمان والمفاتيح
    # ------------------------------------------------------------------
    SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12          # 12 ساعة
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SESSION_COOKIE_NAME: str = "mashia_session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 24 * 7          # 7 أيام
    COOKIE_SECURE: bool = False                               # True في الإنتاج
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = None

    #: المصادر المسموح لها بالوصول إلى الـ API (مفصولة بفواصل).
    #: في التطوير يُتجاهل ويُسمح للجميع.
    CORS_ALLOWED_ORIGINS: str = ""

    #: المضيفون المسموح بهم في ترويسة Host (مفصولة بفواصل).
    #: فارغ = قبول أي مضيف. يُملأ في الإنتاج لمنع Host header injection.
    ALLOWED_HOSTS: str = ""

    #: عناوين الوكيل العكسي الذي نثق بترويساته (مفصولة بفواصل).
    #: Render يمرّر الطلبات من شبكته الداخلية، فنوثّق نطاقه.
    TRUSTED_PROXIES: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    #: عدد جولات bcrypt لتشفير كلمات المرور (12 توازن بين الأمان والسرعة)
    BCRYPT_ROUNDS: int = 12

    #: حدود محاولة تسجيل الدخول الفاشلة قبل القفل المؤقت
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    #: طول رمز التحقق (OTP) للتهيئة
    OTP_LENGTH: int = 6

    # ------------------------------------------------------------------
    # 3) قاعدة البيانات
    # ------------------------------------------------------------------
    #: نوع قاعدة البيانات: sqlite | postgresql
    DB_DRIVER: Literal["sqlite", "postgresql", "mysql"] = "sqlite"

    #: رابط الاتصال الكامل. إذا ترك فارغاً يُبنى تلقائياً من الحقول التالية.
    DATABASE_URL: str | None = None

    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "sports_clubs"

    SQLITE_PATH: str = "data/sports_clubs.db"

    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800
    DB_STATEMENT_TIMEOUT_MS: int = 30_000

    # ------------------------------------------------------------------
    # 4) المصادقة الخارجية والخدمات
    # ------------------------------------------------------------------
    #: تفعيل إرسال الرسائل النصية (SMS)
    SMS_ENABLED: bool = False
    SMS_PROVIDER: str = "generic"
    SMS_SENDER_ID: str = "MASHIA"

    #: تفعيل البريد الإلكتروني
    EMAIL_ENABLED: bool = False
    EMAIL_HOST: str = "localhost"
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = "no-reply@mashia.local"
    EMAIL_USE_TLS: bool = True

    #: تفعيل إشعارات المتصفح (Push Notifications عبر Web Push)
    PUSH_ENABLED: bool = False
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""

    #: رابط تشغيل المهام المجدولة في الخلفية
    SCHEDULER_ENABLED: bool = True

    # ------------------------------------------------------------------
    # 5) إعدادات الأعمال
    # ------------------------------------------------------------------
    #: عدد الأيام قبل انتهاء الاشتراك الذي يبدأ التنبيه
    RENEWAL_ALERT_DAYS: int = 7

    #: عدد الأيام بعد تاريخ الاستحقاق قبل تصنيف الفاتورة كـ "متأخرة"
    INVOICE_GRACE_DAYS: int = 0

    #: الحد الأقصى لعدد الصور المرفوعة (بالميغابايت)
    MAX_UPLOAD_SIZE_MB: int = 8

    #: الصيغ المسموح بها لرفع الملفات
    ALLOWED_IMAGE_TYPES: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    )
    ALLOWED_DOC_TYPES: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    #: هل يسمح بالتسجيل الذاتي للنوادي الجديدة من البوابة العامة؟
    ALLOW_PUBLIC_CLUB_REGISTRATION: bool = True

    #: هل يعمل التطبيق في وضع الاختبارات الآلية؟
    TESTING: bool = False

    # ------------------------------------------------------------------
    #  Validators
    # ------------------------------------------------------------------
    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError("SECRET_KEY يجب أن يكون 32 حرفاً على الأقل")
        return v

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> "Settings":
        """في بيئة الإنتاج نمنع الإعدادات غير الآمنة."""
        if self.APP_ENV == "production":
            if self.DEBUG:
                # لا نرفع استثناءً قاتلاً، لكن نسجّل تحذيراً عبر المحدد أدناه.
                object.__setattr__(self, "DEBUG", False)
            if self.COOKIE_SECURE is False:
                raise ValueError(
                    "COOKIE_SECURE يجب أن يكون True في بيئة الإنتاج"
                )
        return self

    # ------------------------------------------------------------------
    #  Properties المحسوبة
    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_testing(self) -> bool:
        return self.TESTING or self.APP_ENV == "test"

    @property
    def database_url(self) -> str:
        """
        رابط الاتصال بقاعدة البيانات النهائي (غير متزامن).

        **لماذا نضيف السائق بأنفسنا؟** لأن مزوّدي الاستضافة يعطون الرابط
        بصيغة ``postgresql://`` بلا سائق، و SQLAlchemy لا يخمّن السائق
        غير المتزامن تلقائياً — فيختار psycopg2 المتزامن وينهار التطبيق
        عند أول استعلام.
        """
        if self.DATABASE_URL:
            url = _with_async_driver(self.DATABASE_URL)
            _ensure_sqlite_dir(url)
            return url

        if self.DB_DRIVER == "postgresql":
            return (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

        if self.DB_DRIVER == "mysql":
            return (
                f"mysql+aiomysql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
                "?charset=utf8mb4"
            )

        db_path = (BASE_DIR / self.SQLITE_PATH).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path.as_posix()}"

    @property
    def sync_database_url(self) -> str:
        """
        رابط متزامن — يُستخدم في Alembic والسكربتات المخطِّطة فقط.

        التطبيق يعمل غير متزامن، لكن Alembic يعمل متزامناً فنبدّل
        السائق عند هذه الطبقة وحدها.

        نستخدم ``psycopg2-binary``: حزمته مبنية مسبقاً لكل المنصات،
        بينما ``psycopg3`` يحتاج مترّجماً وقت النشر.
        """
        url = _with_async_driver(self.DATABASE_URL or "")
        if not url:
            # لا يوجد رابط صريح: نبنيه من الحقول عبر database_url
            url = self.database_url

        return (
            url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
            .replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            .replace("sqlite+aiosqlite://", "sqlite://", 1)
        )

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "app" / "static"

    @property
    def templates_dir(self) -> Path:
        return BASE_DIR / "app" / "templates"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """نقطة وصول واحدة للإعدادات (مع التخزين المؤقت)."""
    return Settings()


settings = get_settings()
