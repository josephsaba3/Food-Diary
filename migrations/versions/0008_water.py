"""Water drunk per meal slot, in millilitres."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "water",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("category", sa.String(32), primary_key=True),
        sa.Column("ml", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("water")
