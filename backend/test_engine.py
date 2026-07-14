#!/usr/bin/env python3
# SentinelBackup - Engine Unit Tests
# Author: WrkDrvn
# Version: 1.2.0

import os
import tempfile

import pytest

from app import app
from database import db
import engine.orchestrator as orch


@pytest.fixture
def client():
    """Test-Client mit frischer temporaerer DB."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        from models import SystemState
        if not SystemState.query.first():
            db.session.add(SystemState(trigger_run=False))
            db.session.commit()

    with app.test_client() as client:
        yield client

    os.close(db_fd)
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_engine_imports_resolve():
    assert hasattr(orch, "run_full_cycle")
    assert hasattr(orch, "acquire_lock")
    assert hasattr(orch, "release_lock")
    assert hasattr(orch, "lock_exists")
    assert hasattr(orch, "LOCK_FILE")
    assert orch.LOCK_FILE.endswith("sentinel.lock")


def test_lock_exists_returns_false_for_missing_file(monkeypatch):
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "nonexistent.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    assert orch.lock_exists() is False


def test_acquire_and_release_lock(monkeypatch):
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "engine.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)

    orch.acquire_lock()
    assert orch.lock_exists() is True

    orch.release_lock()
    assert orch.lock_exists() is False


def test_release_lock_idempotent(monkeypatch):
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "missing.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    orch.release_lock()
    assert orch.lock_exists() is False


def test_health_reports_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == "1.2.0"
