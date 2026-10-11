#!/usr/bin/env python3
# SentinelBackup - Backend Tests
# Author: WrkDrvn
# Version: 1.2.0

import os
import tempfile

import pytest

# WICHTIG: muss VOR "from app import app" passieren. app.py bindet die
# SQLite-DB bereits beim Import (sonst: produktive DB oder Absturz ohne
# /opt-Verzeichnis - genau das war der Grund, warum die Suite vorher bei
# der Collection starb). SENTINEL_DB_PATH legt die Test-DB ins Temp-Verz.
_TEST_TMP = tempfile.mkdtemp(prefix="sentinelbackup_test_")
os.environ.setdefault("SENTINEL_DB_PATH", os.path.join(_TEST_TMP, "test.db"))

from app import app  # noqa: E402
from database import db  # noqa: E402


def _repo_version():
    """Liest die VERSION-Datei aus dem Repo (kein hartcodierter String)."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "VERSION"), "r", encoding="utf-8") as fh:
        return fh.read().strip()


@pytest.fixture
def client():
    """Test-Client mit frischer DB (pro Test: drop + create). Die DB liegt
    ueber SENTINEL_DB_PATH im Temp-Verzeichnis - die produktive Datei wird
    nie angerasst."""
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        from models import SystemState
        if not SystemState.query.first():
            db.session.add(SystemState(trigger_run=False))
            db.session.commit()

    with app.test_client() as client:
        yield client


def test_index_returns_200_and_contains_sentinelbackup(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"SentinelBackup" in response.data


def test_health_returns_correct_json_and_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["version"] == _repo_version()


def test_create_server_via_post(client):
    payload = {
        "hostname": "mail01.example.com",
        "ip": "10.0.0.21",
        "ssh_user": "root",
        "ssh_port": "22",
        "backup_path": "/opt/sentinel_agent/data/",
        "flag_path": "/opt/sentinel_agent/.backup_done",
        "server_type": "mailcow",
        "active": "on",
    }
    response = client.post("/servers/add", data=payload, follow_redirects=False)
    assert response.status_code == 200
    assert b"<tr" in response.data
    assert b"mail01.example.com" in response.data
    assert response.headers.get("Location") is None

    with app.app_context():
        from models import Server
        server = Server.query.filter_by(hostname="mail01.example.com").first()
        assert server is not None
        assert server.ip == "10.0.0.21"
        assert server.server_type == "mailcow"


def test_create_nas_config(client):
    payload = {
        "mac": "AA:BB:CC:DD:EE:FF",
        "ip": "10.0.0.50",
        "ssh_port": "2022",
        "share_path": "/volume1/backups",
        "user": "backupuser",
        "password": "s3cret",
        "mount_point": "/mnt/nas",
        "ssh_shutdown_cmd": "sudo synoshutdown --shutdown",
    }
    response = client.post("/nas/save", data=payload)
    assert response.status_code == 200
    assert b"<form" in response.data
    assert b"10.0.0.50" in response.data
    assert response.headers.get("Location") is None

    with app.app_context():
        from models import NasConfig
        cfg = NasConfig.query.first()
        assert cfg is not None
        assert cfg.ip == "10.0.0.50"
        assert cfg.ssh_port == 2022


def test_download_agent_returns_200(client):
    response = client.get("/download_script/agent")
    assert response.status_code == 200


def test_download_old_script_type_404(client):
    response = client.get("/download_script/mailcow")
    assert response.status_code == 404


def test_download_unknown_type_404(client):
    response = client.get("/download_script/unknown")
    assert response.status_code == 404


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
    assert b"setup_agent.sh" in response.data


def test_settings_shows_update_panel(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert b"System Einstellungen" in response.data
    assert b'target_version' in response.data


def test_start_update_sets_pending_flag(client):
    """Gueltige Version -> Flag in der DB (DB-Trigger-Design, kein Thread)."""
    response = client.post("/api/start_update", data={"target_version": "v1.1.0"})
    assert response.status_code == 200
    assert b"eingeleitet" in response.data
    with app.app_context():
        from models import SystemState
        assert SystemState.query.first().update_pending == "v1.1.0"


def test_start_update_rejects_invalid_version_format(client):
    """Beliebige Strings duerfen nie als Update-Version akzeptiert werden."""
    response = client.post(
        "/api/start_update", data={"target_version": "v1.1.0; rm -rf /"}
    )
    assert response.status_code == 200
    assert b"Ungueltiges Versionsformat" in response.data
    with app.app_context():
        from models import SystemState
        assert SystemState.query.first().update_pending is None


def test_start_update_empty_version_warning(client):
    response = client.post("/api/start_update", data={})
    assert response.status_code == 200
    assert b"Keine Ziel-Version" in response.data


def test_trigger_engine_creates_state_row_if_missing(client):
    """Fehlt die SystemState-Zeile, soll sie angelegt werden - kein stiller
    Erfolg ohne Wirkung (frueherer Bug #8)."""
    with app.app_context():
        from models import SystemState
        SystemState.query.delete()
        db.session.commit()
    response = client.post("/api/trigger_engine")
    assert response.status_code == 200
    assert b"Backup-Zyklus" in response.data
    with app.app_context():
        from models import SystemState
        assert SystemState.query.first().trigger_run is True
