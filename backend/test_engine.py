#!/usr/bin/env python3
# SentinelBackup - Engine Unit Tests
# Author: WrkDrvn
# Version: 0.7.0
#
# Tests fuer die Orchestrator-Hilfsfunktionen und die
# manuelle Trigger-Route. Echte SSH/rsync/mount-Kommandos
# werden hier nicht ausgefuehrt.

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

    with app.test_client() as client:
        yield client

    os.close(db_fd)
    try:
        os.unlink(db_path)
    except OSError:
        pass


# --- Imports ----------------------------------------------------------------
def test_engine_imports_resolve():
    """Smtliche Module/Konstanten muessen importierbar sein."""
    assert hasattr(orch, "run_full_cycle")
    assert hasattr(orch, "acquire_lock")
    assert hasattr(orch, "release_lock")
    assert hasattr(orch, "LOCK_FILE")
    assert orch.LOCK_FILE.endswith("sentinel.lock")


# --- Lock-Logik -------------------------------------------------------------
def test_lock_exists_returns_false_for_missing_file(monkeypatch):
    """Ohne Lock-Datei muss lock_exists False liefern."""
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "nonexistent.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    assert orch.lock_exists() is False


def test_acquire_and_release_lock(monkeypatch):
    """acquire_lock erstellt die Datei, release_lock entfernt sie."""
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "engine.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)

    orch.acquire_lock()
    assert orch.lock_exists() is True

    orch.release_lock()
    assert orch.lock_exists() is False


def test_release_lock_idempotent(monkeypatch):
    """release_lock darf nicht fehlschlagen, wenn die Datei fehlt."""
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "missing.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    # Darf keine Exception werfen
    orch.release_lock()
    assert orch.lock_exists() is False


# --- Trigger-Route ----------------------------------------------------------
def test_trigger_engine_returns_html_alert(client):
    """POST /api/trigger_engine muss 200 + HTML-Alert-Snippet liefern."""
    response = client.post("/api/trigger_engine")
    assert response.status_code == 200
    assert b"Backup-Zyklus angesto" in response.data


# --- Health (Version konsistent) -------------------------------------------
def test_health_reports_version_068(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == "0.6.8"
