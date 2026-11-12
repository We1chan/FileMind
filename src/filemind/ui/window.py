"""Desktop navigation and background operations."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QThread, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from filemind import __version__
from filemind.config import AppPaths
from filemind.documents import EXTENSIONS
from filemind.library import FileAccessError, ImportReport, Library
from filemind.qa import Questions, RemoteSettings
from filemind.search import Search
from filemind.storage import FileRecord, LocalStore, Passage, Source, StorageStatus
from filemind.ui.pages import (
    ROLE,
    STATUS,
    LibraryPage,
    SearchPage,
    SettingsPage,
    file_origin,
    label,
)
from filemind.ui.theme import WINDOW_STYLE, application_icon

LAST_PAGE = "last_page"


@dataclass(frozen=True, slots=True)
class Page:
    key: str
    title: str
    description: str
    detail_title: str
    detail_hint: str
    empty_title: str = ""
    empty_hint: str = ""


PAGES = (
    Page(
        "library",
        "文件库",
        "查看导入文件和关联来源",
        "文件详情",
        "选择文件以查看来源和索引状态。",
    ),
    Page(
        "search",
        "智能检索",
        "按内容找文件并定位原文",
        "原文与引用",
        "选择检索结果或问答引用以定位原文。",
    ),
    Page(
        "organize",
        "智能整理",
        "审核整理计划并管理受管文件",
        "计划详情",
        "选中计划后查看拟移动的文件及其目标位置。",
        "整理待实现",
        "标签、完全重复检测、整理预览和撤销正在开发。",
    ),
    Page(
        "settings",
        "设置与任务",
        "查看应用的数据目录和本地运行状态",
        "任务详情",
        "选择任务以查看扫描或索引状态。",
    ),
)


class Task(QThread):
    def __init__(
        self,
        parent: QWidget,
        operation: Callable[[Callable[[], bool]], Any],
        completed: Callable[[Any], None],
    ) -> None:
        super().__init__(parent)
        self.operation = operation
        self.completed = completed
        self.result = None
        self.error = ""

    def run(self) -> None:
        try:
            self.result = self.operation(self.isInterruptionRequested)
        except FileAccessError as error:
            self.error = str(error)
        except Exception:
            logging.getLogger(__name__).exception("FileMind background task failed")
            self.error = "任务未完成。请检查资料库路径和本地存储，再重试。"


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, store: LocalStore, storage_status: StorageStatus) -> None:
        super().__init__()
        self.paths = paths
        self.store = store
        self.library = Library(paths, store)
        self.search = Search(self.library)
        self.questions = Questions(self.search)
        self.remote = RemoteSettings.from_environment()
        self.task: Task | None = None
        self.closing = False
        self.records: list[FileRecord] = []
        self.setWindowTitle("FileMind")
        self.setWindowIcon(application_icon())
        self.setMinimumSize(920, 580)
        self.resize(1160, 700)
        self.setStyleSheet(WINDOW_STYLE)

        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setAccessibleName("主导航")
        self.navigation.setSpacing(3)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.pages = QStackedWidget()
        self.pages.setAccessibleName("工作区")
        self.library_page = LibraryPage()
        self.search_page = SearchPage()
        self.settings_page = SettingsPage(storage_status, paths.root, self.remote)
        bodies = (self.library_page, self.search_page, None, self.settings_page)
        for page, body in zip(PAGES, bodies, strict=True):
            item = QListWidgetItem(page.title)
            item.setSizeHint(QSize(0, 43))
            self.navigation.addItem(item)
            self.pages.addWidget(self._make_page(page, body))

        self.detail_title = label("", "SectionTitle")
        self.detail_hint = label("", "Hint", wrap=True)
        self.detail_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("SourcePreview")
        self.preview.setAccessibleName("引用对应的原文")
        self.preview.setReadOnly(True)
        self.setCentralWidget(self._make_shell())

        last_page = self.store.get_preference(LAST_PAGE)
        selected = next((index for index, page in enumerate(PAGES) if page.key == last_page), 0)
        self.navigation.setCurrentRow(selected)
        self._show_page(selected)
        self.navigation.currentRowChanged.connect(self._navigate)
        self._connect_actions()
        self._reload_library()
        if self.records or self.settings_page.tasks.count():
            self._refresh()

    def _make_shell(self) -> QWidget:
        shell = QWidget()
        shell.setObjectName("Shell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._make_sidebar())

        work_area = QWidget()
        content = QHBoxLayout(work_area)
        content.setContentsMargins(24, 24, 22, 24)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(13)
        splitter.addWidget(self.pages)

        detail = QFrame()
        detail.setObjectName("Card")
        detail.setMinimumWidth(218)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(18, 20, 18, 20)
        detail_layout.setSpacing(10)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_hint)
        detail_layout.addWidget(self.preview, 1)
        detail_layout.addStretch()
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([590, 295])
        content.addWidget(splitter)

        layout.addWidget(work_area, 1)
        return shell

    def _make_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(174)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 24, 15, 18)
        layout.setSpacing(21)

        brand = QHBoxLayout()
        brand.setSpacing(9)
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(31, 31))
        brand.addWidget(icon)
        brand.addWidget(label("FileMind", "Brand"), 1)
        layout.addLayout(brand)
        layout.addWidget(self.navigation, 1)
        layout.addWidget(label(f"版本 {__version__}", "Version"))
        return sidebar

    def _make_page(self, page: Page, body: QWidget | None) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 1, 0, 0)
        layout.setSpacing(7)
        layout.addWidget(label(page.title, "PageTitle"))
        layout.addWidget(label(page.description, "Hint", wrap=True))
        layout.addSpacing(10)
        layout.addWidget(body if body is not None else self._make_empty_state(page), 1)
        return widget

    def _make_empty_state(self, page: Page) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        layout.addStretch()
        for text, role in ((page.empty_title, "SectionTitle"), (page.empty_hint, "Hint")):
            text_label = label(text, role, wrap=True)
            text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(text_label)
        layout.addStretch()
        return card

    def _show_page(self, index: int) -> None:
        page = PAGES[index]
        self.pages.setCurrentIndex(index)
        self._reset_detail()
        if page.key == "library":
            self._file_detail()
        elif page.key == "settings":
            self._task_detail()
            self.settings_page.model_status.setText(self.search.semantic.status)

    def _reset_detail(self) -> None:
        page = PAGES[self.pages.currentIndex()]
        self.detail_title.setText(page.detail_title)
        self.detail_hint.setText(page.detail_hint)
        self.preview.clear()
        self.preview.hide()

    def _navigate(self, index: int) -> None:
        if index < 0:
            return
        self._show_page(index)
        try:
            self.store.set_preference(LAST_PAGE, PAGES[index].key)
        except sqlite3.Error:
            self.statusBar().showMessage("当前页面已打开，未能保存导航偏好。", 5000)

    def _open_data_dir(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.root))):
            self.statusBar().showMessage("无法打开数据目录，可复制路径后在资源管理器中打开。", 5000)

    def _connect_actions(self) -> None:
        library = self.library_page
        library.import_button.clicked.connect(self._import_files)
        library.link_button.clicked.connect(self._link_directory)
        library.refresh_button.clicked.connect(self._refresh)
        library.preview_button.clicked.connect(self._preview_file)
        library.files.itemActivated.connect(self._preview_file)
        library.files.currentItemChanged.connect(self._file_detail)
        library.retry_button.clicked.connect(lambda: self._retry(library.selected_file()))

        search = self.search_page
        search.find_button.clicked.connect(lambda: self._search(False))
        search.query.returnPressed.connect(lambda: self._search(False))
        search.ask_button.clicked.connect(lambda: self._search(True))
        search.scope_button.clicked.connect(lambda: search.choose_scope(self.records))
        search.results.itemClicked.connect(self._preview_citation)
        search.results.itemActivated.connect(self._preview_citation)

        settings = self.settings_page
        settings.open_button.clicked.connect(self._open_data_dir)
        settings.tasks.currentItemChanged.connect(self._task_detail)
        settings.retry_button.clicked.connect(lambda: self._retry(settings.selected_task()))
        settings.unlink_button.clicked.connect(self._unlink_directory)

    def _start(
        self,
        message: str,
        operation: Callable[[Callable[[], bool]], Any],
        completed: Callable[[Any], None],
    ) -> None:
        if self.task is not None or self.closing:
            return
        self.task = Task(self, operation, completed)
        self.task.finished.connect(self._finished)
        self._enable_actions(False)
        self.statusBar().showMessage(message)
        self.task.start()

    def _enable_actions(self, enabled: bool) -> None:
        for page in (self.library_page, self.search_page, self.settings_page):
            page.setEnabled(enabled)

    def _finished(self) -> None:
        task = self.task
        if task is None:
            return
        task.wait()
        self.task = None
        task.deleteLater()
        if self.closing:
            self.close()
            return
        self._enable_actions(True)
        self.statusBar().clearMessage()
        self.settings_page.model_status.setText(self.search.semantic.status)
        if task.error:
            self.statusBar().showMessage(task.error, 8000)
        else:
            task.completed(task.result)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.task is not None:
            self.closing = True
            self.task.requestInterruption()
            self.statusBar().showMessage("当前操作结束后关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)

    def _reload_library(self) -> None:
        self.records = self.store.files()
        self.library_page.show_files(self.records)
        self.settings_page.show_tasks(self.records, self.store.sources())

    def _after_index(self, _result: object = None) -> None:
        self._reload_library()
        self.search_page.clear_results()
        if self.pages.currentIndex() == 1:
            self._reset_detail()

    def _import_files(self) -> None:
        patterns = " ".join(f"*{extension}" for extension in EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "导入文件",
            "",
            f"支持的文件 ({patterns})",
            options=QFileDialog.Option.DontResolveSymlinks,
        )
        if files:
            self._start(
                "正在保存受管副本并建立索引…",
                lambda stop: self.library.import_files(map(Path, files), stop),
                self._imported,
            )

    def _imported(self, report: ImportReport) -> None:
        self._after_index()
        if report.skipped:
            notes = "\n".join(report.skipped[:3])
            self.library_page.summary.setText(
                f"接入 {report.count} 份，跳过 {len(report.skipped)} 份。\n{notes}"
            )

    def _link_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "关联只读目录",
            "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if directory:
            self._start(
                "正在扫描关联目录…",
                lambda stop: self.library.link_directory(Path(directory), stop),
                self._after_index,
            )

    def _unlink_directory(self) -> None:
        source = self.settings_page.selected_task()
        if isinstance(source, Source):
            self._start(
                "正在取消目录关联…",
                lambda _: self.store.remove_source(source.id),
                self._after_index,
            )

    def _refresh(self) -> None:
        self._start("正在检查文件变化并更新索引…", self.library.refresh, self._after_index)

    def _retry(self, entry: FileRecord | Source | None) -> None:
        if isinstance(entry, FileRecord):
            self._start("正在重试索引…", lambda _: self.library.retry(entry.id), self._after_index)
        elif isinstance(entry, Source):
            self._refresh()

    def _file_detail(self) -> None:
        if self.pages.currentIndex() != 0:
            return
        self._reset_detail()
        if file := self.library_page.selected_file():
            self._display_file(file)

    def _task_detail(self) -> None:
        if self.pages.currentIndex() != 3:
            return
        self._reset_detail()
        entry = self.settings_page.selected_task()
        if isinstance(entry, FileRecord):
            self._display_file(entry)
        elif isinstance(entry, Source):
            self.detail_hint.setText(f"{entry.path}\n\n{entry.reason or '只读关联目录'}")

    def _display_file(self, file: FileRecord) -> None:
        parts = (
            file.name,
            file_origin(file),
            STATUS[file.status],
            file.reason,
            str(self.library.path_for(file)),
        )
        self.detail_hint.setText("\n\n".join(filter(None, parts)))

    def _search(self, ask: bool) -> None:
        page = self.search_page
        query = page.query.text().strip()
        if not query:
            page.query.setFocus()
            return
        self._reset_detail()
        page.clear_results()
        mode, scope = page.filters()
        if ask:
            remote = self.remote if self.settings_page.remote.isChecked() else None
            self._start(
                "正在检索证据并生成问答…",
                lambda stop: self.questions.ask(query, mode, scope, remote, stop),
                page.show_answer,
            )
        else:
            self._start(
                "正在检索资料库…",
                lambda stop: self.search.find(query, mode, scope, cancelled=stop),
                page.show_search,
            )

    def _preview_file(self) -> None:
        file = self.library_page.selected_file()
        if file is not None and file.status == "ready":
            self._start(
                "正在核对原文…",
                lambda _: self.library.preview_file(file.id),
                lambda passage: self._show_preview(passage, 0),
            )

    def _preview_citation(self, item: QListWidgetItem) -> None:
        citation = item.data(ROLE)
        passage = citation.passage
        self._start(
            "正在核对原文…",
            lambda _: self.library.preview(passage.chunk_id, passage.file.version),
            lambda verified: self._show_preview(verified, 1, citation.quote),
        )

    def _show_preview(self, passage: Passage, page: int, quote: str = "") -> None:
        if self.pages.currentIndex() != page:
            return
        self.detail_title.setText("原文预览")
        self.detail_hint.setText(
            f"{passage.file.name}\n{passage.location} · {file_origin(passage.file)}"
        )
        self.preview.setPlainText(passage.section_text)
        offset = passage.text.find(quote) if quote else -1
        start = passage.start + offset if offset >= 0 else passage.start
        end = start + len(quote) if offset >= 0 else passage.end

        def position(index: int) -> int:
            prefix = passage.section_text[:index].replace("\r\n", "\n").replace("\r", "\n")
            return len(prefix.encode("utf-16-le")) // 2

        cursor = self.preview.textCursor()
        maximum = self.preview.document().characterCount() - 1
        cursor.setPosition(min(position(end), maximum))
        cursor.setPosition(min(position(start), maximum), QTextCursor.MoveMode.KeepAnchor)
        self.preview.setTextCursor(cursor)
        self.preview.show()
        self.preview.ensureCursorVisible()
