"""Server-owned policy v1. Models cannot change these tiers."""

from datetime import UTC, datetime
from types import MappingProxyType

VERSION = "darla-tool-policy/1"
AUTO_LIMIT = 20
AUTO_WINDOW_SECONDS = 600
HEARTBEAT_WINDOW_SECONDS = 120
RUNTIME_EVIDENCE = "RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM"
TIERS = MappingProxyType(
    {"task.create": "AUTO", "plan.create": "AUTO", "batch.create": "AUTO", "draft.create": "ASK"}
)


def clock():
    return datetime.now(UTC)


def approval_required(tool, model_flag=False):
    return TIERS[tool] == "ASK" or model_flag
