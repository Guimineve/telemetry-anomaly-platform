"""rules and alerts

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("metric", sa.String(32), nullable=True),
        sa.Column("device_pattern", sa.String(64), nullable=False, server_default="*"),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("rule_name", sa.String(120), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("metric", sa.String(32), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_alerts_ts", "alerts", ["ts"])
    op.create_index("ix_alerts_device_ts", "alerts", ["device_id", "ts"])
    op.create_index("ix_alerts_rule_device_ts", "alerts", ["rule_id", "device_id", "ts"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("rules")
