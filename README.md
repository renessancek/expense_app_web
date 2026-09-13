# Expense App (web)

A local FastAPI web app for importing, categorizing, and reviewing bank-statement CSV files. Server-rendered Jinja2 HTML — no React/SPA, no Postgres. Designed for Docker Compose on a LAN host or Raspberry Pi.

## Features

- Upload CSV bank statements or scan `BankStatements` under the data directory
- Transaction list with category, month, and text filters
- Rules: list, add, edit keywords, delete, import JSON/CSV, restore latest backup
- Auswertung: yearly statistics report on the web (category filter, former Excel sheets)
- **Belege (OCR):** upload images/PDFs or scan `{EXPENSE_DATA_DIR}/receipts`, show merchant/date/total/items — **not** written into Transactions
- Optional HTTP Basic auth via `EXPENSE_AUTH_USER` / `EXPENSE_AUTH_PASSWORD`

## Quick start (Docker Compose)

Primary deploy path for LAN / Pi:

```bash
mkdir -p ./data/BankStatements ./data/receipts
docker compose up -d --build
```

- Port **8080** is published.
- Host data is bind-mounted into **`/data`** in the container (`EXPENSE_DATA_DIR=/data`).
- Default host path: **`./data`** next to `docker-compose.yml`.
- Put statement CSVs under `BankStatements/` in that folder, or upload via the UI.
- Put receipt PDFs/images under `receipts/` (or upload via **Belege**); results stay in the Belege UI only.
- Rules are stored as `/data/rules.json` (backups under `/data/backups/`).

### Belege / Tesseract

The Docker image installs **Tesseract** with German (`deu`) and English (`eng`) language packs. Rebuild the image after pulling so OCR is available:

```bash
git pull
docker compose up -d --build
```

- **Digital PDFs** (text layer) work without Tesseract.
- **Scanned PDFs and photos** need Tesseract in the image (`pypdfium2` renders PDF pages for OCR).
- Local uvicorn without Docker: install Tesseract on the host (`tesseract-ocr`, `tesseract-ocr-deu`, `tesseract-ocr-eng`) if you want OCR; digital PDFs still work via `pdfplumber` alone.

### External disk on a Raspberry Pi

Point the bind mount at a folder on the attached drive (example):

```bash
sudo mkdir -p /mnt/hdd/expense-app/BankStatements /mnt/hdd/expense-app/receipts
# Ensure Docker can write there (adjust user/group to match your setup):
# sudo chown -R 1000:1000 /mnt/hdd/expense-app

export EXPENSE_HOST_DATA_DIR=/mnt/hdd/expense-app
docker compose up -d --build
```

Or put the same variable in a `.env` file beside `docker-compose.yml`:

```env
EXPENSE_HOST_DATA_DIR=/mnt/hdd/expense-app
```

Replace `/mnt/hdd/expense-app` with your real mount path. Inside the container the path stays `/data`; only the host side changes.

Optional auth in `docker-compose.yml`:

```yaml
environment:
  EXPENSE_DATA_DIR: /data
  EXPENSE_AUTH_USER: admin
  EXPENSE_AUTH_PASSWORD: change-me
```

Then open `http://<host-lan-ip>:8080/` from another device on the network. If both auth variables are unset, the UI is open (fine for a trusted LAN).

The image targets **linux/amd64** and **linux/arm64** (multi-arch friendly). On a Raspberry Pi (64-bit OS), the same `docker compose up -d --build` works when Docker Buildx/BuildKit is available.

## Local run (uvicorn)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export PYTHONPATH=src
export EXPENSE_DATA_DIR="$PWD/.data"   # optional; defaults to a user data dir
mkdir -p "$EXPENSE_DATA_DIR/BankStatements" "$EXPENSE_DATA_DIR/receipts"
.venv/bin/uvicorn web.main:app --host 0.0.0.0 --port 8080
```

Open `http://127.0.0.1:8080/`.

## Data layout

When `EXPENSE_DATA_DIR` is set (Docker and recommended for local web), the app uses:

- `{EXPENSE_DATA_DIR}/rules.json` and `{EXPENSE_DATA_DIR}/backups/`
- `{EXPENSE_DATA_DIR}/BankStatements/` for scanned CSVs
- `{EXPENSE_DATA_DIR}/receipts/` and `{EXPENSE_DATA_DIR}/receipts/uploads/` for Belege (OCR)

Without the override, paths fall back to a per-user data directory (`expense-app` under XDG on Linux, or the platform equivalent).

The app stores rules as `rules.json`. Import accepts JSON **and** CSV (for example a Google Sheets export). A table with `category`/`kategorie` and `keywords`/`keyword` is enough.

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

## Architecture

- `src/web/` — FastAPI app, deps, templates, report helpers, Belege state
- Core modules under `src/`: `parser`, `categorizer`, `expense_data`, `scanner`, `receipt_extractor`, `app_paths`
- Single dependency file: `requirements.txt` (no desktop / PySide6 stack)

## Tests

Application code lives in [`src/`](src/). Unit tests live in [`tests/`](tests/). From the project root, with the virtualenv active:

```bash
python -m unittest discover -s tests -t .
```

Web helper tests live in `tests/test_web_helpers.py`. Belege path-guard tests live in `tests/test_receipts_web.py`.
