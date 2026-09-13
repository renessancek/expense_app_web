"""Minimal unit tests for web path helpers and export helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

# Ensure src/ is on the path the same way unittest discover uses -t .
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app_paths import expense_data_dir, statements_dir, user_data_dir  # noqa: E402
from web.export_helpers import (  # noqa: E402
    build_yearly_statistics_report,
    category_totals,
    listed_amount_sum,
    selected_expenses_for_export,
)


class ExpenseDataDirTests(unittest.TestCase):
    def test_expense_data_dir_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXPENSE_DATA_DIR", None)
            self.assertIsNone(expense_data_dir())

    def test_user_data_and_statements_honor_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXPENSE_DATA_DIR": tmp}):
                self.assertEqual(user_data_dir(), Path(tmp))
                self.assertEqual(statements_dir(), Path(tmp) / "BankStatements")


class ExportHelperTests(unittest.TestCase):
    def _frame(self):
        return pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2024-01-15"),
                    "description": "REWE Markt",
                    "amount": -23.5,
                    "category": "Supermarkt",
                    "file": "a.csv",
                },
                {
                    "date": pd.Timestamp("2024-01-20"),
                    "description": "Amazon",
                    "amount": -10.0,
                    "category": "Amazon",
                    "file": "a.csv",
                },
                {
                    "date": pd.Timestamp("2024-02-01"),
                    "description": "Salary",
                    "amount": 1000.0,
                    "category": "Sonstiges",
                    "file": "a.csv",
                },
            ]
        )

    def test_listed_amount_sum(self):
        frame = self._frame()
        self.assertAlmostEqual(listed_amount_sum(frame), 966.5)

    def test_category_totals_expenses_only(self):
        totals = category_totals(self._frame())
        self.assertEqual(list(totals["Category"]), ["Supermarkt", "Amazon"])
        self.assertAlmostEqual(float(totals.iloc[0]["Total spent (€)"]), 23.5)

    def test_build_yearly_statistics_report(self):
        expenses = selected_expenses_for_export(
            self._frame(), ["Supermarkt", "Amazon"]
        )
        report = build_yearly_statistics_report(
            expenses,
            [{"category": "Supermarkt", "keywords": ["rewe"]}],
        )

        self.assertEqual(len(report["monthly_tables"]), 1)
        jan = report["monthly_tables"][0]
        self.assertEqual(jan["title"], "2024-01")
        self.assertEqual(jan["columns"], ["category", "amount"])
        self.assertEqual(jan["rows"][-1]["category"], "TOTAL")
        self.assertAlmostEqual(jan["rows"][-1]["amount"], 33.5)

        totals_rows = report["monthly_totals"]["rows"]
        self.assertEqual(totals_rows[-1]["Month"], "GRAND TOTAL")
        self.assertAlmostEqual(totals_rows[-1]["amount"], 33.5)

        averages = {row["category"]: row["average_per_month"] for row in report["average_monthly"]["rows"]}
        self.assertAlmostEqual(averages["Supermarkt"], 23.5)
        self.assertAlmostEqual(averages["Amazon"], 10.0)

        comparison = report["yearly_comparison"]
        self.assertIn("category", comparison["columns"])
        self.assertIn("2024", comparison["columns"])
        self.assertEqual(comparison["rows"][-1]["category"], "TOTAL")

        yearly = report["yearly_summary"]["rows"]
        self.assertEqual(yearly[-1]["Year"], "GRAND TOTAL")
        self.assertAlmostEqual(yearly[-1]["amount"], 33.5)

        configured = report["configured_categories"]["rows"]
        self.assertEqual(configured[0]["category"], "Supermarkt")
        self.assertEqual(configured[0]["keywords"], "rewe")

    def test_build_yearly_statistics_report_empty(self):
        empty = selected_expenses_for_export(self._frame(), ["NichtVorhanden"])
        report = build_yearly_statistics_report(empty, [])
        self.assertEqual(report["monthly_tables"], [])
        self.assertEqual(report["monthly_totals"]["rows"], [])


class TransactionsImportReportsTemplateTests(unittest.TestCase):
    def _render(self, **overrides):
        templates_dir = ROOT / "src" / "web" / "templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)))
        context = {
            "title": "Transaktionen",
            "nav": "transactions",
            "message": "",
            "statements_path": "/data/BankStatements",
            "rows": [],
            "categories": ["All"],
            "months": ["All"],
            "category": "All",
            "month": "All",
            "q": "",
            "listed_sum": 0.0,
            "import_reports": [],
        }
        context.update(overrides)
        return env.get_template("transactions.html").render(**context)

    def test_empty_reports_show_placeholder(self):
        html = self._render()
        self.assertIn("Importierte Dateien (0)", html)
        self.assertIn("Noch keine Dateien geladen", html)

    def test_reports_list_source_files_and_status(self):
        html = self._render(
            import_reports=[
                {
                    "File": "EASYBANK_statement.csv",
                    "status": "Imported",
                    "rows_read": 2,
                    "imported_expenses": 1,
                    "skipped_non_expenses": 1,
                    "skipped_missing_data": 0,
                    "skipped_excluded": 0,
                    "skipped_errors": 0,
                    "details": "",
                },
                {
                    "File": "broken.csv",
                    "status": "Not imported",
                    "rows_read": 0,
                    "imported_expenses": 0,
                    "skipped_non_expenses": 0,
                    "skipped_missing_data": 0,
                    "skipped_excluded": 0,
                    "skipped_errors": 0,
                    "details": "Could not read a supported CSV format or find an amount column.",
                },
            ]
        )
        self.assertIn("Importierte Dateien (2)", html)
        self.assertIn("EASYBANK_statement.csv", html)
        self.assertIn("broken.csv", html)
        self.assertIn("Importiert", html)
        self.assertIn("Nicht importiert", html)
        self.assertIn("Could not read a supported CSV format", html)


if __name__ == "__main__":
    unittest.main()
