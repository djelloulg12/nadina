"""
فحص جاهزية النشر (Deployment Readiness)
========================================

يفحص ما لا تفحصه الاختبارات: هل يعمل التطبيق **كما ستراه المنصّة**،
من الشجرة التي رفعناها إلى GitHub؟

لماذا هذا مهم: ``git archive`` يبني ما سيراه الخادم حرفياً. أي ملف
غير متتبَّع في git — أو مستثنى بنمط ``.gitignore`` خاطئ — يظهر في
بيئتك المحلي ويختفي في الإنتاج. حدث ذلك فعلاً مع ملفات
``__init__.py`` التي أسقطها نمط ``_*.py``.

ما يفحصه هذا السكربت:

1. تصدير ``HEAD`` وتصديره في مجلد مؤقت.
2. توليد SecretKey و ``PORT`` و ``SEED_ON_EMPTY`` كمجرة إنتاج.
3. تشغيل ``scripts/startup.py`` من الشجرة المصدَّرة.
4. انتظار جاهزية الخادم، ثم فحص المسارات الأساسية.
5. حذف الشجرة المؤقتة.

الاستخدام::

    python scripts/check_deploy.py
"""

from __future__ import annotations

import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: ملفات يجب أن تكون في الشجرة المصدَّرة، وإلا فشلت المنصّة عند النشر
REQUIRED = [
    "app/__init__.py",
    "app/main.py",
    "app/core/__init__.py",
    "app/core/config.py",
    "app/core/db.py",
    "app/core/schema.py",
    "app/models/__init__.py",
    "app/web/__init__.py",
    "alembic.ini",
    "alembic/env.py",
    "scripts/startup.py",
    "requirements.txt",
    "app/templates/base.html",
    "app/static/css/app.css",
    "app/static/js/app.js",
    "Dockerfile",
    "render.yaml",
]

#: مسارات نفحصها بعد الإقلاع
PROBES = [
    "/api/health",
    "/",
    "/clubs",
    "/auth/login",
    "/static/css/app.css",
]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=True
    ).stdout.strip()


def export_head(dest: pathlib.Path) -> None:
    """تصدير محتويات ``HEAD`` كما ستراها المنصّة تماماً."""
    archive = dest.parent / "head.zip"
    git("archive", "--format=zip", "-o", str(archive), "HEAD")

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)

    archive.unlink()


def check_required(tree: pathlib.Path) -> list[str]:
    missing = [name for name in REQUIRED if not (tree / name).exists()]
    return missing


def wait_for_server(url: str, process: subprocess.Popen, timeout: int = 180) -> bool:
    """انتظار جاهزية الخادم مع إبقاء العملية حيّة."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    return False


def main() -> int:
    if not (ROOT / ".git").exists():
        print("✗ ليس مستودع git — شغّل git init أولاً")
        return 1

    print("=" * 64)
    print("  فحص جاهزية النشر")
    print("=" * 64)

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="mashia_deploy_"))
    tree = workdir / "tree"

    try:
        # --- 1) التصدير ---
        export_head(tree)
        files = sum(1 for _ in tree.rglob("*") if _.is_file())
        print(f"\n▸ صُدِّرت الشجرة: {files} ملفاً")

        # --- 2) الملفات الإلزامية ---
        missing = check_required(tree)
        if missing:
            print(f"\n✗ ملفات ناقصة في الشجرة المصدَّرة ({len(missing)}):")
            for name in missing:
                # نتحقق: هل موجودة محلياً؟ إن في المشكلة الأصل المشكلة في git
                local = (ROOT / name).exists()
                state = "موجودة محلياً لكنها غير متتبَّعة" if local else "مفقودة أصلاً"
                print(f"    {name}  — {state}")
            return 1
        print(f"  ✓ كل الملفات الإلزامية حاضرة ({len(REQUIRED)})")

        # --- 3) الإقلاع بوضع الإنتاج ---
        port = 8077
        env = {
            **os.environ,
            "APP_ENV": "production",
            "DEBUG": "false",
            "COOKIE_SECURE": "true",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": "sqlite+aiosqlite:///./data/deploy_check.db",
            "SEED_ON_EMPTY": "true",
            "PORT": str(port),
            "PYTHONIOENCODING": "utf-8",
        }

        print(f"\n▸ تشغيل startup.py على المنفذ {port} ...")
        log = (workdir / "startup.log").open("w", encoding="utf-8", errors="replace")

        process = subprocess.Popen(
            [sys.executable, "scripts/startup.py"],
            cwd=str(tree),
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )

        base = f"http://127.0.0.1:{port}"

        try:
            ready = wait_for_server(f"{base}/api/health", process)

            if not ready:
                print("\n✗ الخادم لم يُقلع. آخر السجل:\n")
                log.close()
                tail = (workdir / "startup.log").read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[-30:]
                for line in tail:
                    print("   ", line[-110:])
                return 1

            print("  ✓ الخادم جاهز")

            # --- 4) فحص المسارات ---
            print("\n▸ فحص المسارات:")
            failures = []
            for path in PROBES:
                try:
                    import urllib.request

                    with urllib.request.urlopen(f"{base}{path}", timeout=20) as r:
                        ok = r.status == 200
                        size = len(r.read())
                except Exception as exc:
                    ok, size = False, 0
                    print(f"    ✗ {path}  — {type(exc).__name__}")

                mark = "OK  " if ok else "FAIL"
                print(f"    [{mark}] {path}  — {size} bytes")
                if not ok:
                    failures.append(path)

        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            log.close()

        # --- النتيجة ---
        print("\n" + "=" * 64)
        if failures:
            print(f"✗ {len(failures)} مسار فشل — المنصّة لن تعمل بعد النشر")
            for f in failures:
                print(f"    {f}")
            return 1

        print("✓ جاهز للنشر: الشجرة المصدَّرة تُقلع وتعمل بوضع الإنتاج")
        print("=" * 64)
        return 0

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
