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

from receipt_extractor import (  # noqa: E402
    extract_receipt_folder,
    is_quantity_display_line,
    parse_receipt_text,
)
from web.receipts_state import (  # noqa: E402
    aggregate_receipt_items,
    clear_last_rows,
    delete_receipt_from_cache,
    ensure_receipts_dirs,
    format_date_de,
    format_month_de,
    format_status_de,
    get_last_rows,
    prune_missing_receipt_rows,
    receipt_name_taken,
    receipts_dir,
    remove_last_row_by_path,
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

    def test_receipt_name_taken_checks_root_and_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                root = ensure_receipts_dirs()
                self.assertFalse(receipt_name_taken("bon.pdf"))
                (root / "bon.pdf").write_bytes(b"%PDF")
                self.assertTrue(receipt_name_taken("bon.pdf"))
                self.assertTrue(receipt_name_taken("path/to/bon.pdf"))
                (root / "uploads" / "foto.jpg").write_bytes(b"x")
                self.assertTrue(receipt_name_taken("foto.jpg"))
                self.assertFalse(receipt_name_taken("missing.pdf"))

    def test_last_rows_cache(self):
        clear_last_rows()
        set_last_rows([{"path": "/x", "file": "x.pdf", "status": "Extracted"}])
        self.assertEqual(len(get_last_rows()), 1)
        clear_last_rows()
        self.assertEqual(get_last_rows(), [])

    def test_remove_last_row_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep = Path(tmp) / "keep.pdf"
            drop = Path(tmp) / "drop.pdf"
            keep.write_bytes(b"1")
            drop.write_bytes(b"2")
            clear_last_rows()
            set_last_rows(
                [
                    {"path": str(keep.resolve()), "file": "keep.pdf"},
                    {"path": str(drop.resolve()), "file": "drop.pdf"},
                ]
            )
            self.assertTrue(remove_last_row_by_path(drop))
            files = [r["file"] for r in get_last_rows()]
            self.assertEqual(files, ["keep.pdf"])
            self.assertFalse(remove_last_row_by_path(drop))
            clear_last_rows()

    def test_delete_receipt_cascades_positionen_out_of_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep = Path(tmp) / "keep.pdf"
            drop = Path(tmp) / "drop.pdf"
            keep.write_bytes(b"1")
            drop.write_bytes(b"2")
            clear_last_rows()
            set_last_rows(
                [
                    {
                        "path": str(keep.resolve()),
                        "file": "keep.pdf",
                        "status": "Extracted",
                        "date": "2024-01-10",
                        "result": {
                            "date": "2024-01-10",
                            "items": [
                                {"description": "Milch", "amount": 1.29},
                            ],
                        },
                    },
                    {
                        "path": str(drop.resolve()),
                        "file": "drop.pdf",
                        "status": "Extracted",
                        "date": "2024-01-11",
                        "result": {
                            "date": "2024-01-11",
                            "items": [
                                {"description": "Alpromil", "amount": 3.99},
                                {"description": "Milch", "amount": 1.29},
                            ],
                        },
                    },
                ]
            )
            before = aggregate_receipt_items(get_last_rows())
            jan = before[0]
            by_desc = {r["description"]: r["amount"] for r in jan["rows"]}
            self.assertAlmostEqual(by_desc["Alpromil"], 3.99)
            self.assertAlmostEqual(by_desc["Milch"], 2.58)

            drop.unlink()
            self.assertTrue(delete_receipt_from_cache(drop))

            after = aggregate_receipt_items(get_last_rows())
            self.assertEqual(len(after), 1)
            only = {r["description"]: r["amount"] for r in after[0]["rows"]}
            self.assertEqual(list(only), ["Milch"])
            self.assertAlmostEqual(only["Milch"], 1.29)
            self.assertNotIn("Alpromil", only)
            clear_last_rows()

    def test_prune_missing_receipt_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep = Path(tmp) / "keep.pdf"
            gone = Path(tmp) / "gone.pdf"
            keep.write_bytes(b"1")
            gone.write_bytes(b"2")
            clear_last_rows()
            set_last_rows(
                [
                    {"path": str(keep.resolve()), "file": "keep.pdf"},
                    {"path": str(gone.resolve()), "file": "gone.pdf"},
                ]
            )
            gone.unlink()
            self.assertEqual(prune_missing_receipt_rows(), 1)
            self.assertEqual([r["file"] for r in get_last_rows()], ["keep.pdf"])
            clear_last_rows()

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

    def test_aggregate_receipt_items_grouped_by_month_amount_desc(self):
        rows = [
            {
                "status": "Extracted",
                "date": "2024-01-10",
                "path": "/data/Rechnungen/jan.pdf",
                "file": "jan.pdf",
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
                "path": "/data/Rechnungen/feb.pdf",
                "file": "feb.pdf",
                "result": {
                    "date": "2024-02-01",
                    "items": [
                        {"description": "Alpromil", "quantity": 1, "amount": 3.99},
                        {"description": "Brot", "quantity": 1, "amount": 2.50},
                    ],
                },
            },
            {
                "status": "Failed",
                "date": None,
                "result": None,
            },
        ]
        groups = aggregate_receipt_items(rows)
        self.assertEqual([g["month"] for g in groups], ["2024-02", "2024-01"])

        feb = groups[0]
        self.assertAlmostEqual(feb["total"], 6.49)
        self.assertEqual([r["description"] for r in feb["rows"]], ["Alpromil", "Brot"])
        self.assertAlmostEqual(feb["rows"][0]["amount"], 3.99)
        self.assertEqual(feb["rows"][0]["path"], "/data/Rechnungen/feb.pdf")
        self.assertEqual(feb["rows"][0]["file"], "feb.pdf")
        self.assertEqual(feb["rows"][1]["path"], "/data/Rechnungen/feb.pdf")

        jan = groups[1]
        self.assertAlmostEqual(jan["total"], 9.27)
        # Amount descending within month: Alpromil 7.98 before Milch 1.29
        self.assertEqual([r["description"] for r in jan["rows"]], ["Alpromil", "Milch"])
        self.assertAlmostEqual(jan["rows"][0]["amount"], 7.98)
        self.assertAlmostEqual(jan["rows"][1]["amount"], 1.29)
        self.assertNotIn("quantity", jan["rows"][0])
        self.assertEqual(jan["rows"][0]["path"], "/data/Rechnungen/jan.pdf")
        self.assertEqual(jan["rows"][0]["file"], "jan.pdf")
        self.assertEqual(jan["rows"][1]["path"], "/data/Rechnungen/jan.pdf")

    def test_aggregate_keeps_first_receipt_path_when_descriptions_merge(self):
        rows = [
            {
                "status": "Extracted",
                "date": "2024-03-01",
                "path": "/data/Rechnungen/a.pdf",
                "file": "a.pdf",
                "result": {
                    "date": "2024-03-01",
                    "items": [{"description": "Milch", "amount": 1.0}],
                },
            },
            {
                "status": "Extracted",
                "date": "2024-03-15",
                "path": "/data/Rechnungen/b.pdf",
                "file": "b.pdf",
                "result": {
                    "date": "2024-03-15",
                    "items": [{"description": "Milch", "amount": 2.0}],
                },
            },
        ]
        groups = aggregate_receipt_items(rows)
        self.assertEqual(len(groups), 1)
        milk = groups[0]["rows"][0]
        self.assertAlmostEqual(milk["amount"], 3.0)
        # First contributing receipt stays the detail link target.
        self.assertEqual(milk["path"], "/data/Rechnungen/a.pdf")
        self.assertEqual(milk["file"], "a.pdf")



class QuantityDisplayLineTests(unittest.TestCase):
    def test_is_quantity_display_line(self):
        self.assertTrue(is_quantity_display_line("2 x 2.19"))
        self.assertTrue(is_quantity_display_line("2 x 2,19"))
        self.assertTrue(is_quantity_display_line("2×2.69"))
        self.assertTrue(is_quantity_display_line("2 x"))
        self.assertFalse(is_quantity_display_line("Alpro Blueb. Muffin B 4.38"))
        self.assertFalse(is_quantity_display_line("2 x 0,99 1,98"))

    def test_parse_skips_qty_display_keeps_product_line(self):
        parsed = parse_receipt_text(
            "Supermarkt\n"
            "01.08.2024\n"
            "2 x  2.19\n"
            "Alpro Blueb. Muffin B  4.38\n"
            "2 x  2.69\n"
            "Alpro Rote Früchte Dattel B 5.38\n"
            "Summe 9.76\n"
        )
        descs = [i["description"] for i in parsed["items"]]
        self.assertEqual(
            descs,
            ["Alpro Blueb. Muffin B", "Alpro Rote Früchte Dattel B"],
        )
        self.assertAlmostEqual(parsed["items"][0]["amount"], 4.38)
        self.assertAlmostEqual(parsed["items"][1]["amount"], 5.38)

    def test_aggregate_skips_qty_display_and_bare_qty_descriptions(self):
        rows = [
            {
                "status": "Extracted",
                "date": "2024-08-01",
                "result": {
                    "date": "2024-08-01",
                    "items": [
                        {"description": "2 x", "quantity": 1, "amount": 2.19},
                        {
                            "description": "Alpro Blueb. Muffin B",
                            "quantity": 1,
                            "amount": 4.38,
                        },
                        {"description": "2 x 2.19", "quantity": 1, "amount": 2.19},
                        {
                            "description": "Alpro Rote Früchte Dattel B",
                            "quantity": 1,
                            "amount": 5.38,
                        },
                    ],
                },
            },
        ]
        groups = aggregate_receipt_items(rows)
        self.assertEqual(len(groups), 1)
        descs = [r["description"] for r in groups[0]["rows"]]
        self.assertEqual(
            descs,
            ["Alpro Rote Früchte Dattel B", "Alpro Blueb. Muffin B"],
        )
        self.assertAlmostEqual(groups[0]["total"], 9.76)



if __name__ == "__main__":
    unittest.main()
