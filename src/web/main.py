"""FastAPI application factory and HTML routes for the expense web MVP."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app_paths import statements_dir, user_data_dir
from web.deps import AuthDep, StoreDep, data_root, ensure_data_dirs, get_store
from web.export_helpers import (
    build_yearly_statistics_report,
    category_totals,
    default_export_selected,
    listed_amount_sum,
    selected_expenses_for_export,
)
from web.receipts_routes import register_receipts_routes
from web.receipts_state import ensure_receipts_dirs, receipts_dir

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _parse_keywords(raw: str) -> list[str]:
    """Split keywords on commas, semicolons, or newlines."""
    return [k.strip().lower() for k in re.split(r"[,;\n]+", raw or "") if k.strip()]


def create_app() -> FastAPI:
    ensure_data_dirs()
    ensure_receipts_dirs()
    app = FastAPI(title="Expense App Web", docs_url=None, redoc_url=None)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def transactions(
        request: Request,
        _: AuthDep,
        store: StoreDep,
        category: str = "All",
        month: str = "All",
        q: str = "",
        message: str = "",
    ):
        frame = store.filtered(category=category or "All", month=month or "All", query=q or "")
        rows = []
        if not frame.empty:
            display = frame.copy()
            display["date"] = display["date"].dt.strftime("%d.%m.%Y")
            rows = display[["date", "description", "amount", "category", "file"]].to_dict(
                orient="records"
            )
        return templates.TemplateResponse(
            request,
            "transactions.html",
            {
                "title": "Transaktionen",
                "rows": rows,
                "categories": ["All"] + store.categorizer.get_all_categories(),
                "months": ["All"] + store.months(),
                "category": category or "All",
                "month": month or "All",
                "q": q or "",
                "listed_sum": listed_amount_sum(frame),
                "message": message,
                "statements_path": str(statements_dir()),
                "import_reports": store.import_reports,
                "nav": "transactions",
            },
        )

    @app.post("/upload")
    async def upload_csv(
        request: Request,
        _: AuthDep,
        store: StoreDep,
        file: UploadFile = File(...),
    ):
        if not file.filename or not file.filename.lower().endswith(".csv"):
            return RedirectResponse(
                "/?message=Nur+CSV-Dateien+werden+unterst%C3%BCtzt.",
                status_code=303,
            )
        # Persist into BankStatements so "Ordner scannen" picks the file up too.
        upload_dir = statements_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename).name
        target = upload_dir / safe_name
        content = await file.read()
        target.write_bytes(content)
        store.reload()
        return RedirectResponse(
            f"/?message=Importiert%3A+{safe_name}",
            status_code=303,
        )

    @app.post("/scan")
    async def scan_folder(_: AuthDep, store: StoreDep):
        store.reload()
        count = len(store.scanner.scan_for_csvs())
        return RedirectResponse(
            f"/?message=Ordner+gescannt.+{count}+CSV-Datei%28en%29+gefunden.",
            status_code=303,
        )

    @app.get("/rules", response_class=HTMLResponse)
    async def rules_page(
        request: Request,
        _: AuthDep,
        store: StoreDep,
        message: str = "",
        error: str = "",
    ):
        rules = sorted(
            store.categorizer.rules,
            key=lambda rule: str(rule.get("category", "")).casefold(),
        )
        return templates.TemplateResponse(
            request,
            "rules.html",
            {
                "title": "Kategorien",
                "rules": rules,
                "message": message,
                "error": error,
                "nav": "rules",
            },
        )

    @app.post("/rules/add")
    async def rules_add(
        _: AuthDep,
        store: StoreDep,
        category: str = Form(...),
        keywords: str = Form(...),
    ):
        category = category.strip()
        keyword_list = _parse_keywords(keywords)
        if not category or not keyword_list:
            return RedirectResponse(
                "/rules?error=Kategorie+und+mindestens+ein+Stichwort+erforderlich.",
                status_code=303,
            )
        store.categorizer.add_rule(keyword_list, category)
        store.reload(selected_files=store.selected_files)
        return RedirectResponse("/rules?message=Kategorie+gespeichert.", status_code=303)

    @app.post("/rules/update")
    async def rules_update(
        _: AuthDep,
        store: StoreDep,
        category: str = Form(...),
        keywords: str = Form(...),
    ):
        category = category.strip()
        keyword_list = _parse_keywords(keywords)
        if not category:
            return RedirectResponse("/rules?error=Kategorie+fehlt.", status_code=303)
        ok = store.categorizer.update_rule_keywords(category, keyword_list)
        if not ok:
            return RedirectResponse(
                f"/rules?error=Kategorie+{category}+nicht+gefunden.",
                status_code=303,
            )
        store.reload(selected_files=store.selected_files)
        return RedirectResponse("/rules?message=Kategorie+aktualisiert.", status_code=303)

    @app.post("/rules/delete")
    async def rules_delete(
        _: AuthDep,
        store: StoreDep,
        category: str = Form(...),
    ):
        category = category.strip()
        if category:
            store.categorizer.delete_rule(category)
            store.reload(selected_files=store.selected_files)
        return RedirectResponse(f"/rules?message={quote('Kategorie gelöscht.')}", status_code=303)

    @app.post("/rules/import")
    async def rules_import(
        _: AuthDep,
        store: StoreDep,
        file: UploadFile = File(...),
    ):
        if not file.filename:
            return RedirectResponse("/rules?error=Keine+Datei.", status_code=303)
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".json", ".csv"}:
            return RedirectResponse(
                "/rules?error=Nur+JSON+oder+CSV.",
                status_code=303,
            )
        raw = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            store.categorizer.import_rules_from_path(tmp_path)
            store.reload(selected_files=store.selected_files)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            return RedirectResponse(
                f"/rules?error={str(error)[:120]}",
                status_code=303,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return RedirectResponse("/rules?message=Kategorien+importiert.", status_code=303)

    @app.post("/rules/restore")
    async def rules_restore(_: AuthDep, store: StoreDep):
        success, message = store.categorizer.restore_latest_backup()
        store.reload(selected_files=store.selected_files)
        param = "message" if success else "error"
        return RedirectResponse(f"/rules?{param}={quote(message)}", status_code=303)

    @app.get("/stats")
    async def stats_redirect(_: AuthDep):
        return RedirectResponse("/auswertung", status_code=302)

    @app.get("/auswertung", response_class=HTMLResponse)
    async def auswertung_page(
        request: Request,
        _: AuthDep,
        store: StoreDep,
        categories: list[str] = Query(default=[]),
        submitted: str = "",
        message: str = "",
        error: str = "",
    ):
        totals = category_totals(store.dataframe)
        all_categories = (
            sorted((str(c) for c in totals["Category"]), key=str.casefold)
            if not totals.empty
            else []
        )
        if submitted:
            selected = [c for c in categories if c in all_categories]
        else:
            selected = [c for c in all_categories if default_export_selected(c)]

        expenses = selected_expenses_for_export(store.dataframe, selected)
        report = build_yearly_statistics_report(expenses)
        has_expenses = not expenses.empty

        return templates.TemplateResponse(
            request,
            "auswertung.html",
            {
                "title": "Auswertung",
                "categories": all_categories,
                "selected_categories": selected,
                "report": report,
                "has_expenses": has_expenses,
                "message": message,
                "error": error,
                "nav": "auswertung",
            },
        )

    register_receipts_routes(app, templates)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "data_dir": str(data_root()),
            "rules_path": str(user_data_dir() / "rules.json"),
            "statements_dir": str(statements_dir()),
            "receipts_dir": str(receipts_dir()),
        }

    # Eagerly build the store so the first request is warm.
    get_store()
    return app


app = create_app()
