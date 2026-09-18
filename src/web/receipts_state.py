"""In-memory last-run Belege extraction state and path helpers."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from app_paths import expense_data_dir, user_data_dir

_lock = Lock()
_last_rows: list[dict] = []

NO_DATE_MONTH = "ohne Datum"


def data_root_for_receipts() -> Path:
    """Resolve writable root (EXPENSE_DATA_DIR or user data dir)."""
    override = expense_data_dir()
    if override is not None:
        return override
    return user_data_dir()


def receipts_dir() -> Path:
    """Return ``{data_root}/receipts`` for uploaded/scanned Belege."""
    return data_root_for_receipts() / "receipts"


def receipts_uploads_dir() -> Path:
    """Return ``{data_root}/receipts/uploads`` for web uploads."""
    return receipts_dir() / "uploads"


def ensure_receipts_dirs() -> Path:
    """Create receipts and uploads directories; return the receipts root."""
    root = receipts_dir()
    root.mkdir(parents=True, exist_ok=True)
    receipts_uploads_dir().mkdir(parents=True, exist_ok=True)
    return root


def _row_date(row: dict) -> str:
    """Best available ISO date string for a receipt row."""
    date = row.get("date")
    if isinstance(date, str) and date.strip():
        return date.strip()
    result = row.get("result") or {}
    date = result.get("date")
    if isinstance(date, str) and date.strip():
        return date.strip()
    return ""


def sort_receipt_rows_by_date_desc(rows: list[dict] | None) -> list[dict]:
    """Sort extraction rows by date descending; undated rows last."""
    # Empty date sorts last under reverse=True because "" is the smallest string.
    return sorted(rows or [], key=_row_date, reverse=True)


def set_last_rows(rows: list[dict]) -> None:
    """Replace the process-wide last extraction result list (date desc)."""
    global _last_rows
    with _lock:
        _last_rows = sort_receipt_rows_by_date_desc(list(rows or []))


def get_last_rows() -> list[dict]:
    """Return a copy of the last extraction rows."""
    with _lock:
        return list(_last_rows)


def clear_last_rows() -> None:
    """Drop cached rows (tests)."""
    set_last_rows([])


def find_row_by_path(path: str | Path) -> dict | None:
    """Find a cached import row whose path resolves equal to ``path``."""
    try:
        target = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    with _lock:
        for row in _last_rows:
            raw = row.get("path")
            if not raw:
                continue
            try:
                if Path(raw).resolve() == target:
                    return dict(row)
            except (OSError, RuntimeError, ValueError):
                if str(raw) == str(path):
                    return dict(row)
    return None


def resolve_under_receipts(path_str: str | Path) -> Path | None:
    """Resolve ``path_str`` and ensure it stays under the receipts root.

    Returns the resolved absolute path, or ``None`` on traversal / invalid path.
    Does not require the file to exist.
    """
    if path_str is None or str(path_str).strip() == "":
        return None
    try:
        root = receipts_dir().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        candidate = Path(str(path_str))
        if not candidate.is_absolute():
            # Allow relative paths from receipts root (e.g. uploads/foo.pdf).
            candidate = root / candidate
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def safe_upload_filename(filename: str | None) -> str:
    """Return a basename-only upload name (no directories)."""
    name = Path(filename or "").name.strip()
    return name or "upload.bin"


def unique_target(directory: Path, filename: str) -> Path:
    """Pick a non-colliding path under ``directory`` for ``filename"."""
    directory.mkdir(parents=True, exist_ok=True)
    base = safe_upload_filename(filename)
    target = directory / base
    if not target.exists():
        return target
    stem = Path(base).stem
    suffix = Path(base).suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def format_status_de(status: str | None) -> str:
    """German label for an import-row status."""
    if status == "Extracted":
        return "Extrahiert"
    if status == "Failed":
        return "Fehler"
    return status or ""


def format_total_de(total) -> str:
    """Format a total amount for German UI, or empty string."""
    if total is None:
        return ""
    try:
        return f"{float(total):.2f} €"
    except (TypeError, ValueError):
        return str(total)


def format_date_de(date_str: str | None) -> str:
    """Show YYYY-MM-DD as DD.MM.YYYY when possible."""
    text = (date_str or "").strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        y, m, d = text.split("-")
        return f"{d}.{m}.{y}"
    return text


def format_month_de(month_str: str | None) -> str:
    """Show YYYY-MM as MM.YYYY when possible."""
    text = (month_str or "").strip()
    if text == NO_DATE_MONTH:
        return NO_DATE_MONTH
    if len(text) == 7 and text[4] == "-":
        y, m = text.split("-")
        return f"{m}.{y}"
    return text


def _month_sort_key(month: str) -> tuple:
    """Sort key: dated months newest-first, undated last."""
    if month == NO_DATE_MONTH:
        return (1, 0)
    if len(month) == 7 and month[4] == "-":
        try:
            return (0, -(int(month[:4]) * 100 + int(month[5:7])))
        except ValueError:
            return (0, 0)
    return (0, 0)


def aggregate_receipt_items(rows: list[dict] | None) -> list[dict]:
    """Sum line-item amounts by calendar month + exact description text.

    Matching is case- and whitespace-sensitive (as extracted). Only successful
    extractions contribute. Each result row has month, description, and amount
    (no quantity). Sorted by month (newest first), then description.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for row in rows or []:
        if row.get("status") != "Extracted":
            continue
        result = row.get("result") or {}
        date = row.get("date") or result.get("date")
        if isinstance(date, str) and len(date) >= 7 and date[4] == "-":
            month = date[:7]
        else:
            month = NO_DATE_MONTH
        for item in result.get("items") or []:
            description = item.get("description")
            if not isinstance(description, str) or description == "":
                continue
            key = (month, description)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "month": month,
                    "description": description,
                    "amount": 0.0,
                }
                buckets[key] = bucket
            amount = item.get("amount")
            try:
                if amount is not None:
                    bucket["amount"] += float(amount)
            except (TypeError, ValueError):
                pass

    aggregated = list(buckets.values())
    aggregated.sort(key=lambda r: (_month_sort_key(r["month"]), r["description"]))
    return aggregated
