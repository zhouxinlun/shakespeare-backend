"""init schema

Revision ID: 20260313_0001
Revises:
Create Date: 2026-03-13 18:35:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260313_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use IF NOT EXISTS to make baseline migration safe on existing local DBs.
    conn = op.get_bind()
    statements = [
        'CREATE EXTENSION IF NOT EXISTS "pgcrypto"',
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            password VARCHAR(200) NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            intro VARCHAR(1000),
            art_style VARCHAR(200),
            video_ratio VARCHAR(10) DEFAULT '9:16',
            pipeline_state JSONB NOT NULL DEFAULT '{"novel":"pending","outline":"pending","script":"pending","storyboard":"pending","images":"pending","video":"pending","current_stage":null,"current_progress":0,"current_message":"","error":null}'::jsonb,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS novels (
            id SERIAL PRIMARY KEY,
            chapter_index INTEGER NOT NULL,
            chapter_title VARCHAR(500),
            content TEXT NOT NULL,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS outlines (
            id SERIAL PRIMARY KEY,
            episode_index INTEGER NOT NULL,
            title VARCHAR(200),
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS storylines (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '',
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scripts (
            id SERIAL PRIMARY KEY,
            episode_index INTEGER NOT NULL,
            title VARCHAR(200),
            content TEXT NOT NULL DEFAULT '',
            outline_id INTEGER NOT NULL REFERENCES outlines(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS storyboards (
            id SERIAL PRIMARY KEY,
            episode_index INTEGER NOT NULL,
            script_id INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            shots JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS assets (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            intro TEXT,
            prompt TEXT,
            type VARCHAR(20) NOT NULL,
            episode_index INTEGER,
            file_path VARCHAR(500),
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            outline_id INTEGER REFERENCES outlines(id) ON DELETE SET NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_configs (
            id SERIAL PRIMARY KEY,
            type VARCHAR(20) NOT NULL,
            manufacturer VARCHAR(50) NOT NULL,
            model VARCHAR(200) NOT NULL,
            api_key VARCHAR(500) NOT NULL,
            base_url VARCHAR(500),
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_model_maps (
            id SERIAL PRIMARY KEY,
            key VARCHAR(100) NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            config_id INTEGER REFERENCES ai_configs(id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS prompts (
            id SERIAL PRIMARY KEY,
            code VARCHAR(100) NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            type VARCHAR(20) NOT NULL,
            parent_code VARCHAR(100),
            default_value TEXT NOT NULL,
            custom_value TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            task_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            result JSONB,
            error VARCHAR(1000),
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_novels_project_id ON novels(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_outlines_project_id ON outlines(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_storylines_project_id ON storylines(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_scripts_outline_id ON scripts(outline_id)",
        "CREATE INDEX IF NOT EXISTS idx_scripts_project_id ON scripts(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_storyboards_script_id ON storyboards(script_id)",
        "CREATE INDEX IF NOT EXISTS idx_storyboards_project_id ON storyboards(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_assets_project_id ON assets(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_assets_outline_id ON assets(outline_id)",
        "CREATE INDEX IF NOT EXISTS idx_ai_configs_user_id ON ai_configs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    statements = [
        "DROP TABLE IF EXISTS tasks CASCADE",
        "DROP TABLE IF EXISTS prompts CASCADE",
        "DROP TABLE IF EXISTS ai_model_maps CASCADE",
        "DROP TABLE IF EXISTS ai_configs CASCADE",
        "DROP TABLE IF EXISTS assets CASCADE",
        "DROP TABLE IF EXISTS storyboards CASCADE",
        "DROP TABLE IF EXISTS scripts CASCADE",
        "DROP TABLE IF EXISTS storylines CASCADE",
        "DROP TABLE IF EXISTS outlines CASCADE",
        "DROP TABLE IF EXISTS novels CASCADE",
        "DROP TABLE IF EXISTS projects CASCADE",
        "DROP TABLE IF EXISTS users CASCADE",
    ]
    for statement in statements:
        conn.exec_driver_sql(statement)
