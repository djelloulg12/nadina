"""حزمة طبقة الخدمات (Services Layer).

طبقة الخدمات تحتوي منطق الأعمال الخالص، بلا أي اعتماد على HTTP.
كل خدمة تستقبل ``AsyncSession`` وتعمل ضمن ``TenantContext`` الحالي،
فالعزل مُطبَّق تلقائياً على كل استعلام فيها.
"""

from app.services import auth

__all__ = ["auth"]