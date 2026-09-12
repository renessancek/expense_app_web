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

from app_paths import expense_data_dir, statements_dir, user_data_dir  # noqa: E402
from web.export_helpers import (  # noqa: E402
    category_totals,
    listed_amount_sum,
    selected_expenses_for_export,
    write_yearly_statistics_export,
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

    def test_excel_export_bytes(self):
        expenses = selected_expenses_for_export(
            self._frame(), ["Supermarkt", "Amazon"]
        )
        payload = write_yearly_statistics_export(
            expenses,
            ["Supermarkt", "Amazon"],
            [{"category": "Supermarkt", "keywords": ["rewe"]}],
        )
        self.assertTrue(payload.startswith(b"PK"))  # zip/xlsx magic


if __name__ == "__main__":
    unittest.main()
