"""Windows 桌面入口和本地诊断。"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from filemind import __version__
from filemind.config import AppPaths
from filemind.embeddings import ModelError, install_model
from filemind.storage import LocalStore, UnsupportedSchemaError
from filemind.ui.window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="filemind", description="FileMind Windows 桌面应用")
    parser.add_argument("--data-dir", type=Path, help="指定应用数据目录")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="检查本地存储和 FTS5 并退出")
    actions.add_argument("--install-model", action="store_true", help="下载并校验本地 BGE 权重")
    parser.add_argument("--version", action="version", version=f"FileMind {__version__}")
    args = parser.parse_args(argv)

    if args.install_model:
        try:
            paths = AppPaths.resolve(args.data_dir)
            install_model(paths.model)
        except (OSError, ModelError) as error:
            return report_startup_error(None, f"无法安装本地 BGE 权重。{error}")
        print(f"本地 BGE 权重已就绪：{paths.model}")
        return 0

    app = None
    if not args.check:
        app = QApplication([])
        app.setApplicationName("FileMind")
        app.setApplicationVersion(__version__)
        app.setStyle("Fusion")
        font = QFont("Microsoft YaHei UI", 10)
        font.setFamilies(["Inter", "Microsoft YaHei UI", "Segoe UI"])
        app.setFont(font)

    try:
        paths = AppPaths.resolve(args.data_dir)
        paths.prepare()
        if app is not None:
            lock = QLockFile(str(paths.root / "filemind.lock"))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                return report_startup_error(
                    app, "无法锁定此数据目录。请检查是否已有 FileMind 窗口正在使用它。"
                )
        store = LocalStore(paths.database)
        status = store.initialize()
        if app is None:
            print(
                json.dumps(
                    {
                        "version": __version__,
                        "sqlite_version": sqlite3.sqlite_version,
                        "schema_version": status.schema_version,
                        "fts5_available": status.fts5_available,
                    },
                    indent=2,
                )
            )
            return 0 if status.fts5_available else 1
        window = MainWindow(paths, store, status)
    except UnsupportedSchemaError as error:
        return report_startup_error(app, str(error))
    except (OSError, sqlite3.Error):
        return report_startup_error(app, "无法打开本地存储。请检查数据目录和数据库文件。")

    window.show()
    result = app.exec()
    window.deleteLater()
    app.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    lock.unlock()
    return result


def report_startup_error(app: QApplication | None, message: str) -> int:
    if app is None:
        print(message, file=sys.stderr)
    else:
        QMessageBox.critical(None, "FileMind 无法启动", message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
