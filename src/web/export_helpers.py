"""Category totals and yearly statistics report helpers."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

DEFAULT_UNSELECTED_CATEGORIES = {"Abhebung", "Investments", "Firma", "Privat", "Paypal"}
# Backward-compatible alias
DEFAULT_UNSELECTED_EXPORT_CATEGORIES = DEFAULT_UNSELECTED_CATEGORIES

TOTAL_LABEL = "Summe"


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


def _native(value):
    """Convert pandas/numpy scalars to plain Python values for templates."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def _table(df: pd.DataFrame) -> dict:
    """Turn a DataFrame into HTML-ready columns + list-of-dicts rows."""
    columns = [str(c) for c in df.columns]
    rows = []
    for record in df.to_dict(orient="records"):
        rows.append({str(k): _native(v) for k, v in record.items()})
    return {"columns": columns, "rows": rows}


def _month_table(expenses: pd.DataFrame, month: str) -> dict:
    summary = (
        expenses[expenses["Month"] == month]
        .groupby("category", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
    )
    total = pd.DataFrame([{"category": TOTAL_LABEL, "amount": summary["amount"].sum()}])
    return {
        "title": str(month),
        **_table(pd.concat([summary, total], ignore_index=True)),
    }


def _monthly_totals_for_year(year_expenses: pd.DataFrame) -> dict:
    monthly_totals = (
        year_expenses.groupby("Month", as_index=False)["amount"]
        .sum()
        .sort_values("Month", ascending=False)
    )
    total = pd.DataFrame(
        [{"Month": TOTAL_LABEL, "amount": monthly_totals["amount"].sum()}]
    )
    return _table(pd.concat([monthly_totals, total], ignore_index=True))


def _yearly_summary_for_year(year_expenses: pd.DataFrame, year: int) -> dict:
    summary = (
        year_expenses.groupby("category", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
    )
    summary.insert(0, "Year", year)
    total = pd.DataFrame(
        [{"Year": year, "category": TOTAL_LABEL, "amount": summary["amount"].sum()}]
    )
    return _table(pd.concat([summary, total], ignore_index=True))


def build_yearly_statistics_report(expenses: pd.DataFrame) -> dict:
    """Build the multi-section annual report as HTML-ready structures.

    Years are newest-first. Each year carries its month tables (newest first),
    monthly totals, and category summary. Global sections: average monthly
    expenses and yearly comparison. (Configured Categories live under Regeln.)
    """
    empty_averages = {"columns": ["category", "average_per_month"], "rows": []}
    empty_comparison = {"columns": ["category"], "rows": []}

    if expenses is None or getattr(expenses, "empty", True):
        return {
            "years": [],
            "average_monthly": empty_averages,
            "yearly_comparison": empty_comparison,
        }

    years_out: list[dict] = []
    for year in sorted(expenses["Year"].unique(), reverse=True):
        year_expenses = expenses[expenses["Year"] == year]
        months = sorted(year_expenses["Month"].unique(), reverse=True)
        years_out.append(
            {
                "year": int(year),
                "monthly_tables": [_month_table(year_expenses, m) for m in months],
                "monthly_totals": _monthly_totals_for_year(year_expenses),
                "yearly_summary": _yearly_summary_for_year(year_expenses, int(year)),
            }
        )

    months_count = expenses["Month"].nunique()
    averages = expenses.groupby("category", as_index=False)["amount"].sum()
    averages["average_per_month"] = (averages["amount"] / months_count).round(2)
    averages = averages[["category", "average_per_month"]].sort_values(
        "average_per_month", ascending=False
    )
    average_table = _table(averages)

    comparison = expenses.pivot_table(
        index="category", columns="Year", values="amount", aggfunc="sum", fill_value=0
    ).reset_index()
    year_columns = [column for column in comparison.columns if column != "category"]
    comparison["_total"] = comparison[year_columns].sum(axis=1)
    comparison = comparison.sort_values("_total", ascending=False).drop(columns="_total")
    comparison_total = pd.DataFrame(
        [{**{"category": TOTAL_LABEL}, **comparison[year_columns].sum().to_dict()}]
    )
    comparison = pd.concat([comparison, comparison_total], ignore_index=True)
    comparison.columns = [str(c) for c in comparison.columns]
    comparison_table = _table(comparison)

    return {
        "years": years_out,
        "average_monthly": average_table,
        "yearly_comparison": comparison_table,
    }


def default_export_selected(category: str) -> bool:
    return category not in DEFAULT_UNSELECTED_CATEGORIES
