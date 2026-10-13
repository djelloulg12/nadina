"""
Wispbyte entry point (uploaded to the server as ``main.py``)
===========================================================

Wispbyte runs this file through *Use on startup*. Three things have to
be handled here rather than in panel settings, because the panel offers
no shell and its startup command is easy to lose:

* the port lives in *Console -> Server info -> Address* and is not
  exported as ``PORT``, so we probe the usual names and fall back to
  the value baked in at build time;
* the Python egg ships no web framework, so requirements are installed
  on the first boot;
* SQLite has to be migrated before the first request, and the panel
  gives no way to run a migration step.

A failure here exits non-zero on purpose: a silent ``0`` would look
healthy in the panel while the app served nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys

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


def main() -> int:
    load_env_file()
    ensure_requirements()

    port = resolve_port()
    print(f"[wispbyte] root  : {ROOT}", flush=True)
    print(f"[wispbyte] port  : {port}", flush=True)

    apply_migrations()

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
