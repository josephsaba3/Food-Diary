"""Timed Body Battery events (stress, recovery...) beside the 15-minute slots."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("health_timelines", sa.Column("events", sa.Text, nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("health_timelines", "events")
