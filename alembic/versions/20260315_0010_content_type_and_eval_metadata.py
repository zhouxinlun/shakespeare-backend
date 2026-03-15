"""add content_type and evaluation metadata

Revision ID: 20260315_0010
Revises: 20260314_0009
Create Date: 2026-03-15 13:30:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260315_0010"
down_revision: Union[str, None] = "20260314_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    statements = [
        """
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS content_type VARCHAR(50) NOT NULL DEFAULT 'short_drama'
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_projects_user_id_content_type
        ON projects(user_id, content_type)
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS content_type VARCHAR(50) NOT NULL DEFAULT 'short_drama'
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS evaluation_type VARCHAR(50) NOT NULL DEFAULT 'chapter_only'
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS novel_revision INTEGER NOT NULL DEFAULT 1
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS parent_evaluation_id INTEGER REFERENCES novel_evaluations(id) ON DELETE SET NULL
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS model_used VARCHAR(100) NOT NULL DEFAULT 'novel_evaluator'
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(50) NOT NULL DEFAULT 'short_drama.v1'
        """,
        """
        ALTER TABLE novel_evaluations
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        """,
        """
        UPDATE novel_evaluations
        SET content_type = COALESCE(content_type, 'short_drama'),
            evaluation_type = COALESCE(evaluation_type, 'chapter_only'),
            novel_revision = COALESCE(novel_revision, 1),
            model_used = COALESCE(model_used, 'novel_evaluator'),
            prompt_version = COALESCE(prompt_version, 'short_drama.v1'),
            updated_at = COALESCE(updated_at, created_at, NOW())
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_evaluations_novel_created
        ON novel_evaluations(novel_id, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_novel_evaluations_project_content_type
        ON novel_evaluations(project_id, content_type)
        """,
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "DROP INDEX IF EXISTS idx_novel_evaluations_project_content_type",
        "DROP INDEX IF EXISTS idx_novel_evaluations_novel_created",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS updated_at",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS prompt_version",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS model_used",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS parent_evaluation_id",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS novel_revision",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS evaluation_type",
        "ALTER TABLE novel_evaluations DROP COLUMN IF EXISTS content_type",
        "DROP INDEX IF EXISTS idx_projects_user_id_content_type",
        "ALTER TABLE projects DROP COLUMN IF EXISTS content_type",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
