"""
إصلاح تلف الترميز في ``app/models/content.py``.

========================= ما الذي حدث بالضبط =========================

عند كتابة الملف بأداة صدفة (PowerShell) على نظام لا يقرأ UTF-8 بشكل
صحيح، تبدّل كل حرف ``f`` (``ASCII 0x66``) إلى ``r`` (``ASCII 0x72``)
— فرق وحيد في البتّ الثاني. النتيجة: الملف كان يعمل قبل التلف،
وقواعد الإصلاح هنا مبنية على مرجعين موثوقين لا على التخمين:

1. ``app/models/enums.py`` و ``app/models/base.py`` — الأسماء المصدَّرة فعلياً.
2. ``alembic/versions/*.py`` — وُلِّد من النسخة السليمة، فيحتوي أسماء
   الجداول والأعمدة والفهارس الصحيحة.

كل قاعدة أدناه طالِبٌ موثَّق بمسbhاقها في الملف السليم.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "models" / "content.py"

#: (النمط, البديل, الدليل) — النمط يُطبَّق بحدود كلمة.
#: نطبّق من الأطول إلى الأقصر حتى لا يبتلع استبدالٌ أقصر داخل أطول.
RULES: list[tuple[str, str, str]] = [
    # --- أسماء الأصناف: مرجع models/enums.py ---
    (r"NotiricationChannel", "NotificationChannel", "enums.NotificationChannel"),
    (r"NotiricationStatus", "NotificationStatus", "enums.NotificationStatus"),
    (r"NotiricationType", "NotificationType", "enums.NotificationType"),
    (r"\bNotirication\b", "Notification", "أصناف models/content.py"),

    # --- Mixin: مرجع models/base.py ---
    (r"\bSortDeleteMixin\b", "SoftDeleteMixin", "base.SoftDeleteMixin"),

    # --- بنية Python ---
    (r"\b__ruture__\b", "__future__", "__future__ annotations"),
    (r"\bir\b(?= TYPE_CHECKING)", "if", "if TYPE_CHECKING:"),
    (r"\bderault\b", "default", "mapped_column(default=...)"),
    (r"\bder\b", "def", "def __str__ / def <prop>"),
    (r"\bselr\b", "self", "self.<attr>"),
    # بلا حدود لاحقة: الكلمة تُلحق بـ ``_keys`` فحدّ ``\b`` لا يتحقق
    (r"\broreign_", "foreign_", "foreign_keys="),
    (r'(?<=[(\s])r"', 'f"', "f-string"),

    # --- أسماء الأعمدة والجداول: مرجع ملف الترحيل ---
    (r"\brull_name\b", "full_name", "registration_requests.full_name"),
    (r"\bprererred_group_id\b", "preferred_group_id", "preferred_group_id"),
    # بلا حدود بعد الكلمة: الفهارس تُلحق بـ ``_user_read`` فالحدّ لا يتحقق
    (r"\bnotirications\b", "notifications", "جدول notifications"),
    (r"\bix_notirications", "ix_notifications", "فهارس جدول notifications"),
    (r"\bix_reed_order\b", "ix_feed_order", "ix_feed_order(is_pinned, published_at)"),

    # --- قيم وهمية ---
    (r"\bra-(?!inro)", "fa-", "أيقونات FontAwesome (fa-)"),
    (r"\bra-inro-circle\b", "fa-info-circle", "fa-info-circle"),
    (r"\binro\b", "info", "info-circle"),

    # --- نصوص ---
    (r"\bplatrorm\b", "platform", "app.models.platform"),
    (r"-perrormance", "-performance", "performance"),
]

#: بقايا تدلّ على تلف غير مُعالَج
LEFTOVER_MARKERS = (
    "__ruture", "derault", "\bder\b", "\bselr\b", "\broreign\b",
    "SortDelete", "Notirication", "rull_name", "prererred", "roreign",
    "platrorm", "perrormance", "notirications", "ra-inro", "\binro\b",
)


def repair(text: str) -> tuple[str, list[str]]:
    """تطبيق القواعد، وإرجاع النص المعالَج وقائمة المواضع المُصحّحة."""
    applied: list[str] = []
    for pattern, replacement, note in RULES:
        text, count = re.subn(pattern, replacement, text)
        if count:
            applied.append(f"{pattern} → {replacement}  ({count}×)  # {note}")
    return text, applied


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    fixed, applied = repair(original)

    if not applied:
        print("لا يوجد تلف — الملف سليم.")
        return 0

    # فحص تركيبي قبل الكتابة: لا نكتب ملفاً لا يُصرَّف
    try:
        compile(fixed, str(TARGET), "exec")
    except SyntaxError as exc:
        print(f"✗ النتيجة لا تُصرَّف — لم نكتب الملف: {exc}")
        for line in applied:
            print(f"    {line}")
        return 1

    TARGET.write_text(fixed, encoding="utf-8")
    print(f"✓ أُصلح {TARGET.relative_to(ROOT)}\n")
    for line in applied:
        print(f"    {line}")

    leftovers = [m for m in LEFTOVER_MARKERS if re.search(m, fixed)]
    print()
    if leftovers:
        print(f"⚠️  بقايا لم تُعالَج: {leftovers}")
        return 1

    print("✓ لا توجد بقايا")
    return 0


if __name__ == "__main__":
    sys.exit(main())