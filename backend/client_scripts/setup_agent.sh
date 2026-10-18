#!/bin/bash
# SentinelBackup - Agent Installer
# Author: WrkDrvn
# Version: 1.3.0
#
# Interaktiver Installer fuer Zielserver. Richtet den SentinelBackup Agent
# unter /opt/sentinel_agent/ ein, schreibt Konfiguration in eine separate
# .env-Datei und erzeugt das typspezifische Backup-Skript run.sh.
#
# WICHTIG (Design ab 0.3.6): run.sh wird ueber ein striktquotiertes
# Heredoc (<< 'EOF') geschrieben. Dadurch interpretiert die Bash beim
# Schreiben KEINE Variablen, Pipes oder Backticks. Alle dynamischen Werte
# (Passwoerter, Pfade) liegen in /opt/sentinel_agent/.env und werden zur
# Laufzeit von run.sh per `source` geladen.
#
# WICHTIG (ab 0.3.7): ISPConfig nutzt eine temporaere .my.cnf
# (/tmp/.sb_mysql.cnf) statt -p auf der Kommandozeile, damit das Passwort
# nicht in der Prozessliste auftaucht.
#
# Aufruf:  sudo bash setup_agent.sh

set -e

# --- Konstanten -------------------------------------------------------------
AGENT_DIR="/opt/sentinel_agent"
DATA_DIR="${AGENT_DIR}/data"
FLAG_FILE="${AGENT_DIR}/.backup_done"
ENV_FILE="${AGENT_DIR}/.env"
RUN_SCRIPT="${AGENT_DIR}/run.sh"
CRON_LINE="0 1 * * * ${RUN_SCRIPT} > /dev/null 2>&1"

# --- Farb-/Format-Helfer (tput) --------------------------------------------
if [ -t 1 ]; then
    BOLD="$(tput bold)"
    GREEN="$(tput setaf 2)"
    RED="$(tput setaf 1)"
    YELLOW="$(tput setaf 3)"
    CYAN="$(tput setaf 6)"
    RESET="$(tput sgr0)"
else
    BOLD=""; GREEN=""; RED=""; YELLOW=""; CYAN=""; RESET=""
fi

ok()    { echo "${GREEN}[OK]${RESET} $1"; }
err()   { echo "${RED}[FEHLER]${RESET} $1"; }
info()  { echo "${CYAN}[i]${RESET} $1"; }
warn()  { echo "${YELLOW}[!]${RESET} $1"; }

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
echo "SentinelBackup Agent Installer Version 1.3.0"
echo ""
echo ""

# --- Preflight: Root -------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    err "Dieses Skript muss als root ausgefuehrt werden."
    echo "    Bitte starten mit: ${BOLD}sudo bash setup_agent.sh${RESET}"
    exit 1
fi

info "Root-Rechte bestaetigt."
echo

# --- Servertyp abfragen ----------------------------------------------------
echo "${BOLD}Bitte waehlen Sie den Servertyp:${RESET}"
echo "  ${CYAN}1)${RESET} ISPConfig"
echo "  ${CYAN}2)${RESET} MailCow"
echo "  ${CYAN}3)${RESET} Docker / Portainer"
echo "  ${CYAN}4)${RESET} Custom Server (Individuelle DBs, Ordner, Skripte)"
echo
while true; do
    read -r -p "Auswahl [1-4]: " CHOICE
    case "${CHOICE}" in
        1) SERVER_TYPE="ispconfig"; break ;;
        2) SERVER_TYPE="mailcow"; break ;;
        3) SERVER_TYPE="docker"; break ;;
        4) SERVER_TYPE="custom"; break ;;
        *) warn "Ungueltige Eingabe. Bitte 1, 2, 3 oder 4." ;;
    esac
done
echo
ok "Servertyp: ${BOLD}${SERVER_TYPE}${RESET}"

# --- Verzeichnisstruktur anlegen ------------------------------------------
info "Lege Agent-Verzeichnis an: ${AGENT_DIR}"
mkdir -p "${DATA_DIR}"
chmod 0755 "${AGENT_DIR}"

# --- .env leeren / vorbereiten --------------------------------------------
: > "${ENV_FILE}"

# ===========================================================================
# Typspezifische Konfiguration abfragen + .env schreiben
# ===========================================================================
case "${SERVER_TYPE}" in
    ispconfig)
        echo
        read -r -s -p "MySQL Root-Passwort eingeben: " MYSQL_ROOT_PW
        echo
        if [ -z "${MYSQL_ROOT_PW}" ]; then
            warn "Leeres Passwort - mysqldump wird ohne -p aufgerufen."
        fi
        # Passwort ALLEINE in .env - kein Escaping noetig im run.sh
        {
            echo "# SentinelBackup Agent Konfiguration"
            echo "# Autor: WrkDrvn - NICHT manuell aendern."
            echo "DB_PASS=\"${MYSQL_ROOT_PW}\""
        } > "${ENV_FILE}"
        ok "Zugangsdaten nach ${ENV_FILE} geschrieben."
        ;;

    mailcow)
        echo
        read -r -p "MailCow Pfad [${CYAN}/opt/mailcow-dockerized${RESET}]: " MAILCOW_PATH
        MAILCOW_PATH="${MAILCOW_PATH:-/opt/mailcow-dockerized}"
        if [ ! -d "${MAILCOW_PATH}" ]; then
            warn "Pfad existiert (noch) nicht: ${MAILCOW_PATH} - trotzdem fortgesetzt."
        fi
        {
            echo "# SentinelBackup Agent Konfiguration"
            echo "# Autor: WrkDrvn - NICHT manuell aendern."
            echo "MAILCOW_DIR=\"${MAILCOW_PATH}\""
        } > "${ENV_FILE}"
        ok "Konfiguration nach ${ENV_FILE} geschrieben."
        ;;

    docker)
        echo
        read -r -p "Compose-Basispfad [${CYAN}/opt${RESET}]: " COMPOSE_BASE
        COMPOSE_BASE="${COMPOSE_BASE:-/opt}"
        if [ ! -d "${COMPOSE_BASE}" ]; then
            warn "Pfad existiert (noch) nicht: ${COMPOSE_BASE} - trotzdem fortgesetzt."
        fi
        {
            echo "# SentinelBackup Agent Konfiguration"
            echo "# Autor: WrkDrvn - NICHT manuell aendern."
            echo "COMPOSE_BASE=\"${COMPOSE_BASE}\""
        } > "${ENV_FILE}"
        ok "Konfiguration nach ${ENV_FILE} geschrieben."
        ;;

    custom)
        echo
        echo "${BOLD}Custom Agent Konfiguration${RESET}"
        echo "Alle Werte werden in ${ENV_FILE} gespeichert."
        echo

        read -r -s -p "MySQL Root-Passwort (leer = kein DB-Backup): " CUSTOM_DB_PASS
        echo
        read -r -p "DB-Namen (Komma-getrennt, leer = keine DBs): " CUSTOM_DB_NAMES
        read -r -p "Zu sichernde Verzeichnisse (Komma-getrennt, leer = keine): " CUSTOM_DIRS
        read -r -p "Pre-Script Pfad (leer = keins): " CUSTOM_PRE_SCRIPT
        read -r -p "Post-Script Pfad (leer = keins): " CUSTOM_POST_SCRIPT

        {
            echo "# SentinelBackup Agent Konfiguration (Custom)"
            echo "# Autor: WrkDrvn - NICHT manuell aendern."
            echo "DB_PASS=\"${CUSTOM_DB_PASS}\""
            echo "DB_NAMES=\"${CUSTOM_DB_NAMES}\""
            echo "DIRS=\"${CUSTOM_DIRS}\""
            echo "PRE_SCRIPT=\"${CUSTOM_PRE_SCRIPT}\""
            echo "POST_SCRIPT=\"${CUSTOM_POST_SCRIPT}\""
        } > "${ENV_FILE}"
        ok "Custom-Konfiguration nach ${ENV_FILE} geschrieben."
        ;;
esac

# --- .env schuetzen (Passwort!) -------------------------------------------
chmod 600 "${ENV_FILE}"
ok ".env auf 600 restriktiert."

# ===========================================================================
# run.sh erzeugen - striktquotiertes Heredoc (<< 'EOF')
# Die Single-Quotes verhindern JEGLICHE Interpretation durch die Bash beim
# Schreiben. Variablen, Pipes, Backticks landen 1:1 in der Datei und werden
# erst zur Laufzeit von run.sh ausgewertet.
# ===========================================================================
info "Erzeuge Backup-Skript (striktquotiertes Heredoc): ${RUN_SCRIPT}"

case "${SERVER_TYPE}" in
    ispconfig)
        cat << 'EOF' > "${RUN_SCRIPT}"
#!/bin/bash
# SentinelBackup Agent - ISPConfig Backup-Skript
# Autor: WrkDrvn - vom Installer erzeugt, NICHT manuell aendern.
set -e

# Lade die Zugangsdaten
source /opt/sentinel_agent/.env

DATA_DIR="/opt/sentinel_agent/data"
FLAG_FILE="/opt/sentinel_agent/.backup_done"
OUT_DIR="${DATA_DIR}.new"
OLD_DATA="${DATA_DIR}.old"
MYSQL_CNF="/tmp/.sb_mysql.cnf"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)

# Alte Flag loeschen (Sicherheitsnetz)
rm -f "$FLAG_FILE"

# Das laufende DATA_DIR bleibt UNANGETASTET, bis das neue Backup sicher
# fertig ist. Erst beim atomaren Swap wird ausgetauscht - ein
# fehlgeschlagenes Backup hinterlaesst weiterhin die alte Sicherung.
rm -rf "$OUT_DIR" "$OLD_DATA"
mkdir -p "$OUT_DIR"

# Temporaere .my.cnf erzeugen, damit das Passwort nicht in der
# Prozessliste (ps aux) auftaucht. Datei wird sofort nach mysqldump
# wieder geloescht.
echo "[client]" > "$MYSQL_CNF"
echo "user=root" >> "$MYSQL_CNF"
echo "password=$DB_PASS" >> "$MYSQL_CNF"
chmod 600 "$MYSQL_CNF"

echo "[agent] Sichere MySQL-Datenbanken ..."
mysqldump --defaults-extra-file="$MYSQL_CNF" --all-databases | gzip > "$OUT_DIR/all_databases_$TIMESTAMP.sql.gz"

# Credentials-Datei sofort wieder entfernen
rm -f "$MYSQL_CNF"

echo "[agent] Sichere Web- und Mail-Verzeichnisse ..."
tar -czpf "$OUT_DIR/web_vmail_$TIMESTAMP.tar.gz" /var/www/ /var/vmail/ 2>/dev/null || true

echo "[agent] Sichere ISPConfig Konfiguration ..."
tar -czpf "$OUT_DIR/ispconfig_config_$TIMESTAMP.tar.gz" /usr/local/ispconfig/ 2>/dev/null || true

echo "[agent] Bereinige alte Backups (behalte letzte 3) ..."
cd "$OUT_DIR" && ls -t *.sql.gz *.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm --

# Atomarer Swap: neues Backup erst dann sichtbar, wenn es fertig ist.
if [ -d "$DATA_DIR" ]; then
    mv "$DATA_DIR" "$OLD_DATA"
fi
mv "$OUT_DIR" "$DATA_DIR"
rm -rf "$OLD_DATA"

# Flag setzen bei Erfolg
touch "$FLAG_FILE"
echo "[agent] Backup erfolgreich abgeschlossen."
EOF
        ;;

    mailcow)
        cat << 'EOF' > "${RUN_SCRIPT}"
#!/bin/bash
# SentinelBackup Agent - MailCow Backup-Skript
# Autor: WrkDrvn - vom Installer erzeugt, NICHT manuell aendern.
set -e

# Lade die Konfiguration (MAILCOW_DIR aus der Setup-Eingabe)
source /opt/sentinel_agent/.env
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"

DATA_DIR="/opt/sentinel_agent/data"
FLAG_FILE="/opt/sentinel_agent/.backup_done"
TMP_DATA="${DATA_DIR}.new"
OLD_DATA="${DATA_DIR}.old"

# Alte Flag loeschen (Sicherheitsnetz)
rm -f "$FLAG_FILE"

# Temporaere Verzeichnisse vorbereiten. Das laufende DATA_DIR bleibt
# UNANGETASTET, bis das neue Backup sicher fertig ist (kein Datenverlust
# mehr bei fehlgeschlagenem Backup).
rm -rf "$TMP_DATA" "$OLD_DATA"
mkdir -p "$TMP_DATA"

echo "[agent] Starte offizielles MailCow Backup (Ziel: $TMP_DATA) ..."
cd "$MAILCOW_DIR"
MAILCOW_BACKUP_LOCATION="$TMP_DATA" ./helper-scripts/backup_and_restore.sh backup all

echo "[agent] Ueberpruefe Backup-Ergebnis ..."
if [ -n "$(ls -A "$TMP_DATA" 2>/dev/null)" ]; then
    # Atomarer Swap: neues Backup erst dann sichtbar, wenn es fertig ist.
    if [ -d "$DATA_DIR" ]; then
        mv "$DATA_DIR" "$OLD_DATA"
    fi
    mv "$TMP_DATA" "$DATA_DIR"
    rm -rf "$OLD_DATA"
    touch "$FLAG_FILE"
    echo "[agent] MailCow Backup erfolgreich abgeschlossen."
else
    echo "[agent] FEHLER: MailCow Backup-Verzeichnis ist leer!" >&2
    rm -rf "$TMP_DATA"
    exit 1
fi
EOF
        ;;

    docker)
        cat << 'EOF' > "${RUN_SCRIPT}"
#!/bin/bash
# SentinelBackup Agent - Docker / Portainer Backup-Skript
# Autor: WrkDrvn - vom Installer erzeugt, NICHT manuell aendern.
set -e

# Lade die Konfiguration
source /opt/sentinel_agent/.env

DATA_DIR="/opt/sentinel_agent/data"
FLAG_FILE="/opt/sentinel_agent/.backup_done"
OUT_DIR="${DATA_DIR}.new"
OLD_DATA="${DATA_DIR}.old"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)

# Alte Flag loeschen (Sicherheitsnetz)
rm -f "$FLAG_FILE"

# Das laufende DATA_DIR bleibt UNANGETASTET, bis das neue Backup sicher
# fertig ist (atomarer Swap am Ende).
rm -rf "$OUT_DIR" "$OLD_DATA"
mkdir -p "$OUT_DIR"

echo "[agent] Sammle docker-compose und .env Dateien aus ${COMPOSE_BASE} ..."

# find + xargs (Pipes funktionieren dank striktem Heredoc einwandfrei)
FOUND=$(find "$COMPOSE_BASE" -type f \( \
    -name "docker-compose.yml" -o \
    -name "docker-compose.yaml" -o \
    -name ".env" \
\) 2>/dev/null || true)

if [ -z "$FOUND" ]; then
    echo "[agent] Keine Compose-/.env-Dateien in $COMPOSE_BASE gefunden." >&2
    rm -rf "$OUT_DIR"
    exit 1
fi

# Gefundene Dateien mit Struktur ins Temporaerverzeichnis kopieren
echo "$FOUND" | while IFS= read -r f; do
    rel="${f#${COMPOSE_BASE}/}"
    dest="$OUT_DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    cp -p "$f" "$dest"
done

# Zustand der laufenden Container sichern (Portainer-Kompatibilitaet)
echo "[agent] Sichere Container-Inventory ..."
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' \
    > "$OUT_DIR/container_inventory_$TIMESTAMP.txt" 2>/dev/null || true

# Volume-Inventar sichern (Restauration braucht die Volume-Namen)
echo "[agent] Sichere Docker-Volume-Inventar ..."
docker volume ls --format '{{.Name}}' \
    > "$OUT_DIR/docker_volumes_$TIMESTAMP.txt" 2>/dev/null || true

# Aufgeloeste Stack-Konfiguration sichern: `docker compose config` wertet
# .env-Variablen auf, sodass der Stack auch ohne Original-Umgebung
# rekonstruierbar ist.
echo "[agent] Sichere aufgeloeste Stack-Konfigurationen ..."
mkdir -p "$OUT_DIR/resolved_configs"
echo "$FOUND" | while IFS= read -r f; do
    case "$f" in
        *docker-compose.yml|*docker-compose.yaml)
            rel="${f#${COMPOSE_BASE}/}"
            dest="$OUT_DIR/resolved_configs/${rel}.resolved"
            mkdir -p "$(dirname "$dest")"
            (cd "$(dirname "$f")" && \
                docker compose -f "$(basename "$f")" config) \
                > "$dest" 2>/dev/null || rm -f "$dest"
            ;;
    esac
done

echo "[agent] Bereinige alte Backups (behalte letzte 3) ..."
cd "$OUT_DIR" && ls -t container_inventory_*.txt 2>/dev/null | tail -n +4 | xargs -r rm --
cd "$OUT_DIR" && ls -t docker_volumes_*.txt 2>/dev/null | tail -n +4 | xargs -r rm --

# Atomarer Swap: neues Backup erst dann sichtbar, wenn es fertig ist.
if [ -d "$DATA_DIR" ]; then
    mv "$DATA_DIR" "$OLD_DATA"
fi
mv "$OUT_DIR" "$DATA_DIR"
rm -rf "$OLD_DATA"

# Flag setzen bei Erfolg
touch "$FLAG_FILE"
echo "[agent] Backup erfolgreich abgeschlossen."
EOF
        ;;

    custom)
        cat << 'EOF' > "${RUN_SCRIPT}"
#!/bin/bash
# SentinelBackup Agent - Custom Backup-Skript
# Autor: WrkDrvn - vom Installer erzeugt, NICHT manuell aendern.
set -e

# Lade die Konfiguration
source /opt/sentinel_agent/.env

DATA_DIR="/opt/sentinel_agent/data"
FLAG_FILE="/opt/sentinel_agent/.backup_done"
OUT_DIR="${DATA_DIR}.new"
OLD_DATA="${DATA_DIR}.old"
MYSQL_CNF="/tmp/.sb_mysql.cnf"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)

# Trimmt Leerzeichen am Anfang/Ende eines Listen-Eintrags.
trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# Alte Flag loeschen (Sicherheitsnetz)
rm -f "$FLAG_FILE"

# Das laufende DATA_DIR bleibt UNANGETASTET, bis das neue Backup sicher
# fertig ist (atomarer Swap am Ende).
rm -rf "$OUT_DIR" "$OLD_DATA"
mkdir -p "$OUT_DIR"

# --- Pre-Script ausfuehren ---
if [ -n "$PRE_SCRIPT" ] && [ -x "$PRE_SCRIPT" ]; then
    echo "[agent] Fuehre Pre-Script aus: $PRE_SCRIPT"
    bash "$PRE_SCRIPT" || { echo "[agent] Pre-Script fehlgeschlagen!" >&2; exit 1; }
fi

# --- Datenbank-Sicherung ---
if [ -n "$DB_PASS" ] && [ -n "$DB_NAMES" ]; then
    echo "[agent] Sichere Datenbanken ..."
    echo "[client]" > "$MYSQL_CNF"
    echo "user=root" >> "$MYSQL_CNF"
    echo "password=$DB_PASS" >> "$MYSQL_CNF"
    chmod 600 "$MYSQL_CNF"

    # Komma-getrennte Liste als Array: kein Word-Splitting auf Leerzeichen
    # (frueher zerlegten DB-Namen mit Leerzeichen das Backup).
    IFS=',' read -ra DB_ARRAY <<< "$DB_NAMES"
    for db in "${DB_ARRAY[@]}"; do
        db="$(trim "$db")"
        if [ -z "$db" ]; then
            continue
        fi
        echo "[agent] Dump: $db"
        mysqldump --defaults-extra-file="$MYSQL_CNF" --single-transaction "$db" \
            | gzip > "$OUT_DIR/db_${db}_${TIMESTAMP}.sql.gz"
    done
    rm -f "$MYSQL_CNF"
fi

# --- Verzeichnis-Sicherung ---
if [ -n "$DIRS" ]; then
    echo "[agent] Sichere Verzeichnisse ..."
    # Komma-getrennte Liste als Array: Pfade mit Leerzeichen funktionieren.
    IFS=',' read -ra DIR_ARRAY <<< "$DIRS"
    for dir in "${DIR_ARRAY[@]}"; do
        dir="$(trim "$dir")"
        if [ -z "$dir" ]; then
            continue
        fi
        if [ -d "$dir" ]; then
            dir_name=$(echo "$dir" | tr '/' '_' | sed 's/^_//')
            echo "[agent] tar: $dir -> ${dir_name}.tar.gz"
            tar -czpf "$OUT_DIR/${dir_name}_${TIMESTAMP}.tar.gz" "$dir" 2>/dev/null || true
        else
            echo "[agent] WARN: Verzeichnis nicht gefunden: $dir"
        fi
    done
fi

# --- Bereinige alte Backups (behalte letzte 3) ---
echo "[agent] Bereinige alte Backups (behalte letzte 3) ..."
cd "$OUT_DIR" && ls -t *.sql.gz *.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm --

# --- Post-Script ausfuehren ---
if [ -n "$POST_SCRIPT" ] && [ -x "$POST_SCRIPT" ]; then
    echo "[agent] Fuehre Post-Script aus: $POST_SCRIPT"
    bash "$POST_SCRIPT" || { echo "[agent] Post-Script fehlgeschlagen!" >&2; }
fi

# Atomarer Swap: neues Backup erst dann sichtbar, wenn es fertig ist.
if [ -d "$DATA_DIR" ]; then
    mv "$DATA_DIR" "$OLD_DATA"
fi
mv "$OUT_DIR" "$DATA_DIR"
rm -rf "$OLD_DATA"

# Flag setzen bei Erfolg
touch "$FLAG_FILE"
echo "[agent] Custom Backup erfolgreich abgeschlossen."
EOF
        ;;
esac

# --- run.sh ausfuehrbar machen --------------------------------------------
chmod +x "${RUN_SCRIPT}"
ok "Backup-Skript erzeugt und ausfuehrbar gemacht."

# --- Cronjob einrichten ----------------------------------------------------
echo
read -r -p "Um welche Uhrzeit soll das Script taeglich laufen? (Format HH:MM, Standard 01:00): " CRON_TIME
CRON_TIME="${CRON_TIME:-01:00}"

# Validierung (einfach gehalten)
if [[ ! "$CRON_TIME" =~ ^([0-1][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
    warn "Ungueltiges Format! Nutze Standard 01:00"
    CRON_TIME="01:00"
fi

CRON_HOUR="$(echo "$CRON_TIME" | cut -d: -f1)"
CRON_MIN="$(echo "$CRON_TIME" | cut -d: -f2)"
info "Richte Cronjob ein (taeglich ${CRON_TIME} Uhr) ..."
# Bestehende Crontab holen (root), alte Sentinel-Eintraege entfernen, neuen anhängen
(crontab -l 2>/dev/null | grep -v sentinel_agent; echo "${CRON_MIN} ${CRON_HOUR} * * * ${RUN_SCRIPT} > /dev/null 2>&1") | crontab -
ok "Cronjob registriert: ${CRON_MIN} ${CRON_HOUR} * * * ${RUN_SCRIPT}"

# --- Abschluss -------------------------------------------------------------
echo
echo "${GREEN}${BOLD}==================================================${RESET}"
echo "${GREEN}${BOLD} Agent installiert.${RESET}"
echo "${GREEN}${BOLD}==================================================${RESET}"
echo
echo "Bitte fügen Sie nun im SentinelBackup WebUI diesen Server hinzu."
echo
echo "  ${BOLD}Backup-Pfad:${RESET}  /opt/sentinel_agent/data/"
echo "  ${BOLD}Flag-Pfad:${RESET}    /opt/sentinel_agent/.backup_done"
echo
echo "${CYAN}SentinelBackup Agent Setup by WrkDrvn - fertig.${RESET}"
