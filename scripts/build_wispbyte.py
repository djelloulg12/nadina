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


ENTRYPOINT = '''"""
نقطة الدخول على Wispbyte
========================

Wispbyte ينفّذ هذا الملف عبر \"Use on startup\". المنفذ يأتي من
**Console -> Server info -> Address** (الرقم بعد النقطتين).

المنصة تطبيق FastAPI يعمل مع uvicorn على منفذ واحد. نستخدم عاملاً واحداً
لأن ذاكرة الخادم 512 ميغابايت، وعدة عمّال يستهلكون ذاكرة مضاعفة بلا
فائدة على خطة بهذا الحجم.
"""

from __future__ import annotations

import os
import sys

# جذر الخادم هو المجلد الذي يحتوي هذا الملف
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    # ---------------------------------------------------------------
    # 1) تحميل الإعدادات قبل استيراد التطبيق
    #    (pydantic-settings يقرأها عند الاستيراد)
    # ---------------------------------------------------------------
    env_file = os.path.join(ROOT, "wispbyte.env")
    if os.path.exists(env_file):
        _load_env(env_file)

    port = int(os.getenv("PORT", "1234"))

    # ---------------------------------------------------------------
    # 2) الترحيلات: ينشئ الجداول قبل أول طلب
    # ---------------------------------------------------------------
    if os.getenv("RUN_MIGRATIONS", "1") == "1":
        print("[wispbyte] applying database migrations...", flush=True)
        try:
            from app.core import schema
            import asyncio

            asyncio.run(schema.sync_schema(reset=False, quiet=True))
            print("[wispbyte] migrations OK", flush=True)
        except Exception as exc:
            # لا نمنع الإقلاع: التطبيق يعمل حتى لو فشل تطبيق الترحيلات
            print(f"[wispbyte] migration warning: {exc}", flush=True)

    # ---------------------------------------------------------------
    # 3) التشغيل
    # ---------------------------------------------------------------
    import uvicorn

    print(f"[wispbyte] starting uvicorn on 0.0.0.0:{port}", flush=True)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=60,
    )
    return 0


def _load_env(path: str) -> None:
    """قراءة ملف key=value إلى os.environ دون الكتابة فوق الموجود."""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError as exc:
        print(f"[wispbyte] could not read env file: {exc}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
'''


ENV_TEMPLATE = '''# =====================================================================
#  إعدادات المنصة على Wispbyte
# =====================================================================
#  وُلّد محلياً — راجع DEPLOY_WISPBYTE.md قبل الرفع.
# =====================================================================

APP_NAME=المنصة الشاملة
APP_ENV=production
DEBUG=false

# --- الأمان ---
# استبدل هذا المفتاح بمفتاح عشوائي حقيقي قبل النشر:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY={secret_key}

# الخادم يقدّم HTTPS عبر نطاق Wispbyte، لذا الكوكي آمن.
# إن كان الوصول عبر http:// فقط فاجعلها false، وإلا لن يعمل الدخول.
COOKIE_SECURE={cookie_secure}
COOKIE_SAMESITE=lax

# --- قاعدة البيانات ---
# SQLite: لا تستهلك ذاكرة إضافية ولا تحتاج خادماً ثانياً،
# وهو ما يتطلبه_plan المجانية (خانة واحدة).
DB_DRIVER=sqlite
SQLITE_PATH=data/mashia.db

# لو توفّرت خانة ثانية لخادم MariaDB، استبدل ما سبق بالتالي:
#   DB_DRIVER=mysql
#   DATABASE_URL=mysql+aiomysql://USER:PASSWORD@HOST:3306/DBNAME?charset=utf8mb4

# --- سلوك التطبيق ---
SCHEDULER_ENABLED=false
ALLOW_PUBLIC_CLUB_REGISTRATION=true
'''


def build(port: int, cookie_secure: str, out_dir: pathlib.Path) -> pathlib.Path:
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
    (out_dir / "main.py").write_text(ENTRYPOINT, encoding="utf-8")

    # ملف الإعدادات
    secret = subprocess.run(
        [sys.executable, "-c",
         "import secrets; print(secrets.token_urlsafe(48))"],
        capture_output=True, text=True,
    ).stdout.strip()
    env_text = ENV_TEMPLATE.format(secret_key=secret, cookie_secure=cookie_secure)

    (out_dir / "wispbyte.env").write_text(env_text, encoding="utf-8")
    (out_dir / ".env").write_text(env_text, encoding="utf-8")

    # مجلد البيانات
    (out_dir / "data").mkdir(exist_ok=True)

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

    archive = build(int(args.port), args.cookie_secure, out_dir)

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