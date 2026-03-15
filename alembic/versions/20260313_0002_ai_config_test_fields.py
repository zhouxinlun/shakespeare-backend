"""add ai config test result fields

Revision ID: 20260313_0002
Revises: 20260313_0001
Create Date: 2026-03-13 22:30:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260313_0002"
down_revision: Union[str, None] = "20260313_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    statements = [
        "ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS last_test_status VARCHAR(20)",
        "ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS last_test_summary VARCHAR(2000)",
        "ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS last_tested_at TIMESTAMP WITHOUT TIME ZONE",
        "ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS supports_tools BOOLEAN",
        "ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS supports_thinking BOOLEAN",
        "ALTER TABLE ai_configs ADD COLUMN IF NOT EXISTS supports_image_generation BOOLEAN",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "ALTER TABLE ai_configs DROP COLUMN IF EXISTS supports_image_generation",
        "ALTER TABLE ai_configs DROP COLUMN IF EXISTS supports_thinking",
        "ALTER TABLE ai_configs DROP COLUMN IF EXISTS supports_tools",
        "ALTER TABLE ai_configs DROP COLUMN IF EXISTS last_tested_at",
        "ALTER TABLE ai_configs DROP COLUMN IF EXISTS last_test_summary",
        "ALTER TABLE ai_configs DROP COLUMN IF EXISTS last_test_status",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
