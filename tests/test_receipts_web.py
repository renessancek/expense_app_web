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
from web import receipts_db  # noqa: E402
from web.receipts_state import (  # noqa: E402
    DEFAULT_RECEIPT_ITEM_CATEGORY,
    aggregate_receipt_items,
    is_excluded_receipt_aggregate_line,
    clear_last_rows,
    delete_receipt_from_cache,
    ensure_receipts_dirs,
    format_date_de,
    format_month_de,
    format_status_de,
    get_last_rows,
    list_receipt_line_category_rows,
    load_receipt_item_categories,
    prune_missing_receipt_rows,
    receipt_name_taken,
    receipts_dir,
    remove_last_row_by_path,
    resolve_under_receipts,
    safe_upload_filename,
    set_last_rows,
    set_receipt_item_category,
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
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                clear_last_rows()
                set_last_rows([{"path": "/x", "file": "x.pdf", "status": "Extracted"}])
                self.assertEqual(len(get_last_rows()), 1)
                clear_last_rows()
                self.assertEqual(get_last_rows(), [])

    def test_remove_last_row_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                keep = Path(tmp) / "receipts" / "keep.pdf"
                drop = Path(tmp) / "receipts" / "drop.pdf"
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
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                keep = Path(tmp) / "receipts" / "keep.pdf"
                drop = Path(tmp) / "receipts" / "drop.pdf"
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
                # Uncategorized lines default to Sonstiges (merged).
                self.assertEqual(list(by_desc), [DEFAULT_RECEIPT_ITEM_CATEGORY])
                self.assertAlmostEqual(by_desc[DEFAULT_RECEIPT_ITEM_CATEGORY], 6.57)

                drop.unlink()
                self.assertTrue(delete_receipt_from_cache(drop))

                after = aggregate_receipt_items(get_last_rows())
                self.assertEqual(len(after), 1)
                only = {r["description"]: r["amount"] for r in after[0]["rows"]}
                self.assertEqual(list(only), [DEFAULT_RECEIPT_ITEM_CATEGORY])
                self.assertAlmostEqual(only[DEFAULT_RECEIPT_ITEM_CATEGORY], 1.29)
                clear_last_rows()

    def test_prune_missing_receipt_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                keep = Path(tmp) / "receipts" / "keep.pdf"
                gone = Path(tmp) / "receipts" / "gone.pdf"
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
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                clear_last_rows()
                set_last_rows(
                    [
                        {
                            "path": str(Path(tmp) / "receipts" / "old.pdf"),
                            "file": "old.pdf",
                            "date": "2024-01-10",
                            "status": "Extracted",
                            "result": {"date": "2024-01-10", "items": []},
                        },
                        {
                            "path": str(Path(tmp) / "receipts" / "new.pdf"),
                            "file": "new.pdf",
                            "date": "2024-03-01",
                            "status": "Extracted",
                            "result": {"date": "2024-03-01", "items": []},
                        },
                        {
                            "path": str(Path(tmp) / "receipts" / "mid.pdf"),
                            "file": "mid.pdf",
                            "result": {"date": "2024-02-15", "items": []},
                            "status": "Extracted",
                        },
                        {
                            "path": str(Path(tmp) / "receipts" / "nodate.pdf"),
                            "file": "nodate.pdf",
                            "date": None,
                            "status": "Failed",
                        },
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
        # Uncategorized → Sonstiges (Alpromil + Brot merged)
        self.assertEqual(
            [r["description"] for r in feb["rows"]],
            [DEFAULT_RECEIPT_ITEM_CATEGORY],
        )
        self.assertAlmostEqual(feb["rows"][0]["amount"], 6.49)
        self.assertEqual(feb["rows"][0]["path"], "/data/Rechnungen/feb.pdf")
        self.assertEqual(feb["rows"][0]["file"], "feb.pdf")

        jan = groups[1]
        self.assertAlmostEqual(jan["total"], 9.27)
        self.assertEqual(
            [r["description"] for r in jan["rows"]],
            [DEFAULT_RECEIPT_ITEM_CATEGORY],
        )
        self.assertAlmostEqual(jan["rows"][0]["amount"], 9.27)
        self.assertNotIn("quantity", jan["rows"][0])
        self.assertEqual(jan["rows"][0]["path"], "/data/Rechnungen/jan.pdf")
        self.assertEqual(jan["rows"][0]["file"], "jan.pdf")

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
        self.assertEqual(milk["description"], DEFAULT_RECEIPT_ITEM_CATEGORY)
        self.assertAlmostEqual(milk["amount"], 3.0)
        # First contributing receipt stays the detail link target.
        self.assertEqual(milk["path"], "/data/Rechnungen/a.pdf")
        self.assertEqual(milk["file"], "a.pdf")



    def test_aggregate_sums_same_category_defaults_uncategorized_to_sonstiges(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                set_receipt_item_category("Bio-Bananen", "Bananen")
                set_receipt_item_category("Bananen", "Bananen")
                set_receipt_item_category("SPG-Bananen", "Bananen")
                rows = [
                    {
                        "status": "Extracted",
                        "date": "2024-08-01",
                        "path": "/data/Rechnungen/a.pdf",
                        "file": "a.pdf",
                        "result": {
                            "date": "2024-08-01",
                            "items": [
                                {"description": "Bio-Bananen", "amount": 1.50},
                                {"description": "Bananen", "amount": 2.00},
                                {"description": "SPG-Bananen", "amount": 1.20},
                                {"description": "Milch", "amount": 1.29},
                            ],
                        },
                    },
                ]
                groups = aggregate_receipt_items(rows)
                self.assertEqual(len(groups), 1)
                labels = [r["description"] for r in groups[0]["rows"]]
                # Category sum first by amount (4.70), then Sonstiges (1.29)
                self.assertEqual(labels, ["Bananen", DEFAULT_RECEIPT_ITEM_CATEGORY])
                by = {r["description"]: r["amount"] for r in groups[0]["rows"]}
                self.assertAlmostEqual(by["Bananen"], 4.70)
                self.assertAlmostEqual(by[DEFAULT_RECEIPT_ITEM_CATEGORY], 1.29)
                self.assertNotIn("Bio-Bananen", by)
                self.assertNotIn("SPG-Bananen", by)
                self.assertNotIn("Milch", by)




    def test_aggregate_drops_negative_amounts(self):
        rows = [
            {
                "status": "Extracted",
                "date": "2024-09-01",
                "path": "/data/Rechnungen/a.pdf",
                "file": "a.pdf",
                "result": {
                    "date": "2024-09-01",
                    "items": [
                        {"description": "Milch", "amount": 1.29},
                        {"description": "Leergut", "amount": -0.25},
                        {"description": "Pfand", "amount": -1.00},
                        {"description": "Brot", "amount": 2.50},
                    ],
                },
            },
        ]
        groups = aggregate_receipt_items(rows)
        self.assertEqual(len(groups), 1)
        labels = [r["description"] for r in groups[0]["rows"]]
        self.assertEqual(labels, [DEFAULT_RECEIPT_ITEM_CATEGORY])
        self.assertNotIn("Leergut", labels)
        self.assertNotIn("Pfand", labels)
        self.assertNotIn("Brot", labels)
        self.assertNotIn("Milch", labels)
        self.assertAlmostEqual(groups[0]["total"], 3.79)

    def test_aggregate_excludes_irrelevant_category_from_sums(self):
        """Marking Belegzeilen as Irrelevant must drop them from Positionen summiert."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                rows = [
                    {
                        "status": "Extracted",
                        "date": "2024-08-01",
                        "path": "/data/Rechnungen/a.pdf",
                        "file": "a.pdf",
                        "result": {
                            "date": "2024-08-01",
                            "items": [
                                {
                                    "description": "Buch A",
                                    "amount": 10.0,
                                    "category": "Bücher",
                                },
                                {
                                    "description": "Buch B",
                                    "amount": 5.0,
                                    "category": "Bücher",
                                },
                                {
                                    "description": "Brot",
                                    "amount": 2.0,
                                    "category": "Lebensmittel",
                                },
                            ],
                        },
                    },
                ]
                before = aggregate_receipt_items(rows)
                self.assertEqual(len(before), 1)
                self.assertAlmostEqual(before[0]["total"], 17.0)
                by_before = {r["description"]: r["amount"] for r in before[0]["rows"]}
                self.assertAlmostEqual(by_before["Bücher"], 15.0)

                set_receipt_item_category("Buch A", "Irrelevant")
                set_receipt_item_category("Buch B", "irrelevant")  # case-insensitive
                after = aggregate_receipt_items(rows)
                self.assertEqual(len(after), 1)
                labels = [r["description"] for r in after[0]["rows"]]
                self.assertEqual(labels, ["Lebensmittel"])
                self.assertNotIn("Irrelevant", labels)
                self.assertNotIn("Bücher", labels)
                self.assertAlmostEqual(after[0]["total"], 2.0)
                self.assertAlmostEqual(after[0]["rows"][0]["amount"], 2.0)

    def test_aggregate_excludes_pfand_lines_from_sums(self):
        """Pfand by description or by category must drop out of Positionen summiert."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                rows = [
                    {
                        "status": "Extracted",
                        "date": "2024-10-05",
                        "path": "/data/Rechnungen/t.pdf",
                        "file": "t.pdf",
                        "result": {
                            "date": "2024-10-05",
                            "items": [
                                {"description": "Testsaft", "amount": 3.0, "category": "Getraenke"},
                                {"description": "Pfand", "amount": 0.25, "category": "Getraenke"},
                                {"description": "  PFAND 0,25 ", "amount": 0.25},
                                {"description": "Flaschenrueckgabe X", "amount": 0.50, "category": "pfand"},
                                {"description": "Dampfandruck Test", "amount": 4.0, "category": "Haushalt"},
                            ],
                        },
                    },
                ]
                groups = aggregate_receipt_items(rows)
                self.assertEqual(len(groups), 1)
                by = {r["description"]: r["amount"] for r in groups[0]["rows"]}
                self.assertEqual(set(by), {"Getraenke", "Haushalt"})
                self.assertAlmostEqual(by["Getraenke"], 3.0)
                self.assertAlmostEqual(by["Haushalt"], 4.0)
                self.assertNotIn(DEFAULT_RECEIPT_ITEM_CATEGORY, by)
                self.assertNotIn("pfand", {k.casefold() for k in by})
                self.assertAlmostEqual(groups[0]["total"], 7.0)

    def test_is_excluded_receipt_aggregate_line(self):
        self.assertTrue(is_excluded_receipt_aggregate_line("Pfand", None))
        self.assertTrue(is_excluded_receipt_aggregate_line(" pfand 0,25", "Sonstiges"))
        self.assertTrue(is_excluded_receipt_aggregate_line("Wasser", "PFAND"))
        self.assertTrue(is_excluded_receipt_aggregate_line("Buch", "Irrelevant"))
        self.assertFalse(is_excluded_receipt_aggregate_line("Pfandflasche Test", "Sonstiges"))
        self.assertFalse(is_excluded_receipt_aggregate_line("Dampfandruck", "Sonstiges"))
        self.assertFalse(is_excluded_receipt_aggregate_line("Milch", "Lebensmittel"))


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
        self.assertEqual(descs, [DEFAULT_RECEIPT_ITEM_CATEGORY])
        self.assertAlmostEqual(groups[0]["total"], 9.76)




class ReceiptItemCategoryTests(unittest.TestCase):
    def test_assign_list_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                set_receipt_item_category("Banane", "Obst")
                set_receipt_item_category("Bio-Banane", "Obst")
                self.assertEqual(
                    load_receipt_item_categories(),
                    {"Banane": "Obst", "Bio-Banane": "Obst"},
                )
                rows = [
                    {
                        "status": "Extracted",
                        "path": "/data/Rechnungen/markt.pdf",
                        "file": "markt.pdf",
                        "result": {
                            "items": [
                                {"description": "Banane", "amount": 1.0},
                                {"description": "Bananen", "amount": 2.0},
                                {"description": "2 x 2.19", "amount": 2.19},
                            ]
                        },
                    }
                ]
                lines = list_receipt_line_category_rows(rows)
                by_desc = {r["description"]: r for r in lines}
                self.assertIn("Banane", by_desc)
                self.assertIn("Bananen", by_desc)
                self.assertIn("Bio-Banane", by_desc)
                self.assertNotIn("2 x 2.19", by_desc)
                self.assertEqual(by_desc["Banane"]["category"], "Obst")
                self.assertEqual(by_desc["Banane"]["count"], 1)
                self.assertEqual(by_desc["Banane"]["path"], "/data/Rechnungen/markt.pdf")
                self.assertEqual(by_desc["Banane"]["file"], "markt.pdf")
                self.assertEqual(
                    by_desc["Bananen"]["category"], DEFAULT_RECEIPT_ITEM_CATEGORY
                )
                self.assertEqual(by_desc["Bananen"]["path"], "/data/Rechnungen/markt.pdf")
                self.assertEqual(by_desc["Bio-Banane"]["count"], 0)
                self.assertIsNone(by_desc["Bio-Banane"]["path"])
                set_receipt_item_category("Banane", "")
                self.assertNotIn("Banane", load_receipt_item_categories())


class ReceiptItemCategorySyncAndDefaultTests(unittest.TestCase):
    """Mapping↔receipt_items sync, item.category fallback, Sonstiges default."""

    def test_item_category_without_mapping_shows_and_aggregates(self):
        """Lines with only receipt_items.category (no mapping) still count."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                rows = [
                    {
                        "status": "Extracted",
                        "date": "2026-09-05",
                        "path": "/data/Rechnungen/books.pdf",
                        "file": "books.pdf",
                        "result": {
                            "date": "2026-09-05",
                            "items": [
                                {
                                    "description": "Roman XY",
                                    "amount": 12.50,
                                    "category": "Bücher",
                                },
                                {
                                    "description": "Milch",
                                    "amount": 1.29,
                                    "category": "",
                                },
                            ],
                        },
                    }
                ]
                groups = aggregate_receipt_items(rows)
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0]["month"], "2026-09")
                by = {r["description"]: r["amount"] for r in groups[0]["rows"]}
                self.assertAlmostEqual(by["Bücher"], 12.50)
                self.assertAlmostEqual(by[DEFAULT_RECEIPT_ITEM_CATEGORY], 1.29)

                lines = list_receipt_line_category_rows(rows)
                by_desc = {r["description"]: r for r in lines}
                self.assertEqual(by_desc["Roman XY"]["category"], "Bücher")
                self.assertEqual(
                    by_desc["Milch"]["category"], DEFAULT_RECEIPT_ITEM_CATEGORY
                )
                # No mapping rows invented for the defaults.
                self.assertEqual(load_receipt_item_categories(), {})

    def test_set_category_syncs_receipt_items_and_excludes_irrelevant(self):
        """Belegzeilen save updates receipt_items.category and aggregation."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                receipts_db.reset_schema_flag()
                ensure_receipts_dirs()
                path = Path(tmp) / "receipts" / "books.pdf"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"%PDF-1")
                receipts_db.upsert_row_from_import_dict(
                    {
                        "path": str(path),
                        "file": "books.pdf",
                        "status": "Extracted",
                        "date": "2026-09-10",
                        "result": {
                            "date": "2026-09-10",
                            "total": 20.0,
                            "items": [
                                {
                                    "description": "Buch A",
                                    "quantity": 1,
                                    "amount": 10.0,
                                    "category": "Bücher",
                                },
                                {
                                    "description": "Buch B",
                                    "quantity": 1,
                                    "amount": 5.0,
                                    "category": "Bücher",
                                },
                                {
                                    "description": "Stift",
                                    "quantity": 1,
                                    "amount": 2.0,
                                    "category": "",
                                },
                            ],
                        },
                    },
                    file_hash="h1",
                )
                rows = get_last_rows()
                before = aggregate_receipt_items(rows)
                by_before = {
                    r["description"]: r["amount"] for r in before[0]["rows"]
                }
                self.assertAlmostEqual(by_before["Bücher"], 15.0)
                self.assertAlmostEqual(
                    by_before[DEFAULT_RECEIPT_ITEM_CATEGORY], 2.0
                )

                set_receipt_item_category("Buch A", "Irrelevant")
                self.assertEqual(
                    load_receipt_item_categories()["Buch A"], "Irrelevant"
                )
                loaded = receipts_db.get_row_by_path(path)
                by_item = {
                    i["description"]: i["category"]
                    for i in loaded["result"]["items"]
                }
                self.assertEqual(by_item["Buch A"], "Irrelevant")
                # Unchanged sibling still Bücher on the line.
                self.assertEqual(by_item["Buch B"], "Bücher")

                after = aggregate_receipt_items(get_last_rows())
                by_after = {
                    r["description"]: r["amount"] for r in after[0]["rows"]
                }
                self.assertNotIn("Irrelevant", by_after)
                self.assertAlmostEqual(by_after["Bücher"], 5.0)
                self.assertAlmostEqual(
                    by_after[DEFAULT_RECEIPT_ITEM_CATEGORY], 2.0
                )

                # Clearing mapping syncs item category to empty; UI → Sonstiges.
                set_receipt_item_category("Buch A", "")
                self.assertNotIn("Buch A", load_receipt_item_categories())
                loaded2 = receipts_db.get_row_by_path(path)
                by_item2 = {
                    i["description"]: i["category"]
                    for i in loaded2["result"]["items"]
                }
                self.assertEqual(by_item2["Buch A"], "")
                lines = list_receipt_line_category_rows(get_last_rows())
                by_line = {r["description"]: r["category"] for r in lines}
                self.assertEqual(
                    by_line["Buch A"], DEFAULT_RECEIPT_ITEM_CATEGORY
                )


class ReceiptsCategoriesSaveWebTests(unittest.TestCase):
    """HTTP tests for POST /receipts/categories/save (diff + max_fields)."""

    def _client(self, tmp: str):
        from fastapi.testclient import TestClient
        from web.deps import reset_store
        from web.main import create_app

        env = {
            "EXPENSE_DATA_DIR": tmp,
            "EXPENSE_AUTH_USER": "",
            "EXPENSE_AUTH_PASSWORD": "",
        }
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_store()
        ensure_receipts_dirs()
        receipts_db.reset_schema_flag()
        return TestClient(create_app())

    def test_categories_save_partial_diff_keeps_unchanged(self):
        """Only submitted pairs are written; unsubmitted mappings stay."""
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            set_receipt_item_category("item-keep", "KeepCat")
            set_receipt_item_category("item-change", "OldCat")
            set_receipt_item_category("item-clear", "ClearMe")
            response = client.post(
                "/receipts/categories/save",
                data={
                    "description": ["item-change", "item-clear", "item-new"],
                    "category": ["NewCat", "", "Fresh"],
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            loc = response.headers.get("location", "")
            self.assertIn("/receipts/categories?message=", loc)
            self.assertIn("3", loc)
            cats = load_receipt_item_categories()
            self.assertEqual(cats.get("item-keep"), "KeepCat")
            self.assertEqual(cats.get("item-change"), "NewCat")
            self.assertNotIn("item-clear", cats)
            self.assertEqual(cats.get("item-new"), "Fresh")

    def test_categories_save_accepts_more_than_1000_form_fields(self):
        """No-JS fallback: large full-form POST must not hit max_fields=1000."""
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            n = 600
            response = client.post(
                "/receipts/categories/save",
                data={
                    "description": [f"item-{i:04d}" for i in range(n)],
                    "category": ["TestCat"] * n,
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303, response.text[:500])
            self.assertIn("/receipts/categories?message=", response.headers.get("location", ""))
            self.assertNotIn("Too many fields", response.text)
            cats = load_receipt_item_categories()
            self.assertEqual(cats.get("item-0000"), "TestCat")
            self.assertEqual(cats.get("item-0599"), "TestCat")
            self.assertEqual(len(cats), n)

    def test_categories_save_small_diff_with_many_existing(self):
        """JS-diff style: small POST amid many existing mappings updates only those."""
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            for i in range(600):
                set_receipt_item_category(f"item-{i:04d}", "Base")
            response = client.post(
                "/receipts/categories/save",
                data={
                    "description": ["item-0001", "item-0500"],
                    "category": ["Changed", ""],
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            cats = load_receipt_item_categories()
            self.assertEqual(cats.get("item-0000"), "Base")
            self.assertEqual(cats.get("item-0001"), "Changed")
            self.assertNotIn("item-0500", cats)
            self.assertEqual(cats.get("item-0599"), "Base")
            self.assertEqual(len(cats), 599)




class ReceiptsDetailCategoriesWebTests(unittest.TestCase):
    """HTTP tests for editable categories on /receipts/detail."""

    def _client(self, tmp: str):
        from fastapi.testclient import TestClient
        from web.deps import reset_store
        from web.main import create_app

        env = {
            "EXPENSE_DATA_DIR": tmp,
            "EXPENSE_AUTH_USER": "",
            "EXPENSE_AUTH_PASSWORD": "",
        }
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_store()
        ensure_receipts_dirs()
        receipts_db.reset_schema_flag()
        return TestClient(create_app())

    def _seed_receipt(self, tmp: str, name: str = "test-bon.pdf") -> Path:
        root = ensure_receipts_dirs()
        path = root / name
        path.write_bytes(b"%PDF-fake-receipt")
        row = {
            "path": str(path.resolve()),
            "file": name,
            "status": "Extracted",
            "error": "",
            "merchant": "TestMarkt",
            "date": "2024-06-15",
            "total": 5.0,
            "result": {
                "merchant": "TestMarkt",
                "date": "2024-06-15",
                "total": 5.0,
                "subtotal": 4.5,
                "tax": 0.5,
                "merchant_category": "",
                "notes": "",
                "raw_text": "TestMarkt",
                "items": [
                    {
                        "description": "item-alpha",
                        "quantity": 1,
                        "amount": 1.5,
                        "category": "",
                    },
                    {
                        "description": "item-beta",
                        "quantity": 2,
                        "amount": 3.5,
                        "category": "",
                    },
                ],
            },
        }
        receipts_db.upsert_row_from_import_dict(
            row, file_hash=receipts_db.hash_file(path)
        )
        return path.resolve()

    def test_detail_shows_category_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            path = self._seed_receipt(tmp)
            set_receipt_item_category("item-alpha", "Obst")
            response = client.get(f"/receipts/detail?path={path}")
            self.assertEqual(response.status_code, 200)
            body = response.text
            self.assertIn('action="/receipts/detail/categories"', body)
            self.assertIn('name="category"', body)
            self.assertIn('name="description"', body)
            self.assertIn('list="receipt-detail-category-suggestions"', body)
            self.assertIn('data-original="Obst"', body)
            self.assertIn("item-alpha", body)
            self.assertIn("item-beta", body)
            # Menge/Betrag remain plain text, not inputs
            self.assertIn('class="num">1.0</td>', body)
            self.assertIn("1.50", body)
            self.assertNotIn('name="quantity"', body)
            self.assertNotIn('name="amount"', body)

    def test_detail_categories_post_updates_and_reflects(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            path = self._seed_receipt(tmp)
            set_receipt_item_category("item-keep-elsewhere", "KeepCat")
            set_receipt_item_category("item-alpha", "OldCat")
            response = client.post(
                "/receipts/detail/categories",
                data={
                    "path": str(path),
                    "description": ["item-alpha", "item-beta"],
                    "category": ["NewCat", "Dairy"],
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            loc = response.headers.get("location", "")
            self.assertIn("/receipts/detail?", loc)
            self.assertIn("message=", loc)
            self.assertIn("2", loc)
            cats = load_receipt_item_categories()
            self.assertEqual(cats.get("item-alpha"), "NewCat")
            self.assertEqual(cats.get("item-beta"), "Dairy")
            self.assertEqual(cats.get("item-keep-elsewhere"), "KeepCat")

            page = client.get(f"/receipts/detail?path={path}")
            self.assertEqual(page.status_code, 200)
            self.assertIn('data-original="NewCat"', page.text)
            self.assertIn('data-original="Dairy"', page.text)
            self.assertIn('value="NewCat"', page.text)
            self.assertIn('value="Dairy"', page.text)

    def test_detail_categories_post_partial_leaves_unsubmitted(self):
        """Only submitted pairs are written; other mappings stay untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            path = self._seed_receipt(tmp)
            set_receipt_item_category("item-alpha", "AlphaOld")
            set_receipt_item_category("item-beta", "BetaKeep")
            set_receipt_item_category("other-desc", "OtherKeep")
            response = client.post(
                "/receipts/detail/categories",
                data={
                    "path": str(path),
                    "description": ["item-alpha"],
                    "category": ["AlphaNew"],
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            cats = load_receipt_item_categories()
            self.assertEqual(cats.get("item-alpha"), "AlphaNew")
            self.assertEqual(cats.get("item-beta"), "BetaKeep")
            self.assertEqual(cats.get("other-desc"), "OtherKeep")

    def test_detail_categories_post_requires_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            response = client.post(
                "/receipts/detail/categories",
                data={
                    "description": ["item-alpha"],
                    "category": ["X"],
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            loc = response.headers.get("location", "")
            self.assertIn("/receipts?error=", loc)




class RulesRenameWebTests(unittest.TestCase):
    """HTTP tests for POST /rules/rename (rules + receipt category strings)."""

    def _client(self, tmp: str):
        from fastapi.testclient import TestClient
        from web.deps import reset_store
        from web.main import create_app

        env = {
            "EXPENSE_DATA_DIR": tmp,
            "EXPENSE_AUTH_USER": "",
            "EXPENSE_AUTH_PASSWORD": "",
        }
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_store()
        ensure_receipts_dirs()
        receipts_db.reset_schema_flag()
        return TestClient(create_app())

    def test_rules_page_shows_rename_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            from web.deps import get_store

            store = get_store()
            store.categorizer.add_rule(["rewe"], "Supermarkt")
            response = client.get("/rules")
            self.assertEqual(response.status_code, 200)
            body = response.text
            self.assertIn('action="/rules/rename"', body)
            self.assertIn("Umbenennen", body)
            self.assertIn('name="old_category"', body)
            self.assertIn('name="new_category"', body)
            self.assertIn("Supermarkt", body)

    def test_rename_updates_rules_and_receipt_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            from web.deps import get_store

            store = get_store()
            store.categorizer.add_rule(["rewe"], "Supermarkt")
            set_receipt_item_category("Bio-Milch", "Supermarkt")
            set_receipt_item_category("Brot", "Backwaren")
            response = client.post(
                "/rules/rename",
                data={"old_category": "Supermarkt", "new_category": "Lebensmittel"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            loc = response.headers.get("location", "")
            self.assertIn("/rules?message=", loc)
            self.assertIn("umbenannt", loc.lower())
            cats = [r["category"] for r in store.categorizer.rules]
            self.assertIn("Lebensmittel", cats)
            self.assertNotIn("Supermarkt", cats)
            self.assertEqual(store.categorizer.suggest_category("REWE MARKT"), "Lebensmittel")
            mapping = load_receipt_item_categories()
            self.assertEqual(mapping["Bio-Milch"], "Lebensmittel")
            self.assertEqual(mapping["Brot"], "Backwaren")

    def test_rename_merges_into_existing_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            from web.deps import get_store

            store = get_store()
            store.categorizer.add_rule(["rewe"], "Supermarkt")
            store.categorizer.add_rule(["aldi"], "Lebensmittel")
            set_receipt_item_category("Milch", "Supermarkt")
            response = client.post(
                "/rules/rename",
                data={"old_category": "Supermarkt", "new_category": "Lebensmittel"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            loc = response.headers.get("location", "")
            self.assertIn("zusammengef", loc.lower())
            cats = [r["category"] for r in store.categorizer.rules]
            self.assertEqual(cats.count("Lebensmittel"), 1)
            self.assertNotIn("Supermarkt", cats)
            self.assertEqual(store.categorizer.suggest_category("REWE"), "Lebensmittel")
            self.assertEqual(store.categorizer.suggest_category("ALDI"), "Lebensmittel")
            self.assertEqual(load_receipt_item_categories()["Milch"], "Lebensmittel")

    def test_rename_rejects_empty_new_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            from web.deps import get_store

            store = get_store()
            store.categorizer.add_rule(["rewe"], "Supermarkt")
            response = client.post(
                "/rules/rename",
                data={"old_category": "Supermarkt", "new_category": "   "},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            loc = response.headers.get("location", "")
            self.assertIn("/rules?error=", loc)
            self.assertEqual(
                [r["category"] for r in store.categorizer.rules],
                ["Supermarkt"],
            )



if __name__ == "__main__":
    unittest.main()
