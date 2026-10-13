"""MariaDB-safe unique indexes on clubs.slug and users.email.

The initial migration created both as plain ``unique=True`` indexes. Under
``utf8mb4`` those occupy 880 and 1020 bytes, which exceeds InnoDB's legacy
index prefix limit of 767 bytes -- so ``CREATE TABLE`` fails outright on an
older MySQL/MariaDB server.

The fix keeps the stored column at full width (no data truncation) and
recreates each index under its model name with a 191-character prefix
(764 bytes), which fits within every server's limit. Other dialects
ignore the prefix length, so their indexes remain complete.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "ae21f2242ca9"
down_revision: Union[str, None] = "b99f34c1012b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 191 characters x 4 bytes (utf8mb4) = 764 bytes, under the 767 limit
PREFIX = 191

#: (table, column, old index name, new index name in the model)
TABLES = (
    ("clubs", "slug", "ix_clubs_slug", "uq_clubs_slug"),
    ("users", "email", "ix_users_email", "uq_users_email"),
)


def upgrade() -> None:
    """Recreate the unique indexes with a MySQL-safe prefix length."""
    for table, column, old_name, new_name in TABLES:
        with op.batch_alter_table(table, schema=None) as batch:
            batch.drop_index(old_name)
            batch.create_index(
                new_name, [column], unique=True, mysql_length=PREFIX,
            )


def downgrade() -> None:
    """Restore the original full-width unique indexes."""
    for table, column, old_name, new_name in TABLES:
        with op.batch_alter_table(table, schema=None) as batch:
            batch.drop_index(new_name)
            batch.create_index(old_name, [column], unique=True)
