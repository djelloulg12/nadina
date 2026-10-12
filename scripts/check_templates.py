"""فحص سريع: كل القوالب تُصرَّف بنجاح."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent / "app" / "templates"

# نستخدم محرّك القوالب الحقيقي (مع كل الفلاتر والدوال المسجّلة)، وإلا
# أظهر الفحص أخطاء وهمية لكل فلتر خاص بالمنصة.
from app.web.templates_setup import templates  # noqa: E402

env = templates.env


def find_templates() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.html"))


def main() -> int:
    files = find_templates()
    print(f"القوالب الموجودة: {len(files)}")

    errors = 0
    for path in files:
        name = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            env.get_template(name)
            print(f"  OK    {name}")
        except Exception as exc:
            errors += 1
            print(f"  FAIL  {name}\n        {type(exc).__name__}: {exc}")

    print(f"\n{'كل القوالب صالحة' if errors == 0 else f'{errors} قالب به خطأ'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())