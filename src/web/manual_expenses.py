"""Manually entered expenses stored as a parser-compatible CSV in BankStatements.

File: ``BankStatements/manuelle_ausgaben.csv`` with header ``Datum;Name;Betrag``,
``;`` separator, UTF-8, ISO dates and negative EU amounts (``-12,50``). The existing
``Parser`` reads it like any other statement; no special casing is needed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from app_paths import statements_dir

# Must not start with "bank" (that prefix triggers the headerless bank layout).
MANUAL_FILENAME = "manuelle_ausgaben.csv"
HEADER = ["Datum", "Name", "Betrag"]
DELIMITER = ";"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def manual_expenses_path() -> Path:
    return statements_dir() / MANUAL_FILENAME


def parse_amount_input(raw: str) -> float | None:
    """Parse a user-entered amount (EU or US style) into a positive float.

    Accepts ``12,50``, ``1.234,56``, ``12.50``, ``1,234.56``, ``1.234`` (EU thousands)
    and a leading ``-``/``+`` or ``€``. Returns the absolute value rounded to cents,
    or ``None`` when the input is not a number.
    """
    s = (raw or "").strip().replace("€", "").replace(" ", "").replace("\u00a0", "")
    if s[:1] in "-+" and s:
        s = s[1:]
    if not s:
        return None
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # EU 1.234,56
        else:
            s = s.replace(",", "")  # US 1,234.56
    elif "," in s:
        if s.count(",") == 1:
            s = s.replace(",", ".")  # EU decimal comma
        else:
            s = s.replace(",", "")  # 1,234,567
    elif "." in s:
        head, _, tail = s.rpartition(".")
        if s.count(".") > 1 or len(tail) == 3:
            s = s.replace(".", "")  # EU thousands: 1.234 / 1.234.567
    if not _NUMBER_RE.match(s):
        return None
    return round(abs(float(s)), 2)


def format_amount_eu(value: float) -> str:
    """Format an expense as a negative EU amount, e.g. 12.5 -> ``-12,50``."""
    return f"-{abs(value):.2f}".replace(".", ",")


def validate_iso_date(raw: str) -> str | None:
    value = (raw or "").strip()
    if not _ISO_DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def validate_rows(dates, descriptions, amounts):
    """Validate submitted form rows.

    Returns ``(entries, error)``; entries is a list of ``(iso_date, description,
    amount)`` with a positive amount. Rows where description AND amount are empty
    are ignored. Any invalid row yields an error and no entries.
    """
    count = max(len(dates), len(descriptions), len(amounts))
    entries = []
    for i in range(count):
        raw_date = dates[i] if i < len(dates) else ""
        desc = descriptions[i] if i < len(descriptions) else ""
        raw_amount = amounts[i] if i < len(amounts) else ""
        desc = " ".join((desc or "").split())
        raw_amount = (raw_amount or "").strip()
        if not desc and not raw_amount:
            continue
        line = i + 1
        iso = validate_iso_date(raw_date)
        if iso is None:
            return [], f"Zeile {line}: Ungültiges Datum."
        if not desc:
            return [], f"Zeile {line}: Beschreibung fehlt."
        amount = parse_amount_input(raw_amount)
        if amount is None:
            return [], f"Zeile {line}: Betrag „{raw_amount}“ ist ungültig."
        if amount == 0:
            return [], f"Zeile {line}: Betrag darf nicht 0 sein."
        entries.append((iso, desc, amount))
    if not entries:
        return [], "Keine Ausgaben eingegeben."
    return entries, None


def append_manual_expenses(entries, path: Path | None = None) -> Path:
    """Append validated entries; creates the file with header if missing."""
    target = path or manual_expenses_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    exists = target.is_file() and target.stat().st_size > 0
    prefix = ""
    if exists:
        with open(target, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) not in (b"\n", b"\r"):
                prefix = "\n"
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=DELIMITER, lineterminator="\n")
    if not exists:
        writer.writerow(HEADER)
    for iso, desc, amount in entries:
        writer.writerow([iso, desc, format_amount_eu(amount)])
    with open(target, "a", encoding="utf-8", newline="") as fh:
        fh.write(prefix + buf.getvalue())
    return target


def _row_token(row) -> str:
    return hashlib.sha256("\x1f".join(row).encode("utf-8")).hexdigest()[:16]


def _read_rows(path: Path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh, delimiter=DELIMITER))


def list_manual_expenses(path: Path | None = None) -> list[dict]:
    """Return data rows (without header) with their index and a content token."""
    target = path or manual_expenses_path()
    if not target.is_file():
        return []
    try:
        rows = _read_rows(target)
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    result = []
    for index, row in enumerate(rows[1:]):
        if not row:
            continue
        result.append(
            {
                "index": index,
                "token": _row_token(row),
                "date": row[0] if len(row) > 0 else "",
                "description": row[1] if len(row) > 1 else "",
                "amount": row[2] if len(row) > 2 else "",
            }
        )
    return result


def delete_manual_expense(index: int, token: str, path: Path | None = None) -> bool:
    """Remove the data row at ``index`` if its content still matches ``token``."""
    target = path or manual_expenses_path()
    if not target.is_file():
        return False
    rows = _read_rows(target)
    data_pos = index + 1  # skip header
    if index < 0 or data_pos >= len(rows) or not rows[data_pos]:
        return False
    if _row_token(rows[data_pos]) != token:
        return False
    del rows[data_pos]
    buf = io.StringIO()
    csv.writer(buf, delimiter=DELIMITER, lineterminator="\n").writerows(rows)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".manuelle_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(buf.getvalue())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True
