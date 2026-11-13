"""Windows 桌面入口和本地诊断。"""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QTimer
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from filemind import __version__
from filemind.config import AppPaths
from filemind.embeddings import ModelError, install_model
from filemind.library import FileAccessError, Library
from filemind.storage import LocalStore, UnsupportedSchemaError
from filemind.ui.window import MainWindow
from filemind.windows import common_locations, set_startup


def _activate_existing(name: str) -> bool:
    for _ in range(3):
        socket = QLocalSocket()
        socket.connectToServer(name)
        if socket.waitForConnected(350):
            socket.write(b"show")
            socket.flush()
            if socket.bytesToWrite():
                socket.waitForBytesWritten(350)
            if socket.bytesAvailable() or socket.waitForReadyRead(750):
                return bytes(socket.readAll()) == b"ok"
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="filemind", description="FileMind Windows 桌面应用")
    parser.add_argument("--data-dir", type=Path, help="指定应用数据目录")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="检查本地存储和 FTS5 并退出")
    actions.add_argument("--install-model", action="store_true", help="下载并校验本地 BGE 权重")
    actions.add_argument(
        "--add-common-locations", action="store_true", help="关联文档、下载和 D 盘"
    )
    actions.add_argument("--enable-startup", action="store_true", help="登录 Windows 时从托盘启动")
    actions.add_argument("--disable-startup", action="store_true", help="关闭 Windows 登录启动")
    parser.add_argument("--tray", action="store_true", help="启动到系统托盘")
    parser.add_argument("--version", action="version", version=f"FileMind {__version__}")
    args = parser.parse_args(argv)

    if args.enable_startup or args.disable_startup:
        if args.data_dir is not None:
            parser.error("Windows 登录启动使用默认资料库，不能同时指定 --data-dir。")
        try:
            set_startup(args.enable_startup)
        except OSError as error:
            return report_startup_error(None, str(error))
        return 0

    if args.install_model:
        try:
            paths = AppPaths.resolve(args.data_dir)
            install_model(paths.model)
        except (OSError, ModelError) as error:
            return report_startup_error(None, f"无法安装本地 BGE 权重。{error}")
        print(f"本地 BGE 权重已就绪：{paths.model}")
        return 0

    app = None
    if not args.check and not args.add_common_locations:
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
        channel = (
            "filemind-"
            + hashlib.sha256(str(paths.root).casefold().encode("utf-8")).hexdigest()[:24]
        )
        if app is not None or args.add_common_locations:
            lock = QLockFile(str(paths.root / "filemind.lock"))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                if app is not None and (args.tray or _activate_existing(channel)):
                    return 0
                return report_startup_error(
                    app, "无法锁定此数据目录。请检查是否已有 FileMind 窗口正在使用它。"
                )
        store = LocalStore(paths.database)
        status = store.initialize()
        if args.add_common_locations:
            library = Library(paths, store)
            folders = common_locations()
            if not folders:
                return report_startup_error(None, "未找到可关联的文档、下载或 D 盘。")
            for folder in folders:
                library.register_directory(folder)
                print(folder)
            lock.unlock()
            return 0
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
        window = MainWindow(
            paths,
            store,
            status,
            monitor=True,
            startup_allowed=args.data_dir is None and sys.platform == "win32",
        )
    except UnsupportedSchemaError as error:
        return report_startup_error(app, str(error))
    except FileAccessError as error:
        return report_startup_error(app, str(error))
    except (OSError, sqlite3.Error):
        return report_startup_error(app, "无法打开本地存储。请检查数据目录和数据库文件。")

    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def accept_connections() -> None:
        while socket := server.nextPendingConnection():

            def receive(peer: QLocalSocket = socket) -> None:
                if bytes(peer.readAll()) != b"show":
                    return
                window.reveal()
                peer.write(b"ok")
                peer.flush()
                peer.disconnectFromServer()

            socket.disconnected.connect(socket.deleteLater)
            socket.readyRead.connect(receive)
            QTimer.singleShot(3000, socket, socket.abort)
            if socket.bytesAvailable():
                receive()

    QLocalServer.removeServer(channel)
    server.newConnection.connect(accept_connections)
    server.listen(channel)
    if window.tray is not None:
        app.setQuitOnLastWindowClosed(False)
    if not args.tray or window.tray is None:
        window.show()
    app.commitDataRequest.connect(lambda _: window.quit())
    result = app.exec()
    window.shutdown()
    server.close()
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
