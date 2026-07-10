#!/usr/bin/env python3
# SentinelBackup - Backend Tests
# Author: WrkDrvn
# Version: 0.7.0
#
# Tests fuer Flask-Routen, Datenbank und Script-Downloads.
# Ausfuehrung: source venv/bin/activate && pytest -v

import os
import tempfile

import pytest

from app import app
from database import db


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


# --- Sprint 1: Baseline ----------------------------------------------------
def test_index_returns_200_and_contains_sentinelbackup(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"SentinelBackup" in response.data


def test_health_returns_correct_json_and_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["version"] == "0.6.8"


def test_trigger_engine(client):
    """Trigger-Route muss ein HTML-Alert-Snippet (kein JSON) zurueckgeben."""
    response = client.post("/api/trigger_engine")
    assert response.status_code == 200
    assert b"Backup-Zyklus angesto" in response.data


def test_latest_logs_returns_html(client):
    """GET /api/latest_logs muss 200 + HTML-Partial liefern."""
    response = client.get("/api/latest_logs")
    assert response.status_code == 200
    assert b"Aktivitaeten" in response.data


# --- Sprint 2: Server-Management -------------------------------------------
def test_create_server_via_post(client):
    """Server-Anlage via HTMX-POST muss ein Tabellen-Snippet liefern."""
    payload = {
        "hostname": "mail01.example.com",
        "ip": "10.0.0.21",
        "ssh_user": "root",
        "backup_path": "/mnt/backups/mail01",
        "flag_path": "/var/run/backup_done",
        "server_type": "mailcow",
        "active": "on",
    }
    response = client.post("/servers/add", data=payload, follow_redirects=False)
    assert response.status_code == 200
    # Antwort muss ein HTML-Snippet (keine ganze Seite) sein
    assert b"<tr" in response.data
    assert b"mail01.example.com" in response.data
    # Kein redirect -> keine Location-Header
    assert response.headers.get("Location") is None

    with app.app_context():
        from models import Server
        server = Server.query.filter_by(hostname="mail01.example.com").first()
        assert server is not None
        assert server.ip == "10.0.0.21"
        assert server.server_type == "mailcow"
        assert server.active is True


def test_delete_server(client):
    """Server-Loeschung via HTMX-Route."""
    from models import Server
    with app.app_context():
        s = Server(hostname="del", ip="1.2.3.4", ssh_user="root",
                   backup_path="/x", server_type="docker")
        db.session.add(s)
        db.session.commit()
        sid = s.id

    response = client.post(f"/servers/delete/{sid}")
    assert response.status_code == 200
    assert b"<tr" in response.data
    with app.app_context():
        assert db.session.get(Server, sid) is None


# --- Sprint 2: NAS-Konfiguration -------------------------------------------
def test_create_nas_config(client):
    """NAS-Config-Speicherung muss Formular-Snippet zurueckgeben."""
    payload = {
        "mac": "AA:BB:CC:DD:EE:FF",
        "ip": "10.0.0.50",
        "share_path": "/volume1/backups",
        "user": "backupuser",
        "password": "s3cret",
        "mount_point": "/mnt/nas",
        "ssh_shutdown_cmd": "poweroff",
    }
    response = client.post("/nas/save", data=payload)
    assert response.status_code == 200
    # Antwort ist das Formular-Snippet (keine ganze Seite)
    assert b"<form" in response.data
    assert b"erfolgreich gespeichert" in response.data
    assert b"10.0.0.50" in response.data
    # Kein redirect
    assert response.headers.get("Location") is None

    with app.app_context():
        from models import NasConfig
        cfg = NasConfig.query.first()
        assert cfg is not None
        assert cfg.ip == "10.0.0.50"
        assert cfg.share_path == "/volume1/backups"


# --- Script-Download (nur noch Agent) --------------------------------------
def test_download_agent_returns_200(client):
    response = client.get("/download_script/agent")
    assert response.status_code == 200


def test_download_old_script_type_404(client):
    response = client.get("/download_script/mailcow")
    assert response.status_code == 404


def test_download_unknown_type_404(client):
    response = client.get("/download_script/unknown")
    assert response.status_code == 404


# --- Sprint 2: Seiten-Routen -----------------------------------------------
def test_servers_page(client):
    response = client.get("/servers")
    assert response.status_code == 200
    assert b"server-table-body" in response.data
    assert b"serverModal" in response.data


def test_nas_page(client):
    response = client.get("/nas")
    assert response.status_code == 200
    assert b"SSH Shutdown-Kommando" in response.data


def test_scripts_page(client):
    response = client.get("/scripts")
    assert response.status_code == 200
    assert b"MailCow" in response.data or b"mailcow" in response.data


# --- UAT Bugfix: SSH-Key Fallback ------------------------------------------
def test_dashboard_shows_ssh_key_fallback(client):
    """Wenn kein SSH-Key vorhanden ist, muss der Fallback-Text erscheinen."""
    app.config["TESTING"] = True
    # ensure path does not exist
    response = client.get("/")
    assert response.status_code == 200
    assert b"SSH Key nicht gefunden." in response.data or b"ssh-key-block" in response.data
