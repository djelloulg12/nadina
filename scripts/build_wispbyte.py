"""
حزمة نشر Wispbyte
=================

Wispbyte يشغّل حاوية Python عبر Pterodactyl:

* ``main.py`` في المجلد الجذر هو نقطة الدخول التي ينفّذها ``Use on startup``
* المنفذ يأتي من **Console → Server info → Address** (الرقم بعد ``:``)
* لا يوجد ``.env`` ولا متغيرات بيئة من اللوحة — لذا نمرّر الإعدادات
  عبر ``os.environ`` مع قيم افتراضية، أو عبر ملف إعدادات نولّده عند النشر
* حدّ الذاكرة 512 ميغابايت وحجم القرص 1 غيغابايت — لذلك نوفّر
  متطلباً وحيداً خفيفاً فقط، ونستخدم SQLite (بلا خدمة قاعدة بيانات
  ثانية تستهلك الذاكرة نفسها)

هذا السكربت يولّد:

* ``main.py`` — نقطة الدخول
* ``wispbyte.env`` — الإعدادات
* ``deploy_package.zip`` — أرشيف كامل جاهز للرفع
"""

from __future__ import annotations

import argparse
import os
import re
import pathlib
import shutil
import subprocess
import tempfile
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: ما لا يُرفع إلى الخادم (يعاد بناؤه محلياً أو مُستثنى)
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "data", "tests",
    ".venv", "htmlcov", "deploy",
}
# ملاحظة: مجلد scripts/ مقصود إبقاؤه -- يوفّر البذر (seed)
# وأدوات schema التي يحتاجها الخادم عند أول إقلاع.
EXCLUDE_FILES = {
    ".env", ".env.local", ".env.production", ".DS_Store",
    "server.log", "server.err", "DEPLOY.md", "code_artifact.md",
}

#: ملفات البناء التي لا تلزم على الخادم
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3"}


#: The uploaded entry point lives in its own file, so it can be
#: read, linted and tested as real Python instead of as a string.
TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"


def _load_entrypoint(port: int) -> str:
    """Read the entry point and bake the assigned port into it."""
    source = (TEMPLATE_DIR / "wispbyte_main.py").read_text(encoding="utf-8")
    return re.sub(
        r"^DEFAULT_PORT = \d+$",
        f"DEFAULT_PORT = {port}",
        source,
        count=1,
        flags=re.MULTILINE,
    )


ENV_TEMPLATE = '''# =====================================================================
#  إعدادات المنصة على Wispbyte
# =====================================================================
#  وُلّد محلياً — راجع DEPLOY_WISPBYTE.md قبل الرفع.
# =====================================================================

APP_NAME=المنصة الشاملة
APP_DESIGNER=جلول قندوز
APP_ENV=production
DEBUG=false

# --- security ---
# The Python egg serves plain HTTP on the node IP:PORT. There is no
# TLS in front of it, so the session cookie cannot be marked Secure --
# a Secure cookie is never sent over plain HTTP and login would
# silently fail. ALLOW_INSECURE_COOKIES is the explicit
# acknowledgment that turns that guard off. Enable it ONLY because
# this endpoint is HTTP; a real public hostname must use HTTPS with
# both settings back to true.
SECRET_KEY={secret_key}
COOKIE_SECURE={cookie_secure}
ALLOW_INSECURE_COOKIES={allow_insecure}
COOKIE_SAMESITE=lax

# --- database ---
# SQLite: no extra RAM and no second server, which the free Wispbyte
# plan requires (it grants a single slot, which the app itself needs).
DB_DRIVER=sqlite
SQLITE_PATH=data/mashia.db

# If a second slot ever becomes available for MariaDB, replace the
# three lines above with:
#   DB_DRIVER=mysql
#   DATABASE_URL=mysql+aiomysql://USER:PASSWORD@HOST:3306/DBNAME?charset=utf8mb4

# --- سلوك التطبيق ---
SCHEDULER_ENABLED=false
ALLOW_PUBLIC_CLUB_REGISTRATION=true
'''


def _prepare_database(out_dir: pathlib.Path, env_text: str) -> bool:
    """
    تشغيل مخطط البيانات وبذره البيانات الافتراضية.

    يعمل البناء من جاهز: لو يُطلب ملف البيانات يقلي حتى يحتاج الخارم من القائمة.

    إذا فشل: يارصف مذكرة (لا تطبيق) ويخطر الذكرة
    من تطبيقات المرجية — الخارم يحذرساتها عندأول إقلاع.
    """
    data_dir = out_dir / "data"
    data_dir.mkdir(exist_ok=True)

    proc = subprocess.run(
        [sys.executable, "-m", "scripts.seed", "--reset"],
        cwd=str(out_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "APP_ENV": "production",
            "DEBUG": "false",
            "PYTHONIOENCODING": "utf-8",
        },
    )

    db = data_dir / "mashia.db"
    if proc.returncode == 0 and db.exists():
        print(f"  database : {db.stat().st_size / 1024:.0f} KB (seeded)")
        return True

    print("  database : seed failed -- shipping an empty schema")
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
    for line in tail:
        print(f"             {line}")
    return False


def build(
    port: int,
    cookie_secure: str,
    allow_insecure: str,
    out_dir: pathlib.Path,
) -> pathlib.Path:
    """بناء مجلد النشر وأرشيفه."""
    # المجلد الهدف يقع داخل جذر المشروع، فنستثنيه صراحةً --
    # بدون ذلك ينسخ walk محتواه إلى نفسه ويClasses بلا نهاية.
    out_resolved = out_dir.resolve()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # نسخ الكود
    for item in ROOT.iterdir():
        if item.resolve() == out_resolved:
            continue
        if item.name in EXCLUDE_DIRS or item.name in EXCLUDE_FILES:
            continue
        if item.suffix in EXCLUDE_SUFFIX:
            continue
        if item.name.endswith(".md"):
            continue
        target = out_dir / item.name
        if item.is_dir():
            shutil.copytree(
                item, target,
                ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc", "*.log"),
            )
        else:
            shutil.copy2(item, target)

    # نقطة الدخول
    (out_dir / "main.py").write_text(
        _load_entrypoint(port), encoding="utf-8"
    )

    # ملف الإعدادات
    secret = subprocess.run(
        [sys.executable, "-c",
         "import secrets; print(secrets.token_urlsafe(48))"],
        capture_output=True, text=True,
    ).stdout.strip()
    env_text = ENV_TEMPLATE.format(
        secret_key=secret,
        cookie_secure=cookie_secure,
        allow_insecure=allow_insecure,
    )

    (out_dir / "wispbyte.env").write_text(env_text, encoding="utf-8")
    (out_dir / ".env").write_text(env_text, encoding="utf-8")

    # المشر والبيانات:
    # نشغري المخطط ونبذره داخل الأرشيف.
    #
    # لولها مهم: لوحدة المصير تعرض فقط لمجلد
    # بيانات إلى الموقف -- والملف المُحرم مليكيزي
    # نهاً. البرنامج يحتاج مخرجاً بالشيرة ومتطابق.
    _prepare_database(out_dir, env_text)

    # الأرشيف
    archive = pathlib.Path(tempfile.gettempdir()) / "mashia_wispbyte" / "wispbyte_deploy.zip"
    if archive.exists():
        archive.unlink()

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(out_dir))

    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Wispbyte deploy package")
    parser.add_argument("--port", default="1234", help="Server port from Console")
    parser.add_argument(
        "--cookie-secure", default="true", choices=["true", "false"],
        help="Set false if the server is reached over plain HTTP only",
    )
    parser.add_argument(
        "--out",
        default=str(
            pathlib.Path(tempfile.gettempdir()) / "mashia_wispbyte" / "package"
        ),
        help="Output directory (outside the repo, by default)",
    )
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    allow_insecure = "true" if args.cookie_secure == "false" else "false"
    archive = build(int(args.port), args.cookie_secure, allow_insecure, out_dir)

    files = [p for p in out_dir.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)

    print("=" * 62)
    print("  Wispbyte deployment package")
    print("=" * 62)
    print(f"  files   : {len(files)}")
    print(f"  size    : {total / 1024:.0f} KB")
    print(f"  archive : {archive}")
    print(f"  size    : {archive.stat().st_size / 1024:.0f} KB")
    print()
    print("Next:")
    print(f"  1. Upload {archive.name} to the server files")
    print("  2. Extract it in the server root")
    print("  3. Run: python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())