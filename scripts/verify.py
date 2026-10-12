"""
تحقّق نهائي شامل من المنصة
==========================

يعمل على خادم محلي أو **مُنشَر**، ويتحقّق من كل المسارات الحرجة عبر
HTTP حقيقي.

الاستخدام::

    python scripts/verify.py                             # محلي
    python scripts/verify.py https://mashia.onrender.com  # بعد النشر

ملاحظة على الخطة المجانية:Render يوقظ الحاوية بعد خمول، لذلك أول
طلب قد يستغرق نحو 50 ثانية. نضيف مهلة أطول ونعيد الطلب مرة واحدة
عند فشل الاتصال، وإلا سيُبلّغ الفحص عن خادم ميت وهو حيّ.
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8040"

#: هل العنوان بعيد؟ نُطيل المهلة ونُعيد المحاولة عند البوابات المجانية.
IS_REMOTE = BASE.startswith(("http://", "https://")) and "127.0.0.1" not in BASE

TIMEOUT = 90.0 if IS_REMOTE else 20.0
RETRIES = 2 if IS_REMOTE else 0

#: حساب مدير النادي التجريبي الذي ينشئه ``python -m scripts.seed``
DEMO_ADMIN = "admin1@toufik-sport.dz"
DEMO_PASSWORD = "Mashia@2024"

PUBLIC = ["/", "/clubs", "/news", "/auth/login", "/auth/register", "/clubs/نادي-التوفيق-الرياضي"]
PROTECTED = ["/club", "/club/athletes", "/club/groups", "/club/finance",
             "/coach", "/parent", "/admin", "/profile"]
API = ["/api/health", "/api/v1/public/clubs", "/api/v1/public/news",
       "/api/v1/public/sports", "/api/v1/public/clubs/filters", "/api/docs"]
ASSETS = ["/static/css/app.css", "/static/js/app.js", "/static/icons/icon.svg",
          "/manifest.webmanifest", "/sw.js"]

failures: list[str] = []


def _page_error(html: str) -> str:
    """استخراج رسالة الخطأ من صفحة HTML (للتشخيص فقط)."""
    import re

    match = re.search(r"alert-content[^>]*>(.+?)</div>", html, re.S)
    return f"رسالة: {match.group(1).strip()[:80]}" if match else "بلا رسالة"


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def fetch(client: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    """
    طلب مع إعادة محاولة عند فشل الاتصال.

    على المنصّات المجانية، الحاوية المُوقظة تستغرق نحو 50 ثانية في
    الإقلاع. بلا إعادة محاولة سيُبلّغ الفحص عن فشل وهمي -- وهو أسوأ
    من البطء، لأنه يُميّز نشراً ناجحاً وتعطلاً حقيقياً.
    """
    last: Exception | None = None

    for attempt in range(RETRIES + 1):
        try:
            return client.request(method, path, **kw)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last = exc
            if attempt < RETRIES:
                print(f"  … {path}: {type(exc).__name__} — إعادة المحاولة "
                      f"({attempt + 1}/{RETRIES})… (الاستيقاظ قد يستغرق ~50 ثانية)")
                time.sleep(5)

    raise last if last else RuntimeError("unreachable")


def main() -> int:
    print(f"\nالعنوان: {BASE}")
    print(f"المهلة : {TIMEOUT:.0f}s | إعادة المحاولة: {RETRIES}")

    with httpx.Client(base_url=BASE, timeout=TIMEOUT, follow_redirects=True) as c:
        print("\n=== صفحات عامة (بلا مصادقة) ===")
        for path in PUBLIC:
            r = fetch(c, "GET", path)
            check(path, r.status_code == 200 and "<html" in r.text.lower(),
                  f"{r.status_code}, {len(r.text)} chars")

        print("\n=== حماية الصفحات (زائر) ===")
        for path in PROTECTED:
            r = fetch(c, "GET", path)
            redirected = str(r.url.path).startswith("/auth/login")
            check(path, redirected, f"ends at {r.url.path}")

        print("\n=== REST API ===")
        for path in API:
            r = fetch(c, "GET", path)
            check(path, r.status_code == 200, f"{r.status_code}")

        print("\n=== الأصول و PWA ===")
        for path in ASSETS:
            r = fetch(c, "GET", path)
            check(path, r.status_code == 200 and len(r.content) > 0,
                  f"{len(r.content)} bytes")

        print("\n=== تسجيل الدخول والدخول للوحة ===")
        r = fetch(c, "POST", "/auth/login", data={
            "identifier": DEMO_ADMIN,
            "password": DEMO_PASSWORD,
        })

        cookie = c.cookies.get("mashia_session")
        logged_in = bool(cookie)

        # كوكي ``secure=True`` لا يُرسَل عبر HTTP — وهذا سلوك صحيح في
        # الإنتاج، لكنه يجعل الفحص محلياً يظنّ أن الجلسة لم تُضبط.
        # نتحقق من الإرسال الفعلي بترويسة Cookie في الطلب التالي.
        sent = c.get("/profile").request.headers.get("cookie")
        cookie_sent = bool(sent and "mashia_session" in sent)

        detail = f"status={r.status_code}, cookie={'yes' if logged_in else 'no'}"
        if not cookie_sent and logged_in:
            detail += " (غير مُرسل: secure cookie على HTTP)"
        if r.status_code != 200:
            detail += ", " + _page_error(r.text)

        check("تسجيل الدخول", r.status_code == 200 and logged_in, detail)

        # كل ما بعده يتطلّب كوكي مُرسَلاً فعلاً
        if logged_in and cookie_sent:
            for path in ("/club", "/club/athletes", "/club/groups",
                         "/club/sessions", "/club/finance", "/club/staff",
                         "/club/news", "/club/registrations", "/club/import",
                         "/profile"):
                r = fetch(c, "GET", path)
                check(path, r.status_code == 200, f"{r.status_code}")

            r = fetch(c, "GET", "/api/v1/club/athletes")
            ok = r.status_code == 200 and "items" in r.json()
            total = r.json().get("total") if ok else None
            check("GET /api/v1/club/athletes", ok, f"total={total}")

            r = fetch(c, "GET", "/club/export/athletes")
            check("تصدير Excel", r.status_code == 200 and r.content[:2] == b"PK",
                  f"{len(r.content)} bytes")

        elif logged_in and not cookie_sent:
            # كوكي secure على HTTP: نتحقق من المصادقة بالـ JWT بدلاً منها
            print("  … تخطّي فحوص الجلسة (كوكي secure على HTTP) — نستخدم JWT")

            r = fetch(c, "POST", "/api/v1/auth/login", json={
                "identifier": DEMO_ADMIN,
                "password": DEMO_PASSWORD,
            })
            token_ok = r.status_code == 200 and "access_token" in r.text

            if token_ok:
                token = r.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                r = fetch(c, "GET", "/api/v1/club/athletes", headers=headers)
                ok = r.status_code == 200 and "items" in r.json()
                total = r.json().get("total") if ok else None
                check("GET /api/v1/club/athletes (JWT)", ok, f"total={total}")
            else:
                check("دخول API عبر JWT", False, f"status={r.status_code}")

    print("\n" + "=" * 60)
    if failures:
        print(f"فشل {len(failures)} فحصاً:")
        for f in failures:
            print(f"  - {f}")
        print()
        if IS_REMOTE:
            print("نصائح لنشر على Render:")
            print("  • Logs في الخدمة — ابحث عن Traceback")
            print("  • الحاوية المجانية تتوقف بعد خمول: أول طلب يبطؤ ~50 ثانية")
            print("  • تأكد أن الترحيلات طُبّقت (تظهر في Logs)")
        return 1

    print("كل الفحوص ناجحة ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())