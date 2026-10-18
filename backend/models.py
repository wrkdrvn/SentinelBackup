#!/usr/bin/env python3
# SentinelBackup - SQLAlchemy Models
# Author: WrkDrvn
# Version: 1.3.0
#
# Datenmodelle fuer Server und NAS-Konfiguration.

from datetime import datetime, timezone

from database import db


class Server(db.Model):
    """Ein Ziel-Server, von dem Backups gezogen werden."""

    __tablename__ = "servers"

    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column(db.String(255), nullable=False)
    ip = db.Column(db.String(45), nullable=False)
    ssh_user = db.Column(db.String(64), nullable=False, default="root")
    ssh_port = db.Column(db.Integer, default=22, nullable=False)
    backup_path = db.Column(db.String(512), nullable=False)
    flag_path = db.Column(db.String(512), nullable=False, default="/var/run/backup_done")
    server_type = db.Column(db.String(32), nullable=False, default="docker")
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Config-Git-Tracking: Die Engine pflegt fuer aktive Server mit
    # git_track=True eine Versionshistorie der gezogenen Configs
    # (/opt/sentinelbackup/backup_repo) und legt das Repo auf die NAS.
    git_track = db.Column(db.Boolean, nullable=False, default=False)

    # Erlaubte Typen: mailcow, ispconfig, docker, proxmox
    VALID_TYPES = ("mailcow", "ispconfig", "docker", "proxmox", "custom")

    def __repr__(self):
        return f"<Server {self.hostname} ({self.server_type})>"


class NasConfig(db.Model):
    """Konfiguration des NAS, auf dem Backups abgelegt werden."""

    __tablename__ = "nas_config"

    id = db.Column(db.Integer, primary_key=True)
    mac = db.Column(db.String(32), nullable=True)
    ip = db.Column(db.String(45), nullable=False)
    ssh_port = db.Column(db.Integer, default=2022, nullable=False)
    share_path = db.Column(db.String(512), nullable=False)
    user = db.Column(db.String(128), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    mount_point = db.Column(db.String(512), nullable=False, default="/mnt/nas")
    ssh_shutdown_cmd = db.Column(db.String(255), nullable=False, default="sudo /usr/syno/sbin/synoshutdown --shutdown")
    # Aufbewahrungsfrist auf der NAS in Tagen (0 = unbegrenzt). Es werden
    # nur die Unterverzeichnisse der konfigurierten Server bereinigt.
    retention_days = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<NasConfig {self.ip}:{self.share_path}>"


class BackupLog(db.Model):
    """Protokolleintrag eines Backup-Durchlaufs."""

    __tablename__ = "backup_logs"

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(
        db.Integer,
        db.ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=True,
    )
    timestamp = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status = db.Column(db.String(16), nullable=False, default="skipped")
    message = db.Column(db.Text, nullable=True)

    server = db.relationship("Server", backref="logs")

    # Erlaubte Status: success, failed, skipped
    VALID_STATUSES = ("success", "failed", "skipped")

    def __repr__(self):
        return f"<BackupLog {self.status} @ {self.timestamp}>"


class SystemState(db.Model):
    """Globaler Systemzustand (Singleton-Zeile).
    Dient als robuster DB-basierter Trigger-Mechanismus zwischen dem
    Web-Prozess (www-data) und dem Daemon-Prozess (root), ohne dass
    Dateisystem-Rechte (wie frueher bei /tmp/sentinel_trigger) noetig sind."""

    __tablename__ = "system_state"

    id = db.Column(db.Integer, primary_key=True)
    trigger_run = db.Column(db.Boolean, default=False, nullable=False)
    routine_interval_minutes = db.Column(db.Integer, default=60, nullable=False)
    # Update-Wunsch aus der WebUI (z.B. "v1.3.0"). Der root-Daemon pollt
    # diesen Wert, startet update.sh und leert das Feld danach.
    update_pending = db.Column(db.String(32), nullable=True)

    # --- Matrix-Benachrichtigung bei fehlgeschlagenen Zyklen ---------------
    notify_enabled = db.Column(db.Boolean, nullable=False, default=False)
    matrix_homeserver = db.Column(db.String(255), nullable=False, default="")
    matrix_room_id = db.Column(db.String(255), nullable=False, default="")
    matrix_access_token = db.Column(db.String(512), nullable=False, default="")
    notify_cooldown_minutes = db.Column(db.Integer, nullable=False, default=60)
    # Epochen-Sekunden (Float): naive/aware-Datetime-Falle in SQLite meiden.
    last_notified_at = db.Column(db.Float, nullable=True)

    def __repr__(self):
        return f"<SystemState id={self.id} trigger_run={self.trigger_run}>"
