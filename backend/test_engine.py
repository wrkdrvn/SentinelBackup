#!/usr/bin/env python3
# SentinelBackup - Engine Unit Tests
# Author: WrkDrvn
# Version: 1.2.0

import os
import tempfile

import pytest

# Vor Import von app: DB ins Temp-Verzeichnis legen (siehe test_app.py).
os.environ.setdefault(
    "SENTINEL_DB_PATH",
    os.path.join(tempfile.mkdtemp(prefix="sentinelbackup_test_"), "test.db"),
)

from app import app  # noqa: E402
from database import db  # noqa: E402
import engine.orchestrator as orch  # noqa: E402


def _repo_version():
    """Liest die VERSION-Datei aus dem Repo (kein hartcodierter String)."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "VERSION"), "r", encoding="utf-8") as fh:
        return fh.read().strip()


@pytest.fixture
def client():
    """Test-Client mit frischer DB (pro Test: drop + create)."""
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


def test_acquire_lock_refuses_when_process_alive(monkeypatch):
    """Lebender fremder Prozess in der Lock-Datei -> Lock ablehnen,
    bestehende Datei NICHT loeschen (frueher: sys.exit -> Daemon-Loop)."""
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "engine.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    with open(fake_lock, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    assert orch.acquire_lock() is False
    assert orch.lock_exists() is True


def test_acquire_lock_overwrites_stale_lock(monkeypatch):
    """Veraltete Lock-Datei (Prozess tot) -> wird erkannt und uebernommen."""
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "engine.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    with open(fake_lock, "w", encoding="utf-8") as fh:
        fh.write("999999999")  # > pid_max: existiert garantiert nicht
    assert orch.acquire_lock() is True
    with open(fake_lock, "r", encoding="utf-8") as fh:
        assert fh.read().strip() == str(os.getpid())
    orch.release_lock()


def test_acquire_lock_overwrites_garbage_pid(monkeypatch):
    """Lock-Datei ohne gueltige PID -> ueberschreiben statt haengen bleiben."""
    tmp = tempfile.mkdtemp()
    fake_lock = os.path.join(tmp, "engine.lock")
    monkeypatch.setattr(orch, "LOCK_FILE", fake_lock)
    with open(fake_lock, "w", encoding="utf-8") as fh:
        fh.write("not-a-pid")
    assert orch.acquire_lock() is True
    orch.release_lock()


def test_mount_point_uses_db_value():
    """Der Mount-Point kommt aus der DB, nicht aus der Konstanten
    (frueherer Bug #3: WebUI-Einstellung wirkungslos)."""
    class Nas:
        mount_point = "/volume2/backupmnt"

    class NasEmpty:
        mount_point = ""

    assert orch._mount_point(Nas()) == "/volume2/backupmnt"
    assert orch._mount_point(NasEmpty()) == orch.NAS_MOUNT_POINT
    assert orch._mount_point(None) == orch.NAS_MOUNT_POINT


def test_health_reports_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == _repo_version()


# ---------------------------------------------------------------------------
# Feature: NAS-Retention (Engine)
# ---------------------------------------------------------------------------
def test_apply_nas_retention_deletes_only_old_server_files(client):
    """Aeltere Dateien im Server-Ordner werden entfernt, neue bleiben,
    fremde Ordner auf dem Share bleiben unangetastet."""
    import shutil
    import time as _time

    base = tempfile.mkdtemp(prefix="sentinel_retention_")
    mp = os.path.join(base, "mnt")
    srv_dir = os.path.join(mp, "mail01")
    foreign_dir = os.path.join(mp, "fremd")
    os.makedirs(srv_dir)
    os.makedirs(foreign_dir)

    old_file = os.path.join(srv_dir, "alt.sql.gz")
    new_file = os.path.join(srv_dir, "neu.sql.gz")
    foreign_old = os.path.join(foreign_dir, "wichtiger.tgz")
    for path in (old_file, new_file, foreign_old):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
    ten_days_ago = _time.time() - 10 * 86400
    os.utime(old_file, (ten_days_ago, ten_days_ago))
    os.utime(foreign_old, (ten_days_ago, ten_days_ago))

    class Nas:
        mount_point = mp
        retention_days = 5

    with app.app_context():
        from models import Server
        db.session.add(Server(
            hostname="mail01", ip="10.0.0.2", backup_path="/x",
            flag_path="/y", server_type="mailcow",
        ))
        db.session.commit()
        orch._apply_nas_retention(Nas())

    try:
        assert not os.path.exists(old_file), "alte Datei haette weg muessen"
        assert os.path.exists(new_file), "neue Datei darf nicht geloescht sein"
        assert os.path.exists(foreign_old), "fremder Ordner darf NICHT angerasst werden"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_apply_nas_retention_disabled_does_nothing(client):
    """retention_days = 0 (Standard) -> kein Dateizugriff überhaupt."""
    base = tempfile.mkdtemp(prefix="sentinel_retention0_")
    target = os.path.join(base, "mnt", "srv1")
    os.makedirs(target)
    keep = os.path.join(target, "alt.txt")
    with open(keep, "w", encoding="utf-8") as fh:
        fh.write("x")
    old = __import__("time").time() - 100 * 86400
    os.utime(keep, (old, old))

    class Nas:
        mount_point = os.path.join(base, "mnt")
        retention_days = 0

    with app.app_context():
        orch._apply_nas_retention(Nas())
    assert os.path.exists(keep)

    import shutil
    shutil.rmtree(base, ignore_errors=True)
