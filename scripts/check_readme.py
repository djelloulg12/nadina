"""
فحص اتساق الروابط الداخلية في ``README.md``
============================================

يحوّل خطأ التنقّل (رابط جدول محتويات يشير إلى قسم لم يعد موجوداً بعد
تعديل) إلى فشل آلي، بدل اكتشافه عند القراءة.

يحاكي قواعد GitHub في توليد المعرّفات: تطبيع إلى أحرف صغيرة، إفراغ
المسافات، وحذف الترقيم (بما فيه الأقواس و``★``).
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def slugify(heading: str) -> str:
    """توليد معرّف القسم كما يفعل GitHub."""
    # نُسقط الش.Backticks والرموز الخاصة أولاً
    text = heading.replace("`", "").replace("*", "")
    # GitHub يحذف كل المحارف غيرCCP/arabe/حروف/أرقام/شرطات
    text = re.sub(r"[^\w؀-ۿ\s-]", "", text, flags=re.UNICODE)
    # المسافات → شرطات
    return re.sub(r"\s+", "-", text.strip().lower())


def main() -> int:
    if not README.exists():
        print("✗ لا يوجد README.md")
        return 1

    text = README.read_text(encoding="utf-8")

    # الأقسام: من ## فصاعداً (نتجاهل # الأول = العنوان)
    headings = re.findall(r"^#{2,3} (.+)$", text, re.M)
    slugs = {slugify(h) for h in headings}

    # روابط جدول المحتويات: ](#...)
    anchors = re.findall(r"\]\(#([^)]+)\)", text)

    missing = [a for a in anchors if a not in slugs]
    print(f"الأقسام        : {len(headings)}")
    print(f"روابط داخلية   : {len(anchors)}")

    if missing:
        print(f"\n✗ روابط تشير إلى أقسام غير موجودة ({len(missing)}):")
        for anchor in missing:
            print(f"    #{anchor}")
        return 1

    # فحوص إضافية: تلف ترميز شائع
    corruption = [
        token
        for token in ("唯一", "Whether", "Lincoln", "rrom ", "__ruture",
                      "derault", "Notirication", "selr", "SortDelete")
        if token in text
    ]
    if corruption:
        print(f"\n✗ علامات تلف ترميز: {corruption}")
        return 1

    print("\n✓ كل الروابط الداخلية صحيحة، ولا علامات تلف.")
    return 0


if __name__ == "__main__":
    sys.exit(main())