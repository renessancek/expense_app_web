"""Operating-system appropriate locations used by Expense App Desktop."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


APP_DIR_NAME = "expense-app-desktop"

# Localized names used when XDG user-dirs are unavailable.
_DOCUMENTS_DIR_CANDIDATES = ("Dokumente", "Documents", "Documentos")


def expense_data_dir() -> Path | None:
    """Return EXPENSE_DATA_DIR when set (used by the Docker / web deployment)."""
    configured = os.environ.get("EXPENSE_DATA_DIR", "").strip()
    if not configured:
        return None
    return Path(configured).expanduser()


def user_data_dir() -> Path:
    """Return the directory used for rules and backups."""
    override = expense_data_dir()
    if override is not None:
        return override

    if sys.platform == "win32":
        configured_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(configured_base) if configured_base else Path.home()
        return base / "Expense App Desktop"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Expense App Desktop"

    configured_base = os.environ.get("XDG_DATA_HOME")
    base = Path(configured_base) if configured_base else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME


def statements_dir() -> Path:
    """Return the folder scanned for bank-statement CSV files.

    When EXPENSE_DATA_DIR is set (Docker / web), statements live under
    ``{EXPENSE_DATA_DIR}/BankStatements``. Otherwise the desktop default
    ``Documents/BankStatements`` (XDG-aware on Linux) is used.
    """
    override = expense_data_dir()
    if override is not None:
        return override / "BankStatements"
    return documents_dir() / "BankStatements"


def user_cache_dir() -> Path:
    """Return the directory used for launcher logs and its lock file."""
    override = expense_data_dir()
    if override is not None:
        return override / "cache"

    if sys.platform == "win32":
        return user_data_dir() / "logs"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Expense App Desktop"

    configured_base = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured_base) if configured_base else Path.home() / ".cache"
    return base / APP_DIR_NAME


def _expand_xdg_path(raw: str) -> Path | None:
    """Expand an XDG path value such as $HOME/Dokumente or \"/path\"."""
    value = raw.strip().strip('"').strip("'")
    if not value:
        return None
    value = os.path.expandvars(value.replace("$HOME", str(Path.home())))
    value = os.path.expanduser(value)
    return Path(value)


def _documents_dir_from_user_dirs_file() -> Path | None:
    """Read XDG_DOCUMENTS_DIR from ~/.config/user-dirs.dirs (or $XDG_CONFIG_HOME)."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(config_home) if config_home else Path.home() / ".config"
    user_dirs = config_dir / "user-dirs.dirs"
    if not user_dirs.is_file():
        return None

    pattern = re.compile(r'^\s*XDG_DOCUMENTS_DIR\s*=\s*(.+?)\s*$')
    try:
        for line in user_dirs.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = pattern.match(line)
            if match:
                return _expand_xdg_path(match.group(1))
    except OSError:
        return None
    return None


def _documents_dir_from_xdg_user_dir() -> Path | None:
    """Call `xdg-user-dir DOCUMENTS` when the helper is available."""
    binary = shutil.which("xdg-user-dir")
    if not binary:
        return None
    try:
        completed = subprocess.run(
            [binary, "DOCUMENTS"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    path = completed.stdout.strip()
    if not path:
        return None
    return Path(path)


def documents_dir() -> Path:
    """Return the standard Documents directory without creating it.

    On Linux this respects XDG user directories (e.g. ~/Dokumente on German systems)
    instead of always using the English name Documents.
    """
    if sys.platform == "win32":
        user_profile = os.environ.get("USERPROFILE")
        return (Path(user_profile) if user_profile else Path.home()) / "Documents"

    for resolver in (_documents_dir_from_user_dirs_file, _documents_dir_from_xdg_user_dir):
        resolved = resolver()
        if resolved is not None:
            return resolved

    # Fall back to common localized folder names if XDG config is missing.
    for name in _DOCUMENTS_DIR_CANDIDATES:
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate

    return Path.home() / "Documents"
