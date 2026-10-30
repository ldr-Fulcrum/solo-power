"""Opt-in and public-metadata boundary of the optional task-hub adapter."""

import json
import os

import httpx
from bakeoff_stub import TOKEN, Hub
from fastapi.testclient import TestClient
from test_request_005 import AGENT, HUMAN

from backend.app import bakeoff as services_module
from backend.app.bakeoff_client import CHANGES, READS, Adapter, pinned_tools
from backend.app.main import create_app
from backend.app.task_hub_descriptions import DESCRIPTIONS

STUB_URL = "http://task-hub.test/mcp"


def test_unset_url_does_not_read_credentials_check_schemas_or_connect(settings, monkeypatch):
    monkeypatch.delenv("DARLA_BAKEOFF_URL", raising=False)
    monkeypatch.setenv("DARLA_BAKEOFF_TOKEN_ENV", "DEMO_TASK_HUB_TOKEN")
    monkeypatch.setenv("DEMO_TASK_HUB_TOKEN", TOKEN)
    reads = []
    calls = []
    get = os.environ.get

    def tracked(name, *args):
        if name == "DEMO_TASK_HUB_TOKEN":
            reads.append(name)
        return get(name, *args)

    monkeypatch.setattr(os.environ, "get", tracked)

    def forbidden(*args, **kwargs):
        calls.append("unexpected startup")
        raise AssertionError("Disabled adapter must not initialize")

    monkeypatch.setattr(Adapter, "__init__", forbidden)
    monkeypatch.setattr(Adapter, "start", forbidden)
    monkeypatch.setattr(services_module, "pinned_tools", forbidden)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        tools = client.get("/api/v1/runtime/tools", headers=AGENT).json()
        assert not any(name.startswith("bakeoff.") for name in tools)
        assert client.get("/api/v1/integrations", headers=HUMAN).json()["bakeoff"] == "DISABLED"
        assert (
            client.post(
                "/api/v1/tasks",
                headers={**HUMAN, "Idempotency-Key": "off-core"},
                json={"title": "Core works without adapter"},
            ).status_code
            == 201
        )
        schema = client.get("/openapi.json").json()

        def examples(value):
            if isinstance(value, dict):
                for name, child in value.items():
                    if name in {"example", "examples"}:
                        assert "bakeoff." not in json.dumps(child)
                    examples(child)
            elif isinstance(value, list):
                for child in value:
                    examples(child)

        examples(schema)
    assert reads == [] and calls == []
    assert Adapter.from_environment() is None
    assert reads == [] and calls == []


def test_pin_has_only_names_and_argument_validation_constraints():
    tools = pinned_tools()
    assert len(tools) == 10

    def schema_only(schema):
        if not isinstance(schema, dict):
            return
        assert not ({"title", "description", "$comment", "examples", "default"} & set(schema))
        for value in schema.get("properties", {}).values():
            schema_only(value)
        for keyword in ("items", "anyOf", "oneOf", "allOf"):
            value = schema.get(keyword)
            if isinstance(value, list):
                for child in value:
                    schema_only(child)
            else:
                schema_only(value)

    for tool in tools:
        assert set(tool) == {"name", "inputSchema"}
        schema_only(tool["inputSchema"])
    incident = next(t for t in tools if t["name"] == "create_incident")["inputSchema"]
    assert incident["properties"]["description"]["type"] == "string"
    assert "description" in incident["required"]


def test_remote_annotations_are_ignored_and_descriptions_are_original(settings, monkeypatch):
    monkeypatch.setenv("DARLA_BAKEOFF_URL", STUB_URL)
    monkeypatch.delenv("DARLA_BAKEOFF_TOKEN_ENV", raising=False)
    hub = Hub()
    hub.annotations = True
    hub.extra_tools = [
        {
            "name": "not_exposed",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]
    adapter = Adapter(STUB_URL, TOKEN, transport=httpx.MockTransport(hub)).start()
    app = create_app(settings)
    app.state.services.bakeoff = adapter
    with TestClient(app) as client:
        tools = client.get("/api/v1/runtime/tools", headers=AGENT).json()
        optional = {
            n.removeprefix("bakeoff."): v for n, v in tools.items() if n.startswith("bakeoff.")
        }
        assert set(optional) == READS | CHANGES
        for name, item in optional.items():
            assert item["description"] == DESCRIPTIONS[name]
        assert "REMOTE_ANNOTATION_CANARY" not in json.dumps(optional)
        assert "bakeoff.not_exposed" not in tools


def test_ready_client_cannot_make_unset_application_register_optional_tools(settings, monkeypatch):
    monkeypatch.delenv("DARLA_BAKEOFF_URL", raising=False)
    monkeypatch.delenv("DARLA_BAKEOFF_TOKEN_ENV", raising=False)
    app = create_app(settings)
    app.state.services.bakeoff = Adapter(
        STUB_URL, TOKEN, transport=httpx.MockTransport(Hub())
    ).start()
    with TestClient(app) as client:
        assert not any(
            n.startswith("bakeoff.")
            for n in client.get("/api/v1/runtime/tools", headers=AGENT).json()
        )
        assert client.get("/api/v1/integrations", headers=HUMAN).json()["bakeoff"] == "DISABLED"
