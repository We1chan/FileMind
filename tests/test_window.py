import os
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
)

from filemind.config import AppPaths
from filemind.storage import LocalStore
from filemind.ui.tray import TrayMenu
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
    def busy() -> bool:
        return window.task is not None or window.catalog_task is not None

    if not busy():
        return
    loop = QEventLoop()
    poll = QTimer()

    def check() -> None:
        if not busy():
            loop.quit()

    poll.timeout.connect(check)
    poll.start(20)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(10_000)
    loop.exec()
    assert not busy(), "FileMind background task did not finish"


def close_window(window: MainWindow) -> None:
    window.close()
    wait_for_task(window)
    window.deleteLater()
    QApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def test_library_filter_discards_a_slow_obsolete_query(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths.resolve(tmp_path)
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize())
    started, release = Event(), Event()
    files = store.files
    applied = []
    show_files = window.library_page.show_files

    def slower_files(**kwargs):
        if kwargs.get("name") == "old":
            started.set()
            assert release.wait(10)
        return files(**kwargs)

    def record_files(records, counts, offset):
        applied.append([file.name for file in records])
        show_files(records, counts, offset)

    try:
        window.show()
        wait_for_task(window)
        store.register_file(None, Path("old.txt"), "old.txt")
        store.register_file(None, Path("new.txt"), "new.txt")
        monkeypatch.setattr(store, "files", slower_files)
        monkeypatch.setattr(window.library_page, "show_files", record_files)
        window.library_page.filter.setText("old")
        for _ in range(100):
            QTest.qWait(10)
            if started.is_set():
                break
        assert started.is_set()
        click_navigation(window, 3)
        assert window.pages.currentIndex() == 3
        window.library_page.filter.setText("new")
        release.set()
        QTest.qWait(200)
        wait_for_task(window)
        assert applied == [["new.txt"]]
    finally:
        release.set()
        close_window(window)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows managed moves")
def test_desktop_organization_flow(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "计算机学院_跨学期课程项目阶段验收材料（含代码及实验记录）_最终提交版.txt"
    source.write_text("FIRST/FOLLOW 用于构造预测分析表。", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(source)], ""))
    )
    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize())
    window.resize(920, 580)
    window.show()

    def click(control: QPushButton) -> None:
        assert control.isVisible() and control.isEnabled()
        QTest.mouseClick(control, Qt.MouseButton.LeftButton)
        wait_for_task(window)

    def answer(control: QPushButton, response: QMessageBox.StandardButton) -> None:
        def confirm() -> None:
            modal = app.activeModalWidget()
            modal.button(response).click()

        QTimer.singleShot(0, confirm)
        click(control)

    try:
        click_navigation(window, 2)
        wait_for_task(window)
        page = window.organize_page
        assert "导入副本" in page.file_summary.text()
        assert not page.preview.isEnabled()
        assert window.width() == 920
        click_navigation(window, 0)
        click(window.library_page.import_button)
        click(window.library_page.import_button)
        click_navigation(window, 1)
        window.search_page.query.setText("FIRST/FOLLOW")
        click(window.search_page.find_button)
        assert window.search_page.results.count() == 1
        click(window.search_page.ask_button)
        assert "原文摘录" in window.search_page.note.text()
        results = window.search_page.results
        citation = results.item(0)
        QTest.mouseClick(
            results.viewport(),
            Qt.MouseButton.LeftButton,
            pos=results.visualItemRect(citation).center(),
        )
        wait_for_task(window)
        assert "预测分析表" in window.preview.toPlainText()

        click_navigation(window, 2)
        wait_for_task(window)
        assert page.files.count() == 2
        assert source.name in page.files.item(0).toolTip()
        selected = page.selected_file()
        assert selected is not None and source.name in page.detail_text.toPlainText()
        click(page.suggestion)
        assert page.tag_input.text() == "文档"
        page.tag_input.setText("文档，课程项目")
        click(page.save_tags)
        assert store.tags()[selected.id] == ("文档", "课程项目")
        page.filter.setText("没有这个标签")
        assert "没有符合" in page.file_summary.text()
        assert page.tag_panel.isHidden()
        page.filter.clear()

        page.tabs.setCurrentIndex(1)
        assert page.duplicates.count() == 2
        page.duplicates.setCurrentRow(0)
        location_one = page.detail_text.toPlainText()
        page.duplicates.setCurrentRow(1)
        location_two = page.detail_text.toPlainText()
        assert location_one != location_two and "SHA-256" in location_two
        page.tabs.setCurrentIndex(0)
        page.files.setCurrentRow(0)
        QTest.keyClick(page.files, Qt.Key.Key_Space)
        assert len(page.checked_files()) == 1
        click(page.preview)
        assert page.tabs.currentIndex() == 2 and page.plan is not None
        intent = page.plan.items[0]
        origin, target = paths.workspace / intent.origin, paths.workspace / intent.destination
        assert str(origin) in page.detail_text.toPlainText()
        assert str(target) in page.detail_text.toPlainText()
        assert origin.is_file() and not target.exists()
        answer(page.apply, QMessageBox.StandardButton.Cancel)
        assert origin.is_file() and not target.exists()
        answer(page.apply, QMessageBox.StandardButton.Yes)
        assert target.is_file() and not origin.exists()
        assert "1 项已移动" in page.plan_summary.text()
    finally:
        close_window(window)

    reopened_store = LocalStore(paths.database)
    window = MainWindow(paths, reopened_store, reopened_store.initialize())
    window.show()
    try:
        wait_for_task(window)
        assert window.navigation.currentItem().text() == "智能整理"
        page = window.organize_page
        page.tabs.setCurrentIndex(2)
        assert "1 项已移动" in page.plan_summary.text()
        answer(page.undo, QMessageBox.StandardButton.Yes)
        assert origin.is_file() and not target.exists()
        assert "1 项已撤销" in page.plan_summary.text()
        assert reopened_store.tags()[selected.id] == ("文档", "课程项目")
    finally:
        close_window(window)


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


def test_tray_menu_tracks_index_state_while_main_window_is_hidden(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(QSystemTrayIcon, "show", lambda _: None)
    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize(), monitor=True)
    try:
        assert window.tray is not None and window.monitor is not None
        menu = window.tray.contextMenu()
        assert isinstance(menu, TrayMenu)
        assert not window.isVisible()
        assert "已启用" in window.tray.toolTip()

        menu.popup(QPoint(100, 100))
        menu.setActiveAction(menu.open_window)
        QTest.keyClick(menu, Qt.Key.Key_Down)
        assert menu.activeAction() == menu.toggle_pause
        QTest.keyClick(menu, Qt.Key.Key_Return)
        assert window.monitor.paused and window.settings_page.pause.isChecked()
        assert menu.toggle_pause.text() == "恢复后台索引"
        assert "已暂停" in window.tray.toolTip()
        assert menu.refresh_index.isEnabled()

        one_time_refreshes = []
        monkeypatch.setattr(window.library, "refresh", lambda _: one_time_refreshes.append(True))
        menu.refresh_index.trigger()
        wait_for_task(window)
        assert one_time_refreshes == [True] and window.monitor.paused

        window.settings_page.pause.setChecked(False)
        assert not window.monitor.paused
        assert menu.toggle_pause.text() == "暂停后台索引"
        requests = []
        monkeypatch.setattr(window.monitor, "refresh", lambda: requests.append(True))
        menu.refresh_index.trigger()
        assert requests == [True]

        window._update_title_bar(Qt.ContrastPreference.HighContrast)
        assert not menu.styleSheet()
        assert menu.open_window.isEnabled()
        window._update_title_bar(Qt.ContrastPreference.NoPreference)
        assert menu.styleSheet()
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.sendPostedEvents(window, QEvent.Type.DeferredDelete)


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
        monkeypatch.setattr(
            "filemind.ui.window.QInputDialog.getText", lambda *args: ("FIRST/FOLLOW", True)
        )
        citation_file_id = window._result_file().id
        window.ask_file.click()
        wait_for_task(window)
        assert window.search_page.file_ids == (citation_file_id,)
        assert len(requests) == 2
    finally:
        close_window(window)


def test_pdf_result_opens_physical_page_and_reader_controls(app: QApplication, tmp_path: Path):
    from samples.materialize import write_pdf

    source = tmp_path / "课程笔记.pdf"
    write_pdf(source, ["Contents", "Scheduling"])

    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    window = MainWindow(paths, store, store.initialize())
    try:
        window.library.import_files([source])
        window.show()
        window.navigation.setCurrentRow(1)
        page = window.search_page
        page.query.setText("Scheduling")
        page.mode.setCurrentIndex(1)
        page.find_button.click()
        wait_for_task(window)
        reader = window.pdf

        def wait_for_page():
            for _ in range(400):
                QTest.qWait(25)
                if not reader.rendering:
                    break
            assert not reader.image.isNull()

        wait_for_page()
        renderer = reader.process
        assert reader.isVisible()
        assert reader.page.count() == 2
        assert reader.page.currentIndex() == 1
        assert not reader.next.isEnabled()
        reader.previous.click()
        assert reader.page.currentIndex() == 0
        wait_for_page()
        assert renderer is not None and reader.process is renderer
        reader.page.setCurrentIndex(1)
        reader.page.setCurrentIndex(0)
        reader.page.setCurrentIndex(1)
        wait_for_page()
        assert reader.page.currentIndex() == 1 and reader.process is renderer
        reader.zoom(1.25)
        assert reader.scale == 1.25
        reader.fullscreen()
        assert reader.dialog.isFullScreen()
        reader.dialog.reject()
        assert reader.dialog is None and reader.view.parentWidget() == reader
        window.navigation.setCurrentRow(0)
        assert reader.page.count() == 0
        assert reader.process is None
    finally:
        close_window(window)
