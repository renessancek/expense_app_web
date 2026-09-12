import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import app_paths


class TestAppPaths(unittest.TestCase):
    @patch.object(app_paths.sys, "platform", "win32")
    @patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local", "USERPROFILE": r"C:\Users\Test"}, clear=True)
    def test_windows_data_and_documents_paths(self):
        self.assertEqual(
            app_paths.user_data_dir(),
            Path(r"C:\Users\Test\AppData\Local") / "Expense App",
        )
        self.assertEqual(
            app_paths.documents_dir(),
            Path(r"C:\Users\Test") / "Documents",
        )

    @patch.object(app_paths.sys, "platform", "linux")
    @patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/data", "XDG_CACHE_HOME": "/tmp/cache"}, clear=True)
    def test_linux_xdg_paths(self):
        self.assertEqual(app_paths.user_data_dir(), app_paths.Path("/tmp/data") / "expense-app")
        self.assertEqual(app_paths.user_cache_dir(), app_paths.Path("/tmp/cache") / "expense-app")

    @patch.object(app_paths.sys, "platform", "linux")
    def test_linux_documents_dir_uses_xdg_user_dirs_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "user-dirs.dirs").write_text(
                'XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n',
                encoding="utf-8",
            )
            home = Path(tmp) / "home"
            home.mkdir()
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_dir), "HOME": str(home)}, clear=False):
                with patch.object(app_paths.Path, "home", return_value=home):
                    self.assertEqual(app_paths.documents_dir(), home / "Dokumente")

    @patch.object(app_paths.sys, "platform", "linux")
    def test_linux_documents_dir_falls_back_to_dokumente_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Dokumente").mkdir()
            config_dir = home / "missing-config"
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_dir), "HOME": str(home)}, clear=False):
                with patch.object(app_paths.Path, "home", return_value=home):
                    with patch.object(app_paths.shutil, "which", return_value=None):
                        self.assertEqual(app_paths.documents_dir(), home / "Dokumente")
