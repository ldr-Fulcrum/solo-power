from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(tmp_path):
    return Settings(root=ROOT, state_dir=tmp_path, synthetic_mode=True)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client
