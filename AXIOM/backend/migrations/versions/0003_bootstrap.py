"""Append-only evidence that real health superseded a bootstrap window."""

from alembic import op

from backend.app.gate_state import bootstrap_superseded

revision = "0003_bootstrap"
down_revision = "0002_request005"
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    bootstrap_superseded.create(c)
    for operation in ("UPDATE", "DELETE"):
        c.exec_driver_sql(
            f"CREATE TRIGGER bootstrap_superseded_{operation.lower()} BEFORE {operation} ON runtime_bootstrap_superseded BEGIN SELECT RAISE(ABORT,'append-only'); END"
        )


def downgrade():
    raise RuntimeError("Destructive downgrade disabled")
