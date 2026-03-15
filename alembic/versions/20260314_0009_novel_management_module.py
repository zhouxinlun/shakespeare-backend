"""novel management module schema

Revision ID: 20260314_0009
Revises: 20260314_0008
Create Date: 2026-03-14 23:59:30
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260314_0009"
down_revision: Union[str, None] = "20260314_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    statements = [
        """
        ALTER TABLE novels
        ADD COLUMN IF NOT EXISTS volume VARCHAR(200)
        """,
        """
        ALTER TABLE novels
        ADD COLUMN IF NOT EXISTS word_count INTEGER NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE novels
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        """,
        """
        UPDATE novels
        SET word_count = COALESCE(char_length(regexp_replace(content, E'\\s+', '', 'g')), 0),
            updated_at = COALESCE(updated_at, created_at, NOW())
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_novels_project_chapter_index
        ON novels(project_id, chapter_index)
        """,
        """
        CREATE TABLE IF NOT EXISTS novel_evaluations (
            id SERIAL PRIMARY KEY,
            novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
            overall_score DOUBLE PRECISION NOT NULL,
            dimension_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
            summary TEXT,
            suggestions JSONB NOT NULL DEFAULT '[]'::jsonb,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_evaluations_novel_id
        ON novel_evaluations(novel_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_evaluations_project_id
        ON novel_evaluations(project_id)
        """,
    ]

    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "DROP TABLE IF EXISTS novel_evaluations CASCADE",
        "DROP INDEX IF EXISTS uq_novels_project_chapter_index",
        "ALTER TABLE novels DROP COLUMN IF EXISTS updated_at",
        "ALTER TABLE novels DROP COLUMN IF EXISTS word_count",
        "ALTER TABLE novels DROP COLUMN IF EXISTS volume",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
