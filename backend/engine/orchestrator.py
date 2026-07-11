#!/usr/bin/env python3
# SentinelBackup - Orchestrator Engine
# Author: WrkDrvn
# Version: 1.0.2
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
import shutil
import subprocess
import logging
import logging.handlers

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
NAS_MOUNT_POINT = "/mnt/nas"
NAS_BOOT_TIMEOUT_SEC = 300
NAS_POLL_INTERVAL_SEC = 5


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
    """Erstellt die Lock-Datei. Bricht ab, falls sie bereits existiert."""
    if os.path.exists(LOCK_FILE):
        logger.error("Lock-Datei existiert - ein anderer Zyklus laeuft. Abbruch.")
        sys.exit(1)
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        logger.error(f"Konnte Lock-Datei nicht schreiben: {exc}")
        sys.exit(1)


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
    Muss innerhalb eines aktiven Flask app_context aufgerufen werden."""
    entry = BackupLog(
        server_id=server_id,
        status=status,
        message=message,
    )
    db.session.add(entry)
    db.session.commit()


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
    logger.info(f"Mounte NAS-Share {nas.share_path} unter {NAS_MOUNT_POINT} ...")
    cmd = [
        "mount",
        "-t", "cifs",
        nas.share_path,
        NAS_MOUNT_POINT,
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
    mp = subprocess.run(
        ["mountpoint", "-q", NAS_MOUNT_POINT],
        capture_output=True, text=True, timeout=10,
    )
    if mp.returncode != 0:
        logger.error("mountpoint -q: /mnt/nas ist KEIN gueltiger Mountpunkt! "
                     "Mount ist fehlgeschlagen obwohl RC=0.")
        return False

    logger.info("NAS erfolgreich gemountet und validiert.")
    return True


def unmount_nas():
    """Unmountet die NAS-Share."""
    logger.info(f"Unmounte {NAS_MOUNT_POINT} ...")
    cmd = ["umount", NAS_MOUNT_POINT]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error(f"umount fehlgeschlagen: {exc}")


def push_to_nas():
    """Pusht das Cache-Verzeichnis per rsync auf das NAS.
    Gibt True bei Erfolg zurueck."""
    size_mb = _cache_size_mb()
    logger.info(f"NAS Push: Lokaler Cache enthaelt {size_mb:.2f} MB Daten.")

    cmd = [
        "rsync",
        "-avz",
        "--stats",
        CACHE_DIR,
        NAS_MOUNT_POINT + "/",
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
# HAUPTFUNKTION
# ---------------------------------------------------------------------------
def run_full_cycle(flask_app):
    """Ein kompletter Backup-Zyklus: Phase 1 (Pull) -> Phase 2 (NAS) ->
    Phase 3 (Cleanup). Das Flask-App-Objekt wird als Parameter uebergeben."""

    # --- PHASE 1: Vorbereitung & Server-Pull -------------------------------
    logger.info("=== Starte neuen Backup-Zyklus ===")
    acquire_lock()

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
                            os.makedirs(NAS_MOUNT_POINT, exist_ok=True)

                            # Mounten + Validierung
                            if not mount_nas(nas):
                                logger.error("CIFS mount fehlgeschlagen - springe zu Cleanup.")
                                _log(None, "failed", "CIFS mount fehlgeschlagen.")
                                unmount_nas()
                            else:
                                # NAS Push
                                if push_to_nas():
                                    _clear_cache_contents()
                                    logger.info("Cache nach erfolgreichem Push geleert.")
                                    _log(None, "success", "rsync push auf NAS erfolgreich.")
                                else:
                                    logger.error("rsync push auf NAS fehlgeschlagen - Cache NICHT geloescht.")
                                    _log(None, "failed", "rsync push auf NAS fehlgeschlagen.")

                                unmount_nas()
                        finally:
                            remove_cifs_creds()
                            logger.info("CIFS-Credentials entfernt.")

                        # NAS Shutdown (nur wenn NAS erreichbar war)
                        shutdown_nas(nas)

            logger.info("=== Backup-Zyklus abgeschlossen ===")

    except Exception as exc:  # noqa: BLE001 - Daemon darf nicht sterben
        logger.exception(f"Unerwarteter Fehler im Zyklus: {exc}")
    finally:
        # --- PHASE 3: Cleanup ---------------------------------------------
        release_lock()
        logger.info("Lock-Datei freigegeben. Zyklus abgeschlossen.")


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

        if should_run:
            try:
                run_full_cycle(flask_app)
                last_run_time = time.time()
            except Exception as e:
                logger.error(f"Schwerer Fehler im Zyklus: {e}")
                last_run_time = time.time()
        else:
            if (now - last_heartbeat) >= 21600:
                logger.info("Daemon läuft im Hintergrund...")
                last_heartbeat = now

            time.sleep(10)
