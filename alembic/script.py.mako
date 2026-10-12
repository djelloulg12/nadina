"""تهيئة ترحيلات Alembic (قالب الترحيل).

يُنسخ هذا الملف لكل ترحيل جديد عبر ``alembic revision -m "..."``.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

#: معرّف الترحيل
revision: str = ${repr(up_revision)}
#: الترحيل السابق
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """تطبيق الترحيل (اتجاه أعلى)."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """التراجع عن الترحيل (اتجاه أسفل)."""
    ${downgrades if downgrades else "pass"}