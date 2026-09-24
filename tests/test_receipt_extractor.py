import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from receipt_extractor import (
    MAX_PAGES,
    ReceiptExtractError,
    _is_amazon_invoice,
    attach_categories,
    extract_receipt,
    extract_receipt_folder,
    list_receipt_files,
    parse_date,
    parse_receipt_text,
    render_pdf_page_ppm,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AMAZON_EU_INVOICE = (FIXTURES / "amazon_eu_invoice.txt").read_text(encoding="utf-8")
AMAZON_EU_MULTI_INVOICE = (FIXTURES / "amazon_eu_multi_invoice.txt").read_text(encoding="utf-8")

GERMAN_RECEIPT = """
Kassenbon
REWE
Musterstrasse 1
12345 Berlin
Datum: 12.05.2026

Vollmilch 1L          1,29 A
2 x Bananen           4,98 A
Brot                  2,19 B

Zwischensumme         8,46
MwSt 7%               0,14
MwSt 19%              0,79
SUMME EUR             8,46

Kartenzahlung         8,46
Trace-Nr 12345
Vielen Dank
"""

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def write_text_pdf(path, pages_lines):
    """Write a simple multi-page Helvetica PDF for extractor tests."""
    objects = [None, None]
    font_index = 3
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_indices = []
    for lines in pages_lines:
        parts = ["BT /F1 11 Tf 40 560 Td"]
        for index, line in enumerate(lines):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if index:
                parts.append("0 -14 Td")
            parts.append(f"({escaped}) Tj")
        parts.append("ET")
        stream = "\n".join(parts)
        stream_bytes = stream.encode("latin-1")
        objects.append(f"<< /Length {len(stream_bytes)} >>\nstream\n{stream}\nendstream")
        contents_index = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] "
            f"/Contents {contents_index} 0 R /Resources << /Font << /F1 {font_index} 0 R >> >> >>"
        )
        page_indices.append(len(objects))
    kids = " ".join(f"{index} 0 R" for index in page_indices)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_indices)} >>"
    objects[0] = "<< /Type /Catalog /Pages 2 0 R >>"

    chunks = [b"%PDF-1.4\n"]
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref_pos = sum(len(chunk) for chunk in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets)
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    path.write_bytes(b"".join(chunks) + "".join(xref).encode("latin-1") + trailer.encode("ascii"))


def _categorizer(directory):
    from categorizer import Categorizer

    rules_path = Path(directory) / "rules.json"
    rules_path.write_text(json.dumps({
        "rules": [{"category": "Supermarkt", "keywords": ["rewe", "milch"]}],
    }), encoding="utf-8")
    return Categorizer(rules_path=str(rules_path))


class TestParseReceiptText(unittest.TestCase):

    def test_german_receipt_fields_and_skipped_noise(self):
        parsed = parse_receipt_text(GERMAN_RECEIPT)
        self.assertEqual(parsed["merchant"], "REWE")
        self.assertEqual(parsed["date"], "2026-05-12")
        self.assertEqual(parsed["currency"], "EUR")
        self.assertEqual(parsed["subtotal"], 8.46)
        self.assertAlmostEqual(parsed["tax"], 0.93)
        self.assertEqual(parsed["total"], 8.46)
        descriptions = [item["description"] for item in parsed["items"]]
        self.assertEqual(descriptions, ["Vollmilch 1L", "Bananen", "Brot"])
        self.assertEqual(parsed["items"][0]["amount"], 1.29)
        self.assertEqual(parsed["items"][1]["quantity"], 2.0)
        self.assertNotIn("Kartenzahlung", descriptions)
        self.assertNotIn("MwSt 7%", descriptions)
        self.assertNotIn("SUMME EUR", descriptions)

    def test_multiline_quantity_uses_line_total(self):
        parsed = parse_receipt_text(
            "SPAR - SUPERMARKT\n"
            "KRAFTPAPIER TASCHE 0,30 B\n"
            "SPAR BIO-JOGURT 0,90 K\n"
            "SPAR BIO-KORNSP.SES.\n"
            "2   x   0,99                       1,98 A\n"
            "VEGGIE VEG.TOASTBLOC 1,49 A\n"
            "SUMME: 10,85\n"
            "Betrag EUR: 10,85\n"
        )
        by_name = {item["description"]: item for item in parsed["items"]}
        self.assertIn("SPAR BIO-KORNSP.SES.", by_name)
        self.assertEqual(by_name["SPAR BIO-KORNSP.SES."]["quantity"], 2.0)
        self.assertEqual(by_name["SPAR BIO-KORNSP.SES."]["amount"], 1.98)
        self.assertEqual(by_name["SPAR BIO-JOGURT"]["amount"], 0.90)
        descriptions = [item["description"] for item in parsed["items"]]
        self.assertNotIn("Betrag EUR:", descriptions)
        self.assertNotIn("0,99", descriptions)

    def test_thousands_separator_and_iso_date(self):
        parsed = parse_receipt_text("Shop\nDate 2026-01-03\nWidget 1.234,50\nSUMME 1.234,50\n")
        self.assertEqual(parsed["merchant"], "Shop")
        self.assertEqual(parsed["date"], "2026-01-03")
        self.assertEqual(parsed["total"], 1234.50)
        self.assertEqual(parsed["items"][0]["amount"], 1234.50)


class TestExtractReceipt(unittest.TestCase):

    def test_pdf_page_renders_to_ppm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.pdf"
            write_text_pdf(path, [["REWE", "SUMME EUR 1,00"]])
            ppm = render_pdf_page_ppm(path, 0, scale=1)
            self.assertTrue(ppm.startswith(b"P6\n"))
            self.assertIn(b"\n255\n", ppm)
            self.assertGreater(len(ppm), 100)

    def test_digital_pdf_extracts_merchant_total_and_item(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.pdf"
            write_text_pdf(path, [[
                "REWE",
                "Datum: 12.05.2026",
                "Vollmilch 1L    1,29",
                "SUMME EUR       1,29",
            ]])
            categorizer = _categorizer(directory)
            result = extract_receipt(path, categorizer)
            self.assertEqual(result["merchant"], "REWE")
            self.assertEqual(result["total"], 1.29)
            self.assertGreaterEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0]["description"], "Vollmilch 1L")
            self.assertEqual(result["merchant_category"], "Supermarkt")
            self.assertEqual(result["items"][0]["category"], "Supermarkt")
            self.assertIn("Vollmilch", result["raw_text"])

    def test_pdf_page_cap_is_noted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "long.pdf"
            pages = [["REWE", "Item A    1,00", "SUMME EUR 1,00"]]
            pages.extend([[f"Padding page {index} extra text"] for index in range(2, 7)])
            write_text_pdf(path, pages)
            result = extract_receipt(path)
            self.assertIn(str(MAX_PAGES), result["notes"])
            self.assertIn("6", result["notes"])

    def test_image_without_tesseract_is_empty_with_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shot.png"
            path.write_bytes(PNG_1X1)
            with mock.patch("receipt_extractor.find_tesseract", return_value=None):
                result = extract_receipt(path)
            self.assertEqual(result["items"], [])
            self.assertEqual(result["raw_text"], "")
            self.assertIn("Tesseract", result["notes"])

    def test_folder_lists_receipts_and_skips_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_text_pdf(folder / "a.pdf", [["REWE", "Milk 1,00", "SUMME EUR 1,00"]])
            (folder / "shot.PNG").write_bytes(PNG_1X1)
            (folder / "ignore.csv").write_text("x", encoding="utf-8")
            names = [path.name for path in list_receipt_files(folder)]
            self.assertEqual(names, ["a.pdf", "shot.PNG"])

    def test_folder_extract_keeps_per_file_results(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_text_pdf(folder / "rewe.pdf", [["REWE", "Milk 1,00", "SUMME EUR 1,00"]])
            (folder / "photo.png").write_bytes(PNG_1X1)
            with mock.patch("receipt_extractor.find_tesseract", return_value=None):
                rows = extract_receipt_folder(folder)
            by_file = {row["file"]: row for row in rows}
            self.assertEqual(by_file["rewe.pdf"]["status"], "Extracted")
            self.assertEqual(by_file["rewe.pdf"]["merchant"], "REWE")
            self.assertEqual(by_file["rewe.pdf"]["total"], 1.0)
            self.assertGreaterEqual(by_file["rewe.pdf"]["item_count"], 1)
            self.assertIsNotNone(by_file["rewe.pdf"]["result"])
            self.assertEqual(by_file["photo.png"]["status"], "Extracted")
            self.assertEqual(by_file["photo.png"]["item_count"], 0)
            self.assertIn("Tesseract", by_file["photo.png"]["result"]["notes"])

    def test_unsupported_type_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("REWE", encoding="utf-8")
            with self.assertRaises(ReceiptExtractError):
                extract_receipt(path)

    def test_attach_categories_without_categorizer(self):
        parsed = parse_receipt_text("REWE\nMilk 1,00\nSUMME 1,00\n")
        attach_categories(parsed, None)
        self.assertIsNone(parsed["merchant_category"])
        self.assertIsNone(parsed["items"][0]["category"])



class TestAmazonInvoice(unittest.TestCase):

    def test_detects_amazon_eu_invoice(self):
        self.assertTrue(_is_amazon_invoice(AMAZON_EU_INVOICE))
        self.assertFalse(_is_amazon_invoice(GERMAN_RECEIPT))
        self.assertFalse(_is_amazon_invoice("Some shop\nASIN: B00ANON0001\nSUMME 1,00"))
        self.assertTrue(
            _is_amazon_invoice(
                "Order from Amazon marketplace\nASIN: B00ANON0001\nItem 1,00\n"
            )
        )

    def test_named_month_date_parsing(self):
        self.assertEqual(parse_date("02 September 2026"), "2026-09-02")
        self.assertEqual(parse_date("1. März 2025"), "2025-03-01")
        self.assertEqual(parse_date("11 Sep 2026"), "2026-09-11")
        self.assertEqual(parse_date("/Lieferdatum 11 Sep 2026"), "2026-09-11")
        self.assertEqual(parse_date("Bestelldatum 03 Okt 2025"), "2025-10-03")


    def test_amazon_media_abbreviated_date(self):
        text = (
            "Amazon Media EU S.à r.l.\n"
            "Rechnungsdatum\n"
            "/Lieferdatum 11 Sep 2026\n"
            "Bestellnummer D01-0000000-0000001\n"
            "Zahlbetrag 6,87 €\n"
            "Rechnungsdetails\n"
            "Sample Ebook Title 1 6,25 € 10% 6,87 € 6,87 €\n"
            "ASIN: B00ANON0003\n"
            "Gesamtpreis 6,87 €\n"
        )
        self.assertTrue(_is_amazon_invoice(text))
        parsed = parse_receipt_text(text)
        self.assertEqual(parsed["merchant"], "Amazon")
        self.assertEqual(parsed["date"], "2026-09-11")
        self.assertEqual(parsed["total"], 6.87)

    def test_amazon_eu_fixture_fields(self):
        parsed = parse_receipt_text(AMAZON_EU_INVOICE)
        self.assertEqual(parsed["merchant"], "Amazon")
        self.assertEqual(parsed["date"], "2026-09-02")
        self.assertEqual(parsed["currency"], "EUR")
        self.assertEqual(parsed["total"], 34.85)
        self.assertEqual(parsed["subtotal"], 29.04)
        self.assertEqual(parsed["tax"], 5.81)
        self.assertGreaterEqual(len(parsed["items"]), 1)
        item = parsed["items"][0]
        self.assertIn("Intenso", item["description"])
        self.assertIn("USB Stick", item["description"])
        self.assertNotIn("ASIN", item["description"])
        self.assertEqual(item["quantity"], 1.0)
        self.assertEqual(item["amount"], 34.85)
        descriptions = [row["description"] for row in parsed["items"]]
        self.assertNotIn("Versandkosten", descriptions)
        # Must not pick Zahlungsreferenznummer or net-only total.
        self.assertNotIn("Zahlungsreferenznummer", parsed["merchant"] or "")
        self.assertNotEqual(parsed["total"], 29.04)

    def test_amazon_skips_zero_shipping_keeps_nonzero(self):
        text = (
            "Amazon EU S.à r.l.\n"
            "Bestellnummer 306-0000000-0000001\n"
            "Rechnungsdatum 02 September 2026\n"
            "Zahlbetrag 40,85 €\n"
            "Rechnungsdetails\n"
            "Widget Pro 1 30,00 € 20% 36,00 € 36,00 €\n"
            "ASIN: B0ABCDEF12\n"
            "Versandkosten 4,85 € 4,85 € 4,85 €\n"
            "Gesamtpreis 40,85 €\n"
        )
        parsed = parse_receipt_text(text)
        self.assertEqual(parsed["merchant"], "Amazon")
        self.assertEqual(parsed["total"], 40.85)
        by_name = {item["description"]: item for item in parsed["items"]}
        self.assertIn("Widget Pro", by_name)
        self.assertEqual(by_name["Widget Pro"]["amount"], 36.0)
        self.assertIn("Versandkosten", by_name)
        self.assertEqual(by_name["Versandkosten"]["amount"], 4.85)



    def test_amazon_multi_invoice_sums_sections(self):
        parsed = parse_receipt_text(AMAZON_EU_MULTI_INVOICE)
        self.assertEqual(parsed["merchant"], "Amazon")
        self.assertEqual(parsed["date"], "2026-09-02")
        self.assertEqual(parsed["currency"], "EUR")
        self.assertEqual(parsed["total"], 97.13)
        self.assertEqual(parsed["subtotal"], 80.94)
        self.assertEqual(parsed["tax"], 16.19)
        self.assertEqual(len(parsed["items"]), 2)
        amounts = sorted(item["amount"] for item in parsed["items"])
        self.assertEqual(amounts, [10.07, 87.06])
        descriptions = " ".join(item["description"] for item in parsed["items"])
        self.assertIn("Filterbeutel", descriptions)
        self.assertIn("Allesschneider", descriptions)
        self.assertNotIn("ASIN", descriptions)



if __name__ == "__main__":
    unittest.main()
