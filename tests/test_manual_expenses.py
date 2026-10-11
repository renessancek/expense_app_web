"""Tests for manually added expenses (synthetic data only)."""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from expense_data import ExpenseDataStore, parse_transaction_dates  # noqa: E402
from parser import Parser  # noqa: E402
from web.manual_expenses import (  # noqa: E402
    MANUAL_FILENAME,
    append_manual_expenses,
    delete_manual_expense,
    format_amount_eu,
    list_manual_expenses,
    parse_amount_input,
    validate_rows,
)


class AmountParsingTests(unittest.TestCase):
    def test_eu_and_us_inputs(self):
        cases = {
            "12,50": 12.5,
            "1.234,56": 1234.56,
            "12.50": 12.5,
            "1,234.56": 1234.56,
            "-7,30": 7.3,
            "  3 ": 3.0,
            "1.234": 1234.0,
            "0,5": 0.5,
            "12,5 €": 12.5,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertAlmostEqual(parse_amount_input(raw), expected)

    def test_invalid_inputs(self):
        for raw in ["", "abc", "12,50x", "1,2,3.4.5", "-", "€"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_amount_input(raw))

    def test_format_and_parser_roundtrip(self):
        self.assertEqual(format_amount_eu(12.5), "-12,50")
        self.assertEqual(format_amount_eu(1234.56), "-1234,56")
        self.assertAlmostEqual(Parser._parse_amount("-1234,56"), -1234.56)

    def test_validate_rows_ignores_empty_and_rejects_invalid(self):
        entries, error = validate_rows(
            ["2026-10-01", "2026-10-02", "2026-10-03"],
            ["Bäcker", "", ""],
            ["3,20", "", ""],
        )
        self.assertIsNone(error)
        self.assertEqual(entries, [("2026-10-01", "Bäcker", 3.2)])

        for dates, descs, amounts in [
            (["2026-13-01"], ["X"], ["1"]),
            (["03.10.2026"], ["X"], ["1"]),
            ([""], ["X"], ["1"]),
            (["2026-10-01"], [""], ["1"]),
            (["2026-10-01"], ["X"], ["abc"]),
            (["2026-10-01"], ["X"], ["0,00"]),
            (["2026-10-01"], [""], [""]),
        ]:
            with self.subTest(dates=dates, descs=descs, amounts=amounts):
                entries, error = validate_rows(dates, descs, amounts)
                self.assertEqual(entries, [])
                self.assertTrue(error)


class IsoDateMonthTests(unittest.TestCase):
    def test_iso_date_lands_in_correct_month(self):
        parsed = parse_transaction_dates(["2026-10-03", "03.10.2026", "01.07.2026", "2026-10-13"])
        self.assertEqual(
            list(parsed.dt.strftime("%Y-%m-%d")),
            ["2026-10-03", "2026-10-03", "2026-07-01", "2026-10-13"],
        )

    def test_store_month_for_iso_date(self):
        class Scanner:
            def scan_for_csvs(self):
                return ["x.csv"]

        class StubParser:
            def parse_bank_statement_with_report(self, path):
                return ([{"date": "2026-10-03", "description": "Manuell", "amount": -5.0}],
                        {"status": "Imported"})

        class Cat:
            def suggest_category(self, description):
                return "Sonstiges"

        store = ExpenseDataStore(Scanner(), StubParser(), Cat())
        store.reload([])
        self.assertEqual(store.months(), ["2026-10"])
        self.assertEqual(len(store.filtered(month="2026-10")), 1)
        self.assertEqual(len(store.filtered(month="2026-03")), 0)

    def test_day_first_bank_dates_unchanged(self):
        parsed = parse_transaction_dates(["05.03.2026", "05/03/2026", "2026-03-05 10:00:00"])
        self.assertEqual(list(parsed.dt.strftime("%Y-%m-%d")), ["2026-03-05"] * 3)


class StorageTests(unittest.TestCase):
    def test_append_keeps_rows_and_quotes_semicolons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANUAL_FILENAME
            append_manual_expenses([("2026-10-01", "Erste", 1.0)], path)
            append_manual_expenses([("2026-10-02", 'Kaffee; "groß"', 2.5)], path)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "Datum;Name;Betrag")
            self.assertEqual(lines[1], "2026-10-01;Erste;-1,00")
            self.assertEqual(lines[2], '2026-10-02;"Kaffee; ""groß""";-2,50')
            txs = Parser.parse_bank_statement(str(path))
            self.assertEqual([t["description"] for t in txs], ["Erste", 'Kaffee; "groß"'])
            self.assertEqual([t["amount"] for t in txs], [-1.0, -2.5])

    def test_append_to_file_without_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANUAL_FILENAME
            path.write_text("Datum;Name;Betrag\n2026-10-01;Alt;-1,00", encoding="utf-8")
            append_manual_expenses([("2026-10-02", "Neu", 2.0)], path)
            self.assertEqual(len(Parser.parse_bank_statement(str(path))), 2)

    def test_filename_not_bank_prefixed(self):
        self.assertFalse(MANUAL_FILENAME.lower().startswith("bank"))

    def test_delete_removes_exact_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANUAL_FILENAME
            append_manual_expenses(
                [("2026-10-01", "A", 1.0), ("2026-10-02", "B;x", 2.0), ("2026-10-03", "C", 3.0)],
                path,
            )
            entries = list_manual_expenses(path)
            self.assertEqual([e["description"] for e in entries], ["A", "B;x", "C"])
            target = entries[1]
            self.assertFalse(delete_manual_expense(target["index"], "wrong", path))
            self.assertFalse(delete_manual_expense(99, target["token"], path))
            self.assertTrue(delete_manual_expense(target["index"], target["token"], path))
            remaining = list_manual_expenses(path)
            self.assertEqual([e["description"] for e in remaining], ["A", "C"])
            # Stale token (row at index 1 is now "C") must not delete anything.
            self.assertFalse(delete_manual_expense(1, target["token"], path))
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], "Datum;Name;Betrag")


class ManualExpensesWebTests(unittest.TestCase):
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
        self.addCleanup(reset_store)
        return TestClient(create_app())

    @staticmethod
    def _flash(response, key):
        return parse_qs(urlparse(response.headers["location"]).query).get(key, [""])[0]

    def _post(self, client, rows):
        return client.post(
            "/manual/add",
            data={
                "date": [r[0] for r in rows],
                "description": [r[1] for r in rows],
                "amount": [r[2] for r in rows],
            },
            follow_redirects=False,
        )

    def test_page_renders_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            html = client.get("/").text
            self.assertIn("Ausgaben hinzufügen", html)
            self.assertIn("+ Zeile", html)
            self.assertIn(MANUAL_FILENAME, html)
            self.assertEqual(html.count('name="description"'), 1)

    def test_post_rows_creates_csv_and_transactions(self):
        from web.deps import get_store

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            response = self._post(client, [
                ("2026-10-03", "Bäckerei Test", "12,50"),
                ("2026-10-04", "Tanken; Shell", "1.234,56"),
                ("2026-10-05", "Kiosk", "-3.20"),
                ("2026-10-05", "", ""),
            ])
            self.assertEqual(response.status_code, 303)
            self.assertEqual(self._flash(response, "message"), "3 Ausgabe(n) hinzugefügt.")
            path = Path(tmp) / "BankStatements" / MANUAL_FILENAME
            with open(path, encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh, delimiter=";"))
            self.assertEqual(rows, [
                ["Datum", "Name", "Betrag"],
                ["2026-10-03", "Bäckerei Test", "-12,50"],
                ["2026-10-04", "Tanken; Shell", "-1234,56"],
                ["2026-10-05", "Kiosk", "-3,20"],
            ])
            store = get_store()
            manual = [t for t in store.transactions if t["file"] == MANUAL_FILENAME]
            self.assertEqual(
                [(t["date"], t["description"], t["amount"]) for t in manual],
                [
                    ("2026-10-03", "Bäckerei Test", -12.5),
                    ("2026-10-04", "Tanken; Shell", -1234.56),
                    ("2026-10-05", "Kiosk", -3.2),
                ],
            )
            self.assertTrue(all(t["category"] for t in manual))
            self.assertEqual(store.months(), ["2026-10"])
            # Survives a fresh reload from disk.
            store.reload()
            self.assertEqual(len([t for t in store.transactions if t["file"] == MANUAL_FILENAME]), 3)

            page = client.get("/?month=2026-10").text
            self.assertIn("03.10.2026", page)
            self.assertIn("Tanken; Shell", page)

    def test_invalid_row_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            response = self._post(client, [
                ("2026-10-03", "Gültig", "5,00"),
                ("2026-10-04", "Ungültig", "abc"),
            ])
            self.assertEqual(response.status_code, 303)
            self.assertIn("Zeile 2", self._flash(response, "error"))
            self.assertFalse((Path(tmp) / "BankStatements" / MANUAL_FILENAME).exists())
            page = client.get("/?error=" + "Zeile%202").text
            self.assertIn('class="flash err"', page)

    def test_append_keeps_earlier_rows_and_delete(self):
        from web.deps import get_store

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            self._post(client, [("2026-10-01", "Alt", "1,00")])
            self._post(client, [("2026-10-02", "Neu", "2,00"), ("2026-10-03", "Dritte", "3")])
            path = Path(tmp) / "BankStatements" / MANUAL_FILENAME
            entries = list_manual_expenses(path)
            self.assertEqual([e["description"] for e in entries], ["Alt", "Neu", "Dritte"])
            self.assertIn("Löschen", client.get("/").text)

            target = entries[1]
            response = client.post(
                "/manual/delete",
                data={"index": target["index"], "token": target["token"]},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(self._flash(response, "message"), "Ausgabe gelöscht.")
            descs = [t["description"] for t in get_store().transactions if t["file"] == MANUAL_FILENAME]
            self.assertEqual(descs, ["Alt", "Dritte"])

            response = client.post(
                "/manual/delete",
                data={"index": target["index"], "token": target["token"]},
                follow_redirects=False,
            )
            self.assertTrue(self._flash(response, "error"))
            self.assertEqual(len(list_manual_expenses(path)), 2)


if __name__ == "__main__":
    unittest.main()
