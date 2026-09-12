"""Shared transaction loading and filtering for the Expense App."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


REPORT_COLUMNS = [
    "File", "status", "rows_read", "imported_expenses", "skipped_non_expenses",
    "skipped_missing_data", "skipped_excluded", "skipped_errors", "details",
]


class ExpenseDataStore:
    """Keeps the imported transactions and applies the transaction-list filters."""

    def __init__(self, scanner, parser, categorizer):
        self.scanner = scanner
        self.parser = parser
        self.categorizer = categorizer
        self.transactions: list[dict] = []
        self.import_reports: list[dict] = []
        self.selected_files: list[str] = []

    def reload(self, selected_files=None):
        if selected_files is not None:
            self.selected_files = [str(path) for path in selected_files]

        self.transactions = []
        self.import_reports = []
        scanned_files = self.scanner.scan_for_csvs()
        self._load_files(scanned_files)
        scanned_paths = {os.path.normcase(os.path.abspath(path)) for path in scanned_files}
        imported_files = [
            path for path in self.selected_files
            if os.path.normcase(os.path.abspath(path)) not in scanned_paths
        ]
        self._load_files(imported_files)
        return self.transactions

    def _load_files(self, paths):
        for path in paths:
            transactions, report = self.parser.parse_bank_statement_with_report(path)
            report["File"] = os.path.basename(str(path))
            self.import_reports.append(report)
            for transaction in transactions:
                transaction = dict(transaction)
                transaction["file"] = os.path.basename(str(path))
                transaction["category"] = self.categorizer.suggest_category(transaction["description"])
                self.transactions.append(transaction)

    @property
    def dataframe(self):
        if not self.transactions:
            return pd.DataFrame(columns=["date", "description", "amount", "category", "file"])
        frame = pd.DataFrame(self.transactions).copy()
        frame["date"] = pd.to_datetime(frame["date"], dayfirst=True, format="mixed")
        frame["Month"] = frame["date"].dt.strftime("%Y-%m")
        return frame

    def months(self):
        frame = self.dataframe
        return sorted(frame["Month"].dropna().unique(), reverse=True) if not frame.empty else []

    def filtered(self, category="All", month="All", query=""):
        frame = self.dataframe
        if category != "All":
            frame = frame[frame["category"] == category]
        if month != "All":
            frame = frame[frame["Month"] == month]
        query = query.strip()
        if query:
            frame = frame[frame["description"].astype(str).str.contains(
                query, case=False, regex=False, na=False
            )]
        if frame.empty:
            return frame
        return frame.sort_values(by="date", ascending=False, kind="mergesort")

    def reports_dataframe(self):
        if not self.import_reports:
            return pd.DataFrame(columns=REPORT_COLUMNS)
        return pd.DataFrame(self.import_reports).reindex(columns=REPORT_COLUMNS)
