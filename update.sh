#!/bin/bash
set -e

# SentinelBackup - Update Script
# Author: WrkDrvn
# Version: 1.1.0
#
# Aktualisiert eine bestehende SentinelBackup-Installation auf eine neue
# Version, OHNE die SQLite-Datenbank, den SSH-Key oder den Cache zu beruehren.
#
# Aufruf:  sudo bash update.sh v1.1.0
#
# Sicherheit: rsync nutzt --exclude, damit database.db, WAL/SHM-Dateien
# und der cache/ Ordner niemals ueberschrieben oder geloescht werden.

INSTALL_DIR="/opt/sentinelbackup"
DB_FILE="${INSTALL_DIR}/database.db"
DOWNLOAD_URL="https://github.com/wrkdrvn/SentinelBackup/archive/refs/tags/${1}.tar.gz"
TMP_TARBALL="/tmp/sentinel_update.tar.gz"
TMP_EXTRACT="/tmp/sentinel_update"

# --- Farb-Helfer ------------------------------------------------------------
if [ -t 1 ]; then
    GREEN="$(tput setaf 2)"
    RED="$(tput setaf 1)"
    YELLOW="$(tput setaf 3)"
    CYAN="$(tput setaf 6)"
    BOLD="$(tput bold)"
    RESET="$(tput sgr0)"
else
    GREEN=""; RED=""; YELLOW=""; CYAN=""; BOLD=""; RESET=""
fi

ok()    { echo "${GREEN}[OK]${RESET} $1"; }
err()   { echo "${RED}[FEHLER]${RESET} $1"; }
info()  { echo "${CYAN}[i]${RESET} $1"; }
warn()  { echo "${YELLOW}[!]${RESET} $1"; }

echo "${BOLD}SentinelBackup Update Script by WrkDrvn${RESET}"
echo ""

# --- Preflight: Root + Argument --------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    err "Dieses Skript muss als root ausgefuehrt werden."
    echo "    Bitte starten mit: ${BOLD}sudo bash update.sh v1.1.0${RESET}"
    exit 1
fi

if [ -z "$1" ]; then
    err "Keine Versionsnummer angegeben."
    echo "    Aufruf: ${BOLD}sudo bash update.sh v1.1.0${RESET}"
    exit 1
fi

TARGET_VERSION="$1"
info "Ziel-Version: ${BOLD}${TARGET_VERSION}${RESET}"

# --- Schritt 1: Safety Check -----------------------------------------------
if [ ! -f "${DB_FILE}" ]; then
    err "Keine produktive Installation gefunden."
    echo "    Datenbank-Datei fehlt: ${DB_FILE}"
    echo "    Bitte zuerst install.sh ausfuehren."
    exit 1
fi
ok "Produktive Installation erkannt (${DB_FILE})."

# --- Schritt 2: Dienste stoppen --------------------------------------------
info "Stoppe Dienste ..."
systemctl stop sentinelbackup sentinelbackup-engine 2>/dev/null || true
ok "Dienste gestoppt."

# --- Schritt 3: Download ---------------------------------------------------
info "Lade ${TARGET_VERSION} von GitHub herunter ..."
# curl mit Retry, falls GitHub kurz nicht erreichbar
curl -fsSL --retry 3 -o "${TMP_TARBALL}" "${DOWNLOAD_URL}"
ok "Download abgeschlossen: ${TMP_TARBALL}"

# --- Schritt 4: Entpacken --------------------------------------------------
info "Entpacke nach ${TMP_EXTRACT}/ ..."
rm -rf "${TMP_EXTRACT}"
mkdir -p "${TMP_EXTRACT}"
tar xzf "${TMP_TARBALL}" -C "${TMP_EXTRACT}"

# Entpackten Ordnernamen finden (z.B. SentinelBackup-v1.1.0)
EXTRACTED_DIR="$(find "${TMP_EXTRACT}" -maxdepth 1 -type d -name 'SentinelBackup*' | head -1)"
if [ -z "${EXTRACTED_DIR}" ]; then
    err "Entpackter Ordner nicht gefunden. Abbruch."
    rm -rf "${TMP_EXTRACT}" "${TMP_TARBALL}"
    exit 1
fi
ok "Entpackt nach: ${EXTRACTED_DIR}"

# --- Schritt 5: Kritischer Rsync (mit Excludes) ----------------------------
info "Synchronisiere Code nach ${INSTALL_DIR}/ (DB + Cache geschuetzt) ..."
rsync -av --delete \
    --exclude='database.db' \
    --exclude='database.db-wal' \
    --exclude='database.db-shm' \
    --exclude='cache/' \
    --exclude='venv/' \
    --exclude='ssh_pub_key.txt' \
    --exclude='engine.log' \
    "${EXTRACTED_DIR}/" "${INSTALL_DIR}/"
ok "Code aktualisiert. Datenbank, SSH-Key und Cache unangetastet."

# --- Schritt 6: Systemd-Service-Dateien aktualisieren ----------------------
info "Aktualisiere Systemd-Service-Dateien ..."
if [ -f "${INSTALL_DIR}/install.sh" ]; then
    # Service-Dateien aus dem neuen Code extrahieren, falls install.sh
    # sie neu schreibt. Da wir install.sh nicht vollstaendig ausfuehren,
    # kopieren wir die Service-Definitionen manuell, falls sie im Repo
    # mitgeliefert werden. Die install.sh enthaelt die Templates, aber
    # wir koennen sie nicht einfach ausfuehren (wuerde venv etc. neu anlegen).
    # Daher: Service-Dateien bleiben bestehen, daemon-reload reicht aus
    # fuer kleinere Aenderungen. Bei grossen Service-Aenderungen muss
    # install.sh erneut ausgefuehrt werden.
    warn "Bei gaenderten Service-Definitionen bitte install.sh erneut ausfuehren."
fi
systemctl daemon-reload
ok "Systemd daemon-reload ausgefuehrt."

# --- Schritt 7: Dienste starten + Cleanup ----------------------------------
info "Starte Dienste ..."
systemctl start sentinelbackup
systemctl start sentinelbackup-engine
ok "Dienste gestartet."

info "Raeume temporaere Dateien auf ..."
rm -rf "${TMP_EXTRACT}" "${TMP_TARBALL}"
ok "Aufraeumen abgeschlossen."

echo ""
echo "${GREEN}${BOLD}==================================================${RESET}"
echo "${GREEN}${BOLD} Update auf ${TARGET_VERSION} erfolgreich!${RESET}"
echo "${GREEN}${BOLD}==================================================${RESET}"
echo ""
echo "  Web-UI:        http://$(hostname -I | awk '{print $1}')/"
echo "  Health-Check:  http://$(hostname -I | awk '{print $1}')/health"
echo ""
echo "${CYAN}SentinelBackup Update by WrkDrvn - fertig.${RESET}"
