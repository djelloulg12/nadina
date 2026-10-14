"""
فحص قابلية تثبيت التبعيات على خوادم النشر
===========================================

المنصة تُنشر على خوادم لا تشبه جهاز التطوير: Render يبني على
``python:3.12-slim``، وبيضة Wispbyte تعمل على ``cp311``. الحزمة تُنزَّل
من PyPI الحقيقي، لا من مرآة محلية قد تحمل نسخاً مختلفة.

هذا الفحص يحاكي ذلك: يحلّ كل تثبيت في ``requirements.txt`` مقابل
PyPI الحقيقي لـ ``cp312`` و ``cp311`` معاً.

لماذا هذا مهم: ``asyncpg==0.31.0`` و ``email-validator==3.0`` و
``pillow==12.3.0`` كانت مثبّتة على جهاز التطوير لكنها **غير موجودة على
PyPI**. الاختبارات كانت تنجح لأن الحزم مثبّتة أصلاً، بينما بناء Docker
على Render كان سيفشل عند ``pip install`` — الخطأ نفسه الذي أوقف خادم
Wispbyte.

فحص التبعيات بالمستورد لا يكشف هذا: هو يرى ما هو مثبّت، لا ما يمكن
تثبيته. الفحص هنا يرى ما سيراه الخادم.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: PyPI الحقيقي. أي مرآة محليّة تخفي نسخاً غير موجودة على الخادم.
PYPI = "https://pypi.org/simple"

#: (إصدار Python، منصة لينكس، اسم التنفيذ، وسم ABI)
TARGETS = [
    ("312", "manylinux2014_x86_64", "cp", "cp312"),   # Dockerfile: python:3.12-slim
    ("311", "manylinux2014_x86_64", "cp", "cp311"),   # Wispbyte Python egg
]


def requirements() -> list[str]:
    """قائمة التبعيات بلا تعليقات."""
    lines = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def resolves(spec: str, python_tag: str, platform: str, impl: str, abi: str,
            out_dir: Path) -> bool:
    """هل تُحَلّ هذه الحزمة لهذا الـ interpreter من PyPI الحقيقي؟"""
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--quiet",
            "--no-deps", "--only-binary=:all:",
            "--index-url", PYPI,
            "--python-version", python_tag,
            "--platform", platform,
            "--implementation", impl,
            "--abi", abi,
            "-d", str(out_dir), spec,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    specs = requirements()
    out_dir = Path(tempfile.mkdtemp())

    print("=" * 66)
    print("  فحص تثبيت التبعيات على خوادم النشر")
    print("=" * 66)
    print(f"  المصدر     : {PYPI}")
    print(f"  الحزم      : {len(specs)}")
    print()

    broken: list[tuple[str, str]] = []

    for python_tag, platform, impl, abi in TARGETS:
        failures = [s for s in specs
                    if not resolves(s, python_tag, platform, impl, abi, out_dir)]

        label = f"cp{python_tag} / {platform}"
        if failures:
            print(f"  [FAIL] {label:<34} {len(specs) - len(failures)}/{len(specs)}")
            for spec in failures:
                print(f"           {spec}  <- لا حلّ على PyPI لهذا الـ interpreter")
                broken.append((spec, label))
        else:
            print(f"  [OK  ] {label:<34} {len(specs)}/{len(specs)}")

    print()
    print("=" * 66)
    if broken:
        print(f"✗ {len(broken)} تثبيتاً سيفشل على الخادم:\n")
        for spec, label in broken:
            print(f"   • {spec}  ({label})")
        print(
            "\n   هذه الحزم مُثبّتة محلياً — بالضبط سبب نجاح الاختبارات\n"
            "   وفشل النشر. ثبّت أقرب نسخة موجودة على PyPI:"
        )
        raise SystemExit(1)

    print("✓ كل التبعيات قابلة للتثبيت على خوادم النشر")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
