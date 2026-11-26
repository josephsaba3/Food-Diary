"""Web Push subscriptions and the high-stress alerts already sent."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("endpoint", sa.Text, nullable=False, unique=True),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("device", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "stress_alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("start_minute", sa.Integer, nullable=False),
        sa.Column("minutes", sa.Integer, nullable=False),
        sa.Column("average", sa.Integer),
        sa.Column("peak", sa.Integer),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("day", "start_minute", name="uq_stress_alert_day_start"),
    )


def downgrade():
    op.drop_table("stress_alerts")
    op.drop_table("push_subscriptions")
