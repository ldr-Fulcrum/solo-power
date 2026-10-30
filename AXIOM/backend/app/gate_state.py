"""Request 005 control state and its single append-only audit/event source."""

from contextvars import ContextVar

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table

metadata = MetaData()
connection_context = ContextVar("request005_connection", default=None)
request_context = ContextVar("request005_context", default=None)
audit = Table(
    "runtime_control_audit",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("at", String(40), nullable=False),
    Column("owner_id", String(100), nullable=False),
    Column("domain", String(20), nullable=False),
    Column("type", String(40), nullable=False),
    Column("code", String(100), nullable=False),
    Column("agent_id", String(100), nullable=False),
    Column("tool", String(100)),
    Column("caller", String(100), nullable=False),
    Column("reason", String(500)),
    Column("note", String(500)),
    Column("reference_id", String(64)),
    Column("event_key", String(300), unique=True),
)
control = Table(
    "runtime_proposal_control",
    metadata,
    Column("owner_id", String(100), primary_key=True),
    Column("domain", String(20), primary_key=True),
    Column("agent_id", String(100), primary_key=True),
    Column("locked", Boolean, nullable=False),
    Column("updated_at", String(40), nullable=False),
)
anchors = Table(
    "runtime_journal_anchors",
    metadata,
    Column("order_id", Integer, primary_key=True),
    Column("id", String(64), unique=True, nullable=False),
    Column("server_time", String(40), nullable=False),
    Column("owner_id", String(100), nullable=False),
    Column("domain", String(20), nullable=False),
    Column("agent_id", String(100), nullable=False),
    Column("journal_id", String(100), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("head_hash", String(64), nullable=False),
)

bootstrap_superseded = Table(
    "runtime_bootstrap_superseded",
    MetaData(),
    Column("bootstrap_id", String(64), primary_key=True),
    Column("health_id", String(64), nullable=False),
    Column("at", String(40), nullable=False),
)
