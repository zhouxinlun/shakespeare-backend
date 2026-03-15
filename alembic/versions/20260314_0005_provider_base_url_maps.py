"""add provider base url maps table

Revision ID: 20260314_0005
Revises: 20260314_0004
Create Date: 2026-03-14 22:05:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260314_0005"
down_revision: Union[str, None] = "20260314_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS provider_base_url_maps (
            id SERIAL PRIMARY KEY,
            manufacturer VARCHAR(50) NOT NULL,
            base_url_prefix VARCHAR(500) NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_base_url_maps_user_prefix ON provider_base_url_maps(user_id, base_url_prefix)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_provider_base_url_maps_user_id ON provider_base_url_maps(user_id)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS provider_base_url_maps CASCADE")
