#!/bin/bash
set -e

# Author: WrkDrvn
#
# Installiert SentinelBackup auf einem frischen Ubuntu 24.04 LTS.
# Muss als root ausgefuehrt werden.
# Kopiert saemtliche Projektdateien (backend/ inkl. Templates, Partials,
# Client-Skripte und CHANGELOG.md) nach /opt/sentinelbackup/.

# Zentrale Versionsnummer aus VERSION-Datei lesen
APP_VERSION="$(cat "$(cd "$(dirname "$0")" && pwd)/VERSION")"

# --- Preflight --------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] Dieses Skript muss als root ausgefuehrt werden."
    echo "        Bitte mit 'sudo bash install.sh' starten."
    exit 1
fi

INSTALL_DIR="/opt/sentinelbackup"
VENV_DIR="${INSTALL_DIR}/venv"
# Quell-Verzeichnis: dort, wo install.sh liegt (Repo-Root)
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_KEY="/root/.ssh/id_ed25519"
SSH_PUB_FILE="${INSTALL_DIR}/ssh_pub_key.txt"
NGINX_SITE="/etc/nginx/sites-available/sentinelbackup"
SYSTEMD_SERVICE="/etc/systemd/system/sentinelbackup.service"
ENGINE_SERVICE="/etc/systemd/system/sentinelbackup-engine.service"
GUNICORN_BIND="127.0.0.1:5000"

# --- Banner ----------------------------------------------------------------
clear 2>/dev/null || true
echo "+---------------------------------------------------------------+"
echo "|                                                               |"
echo "| ██╗    ██╗██████╗ ██╗  ██╗██████╗ ██████╗ ██╗   ██╗███╗   ██╗ |"
echo "| ██║    ██║██╔══██╗██║ ██╔╝██╔══██╗██╔══██╗██║   ██║████╗  ██║ |"
echo "| ██║ █╗ ██║██████╔╝█████╔╝ ██║  ██║██████╔╝██║   ██║██╔██╗ ██║ |"
echo "| ██║███╗██║██╔══██╗██╔═██╗ ██║  ██║██╔══██╗╚██╗ ██╔╝██║╚██╗██║ |"
echo "| ╚███╔███╔╝██║  ██║██║  ██║██████╔╝██║  ██║ ╚████╔╝ ██║ ╚████║ |"
echo "|  ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═══╝ |"
echo "|                                                               |"
echo "+---------------------------------------------------------------+"
echo ""
echo ""
echo " >>> SentinelBackup v${APP_VERSION} - Installer <<<"
echo ""
echo ""
# --- 1. Systempakete installieren ------------------------------------------
echo "[1/8] Systempakete aktualisieren und installieren ..."
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    nginx \
    python3-venv \
    python3-pip \
    rsync \
    wakeonlan \
    cifs-utils \
    smbclient \
    ssh

# --- 2. Installationsverzeichnis erstellen ---------------------------------
echo "[2/8] Installationsverzeichnis anlegen: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

# --- 3. Projektdateien kopieren (kompletter backend/ Ordner + CHANGELOG) ---
echo "[3/8] Projektdateien nach ${INSTALL_DIR} kopieren ..."
cp -r "${REPO_DIR}/backend" "${INSTALL_DIR}/backend"
cp -f "${REPO_DIR}/CHANGELOG.md" "${INSTALL_DIR}/CHANGELOG.md"
cp -f "${REPO_DIR}/VERSION" "${INSTALL_DIR}/VERSION"

# --- 4. SSH ED25519 Key-Pair generieren ------------------------------------
echo "[4/8] SSH ED25519 Key-Pair generieren ..."
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ ! -f "${SSH_KEY}" ]; then
    ssh-keygen -t ed25519 -f "${SSH_KEY}" -N "" -C "sentinelbackup@$(hostname)"
fi
cp "${SSH_KEY}.pub" "${SSH_PUB_FILE}"
chmod 644 "${SSH_PUB_FILE}"

# --- 5. Python VENV einrichten + Requirements installieren ------------------
echo "[5/8] Python Virtualenv einrichten: ${VENV_DIR}"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/backend/requirements.txt"

# --- 6. Berechtigungen korrigieren (VOR Service-Start) ---------------------
echo "[6/8] Dateibesitz auf www-data uebertragen ..."
chown -R www-data:www-data /opt/sentinelbackup/

# Log-Verzeichnis fuer die Orchestrator-Engine anlegen. Web- (www-data) und
# Engine-Service (root) koennen beide lesen/schreiben - 775 + root:www-data.
mkdir -p /var/log/sentinelbackup
chown root:www-data /var/log/sentinelbackup
chmod 775 /var/log/sentinelbackup

# --- 7. Nginx Reverse Proxy + Systemd-Service konfigurieren ----------------
echo "[7/8] Nginx + Systemd konfigurieren ..."

cat > "${NGINX_SITE}" <<NGINX
# SentinelBackup - Nginx Reverse Proxy
# Author: WrkDrvn
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    server_name _;

    access_log /var/log/nginx/sentinelbackup_access.log;
    error_log  /var/log/nginx/sentinelbackup_error.log;

    location / {
        proxy_pass         http://${GUNICORN_BIND};
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
    }
}
NGINX

rm -f /etc/nginx/sites-enabled/default
ln -sf "${NGINX_SITE}" /etc/nginx/sites-enabled/sentinelbackup
nginx -t

cat > "${SYSTEMD_SERVICE}" <<SYSTEMD
# SentinelBackup - Systemd Unit
# Author: WrkDrvn
[Unit]
Description=SentinelBackup Backend (Gunicorn)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=${INSTALL_DIR}/backend
ExecStart=${VENV_DIR}/bin/gunicorn --workers 2 --bind ${GUNICORN_BIND} 'app:app'
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable sentinelbackup.service
systemctl restart sentinelbackup.service

# Zweiter Service: Orchestrator-Engine (Hintergrund-Daemon)
# HINWEIS: Um DEBUG-Logs zu aktivieren, aendere die Zeile 'Environment=...'
# unten auf SENTINEL_LOG_LEVEL=DEBUG (danach: systemctl daemon-reload &&
# systemctl restart sentinelbackup-engine).
cat > "${ENGINE_SERVICE}" <<ENGINE
# SentinelBackup - Orchestrator Engine (Systemd Unit)
# Author: WrkDrvn
[Unit]
Description=SentinelBackup Backup Orchestrator Engine
After=network.target sentinelbackup.service

[Service]
# Engine muss als root laufen (SSH-Key, mount/umount, rsync zum NAS)
User=root
Group=root
Environment="SENTINEL_LOG_LEVEL=INFO"
WorkingDirectory=${INSTALL_DIR}/backend
ExecStart=${VENV_DIR}/bin/python3 -u ${INSTALL_DIR}/backend/engine/orchestrator.py
# always: Daemon wird bei Crash/Exit automatisch neu gestartet (Stabilitaet)
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
ENGINE

systemctl daemon-reload
systemctl enable sentinelbackup-engine.service
systemctl restart sentinelbackup-engine.service

# --- 8. Nginx neu starten --------------------------------------------------
echo "[8/8] Nginx neu starten ..."
systemctl restart nginx

# --- Abschluss --------------------------------------------------------------
SERVER_IP="$(hostname -I | awk '{print $1}')"

echo ""
echo "========================================"
echo " SentinelBackup v${APP_VERSION} Installation erfolgreich!"
echo "========================================"
echo " Web-UI:        http://${SERVER_IP}/"
echo " Health-Check:  http://${SERVER_IP}/health"
echo " SSH Public Key:${SSH_PUB_FILE}"
echo " Install-Dir:   ${INSTALL_DIR}"
echo " Web-Service:   systemctl status sentinelbackup"
echo " Engine-Service:systemctl status sentinelbackup-engine"
echo "========================================"
echo ""
echo " Naechster Schritt:"
echo "   Kopiere den Public Key aus ${SSH_PUB_FILE}"
echo "   in die authorized_keys der Ziel-Hosts."
echo ""
