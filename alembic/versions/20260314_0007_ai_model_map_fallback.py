"""add ai_model_maps.fallback_config_ids

Revision ID: 20260314_0007
Revises: 20260314_0006
Create Date: 2026-03-14 23:55:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260314_0007"
down_revision: Union[str, None] = "20260314_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        ALTER TABLE ai_model_maps
        ADD COLUMN IF NOT EXISTS fallback_config_ids JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        ALTER TABLE ai_model_maps
        DROP COLUMN IF EXISTS fallback_config_ids
        """
    )

