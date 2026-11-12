"""Qt widgets for browsing, searching and checking index status."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from filemind.documents import EXTENSIONS
from filemind.qa import Answer, Citation, RemoteSettings
from filemind.search import MAX_QUERY_CHARS, SearchMode, SearchResult
from filemind.storage import FileRecord, SearchScope, Source, StorageStatus

ROLE = Qt.ItemDataRole.UserRole
STATUS = {
    "pending": "待索引",
    "ready": "已索引",
    "failed": "索引失败",
    "missing": "文件缺失",
    "ocr": "需要 OCR",
    "empty": "无可索引文本",
}
MODE = {"hybrid": "混合检索", "keyword": "关键词检索", "semantic": "语义检索"}


def label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, name: str = "Action") -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(name)
    widget.setProperty("action", True)
    return widget


def file_origin(file: FileRecord) -> str:
    return f"关联 · {Path(file.source_path).name}" if file.source_path else "受管副本"


def _list(name: str, accessible_name: str) -> QListWidget:
    widget = QListWidget()
    widget.setObjectName(name)
    widget.setProperty("results", True)
    widget.setAccessibleName(accessible_name)
    widget.setWordWrap(True)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return widget


def _add_item(widget: QListWidget, text: str, data: object) -> QListWidgetItem:
    item = QListWidgetItem(text)
    item.setData(ROLE, data)
    widget.addItem(item)
    return item


def _actions(*buttons: QPushButton) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for control in buttons:
        layout.addWidget(control)
    layout.addStretch()
    return layout


class LibraryPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.import_button = button("导入文件", "ImportFiles")
        self.import_button.setProperty("primary", True)
        self.link_button = button("关联目录", "LinkDirectory")
        self.refresh_button = button("刷新索引", "RefreshIndex")
        layout.addLayout(_actions(self.import_button, self.link_button, self.refresh_button))
        self.summary = label("", "Hint", wrap=True)
        layout.addWidget(self.summary)
        self.files = _list("LibraryFiles", "资料库文件")
        layout.addWidget(self.files, 1)
        self.preview_button = button("预览原文", "PreviewFile")
        self.retry_button = button("重试索引", "RetryIndex")
        layout.addLayout(_actions(self.preview_button, self.retry_button))
        self.files.currentItemChanged.connect(self._update_actions)
        self._update_actions()

    def selected_file(self) -> FileRecord | None:
        item = self.files.currentItem()
        return item.data(ROLE) if item is not None else None

    def show_files(self, records: list[FileRecord]) -> None:
        selected = self.selected_file()
        self.files.clear()
        for file in records:
            item = _add_item(
                self.files, f"{file.name}\n{file_origin(file)} · {STATUS[file.status]}", file
            )
            if selected is not None and file.id == selected.id:
                self.files.setCurrentItem(item)
        self.summary.setText(
            f"{len(records)} 份文件 · {sum(file.status == 'ready' for file in records)} 份已索引"
            if records
            else "导入文件以保存副本，或关联目录以只读接入。"
        )

    def _update_actions(self) -> None:
        selected = self.selected_file()
        self.preview_button.setEnabled(selected is not None and selected.status == "ready")
        self.retry_button.setEnabled(selected is not None and selected.status != "ready")


class SearchPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.query = QLineEdit()
        self.query.setObjectName("SearchQuery")
        self.query.setAccessibleName("检索词或问题")
        self.query.setPlaceholderText("输入关键词或问题")
        self.query.setMaxLength(MAX_QUERY_CHARS)
        self.find_button = button("搜索", "FindContent")
        self.find_button.setProperty("primary", True)
        self.ask_button = button("问答", "AskQuestion")
        actions = QHBoxLayout()
        actions.addWidget(self.query, 1)
        actions.addWidget(self.find_button)
        actions.addWidget(self.ask_button)
        layout.addLayout(actions)

        self.mode = QComboBox()
        self.mode.setAccessibleName("检索模式")
        for key, name in MODE.items():
            self.mode.addItem(name, key)
        self.extension = QComboBox()
        self.extension.setAccessibleName("按格式筛选")
        self.extension.addItem("全部格式", None)
        for extension in EXTENSIONS:
            self.extension.addItem(extension[1:].upper(), extension)
        self.source = QComboBox()
        self.source.setAccessibleName("按来源筛选")
        self.source.addItem("全部来源", None)
        self.source.addItem("受管副本", "managed")
        self.source.addItem("关联目录", "linked")
        self.scope_button = button("全部文件", "SearchScope")
        self.scope_button.setAccessibleName("选择检索和问答范围")
        self.file_ids: tuple[int, ...] | None = None
        filters = QHBoxLayout()
        for widget in (self.mode, self.extension, self.source, self.scope_button):
            filters.addWidget(widget)
        filters.addStretch()
        layout.addLayout(filters)

        self.note = label("", "Hint", wrap=True)
        layout.addWidget(self.note)
        self.answer = QPlainTextEdit()
        self.answer.setObjectName("AnswerText")
        self.answer.setAccessibleName("问答内容")
        self.answer.setReadOnly(True)
        self.answer.setMaximumHeight(175)
        self.answer.hide()
        layout.addWidget(self.answer)
        self.results = _list("SearchResults", "检索结果与问答引用")
        layout.addWidget(self.results, 1)

    def filters(self) -> tuple[SearchMode, SearchScope]:
        return self.mode.currentData(), SearchScope(
            self.extension.currentData(), self.source.currentData(), self.file_ids
        )

    def choose_scope(self, files: list[FileRecord]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("选择文件范围")
        dialog.resize(460, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("选择一份或多份文件。留空时使用全部文件。", "Hint", wrap=True))
        choices = _list("ScopeFiles", "文件范围")
        choices.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for file in files:
            if file.status == "ready":
                item = _add_item(
                    choices, f"{file.name}\n{file_origin(file)} · {file.relative_path}", file.id
                )
                item.setSelected(self.file_ids is not None and file.id in self.file_ids)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.file_ids = tuple(item.data(ROLE) for item in choices.selectedItems()) or None
            self.scope_button.setText(
                f"指定 {len(self.file_ids)} 份" if self.file_ids else "全部文件"
            )

    def clear_results(self) -> None:
        self.results.clear()
        self.answer.clear()
        self.answer.hide()
        self.note.clear()

    def show_search(self, result: SearchResult) -> None:
        self.clear_results()
        summary = (
            f"{MODE[result.mode]} · 显示 {len(result.hits)} 份文件"
            if result.hits
            else "未找到匹配文件。"
        )
        self.note.setText(" ".join(filter(None, (summary, result.notice))))
        for hit in result.hits:
            passage = hit.passages[0]
            self._show_citation(Citation(passage, passage.text.strip()[:130]))

    def show_answer(self, result: Answer) -> None:
        self.clear_results()
        self.note.setText(result.notice or "选择引用可查看对应原文。")
        if result.text:
            self.answer.setPlainText(result.text)
            self.answer.show()
        for index, citation in enumerate(result.citations, 1):
            self._show_citation(citation, f"[{index}] ")

    def _show_citation(self, citation: Citation, prefix: str = "") -> None:
        passage = citation.passage
        excerpt = " ".join(citation.quote.split())
        if len(excerpt) > 130:
            excerpt = excerpt[:130] + "…"
        _add_item(
            self.results,
            f"{prefix}{passage.file.name}\n{passage.location} · {file_origin(passage.file)}\n{excerpt}",
            citation,
        )


class SettingsPage(QWidget):
    def __init__(self, status: StorageStatus, root: Path, remote: RemoteSettings | None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        card = QFrame()
        card.setObjectName("Card")
        state = QVBoxLayout(card)
        state.setContentsMargins(16, 14, 16, 14)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.model_status = label("", "Value", wrap=True)
        rows = (
            ("数据库", label(f"结构版本 {status.schema_version}", "Value")),
            ("SQLite FTS5", label("可用" if status.fts5_available else "不可用", "Value")),
            ("语义检索", self.model_status),
        )
        for row, (key, value) in enumerate(rows):
            grid.addWidget(label(key, "Hint"), row, 0)
            grid.addWidget(value, row, 1)
        state.addLayout(grid)

        self.remote = QCheckBox("允许远程问答")
        self.remote.setObjectName("EnableRemoteQA")
        self.remote.setEnabled(remote is not None)
        state.addWidget(self.remote)
        state.addWidget(
            label(
                f"启用后向 {remote.host} 发送问题和相关摘录。"
                if remote
                else "远程问答未配置。当前仅在本机查找摘录。",
                "Hint",
                wrap=True,
            )
        )
        path = label(str(root), "Hint", wrap=True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        state.addWidget(path)
        self.open_button = button("打开数据目录", "OpenData")
        self.open_button.setProperty("primary", True)
        state.addLayout(_actions(self.open_button))
        layout.addWidget(card)

        layout.addWidget(label("关联目录与索引任务", "SectionTitle"))
        self.tasks = _list("IndexTasks", "关联目录和待处理文件")
        layout.addWidget(self.tasks, 1)
        self.unlink_button = button("取消关联", "UnlinkDirectory")
        self.retry_button = button("重试选中任务", "RetryTask")
        layout.addLayout(_actions(self.unlink_button, self.retry_button))
        self.tasks.currentItemChanged.connect(self._update_actions)
        self._update_actions()

    def selected_task(self) -> FileRecord | Source | None:
        item = self.tasks.currentItem()
        return item.data(ROLE) if item is not None else None

    def show_tasks(self, files: list[FileRecord], sources: list[Source]) -> None:
        self.tasks.clear()
        scan_state = {"ready": "扫描完成", "failed": "扫描不完整", "pending": "待扫描"}
        for source in sources:
            _add_item(self.tasks, f"{source.path}\n{scan_state[source.status]}", source)
        for file in files:
            if file.status != "ready":
                _add_item(self.tasks, f"{file.name}\n{STATUS[file.status]} · {file.reason}", file)

    def _update_actions(self) -> None:
        selected = self.selected_task()
        self.unlink_button.setEnabled(isinstance(selected, Source))
        self.retry_button.setEnabled(selected is not None)
