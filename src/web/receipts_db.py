"""SQLite persistence for Belege extractions and line-item categories.

Uses the stdlib sqlite3 module only. Database path:
``{EXPENSE_DATA_DIR or user data dir}/receipts.db``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app_paths import expense_data_dir, user_data_dir

_lock = Lock()
_SCHEMA_READY_FOR: str | None = None

RECEIPTS_DB_FILENAME = "receipts.db"
RECEIPT_ITEM_CATEGORIES_JSON = "receipt_item_categories.json"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    file_name TEXT,
    file_hash TEXT,
    status TEXT,
    error TEXT,
    merchant TEXT,
    date_extracted TEXT,
    date_override TEXT,
    total REAL,
    subtotal REAL,
    tax REAL,
    merchant_category TEXT,
    notes TEXT,
    raw_text TEXT,
    extracted_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL,
    sort_index INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    quantity REAL,
    amount REAL,
    category TEXT,
    FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS receipt_item_categories (
    description TEXT PRIMARY KEY,
    category TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipt_items_receipt_id
    ON receipt_items(receipt_id);
"""


def data_root() -> Path:
    """Resolve writable root (EXPENSE_DATA_DIR or user data dir)."""
    override = expense_data_dir()
    if override is not None:
        return override
    return user_data_dir()


def db_path() -> Path:
    """Return path to receipts.db under the data root."""
    return data_root() / RECEIPTS_DB_FILENAME


def categories_json_path() -> Path:
    """Legacy JSON map path (kept as backup after migration)."""
    return data_root() / RECEIPT_ITEM_CATEGORIES_JSON


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_file(path: str | Path) -> str:
    """SHA-256 hex digest of file contents (empty string if unreadable)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def connect() -> sqlite3.Connection:
    """Open a connection with foreign keys enabled."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create tables if missing; migrate categories JSON when table is empty."""
    global _SCHEMA_READY_FOR
    own = conn is None
    if own:
        conn = connect()
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        migrate_categories_from_json(conn)
        _SCHEMA_READY_FOR = str(db_path())
    finally:
        if own:
            conn.close()


def ensure_db() -> sqlite3.Connection:
    """Return a connection after ensuring schema + one-time JSON import."""
    global _SCHEMA_READY_FOR
    with _lock:
        path = str(db_path())
        conn = connect()
        if _SCHEMA_READY_FOR != path:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
            migrate_categories_from_json(conn)
            _SCHEMA_READY_FOR = path
        return conn


def reset_schema_flag() -> None:
    """Clear cached schema-ready flag (tests that swap EXPENSE_DATA_DIR)."""
    global _SCHEMA_READY_FOR
    with _lock:
        _SCHEMA_READY_FOR = None


def migrate_categories_from_json(conn: sqlite3.Connection) -> int:
    """Import receipt_item_categories.json into DB when the table is empty.

    Does not delete the JSON file (left as backup). Returns imported pair count.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM receipt_item_categories"
    ).fetchone()
    if row and int(row["n"]) > 0:
        return 0
    path = categories_json_path()
    if not path.is_file():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0
    if not isinstance(raw, dict):
        return 0
    imported = 0
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        conn.execute(
            "INSERT OR IGNORE INTO receipt_item_categories(description, category) "
            "VALUES (?, ?)",
            (key.strip(), value.strip()),
        )
        imported += 1
    conn.commit()
    return imported


def _effective_date(date_override: str | None, date_extracted: str | None) -> str | None:
    override = (date_override or "").strip() if date_override else ""
    if override:
        return override
    extracted = (date_extracted or "").strip() if date_extracted else ""
    return extracted or None


def _row_from_db(
    receipt: sqlite3.Row,
    items: list[sqlite3.Row] | None = None,
) -> dict[str, Any]:
    """Build the UI import-row dict shape from DB rows."""
    date_extracted = receipt["date_extracted"]
    date_override = receipt["date_override"]
    effective = _effective_date(date_override, date_extracted)
    status = receipt["status"] or ""
    error = receipt["error"] or ""
    item_dicts: list[dict] = []
    if items:
        for item in items:
            item_dicts.append(
                {
                    "description": item["description"],
                    "quantity": item["quantity"],
                    "amount": item["amount"],
                    "category": item["category"] or "",
                }
            )
    result: dict[str, Any] | None
    if status == "Extracted" or item_dicts or receipt["merchant"] or date_extracted:
        result = {
            "merchant": receipt["merchant"],
            "date": effective,
            "date_extracted": date_extracted,
            "date_override": date_override,
            "total": receipt["total"],
            "subtotal": receipt["subtotal"],
            "tax": receipt["tax"],
            "merchant_category": receipt["merchant_category"],
            "notes": receipt["notes"] or "",
            "raw_text": receipt["raw_text"] or "",
            "items": item_dicts,
        }
    else:
        result = None
    return {
        "path": receipt["path"],
        "file": receipt["file_name"] or Path(receipt["path"] or "").name,
        "status": status,
        "error": error,
        "merchant": receipt["merchant"],
        "date": effective,
        "date_extracted": date_extracted,
        "date_override": date_override,
        "total": receipt["total"],
        "item_count": len(item_dicts),
        "file_hash": receipt["file_hash"],
        "result": result,
    }


def _load_items(conn: sqlite3.Connection, receipt_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY sort_index, id",
            (receipt_id,),
        ).fetchall()
    )


def list_rows() -> list[dict]:
    """Return all receipt rows (with items), newest effective date first."""
    conn = ensure_db()
    try:
        receipts = conn.execute("SELECT * FROM receipts").fetchall()
        out: list[dict] = []
        for receipt in receipts:
            items = _load_items(conn, int(receipt["id"]))
            out.append(_row_from_db(receipt, items))
        # Sort by effective date descending; undated last ("" sorts last under reverse).
        out.sort(key=lambda r: (r.get("date") or ""), reverse=True)
        return out
    finally:
        conn.close()


def get_row_by_path(path: str | Path) -> dict | None:
    """Find a receipt row by resolved path equality."""
    try:
        target = str(Path(path).resolve())
    except (OSError, RuntimeError, ValueError):
        target = str(path)
    conn = ensure_db()
    try:
        receipt = conn.execute(
            "SELECT * FROM receipts WHERE path = ?", (target,)
        ).fetchone()
        if receipt is None:
            # Fallback: compare resolved paths for legacy / relative storage.
            for row in conn.execute("SELECT * FROM receipts").fetchall():
                raw = row["path"]
                if not raw:
                    continue
                try:
                    if str(Path(raw).resolve()) == target:
                        receipt = row
                        break
                except (OSError, RuntimeError, ValueError):
                    if str(raw) == str(path):
                        receipt = row
                        break
        if receipt is None:
            return None
        items = _load_items(conn, int(receipt["id"]))
        return _row_from_db(receipt, items)
    finally:
        conn.close()


def delete_row_by_path(path: str | Path) -> bool:
    """Delete receipt (and cascaded items) for ``path``. Returns True if deleted."""
    try:
        target = str(Path(path).resolve())
    except (OSError, RuntimeError, ValueError):
        target = str(path)
    conn = ensure_db()
    try:
        # Match exact or any resolve-equal path.
        candidates = conn.execute("SELECT id, path FROM receipts").fetchall()
        deleted = False
        for row in candidates:
            raw = row["path"]
            same = False
            if raw == target or str(raw) == str(path):
                same = True
            else:
                try:
                    same = str(Path(raw).resolve()) == target
                except (OSError, RuntimeError, ValueError):
                    same = False
            if same:
                conn.execute("DELETE FROM receipts WHERE id = ?", (row["id"],))
                deleted = True
        if deleted:
            conn.commit()
        return deleted
    finally:
        conn.close()


def prune_missing() -> int:
    """Delete DB rows whose receipt file no longer exists. Returns count removed."""
    conn = ensure_db()
    try:
        rows = conn.execute("SELECT id, path FROM receipts").fetchall()
        removed = 0
        for row in rows:
            raw = row["path"]
            if not raw:
                continue
            try:
                exists = Path(raw).is_file()
            except OSError:
                exists = False
            if not exists:
                conn.execute("DELETE FROM receipts WHERE id = ?", (row["id"],))
                removed += 1
        if removed:
            conn.commit()
        return removed
    finally:
        conn.close()


def clear_all_receipts() -> None:
    """Delete all receipt rows (tests). Categories table is left intact."""
    conn = ensure_db()
    try:
        conn.execute("DELETE FROM receipt_items")
        conn.execute("DELETE FROM receipts")
        conn.commit()
    finally:
        conn.close()


def set_date_override(path: str | Path, date_override: str | None) -> bool:
    """Set or clear date_override for a receipt. Empty/None clears. Returns True if found."""
    value = (date_override or "").strip() or None
    try:
        target = str(Path(path).resolve())
    except (OSError, RuntimeError, ValueError):
        target = str(path)
    conn = ensure_db()
    try:
        receipt = None
        for row in conn.execute("SELECT id, path FROM receipts").fetchall():
            raw = row["path"]
            if raw == target or str(raw) == str(path):
                receipt = row
                break
            try:
                if str(Path(raw).resolve()) == target:
                    receipt = row
                    break
            except (OSError, RuntimeError, ValueError):
                continue
        if receipt is None:
            return False
        conn.execute(
            "UPDATE receipts SET date_override = ?, updated_at = ? WHERE id = ?",
            (value, _utc_now_iso(), receipt["id"]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _normalize_path(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError, ValueError):
        return str(path)


def upsert_row_from_import_dict(
    row: dict,
    *,
    file_hash: str | None = None,
    preserve_date_override: bool = True,
) -> None:
    """Insert or replace a receipt from a ``receipt_import_row``-shaped dict.

    Replaces extracted fields and line items. When ``preserve_date_override``
    is True (default), an existing date_override is kept across re-OCR.
    Categories table is never wiped.
    """
    path = row.get("path")
    if not path:
        return
    stored_path = _normalize_path(path)
    file_name = row.get("file") or Path(str(path)).name
    status = row.get("status") or ""
    error = row.get("error") or ""
    result = row.get("result") or {}
    if not isinstance(result, dict):
        result = {}

    # Prefer explicit extracted date from result / row; do not treat override as extracted.
    date_extracted = (
        result.get("date_extracted")
        if isinstance(result.get("date_extracted"), str)
        else None
    )
    if not date_extracted:
        # Fresh OCR puts date in result["date"] / row["date"].
        candidate = result.get("date") if result else row.get("date")
        if isinstance(candidate, str) and candidate.strip():
            date_extracted = candidate.strip()
        else:
            date_extracted = None

    merchant = result.get("merchant") if result else row.get("merchant")
    if merchant is None:
        merchant = row.get("merchant")
    total = result.get("total") if result else row.get("total")
    if total is None:
        total = row.get("total")
    subtotal = result.get("subtotal")
    tax = result.get("tax")
    merchant_category = result.get("merchant_category")
    notes = result.get("notes") or ""
    raw_text = result.get("raw_text") or ""
    items = result.get("items") if result else None
    if not isinstance(items, list):
        items = []

    if file_hash is None:
        file_hash = row.get("file_hash")
    if not file_hash and Path(stored_path).is_file():
        file_hash = hash_file(stored_path)
    file_hash = file_hash or ""

    now = _utc_now_iso()
    conn = ensure_db()
    try:
        existing = conn.execute(
            "SELECT id, date_override, extracted_at FROM receipts WHERE path = ?",
            (stored_path,),
        ).fetchone()
        # Also try resolve-equal match if exact path miss.
        if existing is None:
            for cand in conn.execute(
                "SELECT id, path, date_override, extracted_at FROM receipts"
            ).fetchall():
                try:
                    if str(Path(cand["path"]).resolve()) == stored_path:
                        existing = cand
                        break
                except (OSError, RuntimeError, ValueError):
                    continue

        kept_override = None
        extracted_at = now
        if existing is not None:
            if preserve_date_override:
                kept_override = existing["date_override"]
            extracted_at = existing["extracted_at"] or now
            # Allow caller to set override via row.
            if "date_override" in row and not preserve_date_override:
                kept_override = row.get("date_override")
            # If row carries an explicit override and we are not preserving from DB,
            # use it; when preserving, DB wins unless row explicitly clears — DB wins.
            receipt_id = int(existing["id"])
            conn.execute(
                """
                UPDATE receipts SET
                    path = ?,
                    file_name = ?,
                    file_hash = ?,
                    status = ?,
                    error = ?,
                    merchant = ?,
                    date_extracted = ?,
                    date_override = ?,
                    total = ?,
                    subtotal = ?,
                    tax = ?,
                    merchant_category = ?,
                    notes = ?,
                    raw_text = ?,
                    extracted_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    stored_path,
                    file_name,
                    file_hash,
                    status,
                    error,
                    merchant,
                    date_extracted,
                    kept_override,
                    total,
                    subtotal,
                    tax,
                    merchant_category,
                    notes,
                    raw_text,
                    extracted_at,
                    now,
                    receipt_id,
                ),
            )
            conn.execute(
                "DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,)
            )
        else:
            override = row.get("date_override")
            if isinstance(override, str):
                override = override.strip() or None
            else:
                override = None
            cur = conn.execute(
                """
                INSERT INTO receipts (
                    path, file_name, file_hash, status, error, merchant,
                    date_extracted, date_override, total, subtotal, tax,
                    merchant_category, notes, raw_text, extracted_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_path,
                    file_name,
                    file_hash,
                    status,
                    error,
                    merchant,
                    date_extracted,
                    override,
                    total,
                    subtotal,
                    tax,
                    merchant_category,
                    notes,
                    raw_text,
                    now,
                    now,
                ),
            )
            receipt_id = int(cur.lastrowid)

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            conn.execute(
                """
                INSERT INTO receipt_items (
                    receipt_id, sort_index, description, quantity, amount, category
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    index,
                    item.get("description"),
                    item.get("quantity"),
                    item.get("amount"),
                    item.get("category") or "",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def load_categories() -> dict[str, str]:
    """Return description→category map from the DB."""
    conn = ensure_db()
    try:
        rows = conn.execute(
            "SELECT description, category FROM receipt_item_categories"
        ).fetchall()
        return {r["description"]: r["category"] for r in rows}
    finally:
        conn.close()


def save_categories(mapping: dict[str, str]) -> None:
    """Replace all category assignments with ``mapping``."""
    clean: dict[str, str] = {}
    for key, value in (mapping or {}).items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        clean[key.strip()] = value.strip()
    conn = ensure_db()
    try:
        conn.execute("DELETE FROM receipt_item_categories")
        for desc in sorted(clean.keys(), key=str.casefold):
            conn.execute(
                "INSERT INTO receipt_item_categories(description, category) VALUES (?, ?)",
                (desc, clean[desc]),
            )
        conn.commit()
    finally:
        conn.close()


def set_category(description: str, category: str) -> None:
    """Assign or clear a single description→category mapping."""
    description = (description or "").strip()
    category = (category or "").strip()
    if not description:
        return
    conn = ensure_db()
    try:
        if not category:
            conn.execute(
                "DELETE FROM receipt_item_categories WHERE description = ?",
                (description,),
            )
        else:
            conn.execute(
                "INSERT INTO receipt_item_categories(description, category) VALUES (?, ?) "
                "ON CONFLICT(description) DO UPDATE SET category = excluded.category",
                (description, category),
            )
        conn.commit()
    finally:
        conn.close()
