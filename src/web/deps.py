"""Shared settings, auth, and ExpenseDataStore wiring for the web UI."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app_paths import expense_data_dir, statements_dir, user_data_dir
from categorizer import Categorizer
from expense_data import ExpenseDataStore
from parser import Parser
from scanner import Scanner

security = HTTPBasic(auto_error=False)

_store: ExpenseDataStore | None = None


def data_root() -> Path:
    """Resolve the writable data root for rules and statements."""
    override = expense_data_dir()
    if override is not None:
        return override
    # Local / non-Docker fallback when EXPENSE_DATA_DIR is unset.
    return user_data_dir()


def auth_credentials() -> tuple[str | None, str | None]:
    user = os.environ.get("EXPENSE_AUTH_USER", "").strip() or None
    password = os.environ.get("EXPENSE_AUTH_PASSWORD", "").strip() or None
    if user and password:
        return user, password
    return None, None


def auth_enabled() -> bool:
    user, password = auth_credentials()
    return bool(user and password)


def ensure_data_dirs() -> None:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    statements_dir().mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(parents=True, exist_ok=True)


def get_store() -> ExpenseDataStore:
    """Return a process-wide ExpenseDataStore (reload after imports/rule changes)."""
    global _store
    ensure_data_dirs()
    if _store is None:
        categorizer = Categorizer(rules_path=str(user_data_dir() / "rules.json"))
        scanner = Scanner(watch_path=str(statements_dir()))
        _store = ExpenseDataStore(scanner, Parser(), categorizer)
        _store.reload()
    return _store


def reset_store() -> None:
    """Drop the cached store (used by tests)."""
    global _store
    _store = None


def require_auth(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)] = None,
) -> None:
    """Optional HTTP Basic auth when EXPENSE_AUTH_USER/PASSWORD are set."""
    user, password = auth_credentials()
    if not user or not password:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username, user)
    pass_ok = secrets.compare_digest(credentials.password, password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


AuthDep = Annotated[None, Depends(require_auth)]
StoreDep = Annotated[ExpenseDataStore, Depends(get_store)]
