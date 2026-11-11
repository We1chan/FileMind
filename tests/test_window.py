import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from filemind.config import AppPaths
from filemind.storage import LocalStore
from filemind.ui.window import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication([])


def click_navigation(window: MainWindow, index: int) -> None:
    item = window.navigation.item(index)
    rect = window.navigation.visualItemRect(item)
    QTest.mouseClick(window.navigation.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_switching_pages_and_reopening_window(app: QApplication, tmp_path: Path) -> None:
    paths = AppPaths.resolve(tmp_path)
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize())
    window.show()
    app.processEvents()

    try:
        expected = [
            ("文件库", "文件详情"),
            ("智能检索", "原文与引用"),
            ("智能整理", "计划详情"),
            ("设置与任务", "任务详情"),
        ]
        for index, (title, detail) in enumerate(expected):
            click_navigation(window, index)
            heading = window.pages.currentWidget().findChild(QLabel, "PageTitle")
            assert heading is not None and heading.text() == title
            assert window.detail_title.text() == detail
    finally:
        window.close()

    reopened_store = LocalStore(paths.database)
    reopened = MainWindow(paths, reopened_store, reopened_store.initialize())
    try:
        assert reopened.navigation.currentItem().text() == "设置与任务"
        assert reopened.detail_title.text() == "任务详情"
    finally:
        reopened.close()


def test_settings_opens_the_configured_data_directory(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []

    def open_url(url: QUrl) -> bool:
        opened.append(Path(url.toLocalFile()))
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(open_url))
    paths = AppPaths.resolve(tmp_path / "本地 数据")
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize())
    window.show()
    app.processEvents()

    try:
        click_navigation(window, 3)
        button = window.pages.currentWidget().findChild(QPushButton, "OpenData")
        assert button is not None and button.isVisible()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert opened == [paths.root]
    finally:
        window.close()
