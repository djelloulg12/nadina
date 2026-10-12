"""
أداة صيانة مؤقتة: تحويل أعمدة التعدادات إلى نوع Enum صحيح.
===================================================================

⚠️  **مُنجَزة — لا تُشغّلها على النماذج الحالية.**

الملفات النمطية كانت تستخدم ``String(NN)`` لأعمدة التعدادات، مما يجعل
SQLAlchemy يعيد القيم كنصوص خام بدل كائنات التعداد -- فتنفشل الشيفرة التي
تقرأ ``user.role.value``.

هذه الأداة حذفت الصريح ``String(NN)`` من أعمدة التعدادات، تاركةً الاستدلال
على النوع لـ SQLAlchemy عبر ``type_annotation_map`` (المسجّل في
:mod:`app.models.base`)، فيُنتج عمود ``VARCHAR`` قابلاً للنقل بين SQLite
و PostgreSQL مع تحويل تلقائي في الاتجاهين.

**لماذا لم تُحذف؟** لتوثيق سبب اختيار ``StrEnumType`` على
``native_enum=True`` — وهو قرار لا يظهر في الكود النهائي. لكن تشغيلها
اليوم عديم الأثر (النماذج محوَّلة بالفعل)، وتركها تُعدّل الملفات بآلية
نصية على شيفرة سليمة هو خطر لا فائدة منه.

**للتحقق أن التحويل تم فعلاً:**

    python scripts/check_schema_match.py
    alembic check
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "app" / "models"

ENUMS = [
    "UserRole", "Status", "ClubStatus", "SportCategory", "Gender", "Level",
    "SessionStatus", "AttendanceStatus", "AttendanceMethod", "EvaluationType",
    "PeriodType", "InvoiceStatus", "PaymentMethod", "PaymentStatus",
    "ExpenseCategory", "PaymentDirection", "PlatformPlanPeriod",
    "SubscriptionStatus", "RegistrationStatus", "NotificationType",
    "NotificationChannel", "NotificationStatus", "AuditAction", "StaffJobTitle",
]
ENUM_SET = set(ENUMS)

#: يطابق تعريف ``name: Mapped[...] = mapped_column( ... )`` عبر عدة أسطر
FIELD_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>\w+)\s*:\s*Mapped\[(?P<type>[^\]]+)\]"
    r"\s*=\s*mapped_column\(",
    re.MULTILINE,
)


def find_pending(path: Path) -> list[str]:
    """أسماء أعمدة التعدادات التي ما زالت تحمل ``String(NN)`` صريحاً."""
    source = path.read_text(encoding="utf-8")
    pending: list[str] = []

    for match in FIELD_RE.finditer(source):
        annotation = match.group("type").replace("| None", "").strip()
        if annotation not in ENUM_SET:
            continue

        start = match.end()
        depth, i = 1, start
        while i < len(source) and depth > 0:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1

        if re.search(r"\bString\(\s*\d+\s*\)", source[start:i]):
            pending.append(match.group("name"))

    return pending


def main() -> int:
    total = 0

    for path in sorted(MODELS_DIR.glob("*.py")):
        if path.name in ("enums.py", "base.py", "__init__.py"):
            continue

        pending = find_pending(path)
        if pending:
            print(f"  ✗ {path.name}: أعمدة تعدادات لم تُحوَّل → {', '.join(pending)}")
            total += len(pending)

    if total:
        print(f"\n✗ {total} عمود تعداد ما زال يحتاج التحويل.")
        print("  راجع app/models/base.py → StrEnumType و type_annotation_map.")
        return 1

    print("✓ كل أعمدة التعدادات تعتمد على الاستدلال (StrEnumType).")
    print("  الأداة انتهت غرضها — يمكن حذفها.")
    return 0


if __name__ == "__main__":
    sys.exit(main())