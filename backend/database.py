#!/usr/bin/env python3
# SentinelBackup - Database Engine Setup
# Author: WrkDrvn
# Version: 0.7.1
#
# Definiert das zentrale SQLAlchemy-Objekt und stellt init_db(app)
# bereit. db wird hier (nicht in app.py) definiert, um
# Zirkularimport-Probleme zwischen app.py <-> models.py zu vermeiden.

import sqlalchemy
import sqlalchemy.event
from sqlalchemy import text
from flask_sqlalchemy import SQLAlchemy

# Zentrales SQLAlchemy-Objekt - ohne App gebunden.
# Wird spaeter via init_db(app) an die Flask-App angebunden.
db = SQLAlchemy()


def init_db(app):
    """Bindet SQLAlchemy an die App, aktiviert WAL und legt Tabellen an."""
    db.init_app(app)

    with app.app_context():
        engine = db.engine

        # WAL-Modus direkt nach jedem Connect aktivieren.
        # Verhindert "database is locked" bei nebenlaeufigen Gunicorn-Workern.
        @sqlalchemy.event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _connection_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA busy_timeout=5000;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.close()

        # Tabellen anlegen, falls noch nicht vorhanden
        db.create_all()

        # SystemState als Singleton sicherstellen: genau ein Eintrag muss
        # existieren. Er dient als DB-basierter Trigger zwischen Web- und
        # Daemon-Prozess (ersetzt das fruehere /tmp-Triggerfile).
        from models import SystemState
        if not SystemState.query.first():
            db.session.add(SystemState(trigger_run=False))
            db.session.commit()

        # Sicheres Hinzufuegen der neuen Spalte fuer den SSH-Port.
        # db.create_all() fuegt einer bestehenden Tabelle keine neuen Spalten
        # hinzu, daher hier eine idempotente ALTER-Table-Migration.
        try:
            db.session.execute(
                text("ALTER TABLE servers ADD COLUMN ssh_port INTEGER DEFAULT 22")
            )
            db.session.commit()
        except Exception:
            # Spalte existiert bereits -> Rollback, ganz normal weiter.
            db.session.rollback()

        # Sicheres Hinzufuegen der neuen Spalte fuer das Routine-Intervall.
        try:
            db.session.execute(
                text("ALTER TABLE system_state ADD COLUMN routine_interval_minutes INTEGER DEFAULT 60")
            )
            db.session.commit()
        except Exception:
            # Spalte existiert bereits -> Rollback, ganz normal weiter.
            db.session.rollback()

        # Sicheres Hinzufuegen der NAS-SSH-Port-Spalte.
        try:
            db.session.execute(
                text("ALTER TABLE nas_config ADD COLUMN ssh_port INTEGER DEFAULT 2022")
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Default-Shutdown-Befehl auf Synology-sicher 'sudo synoshutdown --shutdown'
        # aktualisieren (ersetzt das alte 'poweroff', das sudo erforderte).
        try:
            db.session.execute(
                text("UPDATE nas_config SET ssh_shutdown_cmd = 'sudo synoshutdown --shutdown' "
                     "WHERE ssh_shutdown_cmd = 'poweroff' OR ssh_shutdown_cmd IS NULL")
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
