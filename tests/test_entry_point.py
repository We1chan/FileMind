import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from filemind import __version__
from filemind.storage import SCHEMA_VERSION


def test_startup_check_reports_initialized_local_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "诊断 数据"
    process = subprocess.run(
        [sys.executable, "-m", "filemind", "--check", "--data-dir", str(data_dir)],
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )

    assert json.loads(process.stdout) == {
        "version": __version__,
        "sqlite_version": sqlite3.sqlite_version,
        "schema_version": SCHEMA_VERSION,
        "fts5_available": True,
    }
    assert (data_dir / "filemind.sqlite3").is_file()
    assert (data_dir / "workspace").is_dir()
