"""Minimal Request 005 gate, journal anchors and audit-derived event feed."""

from alembic import op

from backend.app.gate_state import metadata

revision = "0002_request005"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata.create_all(connection)
    for name in ("runtime_control_audit", "runtime_journal_anchors"):
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name}_{operation.lower()}_denied BEFORE {operation} ON {name} BEGIN SELECT RAISE(ABORT, 'append-only'); END"
            )


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; preserve and restore a reviewed copy")
