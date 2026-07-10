# Changelog

All notable changes to SentinelBackup are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2024-07-10

### Added
- Initial stable release of SentinelBackup.
- WebUI based orchestration (HTMX/Flask).
- Unified Agent setup for MailCow, ISPConfig and Docker/Portainer.
- Wake-on-LAN, SMB3 mounting and Synology safe-shutdown integration.
- Forensic logging and connection diagnostics.

## [0.7.3] - 2026-07-07

### Fixed
- Critical: MailCow agent hangs on execution. Re-added missing target directory parameter to official script call.

## [0.7.2] - 2026-07-07

### Changed
- Updated default NAS shutdown command to 'sudo synoshutdown --shutdown'.
- Added help text in WebUI explaining how to configure passwordless sudo for Synology NAS.

## [0.7.1] - 2026-07-07

### Fixed
- Critical: Removed --delete flag from NAS push to prevent accidental deletion of other server backups on the target.
- Fixed NAS shutdown: Added dedicated SSH port field (default 2022) and changed default command to Synology-safe 'synoshutdown -s' to prevent sudo password prompts.

## [0.7.0] - 2026-07-07

### Fixed
- Critical Data Loss Bug: Removed destructive cache wipe at the start of the cycle. Local cache is now strictly preserved until a successful NAS push is verified.

## [0.6.9] - 2026-07-07

### Fixed
- Critical: NAS mount now waits for SMB port 445 instead of just ICMP ping, preventing mount errors during NAS boot sequence.
- Fixed SSH shutdown command execution to properly handle sudo commands for unprivileged users.

## [0.6.8] - 2026-07-07

### Changed
- Removed dead code: Deleted obsolete download routes for individual client scripts from app.py.

## [0.6.6] - 2026-07-07

### Fixed
- Critical: MailCow agent no longer prompts for backup location. Target path is now correctly passed as an argument.

## [0.6.5] - 2026-07-07

### Fixed
- Critical: Added forensic logging to rsync and cache deletion to trace data loss issues.
- NAS shutdown process is now logged.
### Changed
- Unified WrkDrvn ASCII art banner.

## [0.6.3] - 2026-07-07

### Added
- Configurable routine interval via WebUI dropdown (10m to 5h). Changes take effect immediately without restarting services.

## [0.6.2] - 2026-07-07

### Fixed
- Critical: Restored periodic routine backup cycle (runs every 60 minutes) which was accidentally removed.
- Added auto-restart policy to systemd service for higher daemon stability.
- Added daemon heartbeat log every 6 hours to prove the service is alive.
### Added
- Agent setup now prompts user for individual backup time (HH:MM) to prevent network bandwidth bottlenecks.

## [0.6.1] - 2026-07-07

### Changed
- Directories in local cache and on NAS now use the server's hostname instead of the numeric database ID for better readability.

## [0.6.0] - 2026-07-07

### Added
- Configurable SSH port for each server (defaults to 22).

### Fixed
- Critical: Engine now properly throws and logs errors on SSH timeouts or connection failures instead of silently skipping the server.

## [0.5.5] - 2026-07-07

### Fixed
- Fixed MailCow agent syntax error by passing correct arguments ('backup all') to the official script.

## [0.5.4] - 2026-07-07

### Fixed
- Critical: MailCow agent no longer fails on timestamp mismatches. Success is now determined by checking if backup files actually exist, rather than exact folder names.

## [0.5.3] - 2026-07-07

### Fixed
- Critical: NAS test now enforces SMB3 protocol, which is required by Synology and modern NAS systems.
- Fixed potential authentication failures caused by hidden whitespaces in WebUI input fields.

## [0.5.2] - 2026-07-07

### Changed
- Upgraded NAS connection test to a 3-step network diagnostic tool (Ping -> Port 445 -> SMB Login) to help users identify firewall or routing issues.

## [0.5.1] - 2026-07-07

### Fixed
- Improved NAS connection test error messages. Raw Samba errors are now translated into actionable German hints for the user.

## [0.5.0] - 2026-07-07

### Added
- NAS connection test feature in WebUI (validates IP, share, and credentials before saving).

### Fixed
- Synchronized version strings across all components to prevent UI confusion.

## [0.4.2] - 2026-07-07

### Fixed
- Fixed blind daemon loop: Logs are now written even when waiting for triggers, without breaking the WebUI.

## [0.4.0] - 2026-07-07

### Fixed
- Critical: Manual trigger from WebUI failed silently due to filesystem permissions.

### Changed
- Replaced filesystem trigger file with a robust SQLite database trigger mechanism.

## [0.3.8] - 2026-07-07

### Changed
- Added final WrkDrvn ASCII art branding with frame to the agent setup script.

## [0.3.7] - 2026-07-07

### Changed
- ISPConfig agent now uses a temporary .my.cnf file for mysqldump to avoid exposing the password in the process list.

## [0.3.6] - 2026-07-07

### Fixed
- Critical agent crash: Rewrote agent setup to use strictly quoted heredocs, fixing pipe syntax errors.
- Agent now uses a separate .env file for credentials for better security and script stability.

## [0.3.5] - 2026-07-07

### Fixed
- Critical: Background engine can now read servers from database (fixed missing Flask application context).

### Added
- Dynamic LOG_LEVEL support (INFO/DEBUG) via environment variable.
- Detailed DEBUG logging for SSH and Rsync commands.

## [0.3.4] - 2026-07-07

### Fixed
- Critical 502 crash: Moved logging initialization inside run_full_cycle() to prevent permission errors during web app import.
- Fixed log directory permissions in install.sh.

## [0.3.3] - 2026-07-07

### Fixed
- Implemented professional file logging to /var/log/sentinelbackup/engine.log.
- Replaced print statements with Python logging module.
- Added RotatingFileHandler to prevent disk space exhaustion.

## [0.3.2] - 2026-07-07

### Added
- Interactive Agent Installer (setup_agent.sh) for non-technical users.
- Standardized agent paths (/opt/sentinel_agent/) for easier WebUI configuration.

### Changed
- Replaced raw client scripts with the unified agent setup.

## [0.3.1] - 2026-07-07

### Fixed
- Manual trigger button now returns UI alert instead of raw JSON.
- Fixed SSH Key copy button JavaScript execution.

### Added
- Live activity widget on dashboard showing latest backup logs.

## [0.3.0] - 2026-07-07

### Added
- Background orchestrator engine (systemd service)
- SSH flag checking and rsync pull logic
- Wake-on-LAN, CIFS mount, and NAS push logic
- NAS shutdown routine
- Manual trigger button in WebUI
- Process locking to prevent concurrent runs

## [0.2.3] - 2026-07-07

### Fixed
- Updated unit tests to match current UI structure and correct version string.

## [0.2.2] - 2026-07-07

### Fixed
- Critical deployment errors in install.sh
- Added missing Flask-SQLAlchemy dependency
- HTMX form submission logic (removed redirects, implemented partial rendering)

## [0.2.1] - 2026-07-07

### Added
- SQLite Database with WAL mode
- Server management UI
- NAS configuration UI
- Client script download center

## [0.1.0] - 2026-07-07

### Added
- Basic project structure
- Nginx reverse proxy
- Flask baseline
- SSH key generation
- Automated installation
