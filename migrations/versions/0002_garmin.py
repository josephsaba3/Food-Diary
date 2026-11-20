"""Garmin link and cached health data."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("garmin_account", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("api_key", sa.Text, nullable=False), sa.Column("access_token", sa.Text),
        sa.Column("token_expires_at", sa.BigInteger), sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text, nullable=False))
    op.create_table("health_days", sa.Column("day", sa.Date, primary_key=True), sa.Column("summary", sa.Text, nullable=False),
        sa.Column("raw", sa.Text, nullable=False), sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("health_activities", sa.Column("activity_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("day", sa.Date, nullable=False), sa.Column("summary", sa.Text, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_health_activities_day", "health_activities", ["day"])


def downgrade():
    op.drop_table("health_activities")
    op.drop_table("health_days")
    op.drop_table("garmin_account")
