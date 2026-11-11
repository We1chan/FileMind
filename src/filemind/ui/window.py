"""主窗口、导航和本地存储状态。"""

import sqlite3
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from filemind import __version__
from filemind.config import AppPaths
from filemind.storage import LocalStore, StorageStatus
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
        "选中文件后显示来源、标签和索引状态。",
        "文件接入待实现",
        "后续版本将支持导入文件和关联本机目录。",
    ),
    Page(
        "search",
        "智能检索",
        "按内容找文件并定位原文",
        "原文与引用",
        "打开检索结果或问答引用后显示原文。",
        "语义检索未启用",
        "关键词检索、向量检索和带引用问答正在开发。",
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
        "索引任务接入后可查看进度和失败原因。",
    ),
)


def label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, store: LocalStore, storage_status: StorageStatus) -> None:
        super().__init__()
        self.paths = paths
        self.store = store
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
        for page in PAGES:
            item = QListWidgetItem(page.title)
            item.setSizeHint(QSize(0, 43))
            self.navigation.addItem(item)
            self.pages.addWidget(self._make_page(page, storage_status))

        self.detail_title = label("", "SectionTitle")
        self.detail_hint = label("", "Hint", wrap=True)
        self.setCentralWidget(self._make_shell())

        last_page = self.store.get_preference(LAST_PAGE)
        selected = next((index for index, page in enumerate(PAGES) if page.key == last_page), 0)
        self.navigation.setCurrentRow(selected)
        self._show_page(selected)
        self.navigation.currentRowChanged.connect(self._navigate)

    def _make_shell(self) -> QWidget:
        shell = QWidget()
        shell.setObjectName("Shell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._make_sidebar())

        work_area = QWidget()
        content = QHBoxLayout(work_area)
        content.setContentsMargins(27, 25, 25, 25)
        content.setSpacing(19)
        content.addWidget(self.pages, 1)

        detail = QFrame()
        detail.setObjectName("Card")
        detail.setFixedWidth(228)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(18, 20, 18, 20)
        detail_layout.setSpacing(10)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_hint)
        detail_layout.addStretch()
        content.addWidget(detail)

        layout.addWidget(work_area, 1)
        return shell

    def _make_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(192)
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

    def _make_page(self, page: Page, status: StorageStatus) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 1, 0, 0)
        layout.setSpacing(7)
        layout.addWidget(label(page.title, "PageTitle"))
        layout.addWidget(label(page.description, "Hint", wrap=True))
        layout.addSpacing(10)
        if page.key == "settings":
            layout.addWidget(self._make_settings(status))
            layout.addStretch()
        else:
            layout.addWidget(self._make_empty_state(page), 1)
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

    def _make_settings(self, status: StorageStatus) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(label("本地运行状态", "SectionTitle"))

        state = QGridLayout()
        state.setHorizontalSpacing(20)
        state.setVerticalSpacing(10)
        state.setColumnStretch(1, 1)
        rows = (
            ("数据库", f"已初始化 · 结构版本 {status.schema_version}"),
            ("SQLite FTS5", "可用" if status.fts5_available else "不可用"),
            ("远程问答", "未启用"),
        )
        for row, (name, value) in enumerate(rows):
            state.addWidget(label(name, "Hint"), row, 0)
            state.addWidget(label(value, "Value"), row, 1)
        layout.addLayout(state)
        layout.addSpacing(8)

        layout.addWidget(label("应用数据目录", "SectionTitle"))
        data_dir = label(str(self.paths.root), "Value", wrap=True)
        data_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(data_dir)

        open_button = QPushButton("打开数据目录")
        open_button.setObjectName("OpenData")
        open_button.clicked.connect(self._open_data_dir)
        layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _show_page(self, index: int) -> None:
        page = PAGES[index]
        self.pages.setCurrentIndex(index)
        self.detail_title.setText(page.detail_title)
        self.detail_hint.setText(page.detail_hint)

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
