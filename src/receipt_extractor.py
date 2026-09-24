"""Local receipt text extraction and heuristic parsing. No network calls."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_PAGES = 4
MIN_PAGE_CHARS = 12
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
PDF_SUFFIX = ".pdf"
RECEIPT_SUFFIXES = IMAGE_SUFFIXES | {PDF_SUFFIX}

_AMOUNT_TOKEN = r"-?\d{1,3}(?:\.\d{3})+,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2}"
_AMOUNT_RE = re.compile(rf"(?<![\d.,])({_AMOUNT_TOKEN})(?![\d.,])")
_TAX_CLASS = r"[A-Za-z]"
_TRAILING_AMOUNT_RE = re.compile(
    rf"^(?P<desc>.*?)(?:\s+(?P<qty>\d+(?:[.,]\d+)?)\s*[x×])?\s+(?P<amount>{_AMOUNT_TOKEN})(?:\s+{_TAX_CLASS})?\s*$"
)
_LEADING_QTY_RE = re.compile(r"^(?P<qty>\d+(?:[.,]\d+)?)\s*[x×]\s+(?P<desc>.+)$", re.I)
_QTY_DISPLAY_ONLY_RE = re.compile(
    rf"^\d+(?:[.,]\d+)?\s*[x×]\s*(?:{_AMOUNT_TOKEN})\s*$",
    re.I,
)
_QTY_UNIT_LINE_RE = re.compile(
    rf"^(?:(?P<desc>.+?)\s+)?"
    rf"(?P<qty>\d+(?:[.,]\d+)?)\s*[x×]\s+"
    rf"(?P<unit>{_AMOUNT_TOKEN})\s+"
    rf"(?P<amount>{_AMOUNT_TOKEN})"
    rf"(?:\s+{_TAX_CLASS})?\s*$",
    re.I,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_EU_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")
_MONTH_NAME_TO_NUM = {
    "januar": 1, "january": 1,
    "februar": 2, "february": 2,
    "maerz": 3, "märz": 3, "march": 3,
    "april": 4,
    "mai": 5, "may": 5,
    "juni": 6, "june": 6,
    "juli": 7, "july": 7,
    "august": 8,
    "september": 9,
    "oktober": 10, "october": 10,
    "november": 11,
    "dezember": 12, "december": 12,
}
_NAMED_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\.?\s+"
    r"(?P<month>Januar|February|Februar|März|Maerz|March|April|Mai|May|"
    r"Juni|June|Juli|July|August|September|Oktober|October|November|"
    r"Dezember|December)\s+"
    r"(?P<year>\d{4})\b",
    re.I,
)
_AMAZON_EU_RE = re.compile(r"Amazon\s+EU\b", re.I)
_AMAZON_WORD_RE = re.compile(r"\bAmazon\b", re.I)
_BESTELLNUMMER_RE = re.compile(r"\bBestellnummer\b", re.I)
_ASIN_RE = re.compile(r"\bASIN:\s*B[0-9A-Z]{9,10}\b", re.I)
_AMAZON_ITEM_RE = re.compile(
    rf"^(?P<desc>.+?)\s+(?P<qty>\d+)\s+"
    rf"(?P<net>{_AMOUNT_TOKEN})\s*€?\s+"
    rf"(?P<vat>\d+(?:[.,]\d+)?)\s*%\s+"
    rf"(?P<gross_unit>{_AMOUNT_TOKEN})\s*€?\s+"
    rf"(?P<gross>{_AMOUNT_TOKEN})\s*€?\s*$",
    re.I,
)
_AMAZON_SHIPPING_RE = re.compile(
    rf"^Versandkosten\b.*?\s+(?P<a1>{_AMOUNT_TOKEN})\s*€?"
    rf"(?:\s+(?P<a2>{_AMOUNT_TOKEN})\s*€?)?"
    rf"(?:\s+(?P<a3>{_AMOUNT_TOKEN})\s*€?)?\s*$",
    re.I,
)
_TOTAL_RE = re.compile(r"\b(summe|gesamt(?:betrag|summe)?|total|endbetrag|zu\s*zahlen)\b", re.I)
_SUBTOTAL_RE = re.compile(r"\b(zwischensumme|subtotal|netto)\b", re.I)
_TAX_RE = re.compile(r"\b(mwst|m\.?\s*w\.?\s*st\.?|ust|u\.?\s*st\.?|vat|mehrwertsteuer)\b", re.I)
_GENERIC_HEADER_RE = re.compile(
    r"^(rechnung|kassenbon|kassenbeleg|beleg|quittung|receipt|invoice|bon)\b",
    re.I,
)
_ADDRESS_RE = re.compile(r"\b\d{5}\s+\S+")
_STREET_RE = re.compile(r"\b(str\.|strasse|straße|weg|platz|gasse|allee)\b", re.I)
_NOISE_RE = re.compile(
    r"\b("
    r"bar|kartenzahlung|ec[-\s]?karte|girocard|maestro|visa|mastercard|amex|"
    r"kundenkarte|payback|deutschlandcard|trace(?:-?\s*nr)?|terminal|genehmigung|"
    r"r[uü]ckgeld|wechselgeld|gegeben|bezahlt|zahlung|betrag|"
    r"summe|gesamt|total|zu\s*zahlen|zwischensumme|mwst|ust|netto|brutto|"
    r"tse|kassenbon|bediener|bon-nr|belegnr|uid|steuernr|"
    r"vielen\s+dank|auf\s+wiedersehen|willkommen"
    r")\b",
    re.I,
)
_SKIP_MERCHANT_RE = re.compile(
    r"^(filiale|markt|tel\.?|www\.|http|uid|steuer|datum|uhrzeit|bediener)\b",
    re.I,
)


class ReceiptExtractError(Exception):
    """Raised when a receipt file cannot be opened or is an unsupported type."""


_QTY_BARE_RE = re.compile(r"^\d+(?:[.,]\d+)?\s*[x×]\s*$", re.I)


def is_quantity_display_line(text):
    """True for display-only qty lines like '2 x 2.19' or bare '2 x' (no product name)."""
    s = str(text or "").strip()
    if not s:
        return False
    if _QTY_DISPLAY_ONLY_RE.match(s):
        return True
    return bool(_QTY_BARE_RE.match(s))


def find_tesseract():
    """Return the tesseract executable path, or None if it is not on PATH."""
    return shutil.which("tesseract")


def extract_receipt(path, categorizer=None):
    """Extract text from a local image or PDF and parse receipt fields."""
    path = Path(path)
    try:
        is_file = path.is_file()
    except OSError as error:
        raise ReceiptExtractError(f"Could not read file: {error}") from error
    if not is_file:
        raise ReceiptExtractError(f"Could not read file: {path}")

    suffix = path.suffix.lower()
    if suffix == PDF_SUFFIX:
        raw_text, notes = extract_pdf_text(path)
    elif suffix in IMAGE_SUFFIXES:
        raw_text, notes = extract_image_text(path)
    else:
        raise ReceiptExtractError(f"Unsupported file type: {suffix or 'unknown'}")

    parsed = parse_receipt_text(raw_text)
    parsed["notes"] = "; ".join(note for note in notes if note)
    parsed["raw_text"] = raw_text
    attach_categories(parsed, categorizer)
    return parsed


def list_receipt_files(folder):
    """Return receipt image and PDF paths in a folder, sorted by name."""
    folder = Path(folder)
    try:
        is_dir = folder.is_dir()
    except OSError as error:
        raise ReceiptExtractError(f"Could not read folder: {error}") from error
    if not is_dir:
        raise ReceiptExtractError(f"Could not read folder: {folder}")
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in RECEIPT_SUFFIXES
    )


def receipt_import_row(path, result=None, error=None):
    """Build a folder-import list row from an extract result or failure."""
    path = Path(path)
    if error:
        return {
            "path": str(path),
            "file": path.name,
            "status": "Failed",
            "error": str(error),
            "merchant": None,
            "date": None,
            "total": None,
            "item_count": 0,
            "result": None,
        }
    result = result or {}
    return {
        "path": str(path),
        "file": path.name,
        "status": "Extracted",
        "error": "",
        "merchant": result.get("merchant"),
        "date": result.get("date"),
        "total": result.get("total"),
        "item_count": len(result.get("items") or []),
        "result": result,
    }


def extract_receipt_folder(folder, categorizer=None, progress=None):
    """Extract every receipt file in a folder. Per-file failures stay in the list."""
    files = list_receipt_files(folder)
    rows = []
    total = len(files)
    for index, path in enumerate(files, 1):
        if progress:
            progress(index, total, path.name)
        try:
            rows.append(receipt_import_row(path, result=extract_receipt(path, categorizer)))
        except ReceiptExtractError as error:
            rows.append(receipt_import_row(path, error=error))
        except Exception as error:
            rows.append(receipt_import_row(path, error=f"Could not extract the receipt: {error}"))
    return rows


def extract_pdf_text(path):
    """Return (text, notes) from a PDF using pdfplumber, with optional OCR."""
    notes = []
    chunks = []
    try:
        import pdfplumber
    except ImportError as error:
        raise ReceiptExtractError("pdfplumber is required to read PDFs.") from error

    try:
        with pdfplumber.open(str(path)) as pdf:
            total = len(pdf.pages)
            if total > MAX_PAGES:
                notes.append(f"Only the first {MAX_PAGES} of {total} pages were read.")
            for index, page in enumerate(pdf.pages[:MAX_PAGES]):
                text = _page_text(page)
                table_text = tables_to_text(page.extract_tables() or [])
                combined = text
                if table_text and table_text not in text:
                    combined = f"{text}\n{table_text}".strip()
                if _significant_char_count(combined) < MIN_PAGE_CHARS:
                    ocr_text, ocr_notes = ocr_pdf_page(path, index)
                    notes.extend(ocr_notes)
                    combined = ocr_text or combined
                if combined.strip():
                    chunks.append(combined.strip())
    except ReceiptExtractError:
        raise
    except Exception as error:
        raise ReceiptExtractError(f"Could not read the PDF: {error}") from error

    raw = "\n\n".join(chunks)
    if not raw.strip():
        notes.append("No text layer found in this PDF.")
        if not find_tesseract():
            notes.append("Photos and scanned PDFs need Tesseract on PATH for local OCR.")
    return raw, notes


def extract_image_text(path):
    """Return (text, notes) from an image, using Tesseract when available."""
    if not find_tesseract():
        return "", ["Photos need Tesseract on PATH for local OCR."]
    text = run_tesseract(path)
    notes = []
    if not str(text or "").strip():
        notes.append("Tesseract did not read any text from this image.")
    return text, notes


def ocr_pdf_page(path, index):
    """OCR one PDF page when Tesseract is available; otherwise explain the skip."""
    if not find_tesseract():
        return "", ["Page looks scanned; Tesseract is not installed, so OCR was skipped."]
    try:
        ppm = render_pdf_page_ppm(path, index)
    except Exception as error:
        return "", [f"Could not render PDF page {index + 1} for OCR: {error}"]
    handle = tempfile.NamedTemporaryFile(suffix=".ppm", delete=False)
    try:
        handle.write(ppm)
        handle.close()
        text = run_tesseract(handle.name)
        return text, []
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def run_tesseract(image_path):
    """Run Tesseract on an image path. Prefer deu+eng, then eng."""
    command = find_tesseract()
    if not command:
        return ""
    for langs in ("deu+eng", "eng"):
        try:
            completed = subprocess.run(
                [command, str(image_path), "stdout", "-l", langs, "--psm", "6"],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if completed.returncode == 0:
            return completed.stdout.decode("utf-8", errors="replace")
        lowered = completed.stderr.decode("utf-8", errors="replace").lower()
        if "failed loading language" in lowered or "error opening data file" in lowered:
            continue
        break
    return ""


def render_pdf_page_ppm(path, page_index=0, scale=2):
    """Rasterize one PDF page to binary PPM (P6) bytes."""
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise ReceiptExtractError("pypdfium2 is required to preview or OCR PDF pages.") from error
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as error:
        raise ReceiptExtractError(f"Could not open the PDF: {error}") from error
    try:
        if page_index < 0 or page_index >= len(pdf):
            raise ReceiptExtractError("PDF has no such page.")
        bitmap = pdf[page_index].render(scale=scale, rev_byteorder=True)
        return pdf_bitmap_to_ppm(bitmap)
    finally:
        pdf.close()


def pdf_bitmap_to_ppm(bitmap):
    """Convert a pypdfium2 bitmap to RGB PPM bytes without Pillow."""
    width, height = bitmap.width, bitmap.height
    channels = max(1, bitmap.n_channels)
    stride = bitmap.stride
    mode = (bitmap.mode or "").upper()
    # ctypes c_ubyte arrays export format "<B", which memoryview rejects on Python 3.13.
    source = ctypes.string_at(bitmap.buffer, stride * height)
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    if channels == 3 and stride == width * 3 and mode.startswith("RGB"):
        return header + source
    pixels = bytearray(width * height * 3)
    offset = 0
    for y in range(height):
        row_start = y * stride
        for x in range(width):
            index = row_start + x * channels
            red, green, blue = _pixel_rgb(source, index, channels, mode)
            pixels[offset] = red
            pixels[offset + 1] = green
            pixels[offset + 2] = blue
            offset += 3
    return header + bytes(pixels)


def tables_to_text(tables):
    """Flatten pdfplumber tables into newline-separated rows."""
    lines = []
    for table in tables:
        for row in table or []:
            cells = [re.sub(r"\s+", " ", str(cell)).strip() for cell in row if cell not in (None, "")]
            if cells:
                lines.append("  ".join(cells))
    return "\n".join(lines)


def parse_receipt_text(text):
    """Heuristic parse of German/EU receipt text into merchant, totals, and items."""
    if _is_amazon_invoice(text):
        return _parse_amazon_invoice(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    merchant = _guess_merchant(lines)
    date = None
    subtotal = None
    tax = None
    total = None
    items = []
    pending_description = None
    for line in lines:
        if is_quantity_display_line(line):
            continue
        date = date or parse_date(line)
        qty_item = _parse_qty_unit_line(line, pending_description)
        if qty_item:
            items.append(qty_item)
            pending_description = None
            continue
        amount = _first_amount(line)
        if amount is None:
            if _looks_like_item_description(line):
                pending_description = line
            continue
        pending_description = None
        if _SUBTOTAL_RE.search(line) and not _TOTAL_RE.search(line):
            subtotal = amount
            continue
        if _TAX_RE.search(line) and not _TOTAL_RE.search(line):
            tax = (tax or 0.0) + amount
            continue
        if _is_total_line(line):
            total = amount
            continue
        item = _parse_item_line(line)
        if item:
            items.append(item)
    return {
        "merchant": merchant,
        "date": date,
        "currency": "EUR",
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "notes": "",
        "raw_text": text or "",
        "items": items,
        "merchant_category": None,
    }


def attach_categories(parsed, categorizer):
    """Fill merchant_category and per-item category from keyword rules."""
    merchant = parsed.get("merchant") or ""
    if categorizer is None:
        parsed["merchant_category"] = None
        for item in parsed.get("items") or []:
            item["category"] = None
        return parsed
    parsed["merchant_category"] = categorizer.suggest_category(merchant)
    for item in parsed.get("items") or []:
        description = item.get("description") or ""
        item["category"] = categorizer.suggest_category(f"{merchant} {description}".strip())
    return parsed


def parse_amount(token):
    """Parse a German or English decimal amount. Return None if invalid."""
    text = str(token or "").strip()
    if not text:
        return None
    negative = text.startswith("-")
    text = text[1:] if negative else text
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", text):
        value = float(text.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"\d+,\d{2}", text):
        value = float(text.replace(",", "."))
    elif re.fullmatch(r"\d+\.\d{2}", text):
        value = float(text)
    else:
        return None
    return -value if negative else value


def parse_date(text):
    """Return YYYY-MM-DD from a European, named-month, or ISO date in text, or None."""
    iso = _ISO_DATE_RE.search(text or "")
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    named = _NAMED_DATE_RE.search(text or "")
    if named:
        month_key = named.group("month").lower().replace("ä", "ae")
        month_i = _MONTH_NAME_TO_NUM.get(month_key) or _MONTH_NAME_TO_NUM.get(
            named.group("month").lower()
        )
        if month_i:
            day_i = int(named.group("day"))
            year_i = int(named.group("year"))
            if 1 <= day_i <= 31:
                return f"{year_i:04d}-{month_i:02d}-{day_i:02d}"
    match = _EU_DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year = match.group(1), match.group(2), match.group(3)
    if len(year) == 2:
        year = f"20{year}"
    try:
        month_i, day_i = int(month), int(day)
        if not (1 <= month_i <= 12 and 1 <= day_i <= 31):
            return None
    except ValueError:
        return None
    return f"{int(year):04d}-{month_i:02d}-{day_i:02d}"


def format_receipt_debug(result):
    """Human-readable raw text plus JSON for the extract dialog."""
    payload = {key: value for key, value in (result or {}).items() if key != "raw_text"}
    raw = (result or {}).get("raw_text") or ""
    return "=== Raw text ===\n" f"{raw}\n\n" "=== JSON ===\n" + json.dumps(payload, indent=2, ensure_ascii=False)



def _is_amazon_invoice(text):
    """True when strong Amazon EU invoice markers are present in OCR/PDF text."""
    raw = text or ""
    has_amazon_eu = bool(_AMAZON_EU_RE.search(raw))
    has_amazon = bool(_AMAZON_WORD_RE.search(raw))
    has_bestellnummer = bool(_BESTELLNUMMER_RE.search(raw))
    has_asin = bool(_ASIN_RE.search(raw))
    if has_amazon_eu and has_bestellnummer:
        return True
    if has_asin and has_amazon:
        return True
    return False


def _parse_amazon_invoice(text):
    """Parse Amazon EU seller invoices (Bestellinformationen / Rechnungsdetails)."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    date = _amazon_invoice_date(lines)
    total = _amazon_gross_total(lines)
    subtotal, tax = _amazon_tax_block(lines)
    items = _amazon_line_items(lines)
    return {
        "merchant": "Amazon",
        "date": date,
        "currency": "EUR",
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "notes": "",
        "raw_text": text or "",
        "items": items,
        "merchant_category": None,
    }


def _amazon_invoice_date(lines):
    """Prefer Rechnungsdatum; fall back to Bestelldatum / any named date."""
    preferred = None
    fallback = None
    for line in lines:
        lowered = line.lower()
        parsed = parse_date(line)
        if not parsed:
            continue
        if "rechnungsdatum" in lowered or "lieferdatum" in lowered:
            return parsed
        if "bestelldatum" in lowered:
            preferred = preferred or parsed
        else:
            fallback = fallback or parsed
    return preferred or fallback


def _amazon_gross_total(lines):
    """Sum Zahlbetrag across invoices; else sum Gesamtpreis (avoid double-counting)."""

    def _collect(label):
        found = []
        for line in lines:
            lowered = line.lower()
            if label not in lowered:
                continue
            if "ohne ust" in lowered:
                continue
            amounts = [parse_amount(m.group(1)) for m in _AMOUNT_RE.finditer(line)]
            amounts = [a for a in amounts if a is not None]
            if amounts:
                found.append(amounts[-1])
        return found

    zahl = _collect("zahlbetrag")
    if zahl:
        return round(sum(zahl), 2)
    gesamt = _collect("gesamtpreis")
    if gesamt:
        return round(sum(gesamt), 2)
    return None


def _amazon_tax_block(lines):
    """Return (netto subtotal, tax), summing all USt. Gesamt rows across invoices."""
    subtotals = []
    taxes = []
    for line in lines:
        if not re.search(r"USt\.?\s*Gesamt", line, re.I):
            continue
        amounts = [parse_amount(m.group(1)) for m in _AMOUNT_RE.finditer(line)]
        amounts = [a for a in amounts if a is not None]
        if len(amounts) >= 2:
            subtotals.append(amounts[-2])
            taxes.append(amounts[-1])
        elif len(amounts) == 1:
            taxes.append(amounts[0])
    if not subtotals and not taxes:
        return None, None
    subtotal = round(sum(subtotals), 2) if subtotals else None
    tax = round(sum(taxes), 2) if taxes else None
    return subtotal, tax


def _amazon_detail_blocks(lines):
    """Yield (start, end) slices for every Rechnungsdetails … Gesamtpreis/USt. Gesamt block."""
    blocks = []
    start = None
    for index, line in enumerate(lines):
        lowered = line.lower()
        if "rechnungsdetails" in lowered:
            if start is not None:
                blocks.append((start, index))
            start = index + 1
            continue
        if start is not None and (
            lowered.startswith("gesamtpreis")
            or re.search(r"ust\.?\s*gesamt", lowered)
        ):
            blocks.append((start, index))
            start = None
    if start is not None:
        blocks.append((start, len(lines)))
    if not blocks:
        blocks.append((0, len(lines)))
    return blocks


def _amazon_line_items_in_block(block_lines):
    """Parse product/shipping rows inside one Rechnungsdetails window."""
    items = []
    pending = None
    for line in block_lines:
        lowered = line.lower()
        if lowered.startswith("beschreibung") or "stückpreis" in lowered:
            continue
        if "(ohne ust" in lowered or "(inkl. ust" in lowered:
            continue
        if _ASIN_RE.search(line) or lowered.startswith("asin:"):
            continue

        shipping = _AMAZON_SHIPPING_RE.match(line)
        if shipping or lowered.startswith("versandkosten"):
            if pending:
                items.append(pending)
                pending = None
            amounts = [parse_amount(m.group(1)) for m in _AMOUNT_RE.finditer(line)]
            amounts = [a for a in amounts if a is not None]
            ship_amount = amounts[-1] if amounts else 0.0
            if ship_amount and abs(ship_amount) > 0.001:
                item = _item_dict("Versandkosten", 1, ship_amount)
                if item:
                    items.append(item)
            continue

        match = _AMAZON_ITEM_RE.match(line)
        if match:
            if pending:
                items.append(pending)
            description = match.group("desc").strip()
            amount = parse_amount(match.group("gross"))
            qty = match.group("qty")
            pending = _item_dict(description, qty, amount)
            continue

        if pending and _looks_like_item_description(line) and not _AMOUNT_RE.search(line):
            if not re.search(r"\b(menge|ust\.?\s*%|zwischensumme)\b", line, re.I):
                pending["description"] = f"{pending['description']} {line}".strip()
            continue

        # Non-Amazon-shaped amount line inside the block: ignore (headers/noise).
        if pending and _AMOUNT_RE.search(line):
            items.append(pending)
            pending = None

    if pending:
        items.append(pending)
    return items


def _amazon_line_items(lines):
    """Extract product rows from every Rechnungsdetails block; skip zero Versandkosten."""
    items = []
    for start, end in _amazon_detail_blocks(lines):
        items.extend(_amazon_line_items_in_block(lines[start:end]))
    return items


def _page_text(page):
    try:
        text = page.extract_text(layout=True) or ""
    except Exception:
        text = ""
    if _significant_char_count(text) < MIN_PAGE_CHARS:
        try:
            fallback = page.extract_text() or ""
        except Exception:
            fallback = ""
        if _significant_char_count(fallback) > _significant_char_count(text):
            return fallback
    return text


def _significant_char_count(text):
    return len(re.sub(r"\s+", "", text or ""))


def _pixel_rgb(row, index, channels, mode):
    if channels == 1:
        gray = row[index]
        return gray, gray, gray
    if mode.startswith("BGR"):
        return row[index + 2], row[index + 1], row[index]
    return row[index], row[index + 1], row[index + 2]


def _first_amount(line):
    match = _AMOUNT_RE.search(line)
    if not match:
        return None
    return parse_amount(match.group(1))


def _is_total_line(line):
    if _SUBTOTAL_RE.search(line):
        return False
    return bool(_TOTAL_RE.search(line))


def _guess_merchant(lines):
    for line in lines:
        stripped_dates = _EU_DATE_RE.sub("", _ISO_DATE_RE.sub("", line))
        if parse_date(line) and _significant_char_count(stripped_dates) < 4:
            continue
        if _GENERIC_HEADER_RE.search(line) or _SKIP_MERCHANT_RE.search(line):
            continue
        if _ADDRESS_RE.search(line) or _STREET_RE.search(line):
            continue
        letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", line)
        if len(letters) < 2:
            continue
        if _AMOUNT_RE.search(line):
            continue
        return line
    return None


def _parse_quantity(token):
    text = str(token or "").strip()
    if re.fullmatch(r"\d+", text):
        return float(text)
    return parse_amount(text)


def _is_noise_description(description):
    return bool(
        _NOISE_RE.search(description)
        or _TAX_RE.search(description)
        or _is_total_line(description)
    )


def _looks_like_item_description(line):
    if is_quantity_display_line(line):
        return False
    if not re.search(r"[A-Za-zÄÖÜäöüß]", line):
        return False
    if _GENERIC_HEADER_RE.search(line) or _SKIP_MERCHANT_RE.search(line):
        return False
    if _ADDRESS_RE.search(line) or _STREET_RE.search(line):
        return False
    if _is_noise_description(line):
        return False
    return True


def _item_dict(description, quantity, amount):
    description = (description or "").strip()
    if not description or not re.search(r"[A-Za-zÄÖÜäöüß]", description):
        return None
    if is_quantity_display_line(description):
        return None
    if _is_noise_description(description):
        return None
    qty_value = 1.0
    parsed_qty = _parse_quantity(quantity) if quantity else None
    if parsed_qty and parsed_qty > 0:
        qty_value = parsed_qty
    return {
        "description": description,
        "quantity": qty_value,
        "amount": amount,
        "category": None,
    }


def _parse_qty_unit_line(line, pending_description=None):
    """Parse 'NAME 2 x 0,99 1,98 A' or a continuation '2 x 0,99 1,98 A'."""
    match = _QTY_UNIT_LINE_RE.match(line)
    if not match:
        return None
    description = (match.group("desc") or pending_description or "").strip()
    amount = parse_amount(match.group("amount"))
    if amount is None:
        return None
    return _item_dict(description, match.group("qty"), amount)


def _parse_item_line(line):
    match = _TRAILING_AMOUNT_RE.match(line)
    if not match:
        return None
    description = (match.group("desc") or "").strip()
    quantity = match.group("qty")
    amount = parse_amount(match.group("amount"))
    if amount is None:
        return None
    leading = _LEADING_QTY_RE.match(description)
    if leading:
        quantity = quantity or leading.group("qty")
        description = leading.group("desc").strip()
    return _item_dict(description, quantity, amount)
