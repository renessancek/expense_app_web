"""Category totals and Excel export helpers (parity with the desktop app)."""

from __future__ import annotations

import io
from typing import Iterable

import pandas as pd

DEFAULT_UNSELECTED_EXPORT_CATEGORIES = {"Abhebung", "Investments", "Firma", "Privat", "Paypal"}


def listed_amount_sum(frame: pd.DataFrame | None) -> float:
    """Return the numeric sum of amounts in a filtered transaction list."""
    if frame is None or getattr(frame, "empty", True) or "amount" not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame["amount"], errors="coerce").fillna(0).sum())


def category_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """Return absolute expense totals per category, sorted descending."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Category", "Total spent (€)"])
    expenses = frame[frame["amount"] < 0].copy()
    if expenses.empty:
        return pd.DataFrame(columns=["Category", "Total spent (€)"])
    expenses["amount"] = expenses["amount"].abs()
    return (
        expenses.groupby("category", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
        .rename(columns={"category": "Category", "amount": "Total spent (€)"})
    )


def selected_expenses_for_export(frame: pd.DataFrame, categories: Iterable[str]) -> pd.DataFrame:
    """Return selected expense transactions with month and year columns."""
    categories = list(categories)
    expenses = frame[(frame["amount"] < 0) & frame["category"].isin(categories)].copy()
    if expenses.empty:
        return expenses
    expenses["amount"] = expenses["amount"].abs()
    expenses["Month"] = expenses["date"].dt.strftime("%Y-%m")
    expenses["Year"] = expenses["date"].dt.year
    return expenses


def write_yearly_statistics_export(
    expenses: pd.DataFrame,
    categories: Iterable[str],
    rules: list[dict],
) -> bytes:
    """Write the multi-sheet annual report and return the .xlsx bytes."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for month in sorted(expenses["Month"].unique()):
            summary = (
                expenses[expenses["Month"] == month]
                .groupby("category", as_index=False)["amount"]
                .sum()
                .sort_values("amount", ascending=False)
            )
            total = pd.DataFrame([{"category": "TOTAL", "amount": summary["amount"].sum()}])
            pd.concat([summary, total], ignore_index=True).to_excel(writer, sheet_name=month, index=False)

        monthly_totals = expenses.groupby("Month", as_index=False)["amount"].sum().sort_values("Month")
        monthly_total = pd.DataFrame(
            [{"Month": "GRAND TOTAL", "amount": monthly_totals["amount"].sum()}]
        )
        pd.concat([monthly_totals, monthly_total], ignore_index=True).to_excel(
            writer, sheet_name="Monthly Totals", index=False
        )

        months = expenses["Month"].nunique()
        averages = expenses.groupby("category", as_index=False)["amount"].sum()
        averages["average_per_month"] = (averages["amount"] / months).round(2)
        averages = averages[["category", "average_per_month"]].sort_values(
            "average_per_month", ascending=False
        )
        averages.to_excel(writer, sheet_name="Average Monthly Expenses", index=False)
        average_sheet = writer.sheets["Average Monthly Expenses"]
        for cell in average_sheet["B"][1:]:
            cell.number_format = "#,##0.##"

        comparison = expenses.pivot_table(
            index="category", columns="Year", values="amount", aggfunc="sum", fill_value=0
        ).reset_index()
        year_columns = [column for column in comparison.columns if column != "category"]
        comparison["_total"] = comparison[year_columns].sum(axis=1)
        comparison = comparison.sort_values("_total", ascending=False).drop(columns="_total")
        comparison_total = pd.DataFrame(
            [{**{"category": "TOTAL"}, **comparison[year_columns].sum().to_dict()}]
        )
        pd.concat([comparison, comparison_total], ignore_index=True).to_excel(
            writer, sheet_name="Yearly Comparison", index=False
        )

        yearly_summary = (
            expenses.groupby(["Year", "category"], as_index=False)["amount"]
            .sum()
            .sort_values(["Year", "amount"], ascending=[False, False])
        )
        yearly_total = pd.DataFrame(
            [{"Year": "GRAND TOTAL", "category": "-", "amount": yearly_summary["amount"].sum()}]
        )
        pd.concat([yearly_summary, yearly_total], ignore_index=True).to_excel(
            writer, sheet_name="Yearly Summary", index=False
        )

        configured: dict[str, set[str]] = {}
        for rule in rules:
            category = rule.get("category")
            if category:
                configured.setdefault(category, set()).update(rule.get("keywords", []))
        configured_categories = pd.DataFrame(
            [
                {"category": category, "keywords": ", ".join(sorted(keywords))}
                for category, keywords in sorted(
                    configured.items(), key=lambda item: item[0].casefold()
                )
            ],
            columns=["category", "keywords"],
        )
        configured_categories.to_excel(writer, sheet_name="Configured Categories", index=False)

    return buffer.getvalue()


def default_export_selected(category: str) -> bool:
    return category not in DEFAULT_UNSELECTED_EXPORT_CATEGORIES
