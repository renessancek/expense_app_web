"""Lightweight tests for Belege path safety and empty-folder extract."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from receipt_extractor import extract_receipt_folder  # noqa: E402
from web.receipts_state import (  # noqa: E402
    aggregate_receipt_items,
    clear_last_rows,
    ensure_receipts_dirs,
    format_date_de,
    format_month_de,
    format_status_de,
    get_last_rows,
    receipts_dir,
    resolve_under_receipts,
    safe_upload_filename,
    set_last_rows,
    sort_receipt_rows_by_date_desc,
    unique_target,
)


class ReceiptsPathGuardTests(unittest.TestCase):
    def test_rejects_traversal_and_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                ensure_receipts_dirs()
                self.assertIsNone(resolve_under_receipts("../etc/passwd"))
                self.assertIsNone(resolve_under_receipts(str(Path(tmp) / "other" / "x.pdf")))
                self.assertIsNone(resolve_under_receipts(""))
                # Absolute path outside receipts
                outside = Path(tmp) / "BankStatements" / "x.pdf"
                outside.parent.mkdir(parents=True, exist_ok=True)
                outside.write_bytes(b"%PDF")
                self.assertIsNone(resolve_under_receipts(str(outside)))

    def test_accepts_path_under_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                root = ensure_receipts_dirs()
                target = root / "bon.pdf"
                target.write_bytes(b"%PDF")
                resolved = resolve_under_receipts(str(target))
                self.assertEqual(resolved, target.resolve())
                # Relative from receipts root
                rel = resolve_under_receipts("bon.pdf")
                self.assertEqual(rel, target.resolve())
                nested = root / "uploads" / "foto.jpg"
                nested.write_bytes(b"x")
                self.assertEqual(
                    resolve_under_receipts("uploads/foto.jpg"),
                    nested.resolve(),
                )


class ReceiptsEmptyFolderTests(unittest.TestCase):
    def test_extract_empty_folder_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "empty"
            folder.mkdir()
            self.assertEqual(extract_receipt_folder(folder), [])


class ReceiptsStateHelpersTests(unittest.TestCase):
    def test_safe_upload_filename_strips_dirs(self):
        self.assertEqual(safe_upload_filename("../../etc/passwd.pdf"), "passwd.pdf")
        self.assertEqual(safe_upload_filename(""), "upload.bin")

    def test_unique_target_avoids_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = unique_target(directory, "a.pdf")
            first.write_bytes(b"1")
            second = unique_target(directory, "a.pdf")
            self.assertNotEqual(first, second)
            self.assertEqual(second.name, "a_1.pdf")

    def test_last_rows_cache(self):
        clear_last_rows()
        set_last_rows([{"path": "/x", "file": "x.pdf", "status": "Extracted"}])
        self.assertEqual(len(get_last_rows()), 1)
        clear_last_rows()
        self.assertEqual(get_last_rows(), [])

    def test_last_rows_sorted_by_date_descending(self):
        clear_last_rows()
        set_last_rows(
            [
                {"file": "old.pdf", "date": "2024-01-10", "status": "Extracted"},
                {"file": "new.pdf", "date": "2024-03-01", "status": "Extracted"},
                {"file": "mid.pdf", "result": {"date": "2024-02-15"}, "status": "Extracted"},
                {"file": "nodate.pdf", "date": None, "status": "Failed"},
            ]
        )
        files = [r["file"] for r in get_last_rows()]
        self.assertEqual(files, ["new.pdf", "mid.pdf", "old.pdf", "nodate.pdf"])
        clear_last_rows()

    def test_sort_receipt_rows_by_date_desc_helper(self):
        rows = [
            {"file": "a", "date": "2023-12-01"},
            {"file": "b", "date": "2024-01-01"},
            {"file": "c", "date": ""},
        ]
        sorted_rows = sort_receipt_rows_by_date_desc(rows)
        self.assertEqual([r["file"] for r in sorted_rows], ["b", "a", "c"])

    def test_format_helpers(self):
        self.assertEqual(format_status_de("Extracted"), "Extrahiert")
        self.assertEqual(format_status_de("Failed"), "Fehler")
        self.assertEqual(format_date_de("2024-01-15"), "15.01.2024")
        self.assertEqual(format_month_de("2024-01"), "01.2024")

    def test_aggregate_receipt_items_exact_text_and_month(self):
        rows = [
            {
                "status": "Extracted",
                "date": "2024-01-10",
                "result": {
                    "date": "2024-01-10",
                    "items": [
                        {"description": "Alpromil", "quantity": 1, "amount": 3.99},
                        {"description": "Alpromil", "quantity": 1, "amount": 3.99},
                        {"description": "Milch", "quantity": 1, "amount": 1.29},
                    ],
                },
            },
            {
                "status": "Extracted",
                "date": "2024-02-01",
                "result": {
                    "date": "2024-02-01",
                    "items": [
                        {"description": "Alpromil", "quantity": 1, "amount": 3.99},
                    ],
                },
            },
            {
                "status": "Failed",
                "date": None,
                "result": None,
            },
        ]
        totals = aggregate_receipt_items(rows)
        by_key = {(r["month"], r["description"]): r["amount"] for r in totals}
        self.assertAlmostEqual(by_key[("2024-01", "Alpromil")], 7.98)
        self.assertAlmostEqual(by_key[("2024-01", "Milch")], 1.29)
        self.assertAlmostEqual(by_key[("2024-02", "Alpromil")], 3.99)
        self.assertNotIn("quantity", totals[0])
        # Exact match: different casing does not merge
        self.assertEqual(len(totals), 3)


if __name__ == "__main__":
    unittest.main()
