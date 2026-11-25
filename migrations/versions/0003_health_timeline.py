"""Garmin heart rate and Body Battery in 15-minute slots."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("health_timelines", sa.Column("day", sa.Date, primary_key=True), sa.Column("slots", sa.Text, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("health_timelines")
