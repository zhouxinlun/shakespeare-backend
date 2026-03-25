"""add novel chat artifact fields

Revision ID: 20260324_0014
Revises: 20260324_0013
Create Date: 2026-03-24 23:40:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260324_0014"
down_revision: Union[str, None] = "20260324_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    statements = [
        """
        ALTER TABLE novel_chat_messages
        ADD COLUMN IF NOT EXISTS artifact_type VARCHAR(50)
        """,
        """
        ALTER TABLE novel_chat_messages
        ADD COLUMN IF NOT EXISTS artifact_status VARCHAR(30)
        """,
        """
        ALTER TABLE novel_chat_messages
        ADD COLUMN IF NOT EXISTS requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE novel_chat_messages
        ADD COLUMN IF NOT EXISTS artifact_payload JSONB
        """,
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "ALTER TABLE novel_chat_messages DROP COLUMN IF EXISTS artifact_payload",
        "ALTER TABLE novel_chat_messages DROP COLUMN IF EXISTS requires_confirmation",
        "ALTER TABLE novel_chat_messages DROP COLUMN IF EXISTS artifact_status",
        "ALTER TABLE novel_chat_messages DROP COLUMN IF EXISTS artifact_type",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
