"""Belege extraction state backed by SQLite, plus path helpers."""

from __future__ import annotations

from pathlib import Path

from receipt_extractor import is_quantity_display_line
from web import receipts_db

NO_DATE_MONTH = "ohne Datum"


def data_root_for_receipts() -> Path:
    """Resolve writable root (EXPENSE_DATA_DIR or user data dir)."""
    return receipts_db.data_root()


def receipts_dir() -> Path:
    """Return ``{data_root}/receipts`` for uploaded/scanned Belege."""
    return data_root_for_receipts() / "receipts"


def receipts_uploads_dir() -> Path:
    """Return ``{data_root}/receipts/uploads`` for web uploads."""
    return receipts_dir() / "uploads"


def ensure_receipts_dirs() -> Path:
    """Create receipts and uploads directories; init DB schema; return receipts root."""
    root = receipts_dir()
    root.mkdir(parents=True, exist_ok=True)
    receipts_uploads_dir().mkdir(parents=True, exist_ok=True)
    # First access: create schema and one-time JSON category import.
    conn = receipts_db.ensure_db()
    conn.close()
    return root


def _row_date(row: dict) -> str:
    """Best available ISO date string for a receipt row (override or extracted)."""
    date = row.get("date")
    if isinstance(date, str) and date.strip():
        return date.strip()
    override = row.get("date_override")
    if isinstance(override, str) and override.strip():
        return override.strip()
    result = row.get("result") or {}
    date = result.get("date")
    if isinstance(date, str) and date.strip():
        return date.strip()
    extracted = row.get("date_extracted") or result.get("date_extracted")
    if isinstance(extracted, str) and extracted.strip():
        return extracted.strip()
    return ""


def sort_receipt_rows_by_date_desc(rows: list[dict] | None) -> list[dict]:
    """Sort extraction rows by date descending; undated rows last."""
    # Empty date sorts last under reverse=True because "" is the smallest string.
    return sorted(rows or [], key=_row_date, reverse=True)


def set_last_rows(rows: list[dict]) -> None:
    """Upsert each import row into SQLite (merge by path; does not wipe others)."""
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        receipts_db.upsert_row_from_import_dict(row)


def get_last_rows() -> list[dict]:
    """Return all persisted extraction rows (effective date desc)."""
    return receipts_db.list_rows()


def clear_last_rows() -> None:
    """Drop all cached receipt rows (tests). Categories are kept."""
    receipts_db.clear_all_receipts()


def find_row_by_path(path: str | Path) -> dict | None:
    """Find a cached import row whose path resolves equal to ``path``."""
    return receipts_db.get_row_by_path(path)


def remove_last_row_by_path(path: str | Path) -> bool:
    """Drop a cached extraction row for ``path`` (incl. nested Positionen).

    Returns True if a row was removed. Line items live only inside the row's
    ``result``; removing the row is the cascade delete for Positionen.
    """
    return receipts_db.delete_row_by_path(path)


def prune_missing_receipt_rows() -> int:
    """Drop cache rows whose receipt file no longer exists.

    Prevents orphaned Positionen in "Positionen summiert" after a delete or
    after files were removed outside the app. Returns how many rows were dropped.
    """
    return receipts_db.prune_missing()


def delete_receipt_from_cache(path: str | Path) -> bool:
    """Cascade-remove a receipt from the extraction cache after file delete.

    Removes the matching row (and all nested line items), then prunes any
    other cache entries whose files are missing so Positionen summiert stays
    consistent.
    """
    removed = remove_last_row_by_path(path)
    prune_missing_receipt_rows()
    return removed


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


def receipt_name_taken(filename: str | None) -> bool:
    """True if basename already exists under receipts/ or receipts/uploads/."""
    base = safe_upload_filename(filename)
    if not base:
        return False
    return (receipts_dir() / base).is_file() or (
        receipts_uploads_dir() / base
    ).is_file()


def unique_target(directory: Path, filename: str) -> Path:
    """Pick a non-colliding path under ``directory`` for ``filename``.

    Kept for tests/helpers; Belege upload rejects duplicates instead.
    """
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
    """Sum line items by calendar month, grouping by Belegzeilen category.

    Descriptions mapped in ``load_receipt_item_categories()`` (exact text) that
    share a category are summed into one row labeled with that category name.
    Lines without a category stay as their own description rows (exact text).
    Quantity-display lines and negative amounts are skipped. Returns month
    groups newest-first;
    within each month, rows sorted by amount descending. Each group:
    ``{month, rows: [{description, amount, path, file}, ...], total}``.
    ``path`` / ``file`` are from the first contributing receipt.
    """
    categories = load_receipt_item_categories()
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
        receipt_path = row.get("path")
        receipt_file = row.get("file")
        for item in result.get("items") or []:
            description = item.get("description")
            if not isinstance(description, str) or description == "":
                continue
            description = description.strip()
            if not description:
                continue
            # Skip OCR quantity-display lines like "2 x 2.19" (no product name).
            if is_quantity_display_line(description):
                continue
            amount = item.get("amount")
            try:
                if amount is None:
                    continue
                value = float(amount)
            except (TypeError, ValueError):
                continue
            # Drop refunds / negative OCR lines from Positionen summiert.
            if value < 0:
                continue
            category = categories.get(description)
            if not category:
                item_cat = item.get("category")
                if isinstance(item_cat, str) and item_cat.strip():
                    category = item_cat.strip()
            # Bucket key: category name when assigned, else exact description.
            label = category if category else description
            key = (month, label)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "description": label,
                    "amount": 0.0,
                    "path": receipt_path if isinstance(receipt_path, str) else None,
                    "file": receipt_file if isinstance(receipt_file, str) else None,
                }
                buckets[key] = bucket
            bucket["amount"] += value

    by_month: dict[str, list[dict]] = {}
    for (month, _label), bucket in buckets.items():
        # Remove buckets that somehow stayed negative (should not happen).
        if float(bucket["amount"]) <= 0:
            continue
        by_month.setdefault(month, []).append(bucket)

    groups: list[dict] = []
    for month in sorted(by_month.keys(), key=_month_sort_key):
        month_rows = sorted(
            by_month[month],
            key=lambda r: (-float(r["amount"]), r["description"]),
        )
        groups.append(
            {
                "month": month,
                "rows": month_rows,
                "total": sum(float(r["amount"]) for r in month_rows),
            }
        )
    return groups


RECEIPT_ITEM_CATEGORIES_FILENAME = receipts_db.RECEIPT_ITEM_CATEGORIES_JSON


def receipt_item_categories_path() -> Path:
    """Legacy JSON path (backup after DB migration)."""
    return receipts_db.categories_json_path()


def load_receipt_item_categories() -> dict[str, str]:
    """Return description→category assignments from SQLite."""
    return receipts_db.load_categories()


def save_receipt_item_categories(mapping: dict[str, str]) -> None:
    """Persist description→category map into SQLite."""
    receipts_db.save_categories(mapping)


def set_receipt_item_category(description: str, category: str) -> None:
    """Assign ``category`` to ``description``, or clear when category is empty."""
    receipts_db.set_category(description, category)


def list_receipt_line_category_rows(rows: list[dict] | None = None) -> list[dict]:
    """Unique Belegzeilen with optional category, count, and source receipt.

    Includes descriptions that only exist in the saved mapping (orphaned
    assignments) so they can still be edited. Quantity-display lines are
    skipped. Sorted by description (case-insensitive). ``path`` / ``file``
    come from the first Extracted receipt that contained the line (for a
    detail-page link).
    """
    mapping = load_receipt_item_categories()
    counts: dict[str, int] = {}
    sources: dict[str, tuple[str | None, str | None]] = {}
    for row in rows if rows is not None else get_last_rows():
        if row.get("status") != "Extracted":
            continue
        result = row.get("result") or {}
        receipt_path = row.get("path") if isinstance(row.get("path"), str) else None
        receipt_file = row.get("file") if isinstance(row.get("file"), str) else None
        for item in result.get("items") or []:
            description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                continue
            description = description.strip()
            if is_quantity_display_line(description):
                continue
            counts[description] = counts.get(description, 0) + 1
            if description not in sources:
                sources[description] = (receipt_path, receipt_file)

    all_descriptions = set(counts) | set(mapping)
    out: list[dict] = []
    for description in sorted(all_descriptions, key=str.casefold):
        path, file_name = sources.get(description, (None, None))
        out.append(
            {
                "description": description,
                "category": mapping.get(description, ""),
                "count": counts.get(description, 0),
                "path": path,
                "file": file_name,
            }
        )
    return out
