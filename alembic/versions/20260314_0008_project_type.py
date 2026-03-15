"""add projects.type

Revision ID: 20260314_0008
Revises: 20260314_0007
Create Date: 2026-03-14 23:59:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260314_0008"
down_revision: Union[str, None] = "20260314_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS type VARCHAR(100)
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        ALTER TABLE projects
        DROP COLUMN IF EXISTS type
        """
    )

