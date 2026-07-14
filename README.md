+---------------------------------------------------------------+
|                                                               |
| ██╗    ██╗██████╗ ██╗  ██╗██████╗ ██████╗ ██╗   ██╗███╗   ██╗ |
| ██║    ██║██╔══██╗██║ ██╔╝██╔══██╗██╔══██╗██║   ██║████╗  ██║ |
| ██║ █╗ ██║██████╔╝█████╔╝ ██║  ██║██████╔╝██║   ██║██╔██╗ ██║ |
| ██║███╗██║██╔══██╗██╔═██╗ ██║  ██║██╔══██╗╚██╗ ██╔╝██║╚██╗██║ |
| ╚███╔███╔╝██║  ██║██║  ██║██████╔╝██║  ██║ ╚████╔╝ ██║ ╚████║ |
|  ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═══╝ |
|                                                               |
+---------------------------------------------------------------+


# SentinelBackup

![Python](https://img.shields.io/badge/Python-3-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-lightgrey)
![Platform](https://img.shields.io/badge/Platform-Ubuntu%2024.04-orange)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

> A lightweight, pull-based backup orchestrator for MailCow, ISPConfig, and Docker hosts — with Synology NAS support.

---

## What is it?

SentinelBackup is a self-hosted backup orchestrator designed for homelab and small-business infrastructure. It runs on a central Linux VM and **pulls** backups from remote servers over SSH — no agent daemon, no inbound ports on targets. Backups land on a Synology NAS that is woken via Wake-on-LAN, mounted over SMB3, written to, and safely shut down after each cycle.

Everything is configured through a web UI. No CLI gymnastics.

### How it works

1. **Agent on targets:** A single interactive script (`setup_agent.sh`, downloadable from the WebUI) is run once on each target server. It detects the type (MailCow / ISPConfig / Docker), generates a backup script, and installs a cronjob.
2. **Pull cycle:** The SentinelBackup daemon SSHes into each target, checks for a `.backup_done` flag file, and pulls the data via `rsync` into a local cache.
3. **NAS push:** The NAS is woken via WoL, the SMB share is mounted (CIFS, SMB3 enforced), the cache is pushed via `rsync`, the share is unmounted, and the NAS is shut down — all automatically.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask, HTMX |
| Database | SQLite (WAL mode) |
| Web Server | Nginx (reverse proxy) |
| App Server | Gunicorn |
| Agent / Client | Bash |
| OS | Ubuntu 24.04 LTS |

Runs comfortably on **1 vCPU / 2 GB RAM**.

---

## Key Features

- **Full WebUI configuration** — servers, NAS settings, schedules, all managed from the browser. No config files to edit.
- **Unified Agent** — one script for all target types. Interactive setup, automatic cronjob registration, per-server backup time.
- **Synology-first** — enforces SMB3 protocol, supports non-standard SSH ports (default 2022), and uses `synoshutdown` for safe passwordless shutdown.
- **Forensic logging** — every `rsync` command, SSH check, cache size, and mount operation is logged with full detail to `/var/log/sentinelbackup/engine.log` (rotating, 5 MB × 3).
- **Built-in diagnostics** — the WebUI includes a 3-step NAS connection test (Ping → Port 445 → SMB login) with translated, actionable error hints.
- **Configurable routine interval** — choose 10 min to 5 h from the dashboard dropdown. Changes take effect immediately, no service restart.
- **Manual trigger** — kick off a backup cycle on demand from the WebUI.

---

## System Requirements

| Resource | Minimum |
|---|---|
| CPU | 1 vCPU |
| RAM | 2 GB |
| Disk | 64 GB (cache + OS) |
| OS | Ubuntu 24.04 LTS (x86_64) |

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/WrkDrvn/SentinelBackup.git
cd SentinelBackup

# Run the installer (requires root)
sudo bash install.sh
```

The installer will:

- Install all system dependencies (`nginx`, `python3-venv`, `rsync`, `smbclient`, `wakeonlan`, …).
- Deploy the application to `/opt/sentinelbackup/`.
- Generate an SSH ED25519 keypair for pull-based access.
- Configure Nginx as a reverse proxy to `127.0.0.1:5000`.
- Register and start two systemd services: `sentinelbackup` (WebUI) and `sentinelbackup-engine` (daemon).

Once finished, open `http://<server-ip>/` in your browser.

---

## Setting Up Target Servers

1. In the WebUI, navigate to **Client Scripts** and download `setup_agent.sh`.
2. Copy it to the target server:
   ```bash
   scp setup_agent.sh root@target:/root/
   ```
3. Run it as root:
   ```bash
   sudo bash setup_agent.sh
   ```
4. The installer walks you through server type selection (ISPConfig / MailCow / Docker), asks for type-specific parameters, generates `/opt/sentinel_agent/run.sh`, and registers a cronjob at a time of your choosing.
5. Back in the SentinelBackup WebUI, go to **Server → Hinzufügen**. The standard paths are pre-filled:
   - **Backup path:** `/opt/sentinel_agent/data/`
   - **Flag path:** `/opt/sentinel_agent/.backup_done`
6. Copy the **public SSH key** from the dashboard into the target's `authorized_keys`.

---

## Synology Preparation

SentinelBackup is tuned for Synology NAS out of the box. Two things to prepare:

### 1. SSH Port

Synology DSM uses **port 22** for SSH by default. Enter this in the NAS configuration form.

### 2. Passwordless Shutdown

The default shutdown command is `sudo /usr/syno/sbin/synoshutdown --shutdown`. For this to work non-interactively over SSH, create a sudoers entry on the NAS:

```bash
# SSH into the NAS as your admin user, then:
sudo visudo -f /etc/sudoers
or
sudo vim /etc/sudoers
```

Add the following line (replace [username] with your NAS SSH user):

```
[username] ALL=(root) NOPASSWD: /usr/syno/sbin/synoshutdown --shutdown
```

Save and exit. SentinelBackup can now shut down the NAS after each backup cycle without a password prompt.

---

## Credits & License

**Author:** WrkDrvn

SentinelBackup is released under the **MIT License**.

```
MIT License

Copyright (c) 2026 WrkDrvn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
