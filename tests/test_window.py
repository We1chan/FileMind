import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton

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
        close_window(window)

    reopened_store = LocalStore(paths.database)
    reopened = MainWindow(paths, reopened_store, reopened_store.initialize())
    try:
        assert reopened.navigation.currentItem().text() == "设置与任务"
        assert reopened.detail_title.text() == "任务详情"
    finally:
        close_window(reopened)


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
        close_window(window)


def wait_for_task(window: MainWindow) -> None:
    if window.task is None:
        return
    loop = QEventLoop()
    poll = QTimer()

    def check() -> None:
        if window.task is None:
            loop.quit()

    poll.timeout.connect(check)
    poll.start(20)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(10_000)
    loop.exec()
    assert window.task is None, "FileMind background task did not finish"


def close_window(window: MainWindow) -> None:
    window.close()
    wait_for_task(window)
    window.deleteLater()
    QApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows startup visibility")
def test_tray_activation_shows_the_native_window_after_hidden_launch(tmp_path: Path) -> None:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    program = """
import sys
from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from win32gui import IsWindowVisible

from filemind.config import AppPaths
from filemind.storage import LocalStore
from filemind.ui.window import MainWindow

app = QApplication([])
paths = AppPaths.resolve(Path(sys.argv[1]))
paths.prepare()
store = LocalStore(paths.database)
window = MainWindow(paths, store, store.initialize())
try:
    assert not window.isVisible()
    window._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    app.processEvents()
    assert IsWindowVisible(int(window.winId())), "FileMind window is hidden at the Win32 level"
finally:
    window.close()
    window.deleteLater()
    app.sendPostedEvents(window, QEvent.Type.DeferredDelete)
"""
    run = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path / "app")],
        startupinfo=startup,
        env=dict(os.environ, QT_QPA_PLATFORM="windows"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_desktop_import_search_and_open_citation(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "编译原理.txt"
    source.write_text("用 FIRST/FOLLOW 构造预测分析表。", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(source)], ""))
    )
    monkeypatch.setenv("FILEMIND_LLM_URL", "https://provider.invalid/v1/chat/completions")
    monkeypatch.setenv("FILEMIND_LLM_MODEL", "mock")
    monkeypatch.setenv("FILEMIND_LLM_API_KEY", "example-key")
    requests = []

    def mock_answer(settings, question, evidence):
        requests.append((settings.model, question))
        passage = evidence[0].passage
        return {
            "answer": "FIRST/FOLLOW 用来构造预测分析表。[1]",
            "citations": [{"chunk_id": passage.chunk_id, "version": passage.file.version}],
        }

    monkeypatch.setattr("filemind.qa._request_answer", mock_answer)
    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize())
    window.show()
    app.processEvents()
    try:
        QTest.mouseClick(window.library_page.import_button, Qt.MouseButton.LeftButton)
        wait_for_task(window)
        assert window.library_page.files.count() == 1
        assert "已索引" in window.library_page.files.item(0).text()
        click_navigation(window, 1)
        window.search_page.query.setText("FIRST/FOLLOW")
        QTest.mouseClick(window.search_page.find_button, Qt.MouseButton.LeftButton)
        wait_for_task(window)
        assert window.search_page.results.count() == 1
        assert "语义检索未启用" in window.search_page.note.text()

        QTest.mouseClick(window.search_page.ask_button, Qt.MouseButton.LeftButton)
        wait_for_task(window)
        assert "原文摘录" in window.search_page.note.text()
        assert not requests

        click_navigation(window, 3)
        assert window.settings_page.remote.isEnabled()
        window.settings_page.remote.click()
        click_navigation(window, 1)
        QTest.mouseClick(window.search_page.ask_button, Qt.MouseButton.LeftButton)
        wait_for_task(window)
        assert requests == [("mock", "FIRST/FOLLOW")]
        assert window.search_page.answer.isVisible()
        assert window.search_page.answer.toPlainText().endswith("[1]")
        citation = window.search_page.results.item(0)
        assert citation.text().startswith("[1] 编译原理.txt")
        result_list = window.search_page.results
        QTest.mouseClick(
            result_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=result_list.visualItemRect(citation).center(),
        )
        wait_for_task(window)
        assert window.preview.isVisible()
        assert "第 1 行" in window.detail_hint.text()
        assert window.preview.toPlainText() == source.read_text(encoding="utf-8")
    finally:
        close_window(window)
