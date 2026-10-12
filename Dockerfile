# =====================================================================
#  صورة الإنتاج — المنصة الشاملة
# =====================================================================
#  مبنية على slim لا على alpine: asyncpg و psycopg2 و pillow تحتاج
#  مكتبات نظام (libpq, libjpeg, zlib) ودّيات musl غير متاحة لها،
#  و alpine يدفعنا لبناء psycopg2 من المصدر عند كل نشر.
# =====================================================================

# ---------------------------------------------------------------------
#  المرحلة 1: البناء
# ---------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# أدوات بناء psycopg2 و pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        zlib1g-dev \
        libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# ننسخ ملف التبعيات أولاً: طبقة الاعتماديات تُخزَّن مؤقتاً ولا
# تُعاد بناؤها عند كل تغيير في الشيفرة.
COPY requirements.txt .

RUN pip wheel --wheel-dir /wheels -r requirements.txt


# ---------------------------------------------------------------------
#  المرحلة 2: التشغيل
# ---------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# مكتبات تشغيل فقط (لا أدوات بناء)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        zlib1g \
        libjpeg62-turbo \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# التبعيات من عجلات مبنية مسبقاً — لا مترجم في الصورة النهائية
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# --- الشيفرة ---
COPY alembic/ alembic/
COPY app/ app/
COPY scripts/ scripts/
COPY alembic.ini pytest.ini ./

# مجلدات تحتاج كتابة أثناء التشغيل
RUN mkdir -p data \
    && useradd --create-home --shell /bin/bash mashia \
    && chown -R mashia:mashia /app
USER mashia

# فحص الصحة: المنصّات المجانية تستخدمه لاستبدال الحاويات المعطوبة
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

EXPOSE 8000

# ينفّذ الترحيلات ثم يشغّل الخادم — راجع scripts/startup.py
CMD ["python", "scripts/startup.py"]