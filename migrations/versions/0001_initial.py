"""Initial diary and OAuth storage."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("meals", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False), sa.Column("category", sa.String(32), nullable=False),
        sa.Column("time", sa.String(5)), sa.Column("food", sa.Text, nullable=False),
        sa.Column("symptoms", sa.Text, nullable=False), sa.Column("symptom_status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("day", "category", name="uq_meal_day_category"))
    op.create_index("ix_meals_day", "meals", ["day"])
    op.create_table("day_notes", sa.Column("day", sa.Date, primary_key=True), sa.Column("text", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("auth_records", sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("payload", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False))
    op.create_index("ix_auth_records_kind", "auth_records", ["kind"])
    op.create_index("ix_auth_records_expires_at", "auth_records", ["expires_at"])


def downgrade():
    op.drop_table("auth_records")
    op.drop_table("day_notes")
    op.drop_table("meals")
