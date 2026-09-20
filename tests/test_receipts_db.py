"""Tests for Belege SQLite persistence."""

from __future__ import annotations

import json
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

from web import receipts_db  # noqa: E402
from web.receipts_state import (  # noqa: E402
    clear_last_rows,
    ensure_receipts_dirs,
    find_row_by_path,
    get_last_rows,
    load_receipt_item_categories,
    set_last_rows,
    set_receipt_item_category,
)


class ReceiptsDbTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.env = mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": self.tmp})
        self.env.start()
        receipts_db.reset_schema_flag()
        ensure_receipts_dirs()

    def tearDown(self):
        receipts_db.reset_schema_flag()
        self.env.stop()
        self._tmp.cleanup()

    def test_schema_and_upsert_roundtrip(self):
        path = Path(self.tmp) / "receipts" / "bon.pdf"
        path.write_bytes(b"%PDF-1")
        file_hash = receipts_db.hash_file(path)
        row = {
            "path": str(path),
            "file": "bon.pdf",
            "status": "Extracted",
            "error": "",
            "merchant": "Markt",
            "date": "2024-05-01",
            "total": 12.5,
            "result": {
                "merchant": "Markt",
                "date": "2024-05-01",
                "total": 12.5,
                "subtotal": 10.0,
                "tax": 2.5,
                "merchant_category": "Lebensmittel",
                "notes": "ok",
                "raw_text": "Markt\n...",
                "items": [
                    {"description": "Milch", "quantity": 1, "amount": 1.29, "category": ""},
                    {"description": "Brot", "quantity": 2, "amount": 3.0, "category": "Backwaren"},
                ],
            },
        }
        receipts_db.upsert_row_from_import_dict(row, file_hash=file_hash)
        loaded = receipts_db.get_row_by_path(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "Extracted")
        self.assertEqual(loaded["merchant"], "Markt")
        self.assertEqual(loaded["date"], "2024-05-01")
        self.assertEqual(loaded["date_extracted"], "2024-05-01")
        self.assertIsNone(loaded["date_override"])
        self.assertEqual(loaded["file_hash"], file_hash)
        self.assertEqual(loaded["item_count"], 2)
        self.assertEqual(loaded["result"]["items"][0]["description"], "Milch")
        self.assertEqual(loaded["result"]["items"][1]["category"], "Backwaren")
        self.assertEqual(loaded["result"]["date"], "2024-05-01")

    def test_hash_skip_unchanged(self):
        path = Path(self.tmp) / "receipts" / "same.pdf"
        path.write_bytes(b"content-a")
        h1 = receipts_db.hash_file(path)
        receipts_db.upsert_row_from_import_dict(
            {
                "path": str(path),
                "file": "same.pdf",
                "status": "Extracted",
                "merchant": "A",
                "date": "2024-01-01",
                "result": {
                    "merchant": "A",
                    "date": "2024-01-01",
                    "items": [{"description": "X", "amount": 1.0}],
                },
            },
            file_hash=h1,
        )
        existing = receipts_db.get_row_by_path(path)
        self.assertEqual(existing["file_hash"], h1)
        # Same content → same hash
        self.assertEqual(receipts_db.hash_file(path), h1)
        path.write_bytes(b"content-b")
        self.assertNotEqual(receipts_db.hash_file(path), h1)

    def test_date_override_preserved_on_reocr(self):
        path = Path(self.tmp) / "receipts" / "d.pdf"
        path.write_bytes(b"v1")
        receipts_db.upsert_row_from_import_dict(
            {
                "path": str(path),
                "file": "d.pdf",
                "status": "Extracted",
                "date": "2024-01-10",
                "result": {
                    "date": "2024-01-10",
                    "merchant": "Shop",
                    "items": [{"description": "A", "amount": 1.0}],
                },
            },
            file_hash=receipts_db.hash_file(path),
        )
        self.assertTrue(receipts_db.set_date_override(path, "2024-02-20"))
        loaded = receipts_db.get_row_by_path(path)
        self.assertEqual(loaded["date"], "2024-02-20")
        self.assertEqual(loaded["date_extracted"], "2024-01-10")
        self.assertEqual(loaded["date_override"], "2024-02-20")
        self.assertEqual(loaded["result"]["date"], "2024-02-20")

        path.write_bytes(b"v2")
        receipts_db.upsert_row_from_import_dict(
            {
                "path": str(path),
                "file": "d.pdf",
                "status": "Extracted",
                "date": "2024-03-01",
                "result": {
                    "date": "2024-03-01",
                    "merchant": "Shop2",
                    "items": [{"description": "B", "amount": 2.0}],
                },
            },
            file_hash=receipts_db.hash_file(path),
            preserve_date_override=True,
        )
        again = receipts_db.get_row_by_path(path)
        self.assertEqual(again["date_extracted"], "2024-03-01")
        self.assertEqual(again["date_override"], "2024-02-20")
        self.assertEqual(again["date"], "2024-02-20")
        self.assertEqual(again["merchant"], "Shop2")
        self.assertEqual(again["result"]["items"][0]["description"], "B")

    def test_clear_date_override(self):
        path = Path(self.tmp) / "receipts" / "c.pdf"
        path.write_bytes(b"x")
        receipts_db.upsert_row_from_import_dict(
            {
                "path": str(path),
                "file": "c.pdf",
                "status": "Extracted",
                "date": "2024-01-01",
                "result": {"date": "2024-01-01", "items": []},
            },
            file_hash="abc",
        )
        receipts_db.set_date_override(path, "2024-06-01")
        receipts_db.set_date_override(path, None)
        loaded = receipts_db.get_row_by_path(path)
        self.assertIsNone(loaded["date_override"])
        self.assertEqual(loaded["date"], "2024-01-01")

    def test_migrate_categories_from_json(self):
        json_path = Path(self.tmp) / "receipt_item_categories.json"
        json_path.write_text(
            json.dumps({"Banane": "Obst", "Milch": "Milchprodukte"}, ensure_ascii=False),
            encoding="utf-8",
        )
        receipts_db.reset_schema_flag()
        # Fresh ensure should import.
        mapping = receipts_db.load_categories()
        self.assertEqual(mapping["Banane"], "Obst")
        self.assertEqual(mapping["Milch"], "Milchprodukte")
        # JSON left as backup.
        self.assertTrue(json_path.is_file())
        # Second ensure does not wipe / re-import over existing.
        set_receipt_item_category("Brot", "Backwaren")
        receipts_db.reset_schema_flag()
        mapping2 = receipts_db.load_categories()
        self.assertEqual(mapping2["Brot"], "Backwaren")
        self.assertEqual(mapping2["Banane"], "Obst")

    def test_categories_survive_reocr(self):
        set_receipt_item_category("Milch", "Milchprodukte")
        path = Path(self.tmp) / "receipts" / "m.pdf"
        path.write_bytes(b"1")
        receipts_db.upsert_row_from_import_dict(
            {
                "path": str(path),
                "file": "m.pdf",
                "status": "Extracted",
                "date": "2024-01-01",
                "result": {
                    "date": "2024-01-01",
                    "items": [{"description": "Milch", "amount": 1.0}],
                },
            },
            file_hash="h1",
        )
        path.write_bytes(b"2")
        receipts_db.upsert_row_from_import_dict(
            {
                "path": str(path),
                "file": "m.pdf",
                "status": "Extracted",
                "date": "2024-01-02",
                "result": {
                    "date": "2024-01-02",
                    "items": [{"description": "Milch", "amount": 1.5}],
                },
            },
            file_hash="h2",
        )
        self.assertEqual(load_receipt_item_categories()["Milch"], "Milchprodukte")

    def test_prune_and_delete(self):
        keep = Path(self.tmp) / "receipts" / "keep.pdf"
        gone = Path(self.tmp) / "receipts" / "gone.pdf"
        keep.write_bytes(b"1")
        gone.write_bytes(b"2")
        for p in (keep, gone):
            receipts_db.upsert_row_from_import_dict(
                {
                    "path": str(p),
                    "file": p.name,
                    "status": "Extracted",
                    "date": "2024-01-01",
                    "result": {"date": "2024-01-01", "items": []},
                },
                file_hash="x",
            )
        gone.unlink()
        self.assertEqual(receipts_db.prune_missing(), 1)
        self.assertIsNone(receipts_db.get_row_by_path(gone))
        self.assertIsNotNone(receipts_db.get_row_by_path(keep))
        self.assertTrue(receipts_db.delete_row_by_path(keep))
        self.assertEqual(receipts_db.list_rows(), [])

    def test_scan_merge_does_not_wipe_other_rows(self):
        """Upserting one file must not delete another still on disk."""
        a = Path(self.tmp) / "receipts" / "a.pdf"
        b = Path(self.tmp) / "receipts" / "uploads" / "b.pdf"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        set_last_rows(
            [
                {
                    "path": str(a),
                    "file": "a.pdf",
                    "status": "Extracted",
                    "date": "2024-01-01",
                    "result": {"date": "2024-01-01", "items": []},
                },
                {
                    "path": str(b),
                    "file": "b.pdf",
                    "status": "Extracted",
                    "date": "2024-02-01",
                    "result": {"date": "2024-02-01", "items": []},
                },
            ]
        )
        # "Scan" only touches a (upsert again).
        set_last_rows(
            [
                {
                    "path": str(a),
                    "file": "a.pdf",
                    "status": "Extracted",
                    "date": "2024-01-15",
                    "result": {"date": "2024-01-15", "items": []},
                }
            ]
        )
        paths = {r["file"] for r in get_last_rows()}
        self.assertEqual(paths, {"a.pdf", "b.pdf"})
        self.assertEqual(find_row_by_path(a)["date"], "2024-01-15")
        self.assertEqual(find_row_by_path(b)["date"], "2024-02-01")


if __name__ == "__main__":
    unittest.main()
