import os
from pathlib import Path

from app_paths import statements_dir


class Scanner:
    def __init__(self, watch_path=None):
        self.watch_path = watch_path or str(statements_dir())
        os.makedirs(self.watch_path, exist_ok=True)

    def scan_for_csvs(self):
        """Return CSV paths in the watch folder, matching .csv case-insensitively.

        PayPal and some bank exports use .CSV (uppercase). On Linux, a
        case-sensitive '*.csv' glob would skip those files.
        """
        folder = Path(self.watch_path)
        if not folder.is_dir():
            return []
        return sorted(
            str(path)
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() == ".csv"
        )
