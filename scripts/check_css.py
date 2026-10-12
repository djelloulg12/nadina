"""
فحص تطابق القوالب مع نظام التصميم
==================================
يستخرج كل أصناف CSS المستخدمة في القوالب ويتحقق من وجودها فعلياً في
``app/static/css/app.css``.

هذا يفCatch خطأً شائعاً ومكلفاً: كتابة صنف غير موجود يعني أن العنصر
يُعرض بلا تنسيق، والخطأ لا يظهر إلا بالنظر البصري. هنا يتحوّل إلى
فشل آلي.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
CSS_FILE = ROOT / "app" / "static" / "css" / "app.css"

#: أصناف نتجاهلها: وظيفية (JS) أو مولّدة ديناميكياً أو خاصة بالمتصفح
IGNORE = {
    "active", "is-open", "show", "hidden", "open", "sort-asc", "sort-desc",
    "is-loading", "is-leaving", "required", "optional", "selected",
    "card-hover", "text-decoration-none",
}

CLASS_ATTR_RE = re.compile(r'class="([^"{}]*)"')


def css_classes() -> set[str]:
    """كل الأصناف المعرّفة في ملف CSS."""
    text = CSS_FILE.read_text(encoding="utf-8")
    found = set()
    for m in re.finditer(r"\.(-?[_a-zA-Z][\w-]*)", text):
        found.add(m.group(1))
    return found


def template_classes() -> dict[str, set[str]]:
    """الأصناف المستخدمة في كل قالب."""
    result: dict[str, set[str]] = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        used: set[str] = set()
        for m in CLASS_ATTR_RE.finditer(text):
            for token in m.group(1).split():
                token = token.strip()
                if token and not token.startswith(("{{", "{%")):
                    used.add(token)
        name = str(path.relative_to(TEMPLATES)).replace("\\", "/")
        result[name] = used
    return result


def main() -> int:
    defined = css_classes()
    used_map = template_classes()
    all_used: set[str] = set()
    for used in used_map.values():
        all_used |= used

    missing = sorted(
        c for c in all_used
        if c not in defined and c not in IGNORE and not c.startswith("js-")
    )

    print(f"الأصناف المعرّفة في CSS : {len(defined)}")
    print(f"الأصناف المستخدمة في القوالب: {len(all_used)}")
    print(f"القوالب المفحوصة: {len(used_map)}")
    print("-" * 66)

    if missing:
        print(f"أصناف مستخدمة وغير معرّفة ({len(missing)}):")
        for cls in missing:
            used_in = [n for n, u in used_map.items() if cls in u]
            print(f"  ✗ .{cls:<28} في: {', '.join(used_in[:3])}")
        return 1

    print("✓ كل الأصناف المستخدمة معرّفة في ملف CSS")
    return 0


if __name__ == "__main__":
    sys.exit(main())