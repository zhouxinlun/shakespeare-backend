"""add ai config vision capability field

Revision ID: 20260314_0004
Revises: 20260313_0003
Create Date: 2026-03-14 00:10:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260314_0004"
down_revision: Union[str, None] = "20260313_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS supports_vision BOOLEAN")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE ai_configs DROP COLUMN IF EXISTS supports_vision")
