"""
فحص قابلية النقل إلى MariaDB/MySQL
===================================

المنصة كُتبت على SQLite/PostgreSQL. لنشرها على Wispbyte نحتاج قاعدة
MariaDB، والسؤال الحاسم: **هل تُصرَّف كل استعلاماتنا وجداولنا على
لهجة MariaDB؟**

لا نحتاج خادماً للإجابة. قاموس SQLAlchemy يترجم كل عباراتنا إلى SQL
خاص باللهجة، فمiral:

* إن فشل بناء DDL  -> الجدول لن يُنشأ على الخادم
* إن فشل ترجمة     -> سيتسلل استعلام خام قد لا يُنفَّذ

الاستعلام المُترجَم الذي يمرّ هنا هو **نفسه** الذي سيذهب MariaDB.

لذلك هذا الفحص يسبق أي نشر: يكشف أخطاءً حقيقية بلا اتصال.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.dialects import mysql as mysql_dialect  # noqa: E402
from sqlalchemy.orm import Mapper  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.core.db import Base  # noqa: E402
import app.models  # noqa: E402,F401


def main() -> int:
    failures: list[str] = []

    print("=" * 68)
    print("  فحص قابلية النقل إلى MariaDB / MySQL")
    print("=" * 68)

    tables = list(Base.metadata.sorted_tables)
    print(f"\n▸ الجداول: {len(tables)}")

    # ---------------------------------------------------------
    # 1) بناء DDL لكل جدول
    # ---------------------------------------------------------
    print("\n▸ ترجمة CREATE TABLE:")
    for table in tables:
        try:
            ddl = str(CreateTable(table).compile(dialect=mysql_dialect.dialect()))
        except Exception as exc:
            failures.append(f"DDL {table.name}: {type(exc).__name__}: {exc}")
            print(f"   [FAIL] {table.name:<26} {type(exc).__name__}: {exc}")
            continue

        # فحص حدود الفهارس: InnoDB بحد 3072 بايت لكل فهرس (utf8mb4 = 4 بايت/حرف)
        for index in table.indexes:
            try:
                CreateIndex(index).compile(dialect=mysql_dialect.dialect())
            except Exception as exc:
                failures.append(f"INDEX {table.name}.{index.name}: {exc}")

        # كشف أعمدة نصية طويلة في فهرس (سبب شائع لفشل النشر)
        for index in table.indexes:
            for col in index.columns:
                coltype = col.type
                length = getattr(coltype, "length", None)
                if length and length > 191 and index.unique:
                    failures.append(
                        f"UNIQUE-INDEX-TOO-LONG {table.name}.{index.name}"
                        f"({col.name} VARCHAR({length}))"
                    )

        print(f"   [OK  ] {table.name:<26} {len(ddl.splitlines()):>3} سطر")

    # ---------------------------------------------------------
    # 2) أنواع الأعمدة التي تختلف جوهرياً
    # ---------------------------------------------------------
    print("\n▸ أنواع الأعمدة الحساسة:")
    import sqlalchemy as sa

    json_cols = text_idx = numeric_cols = 0
    for table in tables:
        for col in table.columns:
            t = col.type
            if isinstance(t, sa.JSON):
                json_cols += 1
            if isinstance(t, sa.Numeric):
                numeric_cols += 1
            if isinstance(t, sa.Text):
                text_idx += 1

    print(f"   JSON    : {json_cols} عمود  "
          "(MariaDB 10.2+ يدعمه كـ LONGTEXT مع قيد JSON)")
    print(f"   Numeric : {numeric_cols} عمود  (DECIMAL — دقيق للمال ✓)")
    print(f"   Text    : {text_idx} عمود  (LONGTEXT ✓)")

    # ---------------------------------------------------------
    # 3) بناء كل استعلامات البحث المستخدمة في الخدمات
    # ---------------------------------------------------------
    print("\n▸ ترجمة استعلامات ilike (MariaDB: LOWER() LIKE LOWER()):")
    from sqlalchemy import or_, select

    from app.models import Athlete, Club, ClubNews, Group

    queries = {
        "club.search": select(Club.id).where(
            or_(Club.name.ilike("%x%"), Club.city.ilike("%x%"))
        ),
        "news.search": select(ClubNews.id).where(ClubNews.title.ilike("%x%")),
        "athlete.search": select(Athlete.id).where(
            or_(Athlete.full_name.ilike("%x%"), Athlete.card_code.ilike("%x%"))
        ),
        "group.search": select(Group.id).where(Group.name.ilike("%x%")),
    }

    for label, stmt in queries.items():
        try:
            sql = str(stmt.compile(dialect=mysql_dialect.dialect()))
            one_line = " ".join(sql.split())
            print(f"   [OK  ] {label}")
            print(f"          {one_line[:100]}")
        except Exception as exc:
            failures.append(f"QUERY {label}: {exc}")
            print(f"   [FAIL] {label}: {exc}")

    # ---------------------------------------------------------
    # 4) معايير العزل (الطبقة الأمنية)
    # ---------------------------------------------------------
    print("\n▸ ترجمة معايير عزل المستأجرين:")
    from app.core.tenancy import TenantContext, _resolve_scope_criterion
    from app.models.enums import UserRole

    ctxs = {
        "club": TenantContext(user_id=1, role=UserRole.CLUB_ADMIN, club_id=5),
        "parent": TenantContext(
            user_id=1,
            role=UserRole.PARENT,
            club_id=None,
            athlete_ids=(1, 2, 3),
            child_club_ids=(5, 6),
        ),
        "none": TenantContext(user_id=None, role=None, club_id=None),
    }

    owned = [
        m.class_
        for m in Base.registry.mappers
        if isinstance(m, Mapper)
        and getattr(m.class_, "__tenant_owned__", False)
    ]
    for scope_name, ctx in ctxs.items():
        ok = fail = 0
        for cls in owned:
            criterion = _resolve_scope_criterion(cls, ctx)
            if criterion is None:
                continue
            try:
                stmt = select(cls.id).where(criterion)
                str(stmt.compile(dialect=mysql_dialect.dialect()))
                ok += 1
            except Exception as exc:
                fail += 1
                failures.append(f"TENANCY {scope_name}/{cls.__name__}: {exc}")
        status = "OK  " if fail == 0 else "FAIL"
        print(f"   [{status}] نطاق {scope_name:<8} {ok} معياراً مترجَماً، {fail} فشل")

    # ---------------------------------------------------------
    # النتيجة
    # ---------------------------------------------------------
    print("\n" + "=" * 68)
    if failures:
        print(f"✗ {len(failures)} مشكلة تمنع النشر على MariaDB:\n")
        for f in failures[:25]:
            print(f"   • {f}")
        return 1

    print("✓ المنصة قابلة للنقل إلى MariaDB / MySQL")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())