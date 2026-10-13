"""
Wispbyte entry point (uploaded to the server as ``main.py``)
===========================================================

Wispbyte runs this file through *Use on startup*. Four things have to
be handled here rather than in panel settings, because the panel offers
no shell and its startup command is easy to lose:

* the port lives in *Console -> Server info -> Address* and is not
  exported as ``PORT``, so we probe the usual names and fall back to
  the value baked in at build time;
* the Python egg ships no web framework, so requirements are installed
  on the first boot;
* SQLite has to be migrated before the first request, and the panel
  gives no way to run a migration step;
* the plan grants a bare ``IP:PORT`` -- no hostname, no TLS. A
  Cloudflare quick tunnel fixes that from inside the same container.

A failure in the tunnel is never fatal: the app still serves on its
port, and only the convenience URL is lost.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading

#: The server root is the directory holding this file.
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: Port to fall back on when the environment does not name one.
#: ``build_wispbyte.py`` rewrites this with the real value from the panel.
DEFAULT_PORT = 0

#: Environment variable names Pterodactyl-style panels use for the port.
PORT_VARS = ("PORT", "SERVER_PORT", "SERVERPORT", "MC_PORT")

#: Import names that prove the dependencies are present.
_PROBE = ("uvicorn", "fastapi", "sqlalchemy")

#: Where the tunnel binary and its result are kept.
TUNNEL_DIR = os.path.join(ROOT, ".tunnel")
TUNNEL_BIN = os.path.join(TUNNEL_DIR, "cloudflared")
TUNNEL_URL_FILE = os.path.join(ROOT, "public_url.txt")

#: Direct download for the standalone cloudflared build.
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)

#: The quick tunnel prints its public URL on stderr once connected.
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def resolve_port() -> int:
    """
    Read the assigned port from the environment.

    Falls back to :data:`DEFAULT_PORT`, which the build script fills in
    from *Console -> Server info -> Address*.
    """
    for name in PORT_VARS:
        raw = os.environ.get(name, "").strip()
        if raw.isdigit():
            return int(raw)
    return DEFAULT_PORT


def load_env_file() -> None:
    """Read ``key=value`` lines without overriding the real environment."""
    path = os.path.join(ROOT, "wispbyte.env")
    if not os.path.exists(path):
        return

    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(
                    key.strip(), value.strip().strip("\"'")
                )
    except OSError as exc:
        print(f"[wispbyte] could not read env file: {exc}", flush=True)


def dependencies_present() -> bool:
    """True when the web stack is importable."""
    import importlib.util

    return all(importlib.util.find_spec(name) for name in _PROBE)


def ensure_requirements() -> None:
    """
    Install dependencies on the first boot.

    Skipped entirely when the stack is already importable, so later
    boots start immediately instead of waiting on pip.
    """
    if dependencies_present():
        return

    print("[wispbyte] installing dependencies (first boot)...", flush=True)
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "-r", os.path.join(ROOT, "requirements.txt"),
        ],
        cwd=ROOT,
        check=False,
    )

    if result.returncode != 0 or not dependencies_present():
        raise RuntimeError("dependency installation failed")


def apply_migrations() -> None:
    """Bring the SQLite schema up to head before serving any request."""
    import asyncio

    from app.core import schema

    asyncio.run(schema.sync_schema(reset=False, quiet=True))
    print("[wispbyte] database ready", flush=True)


def _download_cloudflared() -> bool:
    """
    Fetch the standalone cloudflared binary once, if it is missing.

    The binary is ~40 MB. On a throttled node that is minutes of work,
    not seconds, so this streams to disk with progress and writes to a
    ``.part`` file first: a download cut short by a restart leaves no
    half-written binary that later looks complete.
    """
    if os.path.exists(TUNNEL_BIN):
        return True

    if shutil.which("cloudflared"):
        return True

    os.makedirs(TUNNEL_DIR, exist_ok=True)
    part = TUNNEL_BIN + ".part"

    import time
    import urllib.request

    for attempt in (1, 2):
        print(f"[wispbyte] fetching cloudflared (attempt {attempt})...", flush=True)
        started = time.time()
        written = 0
        marked = 0

        try:
            request = urllib.request.Request(
                CLOUDFLARED_URL, headers={"User-Agent": "curl/8.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                with open(part, "wb") as handle:
                    while True:
                        chunk = response.read(262144)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if written - marked >= 8 * 1024 * 1024:
                            marked = written
                            print(
                                f"[wispbyte]   {written // 1024 // 1024} MB",
                                flush=True,
                            )

            os.replace(part, TUNNEL_BIN)
            os.chmod(TUNNEL_BIN, 0o755)
            print(
                f"[wispbyte] cloudflared ready "
                f"({written // 1024 // 1024} MB in {time.time() - started:.0f}s)",
                flush=True,
            )
            return True

        except Exception as exc:
            print(f"[wispbyte]   download failed: {exc}", flush=True)
            if os.path.exists(part):
                try:
                    os.unlink(part)
                except OSError:
                    pass

    print("[wispbyte] cloudflared unavailable - continuing without it", flush=True)
    return False


def start_tunnel(port: int) -> None:
    """
    Publish the app on a free HTTPS URL that needs no account.

    The free plan hands out a bare ``IP:PORT``: no hostname and no TLS.
    A Cloudflare quick tunnel is a reverse proxy that needs neither a
    Cloudflare account nor a registered domain -- it returns a random
    ``*.trycloudflare.com`` hostname with a valid certificate.

    Runs in the background so uvicorn keeps the main thread. Every
    failure is swallowed: losing the convenience URL must never stop
    the app from serving on its port.
    """

    def worker() -> None:
        try:
            if not _download_cloudflared():
                return

            binary = TUNNEL_BIN if os.path.exists(TUNNEL_BIN) else "cloudflared"
            proc = subprocess.Popen(
                [binary, "tunnel", "--no-autoupdate",
                 "--url", f"http://127.0.0.1:{port}"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            print(f"[wispbyte] tunnel could not start: {exc}", flush=True)
            return

        for line in proc.stdout:
            match = _URL_RE.search(line)
            if not match:
                continue
            url = match.group(0)
            print("=" * 62, flush=True)
            print(f"[wispbyte] PUBLIC URL : {url}", flush=True)
            print("=" * 62, flush=True)
            try:
                with open(TUNNEL_URL_FILE, "w", encoding="utf-8") as handle:
                    handle.write(url + "\n")
            except OSError:
                pass
            break

    threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    load_env_file()
    ensure_requirements()

    port = resolve_port()
    print(f"[wispbyte] root  : {ROOT}", flush=True)
    print(f"[wispbyte] port  : {port}", flush=True)

    apply_migrations()
    start_tunnel(port)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        timeout_keep_alive=60,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
