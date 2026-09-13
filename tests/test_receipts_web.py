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
    clear_last_rows,
    ensure_receipts_dirs,
    format_date_de,
    format_status_de,
    get_last_rows,
    receipts_dir,
    resolve_under_receipts,
    safe_upload_filename,
    set_last_rows,
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

    def test_format_helpers(self):
        self.assertEqual(format_status_de("Extracted"), "Extrahiert")
        self.assertEqual(format_status_de("Failed"), "Fehler")
        self.assertEqual(format_date_de("2024-01-15"), "15.01.2024")


if __name__ == "__main__":
    unittest.main()
