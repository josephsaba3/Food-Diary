"""Optional Garmin account sign-in (encrypted tokens) for steps and stress every 15 minutes."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("garmin_account", sa.Column("account_tokens", sa.Text))
    op.add_column("garmin_account", sa.Column("account_linked_at", sa.DateTime(timezone=True)))
    op.add_column("garmin_account", sa.Column("account_error", sa.Text, nullable=False, server_default=""))


def downgrade():
    op.drop_column("garmin_account", "account_error")
    op.drop_column("garmin_account", "account_linked_at")
    op.drop_column("garmin_account", "account_tokens")
