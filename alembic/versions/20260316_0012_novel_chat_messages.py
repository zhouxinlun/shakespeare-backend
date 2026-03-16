"""add novel chat messages table

Revision ID: 20260316_0012
Revises: 20260315_0011
Create Date: 2026-03-16 18:20:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260316_0012"
down_revision: Union[str, None] = "20260315_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS novel_chat_messages (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            message TEXT NOT NULL,
            skill VARCHAR(50),
            selected_novel_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_chat_messages_project_user_created
        ON novel_chat_messages(project_id, user_id, created_at DESC)
        """,
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "DROP INDEX IF EXISTS idx_novel_chat_messages_project_user_created",
        "DROP TABLE IF EXISTS novel_chat_messages",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
