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
from filemind.storage import FileRecord, LocalStore, SearchScope, Source, StorageStatus

ROLE = Qt.ItemDataRole.UserRole
STATUS = {
    "pending": "待索引",
    "ready": "已索引",
    "failed": "索引失败",
    "missing": "文件缺失",
    "ocr": "需要 OCR",
    "empty": "无可索引文本",
    "cataloged": "已登记 · 文件信息",
}
MODE = {
    "hybrid": "混合检索",
    "keyword": "关键词检索",
    "semantic": "语义检索",
    "filename": "文件名与路径",
}
PAGE_SIZE = 200


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
    if not file.source_path:
        return "受管副本"
    root = Path(file.source_path)
    return f"关联 · {root.name or root.anchor}"


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
        self.common_button = button("接入常用位置", "CommonLocations")
        self.refresh_button = button("刷新索引", "RefreshIndex")
        layout.addLayout(
            _actions(self.common_button, self.link_button, self.import_button, self.refresh_button)
        )
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("按文件名或路径筛选")
        self.filter.setAccessibleName("筛选文件库")
        self.filter.setClearButtonEnabled(True)
        layout.addWidget(self.filter)
        self.summary = label("", "Hint", wrap=True)
        layout.addWidget(self.summary)
        self.files = _list("LibraryFiles", "资料库文件")
        layout.addWidget(self.files, 1)
        self.preview_button = button("预览原文", "PreviewFile")
        self.locate_button = button("定位文件", "LocateFile")
        self.retry_button = button("重试索引", "RetryIndex")
        self.previous = button("上一页")
        self.next = button("下一页")
        layout.addLayout(
            _actions(
                self.preview_button, self.locate_button, self.retry_button, self.previous, self.next
            )
        )
        self.files.currentItemChanged.connect(self._update_actions)
        self._update_actions()

    def selected_file(self) -> FileRecord | None:
        item = self.files.currentItem()
        return item.data(ROLE) if item is not None else None

    def show_files(self, records: list[FileRecord], counts: tuple[int, int], offset: int) -> None:
        selected = self.selected_file()
        visible = records[:PAGE_SIZE]
        if self.files.count() != len(visible) or any(
            self.files.item(i).data(ROLE) != file for i, file in enumerate(visible)
        ):
            self.files.clear()
            for file in visible:
                item = _add_item(
                    self.files, f"{file.name}\n{file_origin(file)} · {STATUS[file.status]}", file
                )
                if selected is not None and file.id == selected.id:
                    self.files.setCurrentItem(item)
        total, indexed = counts
        summary = f"共 {total:,} 个文件 · {indexed:,} 个已处理"
        if visible:
            summary += f" · 显示第 {offset + 1:,}–{offset + len(visible):,} 个"
        elif self.filter.text().strip():
            summary = "没有匹配的文件。"
        elif not total:
            summary = "接入常用位置或关联目录后，文件会陆续出现在这里。"
        self.summary.setText(summary)
        self.previous.setEnabled(offset > 0)
        self.next.setEnabled(len(records) > PAGE_SIZE)

    def _update_actions(self) -> None:
        selected = self.selected_file()
        self.preview_button.setEnabled(
            selected is not None and selected.status in {"ready", "cataloged"}
        )
        self.locate_button.setEnabled(selected is not None and selected.status != "missing")
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
        self.locate_button = button("定位选中文件", "LocateResult")
        self.locate_button.setEnabled(False)
        self.results.currentItemChanged.connect(
            lambda: self.locate_button.setEnabled(self.results.currentItem() is not None)
        )
        layout.addLayout(_actions(self.locate_button))

    def show_sources(self, sources: list[Source]) -> None:
        current = self.source.currentData()
        items = [("全部来源", None), ("受管副本", "managed"), ("关联目录", "linked")]
        items.extend(
            (Path(source.path).name or Path(source.path).anchor, source.id) for source in sources
        )
        if [self.source.itemData(index) for index in range(self.source.count())] == [
            key for _, key in items
        ]:
            return
        self.source.clear()
        for name, key in items:
            self.source.addItem(name, key)
        self.source.setCurrentIndex(max(0, self.source.findData(current)))

    def filters(self) -> tuple[SearchMode, SearchScope]:
        source = self.source.currentData()
        return self.mode.currentData(), SearchScope(
            self.extension.currentData(),
            source if isinstance(source, str) else None,
            self.file_ids,
            source_id=source if isinstance(source, int) else None,
        )

    def choose_scope(self, store: LocalStore) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("选择文件范围")
        dialog.resize(460, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("选择一份或多份文件。留空时使用全部文件。", "Hint", wrap=True))
        query = QLineEdit()
        query.setPlaceholderText("输入文件名以筛选")
        layout.addWidget(query)
        choices = _list("ScopeFiles", "文件范围")
        choices.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        selected = set(self.file_ids or ())
        hint = label("", "Hint")
        layout.addWidget(hint)

        def update_selection() -> None:
            for index in range(choices.count()):
                item = choices.item(index)
                (selected.add if item.isSelected() else selected.discard)(item.data(ROLE))
            hint.setText(f"已选 {len(selected)} 个 · 最多显示 200 个匹配项")

        def populate() -> None:
            choices.blockSignals(True)
            choices.clear()
            for file in store.files(
                limit=PAGE_SIZE, name=query.text().strip(), status="searchable"
            ):
                item = _add_item(
                    choices, f"{file.name}\n{file_origin(file)} · {file.relative_path}", file.id
                )
                item.setSelected(file.id in selected)
            choices.blockSignals(False)
            hint.setText(f"已选 {len(selected)} 个 · 最多显示 200 个匹配项")

        choices.itemSelectionChanged.connect(update_selection)
        query.textChanged.connect(populate)
        populate()
        layout.addWidget(choices)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        clear = buttons.addButton("清空选择", QDialogButtonBox.ButtonRole.ResetRole)

        def clear_selection() -> None:
            selected.clear()
            populate()

        clear.clicked.connect(clear_selection)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.file_ids = tuple(sorted(selected)) or None
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
            f"{MODE[result.mode]} · 显示 {len(result.hits)} 条结果"
            if result.hits
            else "未找到匹配文件。"
        )
        self.note.setText(" ".join(filter(None, (summary, result.notice))))
        for hit in result.hits:
            copies = f" · 共 {hit.copies} 份相同内容" if hit.copies > 1 else ""
            if hit.passages:
                passage = hit.passages[0]
                self._show_citation(Citation(passage, passage.text.strip()[:130]), copies=copies)
            else:
                _add_item(
                    self.results,
                    f"{hit.file.name}{copies}\n{file_origin(hit.file)} · {hit.file.relative_path}",
                    hit.file,
                )

    def show_answer(self, result: Answer) -> None:
        self.clear_results()
        self.note.setText(result.notice or "选择引用可查看对应原文。")
        if result.text:
            self.answer.setPlainText(result.text)
            self.answer.show()
        for index, citation in enumerate(result.citations, 1):
            self._show_citation(citation, f"[{index}] ")

    def _show_citation(self, citation: Citation, prefix: str = "", *, copies: str = "") -> None:
        passage = citation.passage
        excerpt = " ".join(citation.quote.split())
        if len(excerpt) > 130:
            excerpt = excerpt[:130] + "…"
        _add_item(
            self.results,
            f"{prefix}{passage.file.name}{copies}\n"
            f"{passage.location} · {file_origin(passage.file)}\n{excerpt}",
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
        toggles = QHBoxLayout()
        self.pause = QCheckBox("暂停后台索引")
        self.pause.setObjectName("PauseMonitoring")
        self.startup = QCheckBox("登录 Windows 时启动")
        self.startup.setObjectName("StartWithWindows")
        toggles.addWidget(self.pause)
        toggles.addWidget(self.startup)
        toggles.addStretch()
        state.addLayout(toggles)
        self.open_button = button("打开数据目录", "OpenData")
        self.open_button.setProperty("primary", True)
        self.exclusions_button = button("管理排除目录", "ManageExcludedDirectories")
        state.addLayout(_actions(self.open_button, self.exclusions_button))
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
        selected = self.selected_task()
        entries = [*sources, *files]
        if self.tasks.count() == len(entries) and all(
            self.tasks.item(i).data(ROLE) == item for i, item in enumerate(entries)
        ):
            return
        self.tasks.clear()
        scan_state = {
            "excluded": "已排除",
            "ready": "持续跟踪",
            "failed": "扫描不完整",
            "pending": "待扫描",
            "scanning": "正在扫描目录",
            "indexing": "正在补建内容索引",
        }
        for source in sources:
            item = _add_item(self.tasks, f"{source.path}\n{scan_state[source.status]}", source)
            if isinstance(selected, Source) and source.id == selected.id:
                self.tasks.setCurrentItem(item)
        for file in files:
            item = _add_item(
                self.tasks, f"{file.name}\n{STATUS[file.status]} · {file.reason}", file
            )
            if isinstance(selected, FileRecord) and file.id == selected.id:
                self.tasks.setCurrentItem(item)

    def _update_actions(self) -> None:
        selected = self.selected_task()
        self.unlink_button.setEnabled(isinstance(selected, Source))
        self.retry_button.setEnabled(selected is not None)
