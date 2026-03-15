"""add ai_configs.image_min_size

Revision ID: 20260314_0006
Revises: 20260314_0005
Create Date: 2026-03-14 23:20:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260314_0006"
down_revision: Union[str, None] = "20260314_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        ALTER TABLE ai_configs
        ADD COLUMN IF NOT EXISTS image_min_size VARCHAR(20)
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        ALTER TABLE ai_configs
        DROP COLUMN IF EXISTS image_min_size
        """
    )

