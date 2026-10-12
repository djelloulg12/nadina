"""
فحص سلامة الترميز والتوثيق
===========================

يبحث عن آثار تلف الكتابة عبر أدوات الصدفة على النظام، وهي نمطان
معروفان في هذا المشروع:

1. **تبديل حرف**: ``f`` ← ``r`` في ملف كامل (ASCII 0x66 ↔ 0x72).
   يُكتشف بوجود كلمات مفتاحية معطوبة مثل ``derault`` أو ``selr``.
2. **شهادات يحلّ محلها نص غريب**: ``لأجل ضمان X نستخدم Y`` حيث
   ``Y`` كلمة إنجليزية لا صلة لها (``فنستخدم splice أأمن``).

النمط الثاني لا يمكن آلياً تمييزه دائماً، لذا نرصد مزيج ``لاستخدام``
مع كلمة لاتينية غريبة داخل نفس التعليق.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: كلمات مفتاحية لا وجود لها إلا بعد تبديل f→r
BROKEN_TOKENS = [
    "__ruture", "derault=", "\\bder\\b", "\\bselr\\b", "\\broreign",
    "SortDeleteMixin", "Notirication", "\\brull_name\\b", "\\bprererred",
    "platrorm", "perrormance", "\\bnotirications\\b",
]

#: كلمات صينية/كورية تسرّبت إلى نص عربي أو إنجليزي
CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

#: شهادات يحلّ محلها نص غريب: «لأجل … نستخدم X»
SUSPECT = re.compile(r"(?:لأجل|لضمان|أو نستخدم|فنستخدم)\s+[A-Za-z]{3,}")

#: أدوات الإصلاح نفسها تحتوي الأنماط المكسورة كنصوص — استثناؤها
#: حتى لا يرصد الفحص الشيفرة التي تُصلح الشيفرة.
EXEMPT = {
    "scripts/check_encoding.py",
    "scripts/check_readme.py",
    "scripts/repair_content_model.py",
}


def main() -> int:
    files = [
        p
        for p in sorted(ROOT.rglob("*"))
        if p.is_file()
        and p.suffix in (".py", ".md", ".html", ".css", ".js", ".txt", ".ini")
        and "__pycache__" not in p.parts
        and not any(part in (".git", "data", ".pytest_cache") for part in p.parts)
        and p.relative_to(ROOT).as_posix() not in EXEMPT
    ]

    findings: list[tuple[str, int, str]] = []

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            findings.append((str(path.relative_to(ROOT)), 0, f"تعذّر فكّ الترميز: {exc}"))
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in BROKEN_TOKENS:
                if re.search(pattern, line):
                    findings.append(
                        (str(path.relative_to(ROOT)), lineno, f"تبديل f→r: {line.strip()[:70]}")
                    )
                    break

            if CJK.search(line):
                findings.append(
                    (str(path.relative_to(ROOT)), lineno, f"حرف CJK: {line.strip()[:70]}")
                )

            if SUSPECT.search(line):
                findings.append(
                    (str(path.relative_to(ROOT)), lineno, f"نص مختلط: {line.strip()[:70]}")
                )

    print(f"فُحص {len(files)} ملفاً")

    if not findings:
        print("✓ لا توجد آثار تلف ترميز أو تبديل أحرف.")
        return 0

    print(f"\n✗ {len(findings)} موضعاً يحتاج مراجعة:\n")
    for name, lineno, message in findings:
        print(f"    {name}:{lineno}  {message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())