"""Category totals and yearly statistics report helpers (parity with the desktop app)."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

DEFAULT_UNSELECTED_CATEGORIES = {"Abhebung", "Investments", "Firma", "Privat", "Paypal"}
# Backward-compatible alias
DEFAULT_UNSELECTED_EXPORT_CATEGORIES = DEFAULT_UNSELECTED_CATEGORIES


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


def _configured_categories_table(rules: list[dict]) -> dict:
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
    return _table(configured_categories)


def build_yearly_statistics_report(expenses: pd.DataFrame, rules: list[dict]) -> dict:
    """Build the multi-section annual report as HTML-ready structures.

    Mirrors the former Excel sheets from write_yearly_statistics_export:
    per-month tables, Monthly Totals, Average Monthly Expenses, Yearly Comparison,
    Yearly Summary, and Configured Categories.
    """
    configured = _configured_categories_table(rules)
    empty_monthly_totals = {"columns": ["Month", "amount"], "rows": []}
    empty_averages = {"columns": ["category", "average_per_month"], "rows": []}
    empty_comparison = {"columns": ["category"], "rows": []}
    empty_yearly = {"columns": ["Year", "category", "amount"], "rows": []}

    if expenses is None or getattr(expenses, "empty", True):
        return {
            "monthly_tables": [],
            "monthly_totals": empty_monthly_totals,
            "average_monthly": empty_averages,
            "yearly_comparison": empty_comparison,
            "yearly_summary": empty_yearly,
            "configured_categories": configured,
        }

    monthly_tables: list[dict] = []
    for month in sorted(expenses["Month"].unique()):
        summary = (
            expenses[expenses["Month"] == month]
            .groupby("category", as_index=False)["amount"]
            .sum()
            .sort_values("amount", ascending=False)
        )
        total = pd.DataFrame([{"category": "TOTAL", "amount": summary["amount"].sum()}])
        monthly_tables.append(
            {
                "title": str(month),
                **_table(pd.concat([summary, total], ignore_index=True)),
            }
        )

    monthly_totals = expenses.groupby("Month", as_index=False)["amount"].sum().sort_values("Month")
    monthly_total = pd.DataFrame(
        [{"Month": "GRAND TOTAL", "amount": monthly_totals["amount"].sum()}]
    )
    monthly_totals_table = _table(pd.concat([monthly_totals, monthly_total], ignore_index=True))

    months = expenses["Month"].nunique()
    averages = expenses.groupby("category", as_index=False)["amount"].sum()
    averages["average_per_month"] = (averages["amount"] / months).round(2)
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
        [{**{"category": "TOTAL"}, **comparison[year_columns].sum().to_dict()}]
    )
    comparison = pd.concat([comparison, comparison_total], ignore_index=True)
    comparison.columns = [str(c) for c in comparison.columns]
    comparison_table = _table(comparison)

    yearly_summary = (
        expenses.groupby(["Year", "category"], as_index=False)["amount"]
        .sum()
        .sort_values(["Year", "amount"], ascending=[False, False])
    )
    yearly_total = pd.DataFrame(
        [{"Year": "GRAND TOTAL", "category": "-", "amount": yearly_summary["amount"].sum()}]
    )
    yearly_summary_table = _table(pd.concat([yearly_summary, yearly_total], ignore_index=True))

    return {
        "monthly_tables": monthly_tables,
        "monthly_totals": monthly_totals_table,
        "average_monthly": average_table,
        "yearly_comparison": comparison_table,
        "yearly_summary": yearly_summary_table,
        "configured_categories": configured,
    }


def default_export_selected(category: str) -> bool:
    return category not in DEFAULT_UNSELECTED_CATEGORIES
