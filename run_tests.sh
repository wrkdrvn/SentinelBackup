#!/bin/bash
set -e

# SentinelBackup - Test Runner
# Author: WrkDrvn
# Version: 0.1.0
#
# Aktiviert das virtuelle Environment und fuehrt pytest aus.
# Der produktive Gunicorn-Dienst wird NICHT angefasst.

VENV_DIR="/opt/sentinelbackup/venv"
BACKEND_DIR="/opt/sentinelbackup/backend"

# Fallback auf Repo-Layout, wenn noch nicht installiert wurde
if [ ! -d "$VENV_DIR" ]; then
    VENV_DIR="$(cd "$(dirname "$0")" && pwd)/venv"
    BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)/backend"
fi

echo "========================================"
echo " SentinelBackup - Test Suite"
echo "========================================"

# VENV aktivieren
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "[ERROR] Virtualenv nicht gefunden: $VENV_DIR"
    echo "        Bitte zuerst install.sh ausfuehren."
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

cd "$BACKEND_DIR"
pytest -v

deactivate
echo "========================================"
echo " Tests abgeschlossen."
echo "========================================"
