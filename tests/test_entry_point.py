import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

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


def test_second_launch_is_accepted_while_library_opens(tmp_path: Path) -> None:
    program = """
import sys
from pathlib import Path
from time import monotonic, sleep

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QSystemTrayIcon

from filemind import __main__ as entry

root = Path(sys.argv[1])
primary = sys.argv[2] == 'primary'
if primary:
    initialize = entry.LocalStore.initialize

    def slow_initialize(store):
        (root / 'opening').touch()
        deadline = monotonic() + 30
        while not (root / 'continue').exists():
            if monotonic() >= deadline:
                raise TimeoutError('test did not release startup')
            sleep(0.02)
        return initialize(store)

    reveal = entry.MainWindow.reveal

    def record_reveal(window):
        result = reveal(window)
        (root / 'revealed').write_text(str(result), encoding='ascii')
        QTimer.singleShot(0, window.quit)
        return result

    entry.LocalStore.initialize = slow_initialize
    entry.MainWindow.reveal = record_reveal
    QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
    QSystemTrayIcon.show = lambda _: None

def report_error(app, message):
    print(message, file=sys.stderr)
    return 1

entry.report_startup_error = report_error
args = ['--data-dir', str(root / 'library')]
if primary:
    args.append('--tray')
raise SystemExit(entry.main(args))
"""
    command = [sys.executable, "-c", program, str(tmp_path)]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    with subprocess.Popen(
        [*command, "primary"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    ) as primary:
        try:
            deadline = monotonic() + 20
            while not (tmp_path / "opening").exists() and primary.poll() is None:
                assert monotonic() < deadline, "First launch did not begin opening the library"
                sleep(0.025)
            assert (tmp_path / "opening").exists()

            second = subprocess.run(
                [*command, "secondary"], capture_output=True, timeout=12, env=env
            )
            assert second.returncode == 0, second.stderr.decode("utf-8", errors="replace")
            assert not (tmp_path / "revealed").exists()

            (tmp_path / "continue").touch()
            _, errors = primary.communicate(timeout=20)
            assert primary.returncode == 0, errors.decode("utf-8", errors="replace")
            assert (tmp_path / "revealed").read_text(encoding="ascii") == "True"
            assert not (tmp_path / "library" / "filemind.lock").exists()
        finally:
            if primary.poll() is None:
                primary.kill()
                primary.communicate()
