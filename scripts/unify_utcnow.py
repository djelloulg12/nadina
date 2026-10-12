"""
توحيد استخدام الوقت على ``utcnow()``
=====================================

كل أعمدة ``DateTime`` في المشروع **naive UTC** بلا استثناء (SQLite لا
يدعم المنطقة الزمنية، وخلط القيمتين يسبّب أخطاء مقارنة صامتة).

الكتابة الصحيحة واحدة: ``utcnow()`` من :mod:`app.core.db`. أمّا
``datetime.now(timezone.utc)`` فيُعيد قيمة **واعية** بالمنطقة الزمنية،
فتنفجر المقارنات عند أول طرح بينها وبين قيمة مخزَّنة بلا منطقة زمنية.

هذا السكربت آلة وحيدة المصدر: يوحّد الاستعمال ويضيف الاستيراد الناقص.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: أنماط الاستدعاء المباشر الذي يجب استبداله
PATTERN = re.compile(
    r"(?:\bdt\.datetime\.now\(dt\.timezone\.utc\)"
    r"|\bdatetime\.now\(timezone\.utc\)"
    r"|\bdatetime\.now\(UTC\))"
)

#: ملفات لا نلمسها: ``db.py`` يعرّف ``utcnow``، و``security.py`` يُصدر
#: طابعاً نصياً في JWT لا قيمة مخزَّنة.
SKIP = {
    ROOT / "app" / "core" / "db.py",
    ROOT / "app" / "core" / "security.py",
    ROOT / "scripts" / "unify_utcnow.py",
}


def imports_utcnow(tree: ast.AST) -> bool:
    """هل يستورد الملف ``utcnow`` فعلاً؟"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if "utcnow" in [a.name for a in node.names]:
                return True
        elif isinstance(node, ast.Import):
            if any(a.name == "utcnow" for a in node.names):
                return True
    return False


def uses_utcnow(text: str) -> bool:
    """هل يظهر الاسم ``utcnow`` خارج سطور الاستيراد؟"""
    without_imports = re.sub(r"(?m)^\s*(?:from|import)\s+.*$", "", text)
    return re.search(r"\butcnow\b", without_imports) is not None


def _last_import_end(text: str) -> int | None:
    """
    رقم آخر سطر ينتهي عنده استيراد على المستوى الأعلى.

    نستخدم ``ast`` لأن الاستيرادات قد تُلَفّ بأقواس على عدة أسطر
    (``from app.models import (\\n  A,\\n)``)، والبحث النصّي يضع السطر
    الجديد داخل القوس فيكسر الملف.
    """
    tree = ast.parse(text)
    end: int | None = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = node.end_lineno or node.lineno
    return end


def add_import(text: str, module: str) -> str:
    """
    إضافة ``utcnow`` إلى استيراد ``app.core.db`` القائم، أو سطر جديد.

    نحافظ على تعليق ``# noqa`` اللاحق بإبقائه خارج النمط.
    """
    pattern = re.compile(
        r"(?m)^(?P<head>from app\.core\.db import )(?P<names>[^#\n]+)(?P<tail>#.*)?$"
    )

    match = pattern.search(text)
    if match:
        names = {n.strip() for n in match.group("names").split(",") if n.strip()}
        names.add("utcnow")
        replacement = (
            match.group("head")
            + ", ".join(sorted(names))
            + (match.group("tail") or "")
        )
        return text[: match.start()] + replacement + text[match.end():]

    last = _last_import_end(text)
    if last is None:
        return text

    lines = text.splitlines()
    lines.insert(last, f"from {module} import utcnow")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    changed: list[str] = []

    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path in SKIP:
            continue

        original = path.read_text(encoding="utf-8")
        if not PATTERN.search(original):
            continue

        updated = PATTERN.sub("utcnow()", original)

        if uses_utcnow(updated) and not imports_utcnow(ast.parse(updated)):
            module = "app.models.base" if path.parts[-2] == "models" else "app.core.db"
            updated = add_import(updated, module)

            if not imports_utcnow(ast.parse(updated)):
                print(f"✗ {path.relative_to(ROOT)} يستخدم utcnow بلا استيراد")
                return 1

        try:
            compile(updated, str(path), "exec")
        except SyntaxError as exc:
            print(f"✗ {path.relative_to(ROOT)} لا يُصرَّف: {exc}")
            return 1

        path.write_text(updated, encoding="utf-8")
        changed.append(str(path.relative_to(ROOT)))

    if not changed:
        print("✓ كل الملفات تستخدم utcnow() بالفعل")
        return 0

    print(f"✓ وُحّد {len(changed)} ملفاً:")
    for name in changed:
        print(f"    {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())