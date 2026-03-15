"""add book evaluations table

Revision ID: 20260315_0011
Revises: 20260315_0010
Create Date: 2026-03-15 22:00:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260315_0011"
down_revision: Union[str, None] = "20260315_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS book_evaluations (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            content_type VARCHAR(50) NOT NULL DEFAULT 'short_drama',
            evaluated_novel_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            aggregated_stats JSONB NOT NULL DEFAULT '{}'::jsonb,
            consistency_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
            overall_assessment JSONB NOT NULL DEFAULT '{}'::jsonb,
            model_used VARCHAR(100) NOT NULL DEFAULT 'book_evaluator',
            prompt_version VARCHAR(50) NOT NULL DEFAULT 'book.v1',
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_book_evaluations_project_created
        ON book_evaluations(project_id, created_at DESC)
        """,
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "DROP INDEX IF EXISTS idx_book_evaluations_project_created",
        "DROP TABLE IF EXISTS book_evaluations",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
