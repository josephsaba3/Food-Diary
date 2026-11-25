"""A version for the 15-minute slot format, so days stored before steps, stress and 15-minute Body Battery are fetched again."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("health_timelines", sa.Column("version", sa.Integer, nullable=False, server_default="0"))


def downgrade():
    op.drop_column("health_timelines", "version")
