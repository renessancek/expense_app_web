# Expense App Desktop

A local, browser-free desktop app for importing, categorizing, and reviewing bank-statement CSV files. The UI is native PySide6 — no browser and no local web server.

## Features

- Scan CSV files from `Documents/BankStatements` or import them manually
- Transaction table with sorting, pagination, and category, month, and live text filters
- Import, create, delete, and restore rules from backups
- Sum categories and export them as an Excel file
- Receipts tab: choose a folder of PDFs or images, extract them locally (no API), and inspect each file from the import list. Nothing is saved to Transactions yet. Digital PDFs with a text layer work as-is; photos and scanned PDFs need Tesseract for OCR.

## Windows 10/11

### Set up and run

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_expense_app.ps1
```

For a Start menu entry:

```powershell
.\install_windows_app.ps1
```

To start the app at login:

```powershell
.\install_windows_app.ps1 -EnableAutostart
```

To remove the Start menu and autostart shortcuts:

```powershell
.\uninstall_windows_app.ps1
```

The app opens a native desktop window. No browser is started.

## Ubuntu/Linux

### Set up and run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x start_expense_app.sh install_ubuntu_app.sh
./start_expense_app.sh
```

For an application-menu entry:

```bash
./install_ubuntu_app.sh
```

For autostart, add the absolute path to `start_expense_app.sh` under **Startup Applications** in system settings.

To remove the application-menu entry:

```bash
chmod +x uninstall_ubuntu_app.sh
./uninstall_ubuntu_app.sh
```

On Ubuntu the app also runs natively with PySide6 and does not need a browser or a local web server.

### Optional Tesseract (OCR)

Photos and scanned PDFs need the `tesseract` binary on `PATH` (languages `deu` and `eng`). Digital PDFs with a text layer work without Tesseract.

If the host should not or cannot install Tesseract itself (immutable distros, no `pacman`/`apt`), set it up with Distrobox: [install-tesseract-linux-distrobox.md](install-tesseract-linux-distrobox.md).

## Tests

Application code lives in [`src/`](src/). Unit tests live in [`tests/`](tests/). From the project root, with the virtualenv active:

```bash
python -m unittest discover -s tests -t .
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

## Build a Windows EXE

After installing dependencies:

```powershell
.\build_windows_exe.ps1
```

The result is `dist\Expense App Desktop\Expense App Desktop.exe`.

## Data

Rules and backups stay in the personal data directory. On Windows that is `%LOCALAPPDATA%\Expense App Desktop`; on Linux `~/.local/share/expense-app-desktop` (or the folder configured via `XDG_DATA_HOME`). The default folder for bank statements is `BankStatements` in the personal Documents folder — on Linux the XDG path is used (for example `~/Dokumente/BankStatements` on German systems, otherwise `~/Documents/BankStatements`).

The app still stores rules internally as `rules.json`. Import accepts JSON **and** CSV, for example a Google Sheets export (File → Download → CSV). A table with `category`/`kategorie` and `keywords`/`keyword` is enough.

Ready-to-import examples live in [`rules/`](rules/): [`rules/rules.json`](rules/rules.json) and [`rules/rules.csv`](rules/rules.csv).

JSON (`rules.json`):

```json
{
  "rules": [
    {
      "category": "Supermarkt",
      "keywords": ["rewe", "aldi", "lidl"]
    },
    {
      "category": "Amazon",
      "keywords": ["amazon"]
    },
    {
      "category": "Internet",
      "keywords": ["telekom", "vodafone"]
    }
  ]
}
```

CSV (`rules.csv`):

```csv
category,keywords
Supermarkt,"rewe, aldi, lidl"
Amazon,amazon
Internet,"telekom, vodafone"
```

Instead of a keywords column, keywords can sit in their own columns, or each row can hold a single keyword. Semicolon-separated German CSV exports work too.

## Web UI (FastAPI + Docker)

In addition to the PySide6 desktop app, this repository includes a small
**server-rendered** FastAPI web UI that reuses the same core modules under
[`src/`](src/) (`parser`, `categorizer`, `expense_data`, `scanner`).

### Dependencies

| Use case | Install |
| --- | --- |
| Desktop (PySide6) | `pip install -r requirements.txt` |
| Web / Docker | `pip install -r requirements-web.txt` |

`requirements-web.txt` does **not** include PySide6 or pyinstaller. The desktop
stack stays unchanged.

### Run locally (without Docker)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
export PYTHONPATH=src
export EXPENSE_DATA_DIR="$PWD/.data"   # optional; defaults to the desktop user-data dir
mkdir -p "$EXPENSE_DATA_DIR/BankStatements"
.venv/bin/uvicorn web.main:app --host 0.0.0.0 --port 8080
```

Open `http://127.0.0.1:8080/`. Optional basic auth:

```bash
export EXPENSE_AUTH_USER=admin
export EXPENSE_AUTH_PASSWORD='change-me'
```

If both auth variables are unset, the UI is open (fine for a trusted LAN).

### Docker / Raspberry Pi / LAN deploy

The image targets **linux/amd64** and **linux/arm64** (multi-arch friendly).

```bash
docker compose up -d --build
```

- Port **8080** is published.
- Persistent volume mounts at **`/data`** inside the container (`EXPENSE_DATA_DIR=/data`).
- Bank statement CSVs: put files in the volume under `BankStatements/`, or upload via the UI.
- Rules are stored as `/data/rules.json` (with backups under `/data/backups/`).

Example with auth via compose environment:

```yaml
environment:
  EXPENSE_DATA_DIR: /data
  EXPENSE_AUTH_USER: admin
  EXPENSE_AUTH_PASSWORD: change-me
```

Then browse to `http://<host-lan-ip>:8080/` from another device on the network.

#### Raspberry Pi notes (German)

Auf einem Raspberry Pi (64-bit OS) reicht dasselbe `docker compose up -d --build`.
Stelle sicher, dass Docker Buildx/BuildKit verfügbar ist, damit `arm64`-Images
korrekt gebaut werden. Die Desktop-App (PySide6) bleibt parallel nutzbar; die
Web-UI ist für den Zugriff im Heimnetz gedacht.

### Web features (MVP)

- CSV upload and optional scan of `BankStatements` under the data directory
- Transaction list with category, month, and text filters (same `ExpenseDataStore.filtered`)
- Rules: list, add, edit keywords, delete, import JSON/CSV, restore latest backup
- Category sums and Excel download (same multi-sheet yearly report as desktop)
- Optional HTTP Basic auth via `EXPENSE_AUTH_USER` / `EXPENSE_AUTH_PASSWORD`
- Receipt OCR page is a stub in the MVP (use the desktop Receipts tab)

### Tests

Core tests are unchanged:

```bash
python -m unittest discover -s tests -t .
```

Web helper tests live in `tests/test_web_helpers.py` and do not require FastAPI
to be installed for path/export assertions (openpyxl/pandas are enough). When
running the full suite without web deps, that module still imports
`web.export_helpers` which only needs pandas/openpyxl already present for the
desktop stack.
