"""Category totals and yearly statistics report helpers."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

DEFAULT_UNSELECTED_CATEGORIES = {"Abhebung", "Investments", "Firma", "Privat", "Paypal"}
# Backward-compatible alias
DEFAULT_UNSELECTED_EXPORT_CATEGORIES = DEFAULT_UNSELECTED_CATEGORIES

TOTAL_LABEL = "Summe"

MONTH_NAMES_DE = {
    1: "Januar",
    2: "Februar",
    3: "März",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Dezember",
}


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
    month_total = float(summary["amount"].sum()) if not summary.empty else 0.0
    total = pd.DataFrame([{"category": TOTAL_LABEL, "amount": month_total}])
    return {
        "title": str(month),
        "total": month_total,
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


def _average_monthly_by_year(expenses: pd.DataFrame) -> dict:
    """Category × year pivot of (year total / months present in that year)."""
    pieces: list[pd.DataFrame] = []
    for year in sorted(expenses["Year"].unique()):
        year_expenses = expenses[expenses["Year"] == year]
        months_count = int(year_expenses["Month"].nunique())
        if months_count <= 0:
            continue
        by_category = year_expenses.groupby("category", as_index=False)["amount"].sum()
        by_category["average_per_month"] = (by_category["amount"] / months_count).round(2)
        by_category["Year"] = int(year)
        pieces.append(by_category[["category", "Year", "average_per_month"]])

    if not pieces:
        return {"columns": ["category"], "rows": []}

    long = pd.concat(pieces, ignore_index=True)
    pivot = long.pivot_table(
        index="category",
        columns="Year",
        values="average_per_month",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    year_columns = [column for column in pivot.columns if column != "category"]
    pivot["_total"] = pivot[year_columns].sum(axis=1)
    pivot = pivot.sort_values("_total", ascending=False).drop(columns="_total")
    total = pd.DataFrame(
        [{**{"category": TOTAL_LABEL}, **pivot[year_columns].sum().round(2).to_dict()}]
    )
    pivot = pd.concat([pivot, total], ignore_index=True)
    pivot.columns = [str(c) for c in pivot.columns]
    return _table(pivot)


def _monthly_yearly_comparison(expenses: pd.DataFrame) -> dict:
    """Calendar month × year pivot of absolute expense totals."""
    frame = expenses.copy()
    frame["month_num"] = frame["date"].dt.month
    pivot = frame.pivot_table(
        index="month_num",
        columns="Year",
        values="amount",
        aggfunc="sum",
        fill_value=0,
    )
    for month_num in range(1, 13):
        if month_num not in pivot.index:
            pivot.loc[month_num] = 0
    pivot = pivot.sort_index()
    year_columns = list(pivot.columns)
    pivot = pivot.reset_index()
    pivot.insert(0, "month", pivot["month_num"].map(MONTH_NAMES_DE))
    pivot = pivot.drop(columns=["month_num"])
    total = pd.DataFrame(
        [{**{"month": TOTAL_LABEL}, **pivot[year_columns].sum().to_dict()}]
    )
    pivot = pd.concat([pivot, total], ignore_index=True)
    pivot.columns = [str(c) for c in pivot.columns]
    return _table(pivot)


def build_yearly_statistics_report(expenses: pd.DataFrame) -> dict:
    """Build the multi-section annual report as HTML-ready structures.

    Years are newest-first. Each year carries its month tables (newest first),
    monthly totals, and category summary. Global sections: average monthly
    expenses (per year), category yearly comparison, and month × year totals.
    """
    empty_averages = {"columns": ["category"], "rows": []}
    empty_comparison = {"columns": ["category"], "rows": []}
    empty_monthly_yearly = {"columns": ["month"], "rows": []}

    if expenses is None or getattr(expenses, "empty", True):
        return {
            "years": [],
            "average_monthly": empty_averages,
            "yearly_comparison": empty_comparison,
            "monthly_yearly_comparison": empty_monthly_yearly,
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

    average_table = _average_monthly_by_year(expenses)

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
        "monthly_yearly_comparison": _monthly_yearly_comparison(expenses),
    }


def default_export_selected(category: str) -> bool:
    return category not in DEFAULT_UNSELECTED_CATEGORIES
