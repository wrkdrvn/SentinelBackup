#!/usr/bin/env python3
# SentinelBackup - Orchestrator Engine
# Author: WrkDrvn
# Version: 1.2.0
#
# Hintergrund-Daemon: Fuehrt pull-basierte Backup-Zyklen aus.
#  PHASE 1: SSH-Flag-Check + rsync-Pull aller aktiven Server in den Cache.
#  PHASE 2: NAS wecken (WoL), mounten, rsync-Push auf NAS, Shutdown.
#  PHASE 3: Lock freigeben, Zyklus abschliessen.
#
# Alle externen Kommandos laufen ueber subprocess.run mit Listen-Argumenten
# (kein shell=True), um Command-Injection zu verhindern.
# Logging erfolgt ueber das Python logging-Modul gleichzeitig in eine
# rotierende Datei (/var/log/sentinelbackup/engine.log) und auf die Konsole
# (fuer das Systemd-Journal). Der Log-Level ist via Umgebungsvariable
# SENTINEL_LOG_LEVEL steuerbar.
#
# ARCHITEKTUR: Dieses Modul importiert NICHT app.py auf Modulebene (sonst
# Zirkularimport, da app.py run_full_cycle hier importiert). Das Flask-App-
# Objekt wird als Parameter an run_full_cycle() uebergeben.

import os
import time
import json
import shutil
import subprocess
import logging
import logging.handlers
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Backend-Pfad sicherstellen, damit Importe auch ausserhalb des Gunicorn-
# Kontexts (z.B. systemd python3-Aufruf) funktionieren.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
import sys
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database import db  # noqa: E402
from models import Server, NasConfig, BackupLog, SystemState  # noqa: E402

# --- Konstanten -------------------------------------------------------------
LOCK_FILE = "/tmp/sentinel.lock"
CACHE_DIR = "/opt/sentinelbackup/cache/"
SSH_KEY = "/root/.ssh/id_ed25519"
LOG_DIR = "/var/log/sentinelbackup"
LOG_FILE = os.path.join(LOG_DIR, "engine.log")
CIFS_CREDS_FILE = "/tmp/sbtcifscreds"
UPDATE_SCRIPT = "/opt/sentinelbackup/update.sh"
# Lokales Git-Repository fuer getrackte Config-Backups (Feature: Configs
# versionieren + auf die NAS ziehen)
GIT_REPO_DIR = "/opt/sentinelbackup/backup_repo/"
NAS_MOUNT_POINT = "/mnt/nas"   # Fallback, falls in der DB nichts konfiguriert
NAS_BOOT_TIMEOUT_SEC = 300
NAS_POLL_INTERVAL_SEC = 5


def _mount_point(nas):
    """Liefert den KONFIGURIERTEN Mount-Point aus der DB (nas.mount_point).
    Fallback: Standard /mnt/nas. Frueher wurde die Konstante verwendet und
    die WebUI-Einstellung war wirkungslos."""
    mp = (getattr(nas, "mount_point", "") or "").strip().rstrip("/")
    return mp or NAS_MOUNT_POINT


# ---------------------------------------------------------------------------
# Logging-Setup
# ---------------------------------------------------------------------------
def setup_logging():
    """Konfiguriert den SentinelBackup-Logger mit Datei- (rotierend) und
    Konsolen-Handler. Idempotent - mehrfacher Aufruf ist unbedenklich."""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_level_str = os.environ.get("SENTINEL_LOG_LEVEL", "DEBUG").upper()
    log_level = getattr(logging, log_level_str, logging.DEBUG)

    logger = logging.getLogger("SentinelBackup")
    logger.setLevel(log_level)

    # Doppelte Handler bei wiederholtem Aufruf vermeiden
    if getattr(logger, "_sb_configured", False):
        for h in logger.handlers:
            h.setLevel(log_level)
        return logger

    fmt = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(log_level)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger._sb_configured = True  # type: ignore[attr-defined]
    return logger


# Globales Logger-Null-Objekt: verursacht KEINE Dateizugriffe und stuerzt
# nicht ab, wenn der Web-Prozess (www-data) dieses Modul importiert. Die
# echten Handler werden erst in if __name__ == '__main__' per setup_logging()
# durch den Daemon-Prozess (root) angehaengt.
logger = logging.getLogger("SentinelBackup")


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------
def acquire_lock():
    """Erstellt die Lock-Datei. Gibt True bei Erfolg zurueck, False wenn
    ein anderer Zyklus laeuft. Veraltete Locks (Crash, Prozess tot) werden
    erkannt und uebernommen - ein toter Prozess darf den Daemon nicht in
    eine Endlosschleife stoeren (frueher: sys.exit -> systemd-Neustart-Loop)."""
    if os.path.exists(LOCK_FILE):
        pid = None
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            pid = None

        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
            except OSError:
                alive = False
            if alive:
                logger.error(f"Lock existiert (PID {pid}) - ein anderer Zyklus "
                             f"laeuft. Abbruch.")
                return False
            logger.warning(f"Veraltete Lock-Datei (PID {pid} existiert nicht) - "
                           f"ueberschreibe.")
        else:
            logger.warning("Lock-Datei ohne gueltige PID - ueberschreibe.")
        try:
            os.remove(LOCK_FILE)
        except OSError as exc:
            logger.error(f"Konnte veraltete Lock-Datei nicht entfernen: {exc}")
            return False

    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        logger.error(f"Konnte Lock-Datei nicht schreiben: {exc}")
        return False
    return True


def lock_exists(path=None):
    """True, wenn die Lock-Datei existiert (laeuft bereits ein Zyklus)."""
    target = path if path is not None else LOCK_FILE
    return os.path.exists(target)


def release_lock():
    """Entfernt die Lock-Datei (bestaendig gegen bereits geloeschte Datei)."""
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error(f"Konnte Lock-Datei nicht loeschen: {exc}")


# ---------------------------------------------------------------------------
# DB-Logging
# ---------------------------------------------------------------------------
def _log(server_id, status, message=""):
    """Schreibt einen BackupLog-Eintrag in die DB.
    Muss innerhalb eines aktiven Flask app_context aufgerufen werden.
    Fehler werden abgefangen, damit ein Log-Fehler den Zyklus nicht
    zum Absturz bringt."""
    try:
        entry = BackupLog(
            server_id=server_id,
            status=status,
            message=message,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error(f"DB-Log fehlgeschlagen: {exc}")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _clear_cache_contents():
    """Loescht alle Inhalte des Cache-Verzeichnisses, behaelt das Verzeichnis
    selbst. Forensisch protokolliert."""
    logger.info(f"Loesche lokalen Cache unter: {CACHE_DIR}")
    os.makedirs(CACHE_DIR, exist_ok=True)
    for entry in os.listdir(CACHE_DIR):
        full = os.path.join(CACHE_DIR, entry)
        logger.debug(f"Loesche Cache-Eintrag: {full}")
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass


def _cache_has_data():
    """True, wenn sich Daten im Cache befinden."""
    if not os.path.isdir(CACHE_DIR):
        return False
    return len(os.listdir(CACHE_DIR)) > 0


def _cache_size_mb():
    """Berechnet die Groesse des Cache-Verzeichnisses in MB."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(CACHE_DIR):
        for filename in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, filename))
            except OSError:
                pass
    return total / (1024 * 1024)


# ---------------------------------------------------------------------------
# PHASE 1: Vorbereitung & Server-Pull
# ---------------------------------------------------------------------------
def check_flag(server):
    """Prueft per SSH, ob die Flag-Datei auf dem Ziel existiert.
    Gibt zurueck: 'found', 'missing' oder 'error'."""
    cmd = [
        "ssh",
        "-i", SSH_KEY,
        "-p", str(server.ssh_port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        f"{server.ssh_user}@{server.ip}",
        f"test -f {server.flag_path}",
    ]
    logger.debug(f"SSH Check: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.error(f"Server {server.hostname}: SSH Timeout nach 30s! "
                     f"Ist der Port {server.ssh_port} richtig? Firewall aktiv?")
        return "error"
    except FileNotFoundError as exc:
        logger.error(f"Server {server.hostname}: SSH nicht gefunden: {exc}")
        return "error"

    if result.returncode == 0:
        return "found"
    if result.returncode == 1:
        return "missing"
    # RC > 1 (z.B. 255) = SSH-Fehler (Passwort, Port, Key)
    logger.error(f"Server {server.hostname}: SSH Fehler (Code "
                 f"{result.returncode}). Details: {result.stderr.strip()}")
    return "error"


def pull_rsync(server, local_dir):
    """Zieht die Backup-Daten per rsync vom Ziel in das Cache-Verzeichnis."""
    ssh_opts = "ssh -i {} -o StrictHostKeyChecking=no -p {}".format(
        SSH_KEY, server.ssh_port
    )
    cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e", ssh_opts,
        f"{server.ssh_user}@{server.ip}:{server.backup_path}/",
        local_dir + "/",
    ]
    logger.info(f"AUSFUEHRUNG RSYNC: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        logger.error(f"Server {server.hostname}: rsync pull Timeout nach 3600s.")
        return 1
    except FileNotFoundError as exc:
        logger.error(f"Server {server.hostname}: rsync nicht gefunden: {exc}")
        return 1

    logger.debug(f"RSYNC STDOUT: {result.stdout[:500]}")
    if result.returncode != 0:
        logger.error(f"rsync pull RC={result.returncode} ({server.hostname}): "
                     f"{result.stderr[:500]}")
    else:
        logger.info(f"RSYNC STATS: {result.stdout[:500]}")
    return result.returncode


def delete_flag(server):
    """Loescht die Flag-Datei auf dem Zielserver per SSH."""
    cmd = [
        "ssh",
        "-i", SSH_KEY,
        "-p", str(server.ssh_port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        f"{server.ssh_user}@{server.ip}",
        f"rm -f {server.flag_path}",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


# ---------------------------------------------------------------------------
# PHASE 2: NAS-Aktionen
# ---------------------------------------------------------------------------
def wake_nas(nas):
    """Sendet das Wake-on-LAN Magic Packet."""
    if not nas.mac:
        logger.warning("Keine MAC-Adresse konfiguriert - ueberspringe WoL.")
        return
    logger.info(f"Sende WoL an NAS {nas.mac} ...")
    try:
        subprocess.run(["wakeonlan", nas.mac], capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error(f"WoL fehlgeschlagen: {exc}")


def wait_for_nas(nas):
    """Wartet auf den SMB-Dienst (Port 445) der NAS. Zuverlaessiger als
    ein ICMP-Ping, da die Synology den SMB-Dienst erst nach dem Boot
    startet. Gibt True zurueck, wenn Port 445 offen ist."""
    logger.info(f"Warte auf SMB-Dienst der NAS ({nas.ip}:445) ...")
    nas_online = False
    wait_time = 0
    while wait_time < NAS_BOOT_TIMEOUT_SEC:
        port_check = subprocess.run(
            ["nc", "-z", "-w2", nas.ip, "445"],
            capture_output=True,
        )
        if port_check.returncode == 0:
            logger.info(f"NAS SMB-Dienst ist nach {wait_time} Sekunden erreichbar.")
            nas_online = True
            break
        time.sleep(NAS_POLL_INTERVAL_SEC)
        wait_time += NAS_POLL_INTERVAL_SEC

    if not nas_online:
        logger.error(f"NAS {nas.ip}: SMB-Dienst (Port 445) nach "
                     f"{NAS_BOOT_TIMEOUT_SEC}s nicht erreichbar.")
    return nas_online


def write_cifs_creds(nas):
    """Schreibt die CIFS-Credentials temporaer fuer den mount-Befehl."""
    content = (
        f"username={nas.user}\n"
        f"password={nas.password}\n"
    )
    with open(CIFS_CREDS_FILE, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(CIFS_CREDS_FILE, 0o600)


def remove_cifs_creds():
    """Loescht die Credentials-Datei zuverlaessig."""
    try:
        os.remove(CIFS_CREDS_FILE)
    except FileNotFoundError:
        pass


def mount_nas(nas):
    """Mountet die NAS-Share per CIFS und validiert den Mountpunkt."""
    mp = _mount_point(nas)
    logger.info(f"Mounte NAS-Share {nas.share_path} unter {mp} ...")
    cmd = [
        "mount",
        "-t", "cifs",
        nas.share_path,
        mp,
        "-o", "credentials={},vers=3.0,iocharset=utf8".format(CIFS_CREDS_FILE),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error(f"mount fehlgeschlagen: {exc}")
        return False
    if result.returncode != 0:
        logger.error(f"mount RC={result.returncode}: {result.stderr.strip()}")
        return False
    logger.info("Mount-Befehl ausgefuehrt. Validiere Mountpunkt ...")

    # SEHR WICHTIG: mountpoint -q prueft, ob es wirklich ein Mount ist.
    mp_check = subprocess.run(
        ["mountpoint", "-q", mp],
        capture_output=True, text=True, timeout=10,
    )
    if mp_check.returncode != 0:
        logger.error(f"mountpoint -q: {mp} ist KEIN gueltiger Mountpunkt! "
                     "Mount ist fehlgeschlagen obwohl RC=0.")
        return False

    logger.info("NAS erfolgreich gemountet und validiert.")
    return True


def unmount_nas(nas=None):
    """Unmountet die NAS-Share (konfigurierter Mount-Point)."""
    mp = _mount_point(nas) if nas is not None else NAS_MOUNT_POINT
    logger.info(f"Unmounte {mp} ...")
    cmd = ["umount", mp]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error(f"umount fehlgeschlagen: {exc}")


def push_to_nas(nas=None):
    """Pusht das Cache-Verzeichnis per rsync auf das NAS.
    Gibt True bei Erfolg zurueck."""
    mp = _mount_point(nas) if nas is not None else NAS_MOUNT_POINT
    size_mb = _cache_size_mb()
    logger.info(f"NAS Push: Lokaler Cache enthaelt {size_mb:.2f} MB Daten.")

    cmd = [
        "rsync",
        "-avz",
        "--stats",
        CACHE_DIR,
        mp + "/",
    ]
    logger.info(f"AUSFUEHRUNG RSYNC: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error(f"rsync push fehlgeschlagen: {exc}")
        return False

    logger.debug(f"RSYNC STDOUT: {result.stdout[:500]}")
    if result.returncode != 0:
        logger.error(f"rsync push RC={result.returncode}: {result.stderr[:500]}")
        return False
    logger.info(f"RSYNC STATS: {result.stdout[:500]}")
    logger.info("rsync push auf NAS erfolgreich.")
    return True


def shutdown_nas(nas):
    """Faehrt die NAS per SSH herunter. Der gesamte Shutdown-Befehl
    (z.B. 'sudo poweroff') wird als ein String an die Remote-Shell
    uebergeben, damit sudo-Befehle fuer unprivilegierte User funktionieren."""
    # SSH Shutdown
    logger.info(f"Fahre NAS herunter (Befehl: {nas.ssh_shutdown_cmd})...")
    # Wichtig: Der gesamte Befehl muss als ein String an SSH uebergeben werden
    shutdown_cmd = [
        "ssh",
        "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-p", str(nas.ssh_port),
        f"{nas.user}@{nas.ip}",
        nas.ssh_shutdown_cmd,
    ]
    try:
        subprocess.run(shutdown_cmd, capture_output=True, text=True, timeout=15)
        logger.info("Shutdown Befehl erfolgreich gesendet.")
    except subprocess.TimeoutExpired:
        # Ein Timeout beim Shutdown ist normal, da die Verbindung sofort abreisst
        logger.info("Shutdown Befehl gesendet (Verbindung abgebrochen - normal).")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Fehler beim NAS Shutdown: {e}")


# ---------------------------------------------------------------------------
# Config-Git-Repo (Feature: Configs versionieren + auf die NAS ziehen)
# ---------------------------------------------------------------------------
def _sync_config_git():
    """Synchronisiert die gezogenen Configs aller git-Tracking-Server in
    ein lokales Git-Repository und committet Aenderungen. So entsteht pro
    Zyklus eine Versionshistorie, aus der sich Configs zurueckholen lassen.
    Fehler duerfen den Backup-Zyklus nie beeintraechtigen."""
    try:
        tracked = Server.query.filter_by(active=True, git_track=True).all()
        if not tracked:
            return

        os.makedirs(GIT_REPO_DIR, exist_ok=True)
        if not os.path.isdir(os.path.join(GIT_REPO_DIR, ".git")):
            result = subprocess.run(
                ["git", "init"], cwd=GIT_REPO_DIR,
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error(f"git init fehlgeschlagen: {result.stderr.strip()}")
                return
            logger.info(f"Git-Config-Repo initialisiert: {GIT_REPO_DIR}")

        synced = 0
        for srv in tracked:
            dirname = srv.hostname.replace(" ", "_")
            src = os.path.join(CACHE_DIR, dirname) + "/"
            if not os.path.isdir(src):
                continue
            dest = os.path.join(GIT_REPO_DIR, dirname)
            result = subprocess.run(
                ["rsync", "-a", "--delete", src, dest + "/"],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                logger.error(f"Git-Sync fuer {srv.hostname} fehlgeschlagen: "
                             f"{result.stderr.strip()}")
                continue
            synced += 1

        if synced == 0:
            return

        subprocess.run(
            ["git", "-C", GIT_REPO_DIR, "add", "-A"],
            capture_output=True, text=True, timeout=120,
        )
        status = subprocess.run(
            ["git", "-C", GIT_REPO_DIR, "status", "--porcelain"],
            capture_output=True, text=True, timeout=60,
        )
        if not status.stdout.strip():
            logger.debug("Git-Config-Repo: keine Aenderungen, Commit uebersprungen.")
            return

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit = subprocess.run(
            ["git", "-C", GIT_REPO_DIR,
             "-c", "user.name=SentinelBackup",
             "-c", "user.email=sentinelbackup@localhost",
             "commit", "-m", f"Backup-Zyklus {stamp}"],
            capture_output=True, text=True, timeout=120,
        )
        if commit.returncode == 0:
            logger.info("Git-Config-Repo: Aenderungen committet "
                        f"({stamp}).")
        else:
            logger.error(f"git commit fehlgeschlagen: {commit.stderr.strip()}")
    except Exception as exc:  # noqa: BLE001 - Tracking darf den Zyklus nicht killen
        logger.error(f"Git-Config-Sync fehlgeschlagen: {exc}")


def _push_config_repo_to_nas(nas):
    """Zieht das Config-Git-Repo nach erfolgreichem Push mit auf die NAS
    (_config_repo/), damit die Versionshistorie auch ausserhalb der VM
    liegt. Nur wenn ein Repo existiert."""
    if not os.path.isdir(os.path.join(GIT_REPO_DIR, ".git")):
        return
    dest = os.path.join(_mount_point(nas), "_config_repo")
    try:
        os.makedirs(dest, exist_ok=True)
        result = subprocess.run(
            ["rsync", "-a", "--delete", GIT_REPO_DIR + "/", dest + "/"],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            logger.info(f"Git-Config-Repo auf die NAS gespiegelt: {dest}")
        else:
            logger.error(f"Spiegelung des Git-Repos auf die NAS fehlgeschlagen: "
                         f"{result.stderr.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.error(f"Git-Repo-NAS-Push fehlgeschlagen: {exc}")


# ---------------------------------------------------------------------------
# NAS-Retention (Feature: alte Backups automatisch bereinigen)
# ---------------------------------------------------------------------------
def _apply_nas_retention(nas):
    """Raeumt alte Backup-Dateien auf der NAS auf (Aufbewahrungsfrist aus
    nas.retention_days, 0 = aus). SICHERHEIT: Es werden ausschliesslich die
    Unterverzeichnisse der in der Datenbank konfigurierten Server angefasst
    (mp/<hostname>) - niemals der Share selbst. Fremde Daten auf der NAS
    bleiben unangetastet."""
    try:
        days = int(getattr(nas, "retention_days", 0) or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        logger.debug("NAS-Retention deaktiviert (0 Tage) - kein Aueraeumen.")
        return

    mp = os.path.realpath(_mount_point(nas))
    for srv in Server.query.filter_by(active=True).all():
        target = os.path.realpath(os.path.join(mp, srv.hostname.replace(" ", "_")))
        # Harter Schutz: nur unterhalb des Mount-Points arbeiten
        if not target.startswith(mp + os.sep):
            logger.warning(f"Retention: Ziel {target} liegt ausserhalb des "
                           f"Mount-Points {mp} - uebersprungen.")
            continue
        if not os.path.isdir(target):
            continue
        try:
            result = subprocess.run(
                ["find", target, "-type", "f", "-mtime", f"+{days}", "-delete"],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                logger.error(f"Retention fehlgeschlagen fuer {target}: "
                             f"{result.stderr.strip()}")
                continue
            deleted = [line for line in result.stdout.splitlines() if line.strip()]
            # Leere Verzeichnisse nach dem Datei-Delete entfernen
            subprocess.run(
                ["find", target, "-type", "d", "-empty", "-delete"],
                capture_output=True, text=True, timeout=60,
            )
            if deleted:
                logger.info(f"Retention: {len(deleted)} Datei(en) aelter als "
                            f"{days} Tage in {target} entfernt.")
            else:
                logger.debug(f"Retention: nichts zu entfernen in {target}.")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.error(f"Retention-Fehler fuer {target}: {exc}")


# ---------------------------------------------------------------------------
# Matrix-Benachrichtigung (Feature: Fehler-Alarm)
# ---------------------------------------------------------------------------
def _send_matrix(state, text):
    """Sendet eine m.text-Nachricht in den konfigurierten Matrix-Raum.
    Genutzt wird der PUT-Endpoint mit Transaction-ID. bewusst stdlib
    (urllib): update.sh installiert keine Requirements neu, daher darf
    hier keine neue Abhaengigkeit auftauchen."""
    room = urllib.parse.quote(state.matrix_room_id, safe="")
    txn = f"sentinel{int(time.time() * 1000)}"
    url = (f"{state.matrix_homeserver.rstrip('/')}"
           f"/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}")
    payload = json.dumps({"msgtype": "m.text", "body": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="PUT",
        headers={
            "Authorization": f"Bearer {state.matrix_access_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        logger.info(f"Matrix-Alarm gesendet (HTTP {resp.status}).")


def _notify_if_failed(cycle_start):
    """Sendet einen Matrix-Alarm, wenn im aktuellen Zyklus Fehler auftraten.
    Respektiert Aktivierung, Vollstaendigkeit der Konfiguration und
    Cooldown. Versandfehler duerfen den Zyklus nie beeintraechtigen."""
    try:
        state = SystemState.query.first()
        if state is None or not state.notify_enabled:
            return
        if not (state.matrix_homeserver and state.matrix_room_id
                and state.matrix_access_token):
            logger.warning("Matrix-Alarm aktiv, aber Homeserver/Room/Token "
                           "unvollstaendig - ueberspringe.")
            return

        failed = BackupLog.query.filter(
            BackupLog.status == "failed",
            BackupLog.timestamp >= cycle_start,
        ).all()
        if not failed:
            return

        # Cooldown: last_notified_at speichert Epochen-Sekunden (Float)
        now = time.time()
        cooldown_sec = (state.notify_cooldown_minutes or 60) * 60
        if state.last_notified_at and (now - state.last_notified_at) < cooldown_sec:
            logger.info(f"Matrix-Alarm wegen Cooldown unterdrueckt "
                        f"({len(failed)} Fehler, letzter Alarm vor "
                        f"{int(now - state.last_notified_at)}s).")
            return

        parts = []
        for entry in failed[:5]:
            who = entry.server.hostname if entry.server else "NAS/System"
            parts.append(f"{who}: {entry.message or 'unbekannter Fehler'}")
        text = (f"SentinelBackup-Alarm: {len(failed)} Fehler im Backup-Zyklus. "
                + " | ".join(parts))
        _send_matrix(state, text)
        state.last_notified_at = now
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - Alarm darf den Zyklus nicht killen
        logger.error(f"Matrix-Benachrichtigung fehlgeschlagen: {exc}")


# ---------------------------------------------------------------------------
# HAUPTFUNKTION
# ---------------------------------------------------------------------------
def run_full_cycle(flask_app):
    """Ein kompletter Backup-Zyklus: Phase 1 (Pull) -> Phase 2 (NAS) ->
    Phase 3 (Cleanup). Das Flask-App-Objekt wird als Parameter uebergeben."""

    # --- PHASE 1: Vorbereitung & Server-Pull -------------------------------
    logger.info("=== Starte neuen Backup-Zyklus ===")
    if not acquire_lock():
        logger.warning("Zyklus uebersprungen - Lock ist nicht frei "
                       "(anderer Zyklus laeuft).")
        return
    cycle_start = datetime.now(timezone.utc)

    try:
        with flask_app.app_context():
            # WICHTIG: Der Cache wird hier NICHT mehr geloescht! Ein frueherer
            # Loeschvorgang am Zyklus-Anfang hat bei gescheitertem NAS-Push im
            # Vorgaengerzyklus zum unwiderruflichen Datenverlust gefuehrt.
            # Der Cache wird ausschliesslich nach einem erfolgreichen NAS-Push
            # am Ende von Phase 2 geleert.
            os.makedirs(CACHE_DIR, exist_ok=True)

            servers = Server.query.filter_by(active=True).all()
            logger.info(f"{len(servers)} aktive Server in der Datenbank.")

            for srv in servers:
                logger.debug(f"Pruefe SSH-Verbindung zu {srv.ip}:{srv.ssh_port} ...")
                logger.info(f"Pruefe Server {srv.hostname} ({srv.ip}:{srv.ssh_port}) ...")

                flag_state = check_flag(srv)

                if flag_state == "error":
                    logger.error(f"Server {srv.hostname}: SSH-Verbindungsfehler - Server uebersprungen.")
                    _log(srv.id, "failed", "SSH-Verbindungsfehler (Port/Key/Firewall).")
                    continue

                if flag_state == "missing":
                    logger.info(f"Server {srv.hostname}: Flag nicht vorhanden - ueberspringe.")
                    _log(srv.id, "skipped", "Flag-Datei nicht vorhanden.")
                    continue

                # Flag gefunden -> rsync pull
                logger.info(f"Server {srv.hostname}: Flag gefunden, starte rsync pull ...")
                local_dir = f"{CACHE_DIR}{srv.hostname.replace(' ', '_')}/"
                os.makedirs(local_dir, exist_ok=True)

                rc = pull_rsync(srv, local_dir)
                if rc == 0:
                    delete_flag(srv)
                    logger.info(f"Server {srv.hostname}: Backup erfolgreich gezogen, Flag geloescht.")
                    _log(srv.id, "success", "rsync pull erfolgreich, Flag geloescht.")
                else:
                    logger.error(f"Server {srv.hostname}: rsync pull fehlgeschlagen (RC={rc}).")
                    _log(srv.id, "failed", f"rsync pull RC={rc}")

            # Config-Git-Repo aktualisieren, solange die Daten noch im
            # Cache liegen (Phase 2 leert ihn erst nach erfolgreichem Push).
            _sync_config_git()

            # --- PHASE 2: NAS-Aktionen (nur wenn Cache nicht leer) -----------
            if not _cache_has_data():
                logger.info("Cache ist leer - keine NAS-Aktionen erforderlich.")
            else:
                nas = NasConfig.query.first()
                if nas is None:
                    logger.error("Keine NAS-Konfiguration vorhanden - Cache bleibt bestehen.")
                else:
                    logger.info("Beginne NAS-Aktionen.")

                    # Wake on LAN
                    wake_nas(nas)

                    # Warten auf NAS
                    if not wait_for_nas(nas):
                        logger.error("NAS nicht erreichbar - Abbruch (kein Push, kein Shutdown).")
                        _log(None, "failed", "NAS nach WoL nicht erreichbar.")
                    else:
                        # Credentials erstellen
                        write_cifs_creds(nas)
                        try:
                            os.makedirs(_mount_point(nas), exist_ok=True)

                            # Mounten + Validierung
                            if not mount_nas(nas):
                                logger.error("CIFS mount fehlgeschlagen - springe zu Cleanup.")
                                _log(None, "failed", "CIFS mount fehlgeschlagen.")
                                unmount_nas(nas)
                            else:
                                # NAS Push
                                if push_to_nas(nas):
                                    _clear_cache_contents()
                                    logger.info("Cache nach erfolgreichem Push geleert.")
                                    _log(None, "success", "rsync push auf NAS erfolgreich.")
                                    # Retention: erst NACH erfolgreichem Push,
                                    # solange die Share noch gemountet ist.
                                    _apply_nas_retention(nas)
                                    # Config-Git-Repo mit auf die NAS ziehen
                                    _push_config_repo_to_nas(nas)
                                else:
                                    logger.error("rsync push auf NAS fehlgeschlagen - Cache NICHT geloescht.")
                                    _log(None, "failed", "rsync push auf NAS fehlgeschlagen.")

                                unmount_nas(nas)
                        finally:
                            remove_cifs_creds()
                            logger.info("CIFS-Credentials entfernt.")

                        # NAS Shutdown (nur wenn NAS erreichbar war)
                        shutdown_nas(nas)

            # Matrix-Alarm, falls in DIESEM Zyklus Fehler auftraten
            _notify_if_failed(cycle_start)

            logger.info("=== Backup-Zyklus abgeschlossen ===")

    except Exception as exc:  # noqa: BLE001 - Daemon darf nicht sterben
        logger.exception(f"Unerwarteter Fehler im Zyklus: {exc}")
    finally:
        # --- PHASE 3: Cleanup ---------------------------------------------
        release_lock()
        logger.info("Lock-Datei freigegeben. Zyklus abgeschlossen.")


# ---------------------------------------------------------------------------
# Update-Uebergabe (WebUI -> DB -> root-Daemon -> systemd-Transient-Unit)
# ---------------------------------------------------------------------------
def _launch_update(version):
    """Startet update.sh in EIGENER systemd-Transient-Unit.
    WARUM systemd-run: update.sh stoppt 'sentinelbackup-engine'. Wuerde der
    Daemon das Skript direkt als Kind ausfuehren, waere der Stop des Dienstes
    gleichzeitig das Ende der eigenen cgroup - und damit der Tod des laufen-
    den Updates. Die Transient-Unit laeuft unabhaengig vom Daemon und
    startet am Ende beide Dienste neu.
    Gibt True zurueck, wenn das Update gestartet wurde."""
    if not os.path.isfile(UPDATE_SCRIPT):
        logger.error(f"Update-Skript fehlt: {UPDATE_SCRIPT} - Update nicht gestartet.")
        return False

    unit = f"sentinelbackup-update-{int(time.time())}"
    cmd = [
        "systemd-run",
        "--unit", unit,
        "--collect",
        "--property=Restart=no",
        "bash", UPDATE_SCRIPT, version,
    ]
    logger.info(f"Starte Update {version} via systemd-Unit {unit} ...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        # Fallback ohne systemd-run (sollte auf Ubuntu nicht vorkommen)
        logger.warning("systemd-run nicht verfuegbar - Fallback: direkter Start. "
                       "ACHTUNG: update.sh stoppt den Engine-Dienst und damit "
                       "moeglicherweise diesen eigenen Prozess.")
        try:
            subprocess.Popen(
                ["bash", UPDATE_SCRIPT, version],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError as exc:
            logger.error(f"Update-Start fehlgeschlagen: {exc}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("systemd-run Timeout nach 30s - Update nicht gestartet.")
        return False

    if result.returncode != 0:
        logger.error(f"systemd-run fehlgeschlagen (RC={result.returncode}): "
                     f"{result.stderr.strip()}")
        return False
    logger.info(f"Update {version} gestartet (Unit {unit}). Die Dienste werden "
                "vom Update-Skript neu gestartet.")
    return True


# ---------------------------------------------------------------------------
# Main-Block (nur bei direktem Aufruf als Daemon)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from app import app as flask_app
    from models import SystemState

    setup_logging()
    logger.info("SentinelBackup Engine (Daemon) gestartet. Warte auf Trigger...")

    last_run_time = 0
    last_heartbeat = 0

    while True:
        now = time.time()
        should_run = False
        pending_update = None

        with flask_app.app_context():
            state = SystemState.query.first()
            if state and state.trigger_run:
                state.trigger_run = False
                db.session.commit()
                should_run = True
                logger.info("Manueller Trigger empfangen.")

            routine_seconds = state.routine_interval_minutes * 60 if state else 3600
            if not should_run and (now - last_run_time) >= routine_seconds:
                should_run = True
                logger.info("Routine-Zyklus gestartet.")

            if state and state.update_pending:
                pending_update = state.update_pending

        if should_run:
            try:
                run_full_cycle(flask_app)
                last_run_time = time.time()
            except Exception as e:
                logger.error(f"Schwerer Fehler im Zyklus: {e}")
                last_run_time = time.time()
        else:
            # --- Update-Wunsch aus der WebUI abarbeiten ---------------------
            if pending_update:
                if lock_exists():
                    logger.info(f"Update {pending_update} wartet auf einen "
                                "freien Lock (Zyklus laeuft gerade) ...")
                else:
                    # Flag ZUERST leeren: update.sh stoppt den Engine-Dienst,
                    # der Daemon kann danach nicht mehr selbst loeschen. Ein
                    # verbliebenes Flag triggere nach dem Neustart sofort ein
                    # zweites Update (Endlosschleife).
                    with flask_app.app_context():
                        st = SystemState.query.first()
                        if st and st.update_pending:
                            st.update_pending = None
                            db.session.commit()
                    if not _launch_update(pending_update):
                        # Start fehlgeschlagen -> Flag zuruecksetzen fuer Retry.
                        with flask_app.app_context():
                            st = SystemState.query.first()
                            if st:
                                st.update_pending = pending_update
                                db.session.commit()

            if (now - last_heartbeat) >= 21600:
                logger.info("Daemon läuft im Hintergrund...")
                last_heartbeat = now

            time.sleep(10)
