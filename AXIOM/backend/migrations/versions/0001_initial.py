"""Initial durable application and idempotency tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("domain", sa.String(20), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_records_kind", "records", ["kind"])
    op.create_index("ix_records_owner_id", "records", ["owner_id"])
    op.create_table(
        "idempotency",
        sa.Column("scope", sa.String(400), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
    )


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is disabled; preserve source and restore a reviewed copy"
    )
