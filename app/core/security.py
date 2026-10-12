"""
طبقة الأمان (Security Layer)
=============================
* تشفير كلمات المرور (bcrypt)
* توليد رموز عشوائية آمنة
* توكنات JWT (للـ REST API)
* جلسات موقّعة (للواجهة المبنية على الخادم)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings
from app.core.exceptions import (
    InvalidCredentialsError,
    ValidationError,
)
from app.models.enums import UserRole

logger = logging.getLogger(__name__)


# =====================================================================
# 1) كلمات المرور
# =====================================================================
def hash_password(password: str) -> str:
    """
    تشفير كلمة المرور باستخدام bcrypt.

    ``bcrypt`` يضيف salt عشوائياً لكل كلمة، لذا نفس كلمة المرور تُنتج ناتجاً
    مختلفاً في كل مرة -- وهذا هو السلوك الصحيح.
    """
    if not password:
        raise ValidationError("كلمة المرور لا يمكن أن تكون فارغة")
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """التحقق من مطابقة كلمة المرور للهاش المخزّن."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        # هاش تالف أو بصيغة غير مدعومة
        logger.warning("تعذّر التحقق من كلمة مرور بسبب بصيغة غير صالحة")
        return False


def needs_rehash(password_hash: str) -> bool:
    """هل يجب إعادة تشفير كلمة المرور بعد تغيّر عدد الجولات؟"""
    try:
        rounds = int(password_hash.split("$")[2])
        return rounds < settings.BCRYPT_ROUNDS
    except (IndexError, ValueError):
        return True


def validate_password_strength(password: str, min_length: int = 8) -> None:
    """
    التحقق من قوة كلمة المرور.

    يرفع :class:`ValidationError` إذا كانت ضعيفة.
    """
    if len(password) < min_length:
        raise ValidationError(f"كلمة المرور يجب أن تكون {min_length} أحرف على الأقل")

    checks = {
        "حرف صغير": any(c.islower() for c in password),
        "حرف كبير": any(c.isupper() for c in password),
        "رقم": any(c.isdigit() for c in password),
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing:
        raise ValidationError(
            "كلمة المرور يجب أن تحتوي على: " + "، ".join(missing)
        )


# =====================================================================
# 2) المولّدات العشوائية الآمنة
# =====================================================================
def generate_token(nbytes: int = 32) -> str:
    """رمز عشوائي آمن بصيغة URL-safe."""
    return secrets.token_urlsafe(nbytes)


def generate_uuid() -> str:
    """معرّف UUID4 نصي."""
    return str(uuid.uuid4())


def generate_card_code() -> str:
    """
    كود فريد للبطاقة الرياضية.

    التنسيق: ``ABC-1234`` -- 3 أحرف + 4 أرقام، سهل النطق والكتابة،
    وقابل للتحقق بـ regex بسيط.
    """
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(3))
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"{letters}-{digits}"


def generate_otp(length: int | None = None) -> str:
    """رمز تحقق قصير للأرقام."""
    length = length or settings.OTP_LENGTH
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_invoice_number(prefix: str = "INV") -> str:
    """رقم فاتورة فريد مبني على الوقت + عشوائية."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"{prefix}-{stamp}-{suffix}"


def generate_receipt_number() -> str:
    """رقم وصل دفع."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"REC-{stamp}-{suffix}"


def generate_slug(text: str, max_length: int = 60) -> str:
    """تحويل نص عربي/إنجليزي إلى slug صالح للرابط."""
    import re

    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-")[:max_length] or generate_token(6)


# =====================================================================
# 3) توكنات JWT (للـ REST API)
# =====================================================================
class TokenType:
    ACCESS = "access"
    REFRESH = "refresh"


def create_access_token(
    user_id: int,
    role: UserRole | str,
    club_id: int | None = None,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """إنشاء توكن وصول (Access Token) موقّع بـ HS256."""
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    role_value = role.value if isinstance(role, UserRole) else str(role)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "uid": user_id,
        "role": role_value,
        "club_id": club_id,
        "type": TokenType.ACCESS,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": generate_uuid(),
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token


def create_refresh_token(user_id: int) -> str:
    """إنشاء توكن تجديد (Refresh Token) بعمر أطول."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "uid": user_id,
        "type": TokenType.REFRESH,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": generate_uuid(),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: str = TokenType.ACCESS) -> dict[str, Any]:
    """
    فك تشفير التوكن والتحقق من توقيعه وصلاحيته.

    يرفع :class:`InvalidCredentialsError` عند أي فشل.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidCredentialsError("انتهت صلاحية الجلسة. يرجى تسجيل الدخول مجدداً") from None
    except jwt.InvalidTokenError:
        raise InvalidCredentialsError("رمز الدخول غير صالح") from None

    if payload.get("type") != expected_type:
        raise InvalidCredentialsError("نوع التوكن غير صحيح")

    return payload


# =====================================================================
# 4) جلسات الواجهة (Server-rendered)
# =====================================================================
def _session_serializer() -> URLSafeTimedSerializer:
    """بناء الموقّع المستخدم في كوكي الجلسة."""
    return URLSafeTimedSerializer(
        secret_key=settings.SECRET_KEY,
        salt="mashia.session.v1",
    )


def create_session_token(data: dict[str, Any]) -> str:
    """توقيع بيانات الجلسة داخل توken موقّع."""
    return _session_serializer().dumps(data)


def read_session_token(token: str, max_age: int | None = None) -> dict[str, Any] | None:
    """
    قراءة وتوقيع بيانات الجلسة.

    تُعيد ``None`` عند انتهاء الصلاحية أو وجود تلاعب (لا ترمي استثناءً،
    لأن كوكي تالف يجب أن يُتجاهل بهدوء).
    """
    try:
        return _session_serializer().loads(
            token, max_age=max_age or settings.SESSION_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return None
    except Exception:  # pragma: no cover
        logger.warning("تعذّر قراءة كوكي الجلسة", exc_info=True)
        return None


# =====================================================================
# 5) أدوات تشفير مساعدة
# =====================================================================
def hash_value(value: str) -> str:
    """هاش SHA-256 لقيمة نصية (يُستخدم لفهرسة البحث عن البريد مثلاً)."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    """مقارنة نصية ثابتة الزمن (منع هجمات التوقيت)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def encode_qr_payload(athlete_id: int, card_code: str, salt: str = "") -> str:
    """بناء محتوى رمز QR المطبوع على البطاقة."""
    return f"MASHIA:{athlete_id}:{card_code}:{salt}"


def decode_qr_payload(payload: str) -> tuple[int, str] | None:
    """
    تحليل محتوى رمز QR المستُخدم عند تسجيل الحضور.

    يُعيد ``(athlete_id, card_code)`` أو ``None`` إذا كان الرمز غير صالح.
    """
    parts = (payload or "").strip().split(":")
    if len(parts) < 3 or parts[0] != "MASHIA":
        return None
    try:
        athlete_id = int(parts[1])
    except ValueError:
        return None
    if not parts[2]:
        return None
    return athlete_id, parts[2]


def encode_file_token(path: str, expires_in: int = 3600) -> str:
    """توقيع رابط ملف مؤقت (منع الوصول المباشر لنظام الملفات)."""
    signer = URLSafeTimedSerializer(secret_key=settings.SECRET_KEY, salt="mashia.file.v1")
    return signer.dumps({"path": path})


def decode_file_token(token: str, max_age: int = 3600) -> str | None:
    """التحقق من توقيع رابط ملف واستخراج المسار."""
    try:
        data = URLSafeTimedSerializer(
            secret_key=settings.SECRET_KEY, salt="mashia.file.v1"
        ).loads(token, max_age=max_age)
        return data.get("path") if isinstance(data, dict) else None
    except (BadSignature, SignatureExpired):
        return None


def b64url_encode(data: bytes) -> str:
    """ترميز base64 بدون حشو (للمعرفات النصية)."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(data: str) -> bytes:
    """فك ترميز base64 بدون حشو."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)