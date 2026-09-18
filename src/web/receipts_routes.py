"""Belege (OCR) HTML routes — extract only; never writes transactions."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from receipt_extractor import (
    RECEIPT_SUFFIXES,
    ReceiptExtractError,
    extract_receipt,
    extract_receipt_folder,
    list_receipt_files,
    receipt_import_row,
)
from web.deps import AuthDep, StoreDep
from web.receipts_state import (
    aggregate_receipt_items,
    ensure_receipts_dirs,
    find_row_by_path,
    format_date_de,
    format_month_de,
    format_status_de,
    format_total_de,
    get_last_rows,
    receipt_name_taken,
    receipts_dir,
    receipts_uploads_dir,
    remove_last_row_by_path,
    resolve_under_receipts,
    safe_upload_filename,
    set_last_rows,
)


def _receipt_folder_files():
    """List receipt files in receipts/ and receipts/uploads/ (non-recursive)."""
    ensure_receipts_dirs()
    files = []
    seen = set()
    for folder in (receipts_dir(), receipts_uploads_dir()):
        try:
            for path in list_receipt_files(folder):
                key = str(path.resolve())
                if key not in seen:
                    seen.add(key)
                    files.append(path)
        except ReceiptExtractError:
            continue
    return sorted(files, key=lambda p: p.name.lower())


def _extract_receipts_folders(categorizer=None):
    """Run folder extract on receipts root and uploads; merge rows."""
    ensure_receipts_dirs()
    rows = []
    seen = set()
    for folder in (receipts_dir(), receipts_uploads_dir()):
        try:
            folder_rows = extract_receipt_folder(folder, categorizer=categorizer)
        except ReceiptExtractError:
            continue
        for row in folder_rows:
            key = row.get("path")
            if key and key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def register_receipts_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """Attach Belege routes and Jinja filters to the FastAPI app."""
    templates.env.filters["status_de"] = format_status_de
    templates.env.filters["total_de"] = format_total_de
    templates.env.filters["date_de"] = format_date_de
    templates.env.filters["month_de"] = format_month_de

    @app.get("/receipts", response_class=HTMLResponse)
    async def receipts_page(
        request: Request,
        _: AuthDep,
        message: str = "",
        error: str = "",
    ):
        ensure_receipts_dirs()
        folder_files = _receipt_folder_files()
        rows = get_last_rows()
        item_totals = aggregate_receipt_items(rows)
        return templates.TemplateResponse(
            request,
            "receipts.html",
            {
                "title": "Belege",
                "nav": "receipts",
                "message": message,
                "error": error,
                "receipts_path": str(receipts_dir()),
                "folder_files": [
                    {"name": p.name, "path": str(p.resolve())} for p in folder_files
                ],
                "folder_count": len(folder_files),
                "rows": rows,
                "item_totals": item_totals,
            },
        )

    @app.post("/receipts/upload")
    async def receipts_upload(
        _: AuthDep,
        store: StoreDep,
        files: list[UploadFile] = File(...),
    ):
        ensure_receipts_dirs()
        upload_root = receipts_uploads_dir()
        rows = []
        saved = 0
        skipped = 0
        duplicates: list[str] = []
        claimed_names: set[str] = set()
        for upload in files or []:
            if not upload.filename:
                skipped += 1
                continue
            suffix = Path(upload.filename).suffix.lower()
            if suffix not in RECEIPT_SUFFIXES:
                skipped += 1
                continue
            base = safe_upload_filename(upload.filename)
            if base in claimed_names or receipt_name_taken(base):
                if base not in duplicates:
                    duplicates.append(base)
                continue
            target = upload_root / base
            content = await upload.read()
            target.write_bytes(content)
            claimed_names.add(base)
            saved += 1
            try:
                result = extract_receipt(target, categorizer=store.categorizer)
                rows.append(receipt_import_row(target, result=result))
            except ReceiptExtractError as err:
                rows.append(receipt_import_row(target, error=err))
            except Exception as err:
                rows.append(
                    receipt_import_row(
                        target, error=f"Could not extract the receipt: {err}"
                    )
                )
        if rows:
            previous = {
                r.get("path"): r for r in get_last_rows() if r.get("path")
            }
            for row in rows:
                previous[row.get("path")] = row
            set_last_rows(list(previous.values()))

        dup_msg = ""
        if duplicates:
            listed = ", ".join(duplicates)
            if len(duplicates) == 1:
                dup_msg = f"{listed} ist bereits vorhanden."
            else:
                dup_msg = f"Bereits vorhanden: {listed}."

        if saved == 0 and not duplicates:
            return RedirectResponse(
                f"/receipts?error={quote('Keine gültigen Bild-/PDF-Dateien hochgeladen.')}",
                status_code=303,
            )
        if saved == 0 and duplicates:
            return RedirectResponse(
                f"/receipts?error={quote(dup_msg)}",
                status_code=303,
            )

        msg = f"{saved} Datei(en) hochgeladen und extrahiert."
        if skipped:
            msg += f" {skipped} übersprungen."
        if dup_msg:
            msg += f" {dup_msg}"
        return RedirectResponse(f"/receipts?message={quote(msg)}", status_code=303)

    @app.post("/receipts/delete")
    async def receipts_delete(_: AuthDep, path: str = Form("")):
        resolved = resolve_under_receipts(path)
        if resolved is None:
            return RedirectResponse(
                f"/receipts?error={quote('Ungültiger Dateipfad.')}",
                status_code=303,
            )
        if not resolved.is_file():
            return RedirectResponse(
                f"/receipts?error={quote('Beleg-Datei nicht gefunden.')}",
                status_code=303,
            )
        name = resolved.name
        try:
            resolved.unlink()
        except OSError as err:
            return RedirectResponse(
                f"/receipts?error={quote(f'Datei konnte nicht gelöscht werden: {err}')}",
                status_code=303,
            )
        remove_last_row_by_path(resolved)
        return RedirectResponse(
            f"/receipts?message={quote(f'{name} gelöscht.')}",
            status_code=303,
        )

    @app.post("/receipts/scan")
    async def receipts_scan(_: AuthDep, store: StoreDep):
        ensure_receipts_dirs()
        rows = _extract_receipts_folders(categorizer=store.categorizer)
        set_last_rows(rows)
        if not rows:
            return RedirectResponse(
                f"/receipts?message={quote('Ordner gescannt. Keine Belege gefunden.')}",
                status_code=303,
            )
        ok = sum(1 for r in rows if r.get("status") == "Extracted")
        failed = len(rows) - ok
        msg = f"Ordner gescannt. {ok} extrahiert"
        if failed:
            msg += f", {failed} mit Fehler"
        msg += "."
        return RedirectResponse(f"/receipts?message={quote(msg)}", status_code=303)

    @app.get("/receipts/detail", response_class=HTMLResponse)
    async def receipts_detail(
        request: Request,
        _: AuthDep,
        store: StoreDep,
        path: str = "",
    ):
        resolved = resolve_under_receipts(path)
        if resolved is None:
            return RedirectResponse(
                f"/receipts?error={quote('Ungültiger Dateipfad.')}",
                status_code=303,
            )
        row = find_row_by_path(resolved)
        if row is None and resolved.is_file():
            try:
                result = extract_receipt(resolved, categorizer=store.categorizer)
                row = receipt_import_row(resolved, result=result)
            except ReceiptExtractError as err:
                row = receipt_import_row(resolved, error=err)
            except Exception as err:
                row = receipt_import_row(
                    resolved, error=f"Could not extract the receipt: {err}"
                )
        if row is None:
            return RedirectResponse(
                f"/receipts?error={quote('Beleg nicht gefunden. Bitte zuerst scannen oder hochladen.')}",
                status_code=303,
            )
        result = row.get("result") or {}
        return templates.TemplateResponse(
            request,
            "receipts_detail.html",
            {
                "title": "Beleg-Details",
                "nav": "receipts",
                "row": row,
                "result": result,
                "items": result.get("items") or [],
                "file_name": row.get("file") or resolved.name,
                "path": str(resolved),
            },
        )
