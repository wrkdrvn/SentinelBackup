#!/usr/bin/env python3
# SentinelBackup - Backend Application
# Author: WrkDrvn
# Version: 1.0.0
#
# Flask-Backend mit SQLAlchemy-Datenbank und HTMX-Fragment-Routen.
# Gewartet von Gunicorn hinter Nginx.
#
# WICHTIG (HTMX): POST-Routen geben NIE redirect() und NIE eine ganze
# HTML-Seite zurueck. Es werden ausschliesslich Partials/Snippets
# geliefert, die HTMX in die Ziel-Container swappt.

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    abort,
)

from database import db, init_db
from models import Server, NasConfig, BackupLog, SystemState

from engine.orchestrator import run_full_cycle

VERSION = "1.0.0"
AUTHOR = "WrkDrvn"

# App-Objekt MUSS "app" heissen (Gunicorn-Referenz: 'app:app')
app = Flask(__name__)

# SQLite-Datenbank unter dem Installationspfad
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////opt/sentinelbackup/database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# DB initialisieren (WAL-Modus + Tabellen via create_all)
init_db(app)

# Pfad zum Public-Key, der auf dem Dashboard angezeigt wird
SSH_PUB_KEY_PATH = "/opt/sentinelbackup/ssh_pub_key.txt"

# Verzeichnis mit den Client-Skripten
import os
CLIENT_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "client_scripts")
AGENT_SCRIPT = "setup_agent.sh"


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _read_ssh_pub_key():
    """Liest den oeffentlichen SSH-Key. Fallback-String bei Fehler."""
    try:
        with open(SSH_PUB_KEY_PATH, "r", encoding="utf-8") as fh:
            content = fh.read().strip()
            if not content:
                return "SSH Key nicht gefunden."
            return content
    except (OSError, IOError):
        return "SSH Key nicht gefunden."


# ---------------------------------------------------------------------------
# Seiten-Routen (ganze HTML-Seiten)
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    """Dashboard: SSH-Key + Statistiken + Routine-Einstellungen."""
    ssh_key = _read_ssh_pub_key()
    active_servers = Server.query.filter_by(active=True).count()
    total_servers = Server.query.count()
    state = SystemState.query.first()
    routine_interval = state.routine_interval_minutes if state else 60
    return render_template(
        "dashboard.html",
        ssh_key=ssh_key,
        active_servers=active_servers,
        total_servers=total_servers,
        routine_interval=routine_interval,
        version=VERSION,
        author=AUTHOR,
    )


@app.route("/servers")
def servers():
    """Server-Liste mit Tabelle und Add-Modal."""
    all_servers = Server.query.order_by(Server.id.desc()).all()
    return render_template(
        "servers.html",
        servers=all_servers,
        version=VERSION,
        author=AUTHOR,
    )


@app.route("/nas")
def nas_config():
    """NAS-Konfigurationsseite."""
    nas = NasConfig.query.first()
    return render_template(
        "nas_config.html",
        nas=nas,
        version=VERSION,
        author=AUTHOR,
    )


@app.route("/scripts")
def scripts():
    """Download-Center fuer Client-Skripte."""
    return render_template(
        "scripts.html",
        version=VERSION,
        author=AUTHOR,
    )


# ---------------------------------------------------------------------------
# HTMX-Routen - liefern NUR Partials/Snippets, niemals redirect()
# ---------------------------------------------------------------------------
@app.route("/servers/add", methods=["POST"])
def add_server():
    """Legt einen neuen Server an und gibt die aktualisierten Tabellen-Zeilen
    als HTML-Snippet zurueck (kein redirect, keine ganze Seite)."""
    data = request.form
    server_type = data.get("server_type", "docker")
    if server_type not in Server.VALID_TYPES:
        abort(400, "Ungueltiger Servertyp")

    ssh_port_str = data.get("ssh_port", "22").strip()
    try:
        ssh_port = int(ssh_port_str) if ssh_port_str else 22
    except ValueError:
        ssh_port = 22

    server = Server(
        hostname=data.get("hostname", "").strip(),
        ip=data.get("ip", "").strip(),
        ssh_user=data.get("ssh_user", "root").strip(),
        ssh_port=ssh_port,
        backup_path=data.get("backup_path", "").strip(),
        flag_path=data.get("flag_path", "/var/run/backup_done").strip(),
        server_type=server_type,
        active=data.get("active", "on") == "on",
    )
    db.session.add(server)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        abort(500, f"Fehler beim Speichern des Servers: {exc}")

    # Nur die Tabellen-Zeilen als Fragment zurueckgeben
    return render_template(
        "partials/server_rows.html",
        servers=Server.query.all(),
    )


@app.route("/servers/delete/<int:server_id>", methods=["POST"])
def delete_server(server_id):
    """Loescht einen Server und gibt die aktualisierten Zeilen zurueck."""
    server = db.session.get(Server, server_id)
    if server is None:
        abort(404)
    db.session.delete(server)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        abort(500, f"Fehler beim Loeschen: {exc}")

    return render_template(
        "partials/server_rows.html",
        servers=Server.query.all(),
    )


@app.route("/nas/save", methods=["POST"])
def save_nas():
    """Erstellt oder aktualisiert die NAS-Konfiguration.
    Gibt das Formular-Template mit den neuen Werten zurueck
    (kein redirect, keine ganze Seite)."""
    data = request.form
    nas = NasConfig.query.first()

    if nas is None:
        nas = NasConfig()
        db.session.add(nas)

    nas.mac = data.get("mac", "").strip()
    nas.ip = data.get("ip", "").strip()
    nas_ssh_port_str = data.get("ssh_port", "2022").strip()
    try:
        nas_ssh_port = int(nas_ssh_port_str) if nas_ssh_port_str else 2022
    except ValueError:
        nas_ssh_port = 2022
    nas.ssh_port = nas_ssh_port
    nas.share_path = data.get("share_path", "").strip()
    nas.user = data.get("user", "").strip()
    nas.password = data.get("password", "").strip()
    nas.mount_point = data.get("mount_point", "/mnt/nas").strip() or "/mnt/nas"
    nas.ssh_shutdown_cmd = (
        data.get("ssh_shutdown_cmd", "sudo synoshutdown --shutdown").strip() or "sudo synoshutdown --shutdown"
    )

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        abort(500, f"Fehler beim Speichern der NAS-Konfiguration: {exc}")

    # Frischen Stand aus der DB holen und als Formular-Snippet ausliefern
    return render_template(
        "partials/nas_form.html",
        nas=NasConfig.query.first(),
        success=True,
    )


@app.route("/nas/test", methods=["POST"])
def test_nas():
    """Mehrstufiger NAS-Verbindungstest (Ping -> Port 445 -> SMB Login).
    Liest die Werte direkt aus request.form, damit der User testen kann,
    bevor er speichert. Der Webserver (www-data) fuehrt dabei KEIN mount
    aus. Gibt eine HTMX-Liste mit Schritt-fuer-Schritt-Diagnose zurueck."""
    import subprocess

    # --- Schritt 1: Inputs saeubern (unsichtbare Leerzeichen via Copy/Paste) -
    ip = request.form.get("ip", "").strip()
    share_path = request.form.get("share_path", "").strip()
    user = request.form.get("user", "").strip()
    password = request.form.get("password", "").strip()

    # --- Schritt 2: Share-Pfad validieren ---------------------------------
    # Muss mit // beginnen und darf keinen Slash am Ende haben.
    if not share_path.startswith("//"):
        # Falls der Nutzer nur den Share-Namen oder IP/Share eingegeben hat,
        # versuchen wir den Pfad zu reparieren: //IP/Share
        bare = share_path.lstrip("/")
        share_path = f"//{ip}/{bare}" if ip and bare else share_path

    # Keine doppelten Slashes am Ende
    share_path = share_path.rstrip("/")

    if not ip or not share_path or not user:
        return _nas_alert("IP-Adresse, Share-Pfad und Benutzer muessen ausgefuellt sein.", kind="warning")

    steps = []  # Liste von (ok: bool, text: str)

    # --- Schritt 1: Ping -------------------------------------------------
    try:
        ping = subprocess.run(
            ["ping", "-c", "1", "-W", "2", ip],
            capture_output=True, text=True, timeout=10,
        )
        ping_ok = ping.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ping_ok = False

    if not ping_ok:
        steps.append((False, "Host nicht erreichbar (Ping fehlgeschlagen). "
                             "Ueberpruefen Sie die IP-Adresse und das Netzwerk."))
        return _nas_diag_list(steps)

    steps.append((True, "Host erreichbar (Ping erfolgreich)."))

    # --- Schritt 2: Port 445 (SMB) ---------------------------------------
    try:
        port = subprocess.run(
            ["nc", "-z", "-w2", ip, "445"],
            capture_output=True, text=True, timeout=10,
        )
        port_ok = port.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        port_ok = False

    if not port_ok:
        steps.append((False, "SMB-Port (445) ist geschlossen oder wird von "
                             "einer Firewall blockiert."))
        return _nas_diag_list(steps)

    steps.append((True, "SMB-Port (445) ist offen."))

    # --- Schritt 3: SMB Login (SMB3 erzwingen - Synology blockiert alt) ---
    cmd = [
        "smbclient",
        share_path,
        "-m", "SMB3",          # FIX: Synology/moderne NAS verlangen SMB3
        "-U", f"{user}%{password}",
        "-c", "ls",
    ]
    try:
        smb = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        steps.append((False, "SMB-Login: Timeout - die NAS hat nicht "
                             "geantwortet."))
        return _nas_diag_list(steps)
    except FileNotFoundError:
        steps.append((False, "smbclient ist nicht installiert. Bitte "
                             "installieren Sie das Paket 'smbclient'."))
        return _nas_diag_list(steps)

    if smb.returncode == 0:
        steps.append((True, "SMB Login erfolgreich! Freigabe erreichbar."))
    else:
        stderr = (smb.stderr or smb.stdout or "").strip()
        hint = _translate_smb_error(stderr)
        steps.append((False, f"SMB Login fehlgeschlagen: {hint}"))

    return _nas_diag_list(steps)


def _nas_diag_list(steps):
    """Baut eine Bootstrap list-group aus den Diagnose-Schritten.
    steps: Liste von (ok, text) - ok=True (gruen), False (rot)."""
    items = []
    for ok, text in steps:
        if ok:
            icon = "&#10004;"  # ✅
            cls = "list-group-item-success"
        else:
            icon = "&#10006;"  # ❌
            cls = "list-group-item-danger"
        items.append(
            f'<li class="list-group-item {cls}">{icon} {text}</li>'
        )
    return '<ul class="list-group list-group-flush">' + "".join(items) + '</ul>'


def _translate_smb_error(stderr):
    """Uebersetzt rohe smbclient-Fehlermeldungen in verstaendliche,
    deutsche Loesungshinweise fuer den Nutzer."""
    error_lower = stderr.lower()
    if "logon_failure" in error_lower or "access_denied" in error_lower:
        return ("Zugriff verweigert. Bitte ueberpruefen Sie Benutzername und "
                "Passwort. Stellen Sie sicher, dass der Benutzer auf der NAS "
                "fuer SMB/Freigaben berechtigt ist.")
    elif "bad_network_name" in error_lower:
        return ("Der Freigabepfad (Share) existiert nicht auf der NAS. Bitte "
                "ueberpruefen Sie die Schreibweise des Pfades (z.B. //IP/Backup).")
    elif "host_unreachable" in error_lower or "timed out" in error_lower or "network unreachable" in error_lower:
        return ("Die NAS ist nicht erreichbar. Ueberpruefen Sie die IP-Adresse "
                "und ob die NAS eingeschaltet ist.")
    elif "connection refused" in error_lower:
        return ("Verbindung abgelehnt. Laeuft der SMB/Dienst auf der NAS? Wird "
                "die Verbindung durch eine Firewall blockiert?")
    else:
        return f"Unbekannter Fehler: {stderr}"


def _nas_alert(message, kind="danger"):
    """Baut ein Bootstrap-Alert-Snippet (fuer Validierungs-Hinweise)."""
    icon = "bi-check-circle" if kind == "success" else "bi-exclamation-triangle"
    return (f'<div class="alert alert-{kind} alert-dismissible fade show" '
            f'role="alert"><i class="bi {icon} me-1"></i>{message} '
            f'<button type="button" class="btn-close" data-bs-dismiss="alert">'
            f'</button></div>')


@app.route("/download_script/agent")
def download_agent():
    """Liefert den unified Agent-Installer (setup_agent.sh) als Download aus.
    Alle anderen Skript-Typen wurden entfernt (Dead Code aus frueheren
    Sprints) - nur der Agent ist gueltig."""
    path = os.path.join(CLIENT_SCRIPTS_DIR, AGENT_SCRIPT)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=AGENT_SCRIPT)


@app.route("/api/trigger_engine", methods=["POST"])
def trigger_engine():
    """Stoesst einen manuellen Backup-Zyklus an, indem in der Datenbank der
    trigger_run-Flag gesetzt wird. Der Daemon-Prozess pollt diesen Wert und
    fuehrt den Zyklus als root aus. So entfaellt das fruehere, fehleranfaellige
    /tmp-Triggerfile (Permission-Denied zwischen www-data und root).
    Gibt ein HTML-Alert-Snippet zurueck, das HTMX in die Status-Flaeche swappt."""
    state = SystemState.query.first()
    if state:
        state.trigger_run = True
        db.session.commit()
    return '''<div class="alert alert-success alert-dismissible fade show" role="alert">
            Backup-Zyklus angestoßen (startet in wenigen Sekunden).
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
          </div>'''


@app.route("/api/latest_logs")
def latest_logs():
    """Liefert die letzten 5 BackupLog-Eintraege als HTML-Partial."""
    logs = BackupLog.query.order_by(BackupLog.timestamp.desc()).limit(5).all()
    return render_template("partials/latest_logs.html", logs=logs)


@app.route("/settings/save_interval", methods=["POST"])
def save_interval():
    """Speichert das Routine-Intervall (Minuten) in SystemState.
    Gibt ein HTMX-Alert-Snippet zurueck. Aenderung wirkt sofort, da der
    Daemon den Wert live aus der DB liest."""
    interval = request.form.get("interval", "60")
    try:
        interval_int = int(interval)
    except (TypeError, ValueError):
        interval_int = 60
    # Erlaubte Werte begrenzen
    if interval_int not in (10, 20, 30, 60, 120, 300):
        interval_int = 60

    state = SystemState.query.first()
    if state:
        state.routine_interval_minutes = interval_int
        db.session.commit()

    return ('<div class="alert alert-success alert-dismissible fade show" '
            'role="alert"><i class="bi bi-check-circle me-1"></i>'
            'Intervall erfolgreich aktualisiert. '
            '<button type="button" class="btn-close" data-bs-dismiss="alert">'
            '</button></div>')


@app.route("/health")
def health():
    """Health-Endpoint fuer Monitoring."""
    return jsonify({"status": "ok", "version": "1.0.0"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
