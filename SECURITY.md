# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :--- |
| latest (main) | ✅ |
| older releases | ❌ |

Project RAPHAEL is a local-first CLI; there is no hosted service. Security fixes are
shipped on `main` and the latest tagged release.

## Reporting a Vulnerability

If you discover a security issue (e.g. path traversal in vault ingest, unsafe deserialization,
secret leakage, daemon auth weakness), please **do not open a public issue**.

Instead, report it privately:

- **Email:** ferdi@nawasena.site
- **GitHub:** open a private vulnerability report at
  https://github.com/FerrDxD/raphael-cli/security/advisories/new

Please include:

- A description of the issue and its impact
- Steps to reproduce (minimal example)
- Affected version / commit
- Any suggested fix (optional)

## What to expect

- Acknowledgement within 72 hours
- A status update within 7 days
- Coordinated disclosure once a fix is available

## Scope

In scope:

- The `rph` CLI, `raphaeld` daemon, and storage layer (`~/.raphael`)
- File ingestion and parsing (PDF/DOCX/XLSX/MD/TXT/CSV/JSON)
- Daemon HTTP API and watcher

Out of scope:

- Vulnerabilities in upstream dependencies (report to those projects)
- Issues requiring a compromised local machine or vault directory
- Optional cloud sync (`sync/cloud.py`) configured with user-supplied endpoints

## Local-first note

RAPHAEL never sends note content off-machine unless you explicitly enable the optional
cloud sync. Your vault lives at `~/.raphael` (or `$RAPHAEL_HOME`); keep it protected
like any other personal data.