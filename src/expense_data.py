"""Shared transaction loading and filtering for the Expense App."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


_ISO_DATE_RE = r"^\d{4}-\d{1,2}-\d{1,2}(?:$|[ T])"


def parse_transaction_dates(values) -> pd.Series:
    """Parse transaction date strings into timestamps.

    Bank exports use day-first formats (``03.10.2026``), so the general path keeps
    ``dayfirst=True``. ISO dates (``2026-10-03``, e.g. from manual entries) would be
    misread as 10 March by that path, so they are parsed as ISO 8601 explicitly.
    """
    raw = pd.Series(values)
    if raw.empty:
        return pd.to_datetime(raw, dayfirst=True, format="mixed")
    text = raw.astype(str).str.strip()
    iso_mask = text.str.match(_ISO_DATE_RE)
    if not iso_mask.any():
        return pd.to_datetime(raw, dayfirst=True, format="mixed")
    parts = [pd.to_datetime(text[iso_mask], format="ISO8601")]
    if (~iso_mask).any():
        parts.append(pd.to_datetime(raw[~iso_mask], dayfirst=True, format="mixed"))
    # Align resolutions so concat keeps a datetime dtype (tz-aware parts stay as-is).
    parts = [part if part.dt.tz is not None else part.astype("datetime64[ns]") for part in parts]
    return pd.concat(parts).reindex(raw.index)


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
        frame["date"] = parse_transaction_dates(frame["date"])
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
