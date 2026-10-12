"""
مطابقة نماذج ORM بملف الترحيل
==============================

الترحيل وُلِّد من النسخة السليمة من النماذج، فهو المرجع الموثوق. بعد أي
إصلاح يدوي لنموذج، نتحقق أن أسماء الجداول والأعمدة ما تزال مطابقة --
وإلا فإن ``alembic check`` سيقترح ترحيلاً وهمياً أو يفشل عند التطبيق.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import MetaData  # noqa: E402

from app.core.db import Base  # noqa: E402
import app.models  # noqa: E402,F401


def migration_tables(text: str) -> dict[str, set[str]]:
    """
    استخراج {جدول: {أعمدة}} من ملف الترحيل.

    نقسّم النص على ``op.create_table(`` ثم نأخذ ما بين بداية الكتلة
    وأول سطر ``)`` يُغلقها.
    """
    tables: dict[str, set[str]] = {}

    chunks = text.split("op.create_table(")[1:]
    for chunk in chunks:
        name_match = re.match(r"\s*'([a-z_]+)'", chunk)
        if not name_match:
            continue

        name = name_match.group(1)

        # نحدّد النهاية بأول سطر يتكوّن من ``)`` وحدها — وهو إغلاق
        # الكتلة. (``chunk.find("\n    )\n")`` كان يقطع الكتلت�� الطويلة
        # لأن بعض الفهارس تُغلَّف بأقواس إضافية قبلها.)
        end_match = re.search(r"^\s*\)\s*$", chunk, re.M)
        body = chunk[: end_match.start()] if end_match else chunk

        # ``[a-z_0-9]`` لا ``[a-z_]``: أسماء الأعمدة تحوي أرقاماً
        # (``phone_2``)، و``[a-z_]+`` كان يُسقطها بصمت.
        columns = set(re.findall(r"sa\.Column\(\s*'([a-z_0-9]+)'", body))
        tables[name] = columns

    return tables


def main() -> int:
    versions = sorted((ROOT / "alembic" / "versions").glob("*.py"))
    if not versions:
        print("لا يوجد ملف ترحيل.")
        return 1

    text = versions[0].read_text(encoding="utf-8")
    mig = migration_tables(text)

    model_tables = set(Base.metadata.tables)
    print(f"جداول النموذج : {len(model_tables)}")
    print(f"جداول الترحيل: {len(mig)}\n")

    failures: list[str] = []

    for table_name in sorted(model_tables):
        table = Base.metadata.tables[table_name]
        model_cols = {c.name for c in table.columns}
        mig_cols = mig.get(table_name)

        if mig_cols is None:
            print(f"  ✗ {table_name:<28} غير موجود في الترحيل")
            failures.append(f"{table_name}: مفقود من الترحيل")
            continue

        only_model = model_cols - mig_cols
        only_mig = mig_cols - model_cols

        if only_model or only_mig:
            print(f"  ✗ {table_name}")
            if only_model:
                print(f"      في النموذج فقط : {sorted(only_model)}")
            if only_mig:
                print(f"      في الترحيل فقط : {sorted(only_mig)}")
            failures.append(table_name)

    only_in_migration = set(mig) - model_tables
    if only_in_migration:
        print(f"\n  ✗ جداول في الترحيل فقط: {sorted(only_in_migration)}")
        failures.extend(sorted(only_in_migration))

    print()
    if failures:
        print(f"تعارض في {len(failures)} جدولاً — نموذج غير متطابق مع الترحيل.")
        return 1

    print(f"✓ النماذج ({len(model_tables)} جدولاً) متطابقة تماماً مع الترحيل.")
    return 0


if __name__ == "__main__":
    sys.exit(main())