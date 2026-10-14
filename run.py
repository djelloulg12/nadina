"""
أداة التشغيل السريعة (Run Script)
================================
تغني عن تذكّر أوامر التشغيل المتكرّرة:

    python run.py              # خادم التطوير (إعادة تحميل تلقائية)
    python run.py serve        # نفس الشيء
    python run.py prod         # خادم الإنتاج (بلا إعادة تحميل)
    python run.py seed         # تعبئة بيانات تجريبية
    python run.py reset        # حذف كل شيء + تعبئة
    python run.py migrate      # تطبيق الترحيلات
    python run.py test         # تشغيل الاختبارات
    python run.py check        # فحوص الصيانة (قوالب + CSS + مخطط + اختبارات)
    python run.py verify       # فحص حيّ للتطبيق المُشغَّل
    python run.py routes       # عرض كل المسارات
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(*args: str, label: str = "") -> int:
    """تنفيذ أمر فرعي وتمرير رمز الخروج."""
    print(f"\n{'=' * 60}\n▶ {label or ' '.join(args)}\n{'=' * 60}")
    result = subprocess.run([sys.executable, *args], cwd=str(ROOT))
    return result.returncode


def serve(reload: bool = True) -> int:
    """تشغيل خادم Uvicorn."""
    import uvicorn

    host = "0.0.0.0"
    port = 8000

    print(f"\n{'=' * 60}")
    print(f"  المنصة الشاملة — http://localhost:{port}")
    print(f"  توثيق API — http://localhost:{port}/api/docs")
    print(f"{'=' * 60}\n")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
    return 0


def main() -> int:
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "serve"

    if command in ("serve", "dev", "run"):
        return serve(reload=True)

    if command == "prod":
        return serve(reload=False)

    if command == "seed":
        return run("-m", "scripts.seed", label="تعبئة البيانات التجريبية")

    if command == "reset":
        return run("-m", "scripts.seed", "--reset", label="حذف كل شيء + تعبئة")

    if command == "migrate":
        return run("-m", "alembic", "upgrade", "head", label="تطبيق الترحيلات")

    if command == "makemigration":
        message = sys.argv[2] if len(sys.argv) > 2 else "auto"
        return run("-m", "alembic", "revision", "--autogenerate", "-m", message,
                   label="توليد ترحيل")

    if command == "test":
        return run("-m", "pytest", "-q", label="تشغيل الاختبارات")

    if command == "check":
        # الترتيب من الأرخص للأغلى: خطأ في القالب لا يستحق تشغيل الاختبارات
        checks: list[tuple[tuple[str, ...], str]] = [
            (("scripts/check_encoding.py",), "فحص سلامة الترميز"),
            (("scripts/check_readme.py",), "فحص اتساق التوثيق"),
            (("scripts/check_templates.py",), "فحص القوالب"),
            (("scripts/check_css.py",), "فحص تطابق CSS"),
            (("scripts/check_schema_match.py",), "فحص تطابق المخطط مع الترحيل"),
            (("scripts/check_mysql_compat.py",), "فحص قابلية النقل إلى MariaDB"),
            (("scripts/check_requirements_installable.py",), "فحص تثبيت التبعيات على خوارم النشر"),
            (("-m", "alembic", "check"), "فحص انحراف المخطط"),
            (("-m", "pytest", "-q"), "تشغيل الاختبارات"),
        ]

        code = 0
        for args, label in checks:
            code |= run(*args, label=label)

        # فحص النشر يستحق دقيقة: هو الوحيد الذي يثبت أن المستودع نفسه
        # قابل للنشر -- فقد أسقط .gitignore ملفات الحزم مرة واحدة بصمت.
        if code == 0:
            code |= run("scripts/check_deploy.py", label="فحص جاهزية النشر")

        print(f"\n{'✅ كل الفحوص ناجحة' if code == 0 else '❌ توجد إخفاقات'}")
        return code

    if command == "routes":
        return run("scripts/list_routes.py", label="عرض المسارات")

    if command == "verify":
        return run("scripts/verify.py", label="فحص حيّ للتطبيق")

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())