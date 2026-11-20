"""Qt widgets for browsing, searching and checking index status."""

from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
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
    QSplitter,
    QStyle,
    QStyledItemDelegate,
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


class ResultDelegate(QStyledItemDelegate):
    """Draw the same accessible item text as a file heading, location and excerpt."""

    def __init__(self, parent):
        super().__init__(parent)
        self.term = ""

    def sizeHint(self, option, index):
        return QSize(180, 131)

    def paint(self, painter, option, index):
        lines = index.data().split("\n", 2)
        name, info = lines[:2]
        selected = option.state & QStyle.StateFlag.State_Selected
        painter.save()
        painter.fillRect(option.rect, QColor("#e0eadb" if selected else "#faf9f5"))
        painter.setPen(QColor("#daddd2"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        rect = option.rect.adjusted(12, 9, -12, -8)
        painter.setClipRect(rect)
        painter.translate(rect.topLeft())
        entry = index.data(ROLE)
        file = entry if isinstance(entry, FileRecord) else entry.passage.file
        ext = file.extension.lstrip(".").upper()
        color = {"PDF": "#b73c31", "DOCX": "#396ea8", "PPTX": "#b95a30"}.get(ext, "#49735a")
        painter.setPen(QColor(color))
        painter.setBrush(QColor("#ffffff"))
        path = QPainterPath()
        path.moveTo(4, 6)
        path.lineTo(21, 6)
        path.lineTo(30, 15)
        path.lineTo(30, 40)
        path.lineTo(4, 40)
        path.closeSubpath()
        path.moveTo(21, 6)
        path.lineTo(21, 15)
        path.lineTo(30, 15)
        painter.drawPath(path)
        painter.fillRect(QRectF(0, 25, 35, 16), QColor(color))
        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        painter.drawText(QRectF(0, 25, 35, 16), Qt.AlignmentFlag.AlignCenter, ext[:5])
        painter.translate(44, 0)
        document = QTextDocument()
        document.setDefaultFont(option.font)
        width = max(40, rect.width() - 44)
        document.setTextWidth(width)
        title = option.fontMetrics.elidedText(name, Qt.TextElideMode.ElideMiddle, width - 10)
        excerpt = escape(lines[2]) if len(lines) > 2 else ""
        term = escape(self.term)
        if term:
            excerpt = excerpt.replace(term, f'<span style="background:#f1d896">{term}</span>')
        metadata = [info]
        if file.size is not None:
            metadata.append(f"{file.size / 1024:.0f} KB")
        if file.modified_ns:
            date = datetime.fromtimestamp(file.modified_ns / 1e9)
            metadata.append(f"修改于 {date:%Y-%m-%d}")
        document.setHtml(
            f'<p style="margin:0 0 5px;color:#192f23;font: bold 13pt SimSun">{escape(title)}</p>'
            f'<p style="margin:0 0 6px;color:#59675b">{escape(" · ".join(metadata))}</p>'
            f'<span style="font-size:11pt">{excerpt}</span>'
        )
        document.drawContents(painter)
        painter.restore()


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
        mark = QPixmap(24, 24)
        mark.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mark)
        painter.setFont(QFont("Segoe MDL2 Assets", 14))
        painter.setPen(QColor("#254133"))
        painter.drawText(mark.rect(), Qt.AlignmentFlag.AlignCenter, "\ue721")
        painter.end()
        self.query.addAction(QIcon(mark), QLineEdit.ActionPosition.LeadingPosition)
        self.query.setClearButtonEnabled(True)
        self.query.setMaxLength(MAX_QUERY_CHARS)
        self.find_button = button("检索", "FindContent")
        self.find_button.setProperty("primary", True)
        self.ask_button = button("问答", "AskQuestion")
        actions = QHBoxLayout()
        actions.addWidget(self.query, 1)
        actions.addWidget(self.find_button)

        layout.addLayout(actions)

        self.mode = QComboBox(self)
        self.mode.hide()
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
        filters.addWidget(label("检索模式", "Hint"))
        modes = QButtonGroup(self)
        for index, title in enumerate(("混合", "关键词", "语义", "文件名")):
            choice = button(title, "ModeChoice")
            choice.setCheckable(True)
            choice.setChecked(index == 0)
            modes.addButton(choice, index)
            filters.addWidget(choice)
        modes.idClicked.connect(self.mode.setCurrentIndex)
        self.mode.currentIndexChanged.connect(lambda index: modes.button(index).setChecked(True))
        filters.addStretch()
        filters.addWidget(self.ask_button)
        options = button("筛选", "FilterOptions")
        options.setCheckable(True)
        filters.addWidget(options)
        advanced = QWidget()
        advanced_row = QHBoxLayout(advanced)
        advanced_row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.extension, self.source, self.scope_button):
            advanced_row.addWidget(widget)
        advanced_row.addStretch()
        options.toggled.connect(advanced.setVisible)
        advanced.hide()
        layout.addLayout(filters)
        layout.addWidget(advanced)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        results_panel = QWidget()
        self.splitter.addWidget(results_panel)
        layout = QVBoxLayout(results_panel)
        layout.setContentsMargins(0, 8, 10, 0)
        self.note = label("输入检索词，查看相关文件。", "Hint", wrap=True)
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.note, 1)
        self.sort = QComboBox()
        self.sort.setAccessibleName("检索结果排序")
        self.sort.addItems(["按相关度排序", "按文件名排序", "按修改时间排序"])
        self.last_search = None
        self.sort.currentIndexChanged.connect(self.reorder)
        summary_row.addWidget(self.sort)
        layout.addLayout(summary_row)
        self.answer = QPlainTextEdit()
        self.answer.setObjectName("AnswerText")
        self.answer.setAccessibleName("问答内容")
        self.answer.setReadOnly(True)
        self.answer.setMaximumHeight(175)
        self.answer.hide()
        layout.addWidget(self.answer)
        self.results = _list("SearchResults", "检索结果与问答引用")
        self.result_delegate = ResultDelegate(self.results)
        self.results.setItemDelegate(self.result_delegate)
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

    def reorder(self):
        if self.last_search is not None:
            self.show_search(self.last_search)

    def clear_results(self) -> None:
        self.last_search = None
        self.sort.hide()
        self.results.clear()
        self.answer.clear()
        self.answer.hide()
        self.note.clear()

    def show_search(self, result: SearchResult) -> None:
        self.clear_results()
        self.result_delegate.term = self.query.text().strip()
        self.last_search = result
        self.sort.setVisible(bool(result.hits))
        summary = f"找到 {len(result.hits)} 个相关文件" if result.hits else "未找到匹配文件。"
        self.note.setText(" ".join(filter(None, (summary, result.notice))))
        hits = result.hits
        if self.sort.currentIndex() == 1:
            hits = sorted(hits, key=lambda hit: hit.file.name.casefold())
        elif self.sort.currentIndex() == 2:
            hits = sorted(hits, key=lambda hit: hit.file.modified_ns or 0, reverse=True)
        for hit in hits:
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

        if self.results.count():
            self.results.setCurrentRow(0)

    def show_answer(self, result: Answer) -> None:
        self.clear_results()
        self.result_delegate.term = ""
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
