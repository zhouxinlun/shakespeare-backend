"""add novel chat sessions

Revision ID: 20260324_0013
Revises: 20260316_0012
Create Date: 2026-03-24 22:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260324_0013"
down_revision: Union[str, None] = "20260316_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS novel_chat_sessions (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            last_message_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_chat_sessions_project_user_last_message
        ON novel_chat_sessions(project_id, user_id, last_message_at DESC)
        """,
        """
        ALTER TABLE novel_chat_messages
        ADD COLUMN IF NOT EXISTS session_id INTEGER
        REFERENCES novel_chat_sessions(id) ON DELETE CASCADE
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_chat_messages_session_created
        ON novel_chat_messages(session_id, created_at DESC)
        """,
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)

    grouped_rows = conn.exec_driver_sql(
        """
        SELECT
            project_id,
            user_id,
            MIN(created_at) AS created_at,
            MAX(created_at) AS last_message_at
        FROM novel_chat_messages
        WHERE session_id IS NULL
        GROUP BY project_id, user_id
        """
    ).mappings()

    for row in grouped_rows:
        session_id = conn.execute(
            sa.text(
                """
            INSERT INTO novel_chat_sessions (
                project_id,
                user_id,
                title,
                created_at,
                updated_at,
                last_message_at
            )
            VALUES (
                :project_id,
                :user_id,
                :title,
                :created_at,
                :updated_at,
                :last_message_at
            )
            RETURNING id
            """
            ),
            {
                "project_id": row["project_id"],
                "user_id": row["user_id"],
                "title": "历史导入会话",
                "created_at": row["created_at"],
                "updated_at": row["last_message_at"],
                "last_message_at": row["last_message_at"],
            },
        ).scalar_one()
        conn.execute(
            sa.text(
                """
            UPDATE novel_chat_messages
            SET session_id = :session_id
            WHERE project_id = :project_id
              AND user_id = :user_id
              AND session_id IS NULL
            """
            ),
            {
                "session_id": session_id,
                "project_id": row["project_id"],
                "user_id": row["user_id"],
            },
        )

    conn.exec_driver_sql(
        """
        ALTER TABLE novel_chat_messages
        ALTER COLUMN session_id SET NOT NULL
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        """
        ALTER TABLE novel_chat_messages
        ALTER COLUMN session_id DROP NOT NULL
        """,
        "DROP INDEX IF EXISTS idx_novel_chat_messages_session_created",
        """
        ALTER TABLE novel_chat_messages
        DROP COLUMN IF EXISTS session_id
        """,
        "DROP INDEX IF EXISTS idx_novel_chat_sessions_project_user_last_message",
        "DROP TABLE IF EXISTS novel_chat_sessions",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
