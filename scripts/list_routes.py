"""
عرض كل مسارات التطبيق
=====================
مساران للمعلومات، كلٌّ من مصدره الموثوق:

* **REST API** من مخطط OpenAPI (``app.openapi()``) -- هذا هو ما يوثّقه
  FastAPI نفسه ويطابق ما يُنفَّذ فعلاً.
* **صفحات الواجهة** من المشي في الموجّهات، لأن صفحات HTML مستثناة من
  OpenAPI (``include_in_schema=False``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402


def api_rows() -> list[tuple[str, str]]:
    """مسارات الـ API من مخطط OpenAPI."""
    schema = app.openapi()
    rows: list[tuple[str, str]] = []
    for path, operations in schema.get("paths", {}).items():
        for method in operations:
            rows.append((method.upper(), path))
    for path in ("/api/health", "/manifest.webmanifest", "/sw.js"):
        rows.append(("GET", path))
    return rows


def web_rows(router=None, prefix: str = "") -> list[tuple[str, str]]:
    """
    مسارات صفحات الواجهة.

    نبدأ من ``app.web.router`` مباشرة بدل جذر التطبيق: فبعض الموجّهات
    تُضمَّن في شجرة الـ API أيضاً، ومشيُها من الجذر كان يخلط مسارات
    ``/admin/*`` بين الشجرتين.
    """
    if router is None:
        from app.web import router as web_router

        router = web_router

    rows: list[tuple[str, str]] = []

    for route in getattr(router, "routes", []):
        inner = getattr(route, "original_router", None) or getattr(route, "router", None)
        if inner is not None:
            # مسارات الموجّه المُضمَّن مطلقة في FastAPI 0.141،
            # فلا نضيف ``inner.prefix`` (كانت ستضاعف كل مسار).
            rows.extend(web_rows(inner, prefix))
            continue

        path = getattr(route, "path", "") or ""
        methods = sorted(getattr(route, "methods", None) or [])
        full = prefix + path
        for method in methods:
            rows.append((method, full))

    return rows


def group_by_root(rows: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for method, path in rows:
        segments = path.strip("/").split("/")
        root = f"/{segments[0]}" if path.strip("/") else "/"
        groups.setdefault(root, []).append((method, path))
    return groups


def main() -> int:
    api = api_rows()
    web = web_rows()

    line = "=" * 74
    print(line)
    print(f"  مسارات API    : {len(api)}")
    print(f"  صفحات الواجهة : {len(web)}")
    print(line)

    for title, rows in (("REST API  (/api)", api), ("الواجهة", web)):
        print(f"\n{'─' * 20} {title} {'─' * 20}")
        for root, items in sorted(group_by_root(rows).items()):
            print(f"\n  {root}")
            for method, path in sorted(set(items), key=lambda x: x[1]):
                print(f"      {method:<7} {path}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())