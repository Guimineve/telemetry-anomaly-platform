"""users.token_version

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("token_version", sa.Integer, nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
