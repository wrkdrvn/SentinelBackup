# Known Issues

Diese Datei dokumentiert bekannte, nicht-kritische Bugs und UX-Unschönheiten in SentinelBackup, die in zukünftigen Versionen behoben werden.

---

## Formular-Werte bleiben nach dem Anlegen eines Servers bestehen

**Komponente:** WebUI (Server hinzufügen)

**Beschreibung:** Wenn man den ersten Server angelegt hat und das Modal öffnet, um einen zweiten Server hinzuzufügen, sind die Eingabefelder (IP, Pfad, Port etc.) noch mit den Werten des ersten Servers gefüllt. Es werden nicht die Standardwerte (z.B. Port 22, leere Pfade) angezeigt.

**Status:** Behoben (Reset-Handler `hx-on::after-request` in `backend/templates/servers.html`, eingeführt in Commit `5ec72b3`). Die Eingabefelder setzen sich nach erfolgreichem Speichern auf ihre HTML-Defaultwerte zurück. Hinweis: vorbelegte Werte wie Backup-Pfad `/opt/sentinel_agent/data/` sind **gewollte** Standards des Agents, keine Restwerte.

**Stand:** 2026-10-11 beim Code-Review verifiziert (Logik-Review; kein Browser-Test). Sollten Restwerte auftreten, bitte melden.
