"""Timed Body Battery events (stress, recovery...) beside the 15-minute slots, and a version for the slot format."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("health_timelines", sa.Column("events", sa.Text, nullable=False, server_default="[]"))
    # Days stored before steps, stress and 15-minute Body Battery were fetched count as version 0 and are fetched again.
    op.add_column("health_timelines", sa.Column("version", sa.Integer, nullable=False, server_default="0"))


def downgrade():
    op.drop_column("health_timelines", "version")
    op.drop_column("health_timelines", "events")
