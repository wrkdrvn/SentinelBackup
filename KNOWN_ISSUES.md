# Known Issues

Diese Datei dokumentiert bekannte, nicht-kritische Bugs und UX-Unschönheiten in SentinelBackup, die in zukünftigen Versionen behoben werden.

---

## Formular-Werte bleiben nach dem Anlegen eines Servers bestehen

**Komponente:** WebUI (Server hinzufügen)

**Beschreibung:** Wenn man den ersten Server angelegt hat und das Modal öffnet, um einen zweiten Server hinzuzufügen, sind die Eingabefelder (IP, Pfad, Port etc.) noch mit den Werten des ersten Servers gefüllt. Es werden nicht die Standardwerte (z.B. Port 22, leere Pfade) angezeigt.

**Status:** Open (Kosmetik)

**Workaround:** Die alten Werte einfach manuell löschen oder überschreiben, bevor man den neuen Server speichert.
