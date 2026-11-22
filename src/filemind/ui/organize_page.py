"""The managed-file, duplicate, and move-plan views."""

from collections.abc import Iterator
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidgetItem,
    QPlainTextEdit,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from filemind.organize import OrganizationSnapshot, archived, suggested_tag
from filemind.storage import FileRecord, MovePlan
from filemind.ui.pages import ROLE, STATUS, _actions, _add_item, _list, button, file_origin, label

_MOVE_STATUS = {
    "pending": "待执行",
    "moving": "待核对移动",
    "moved": "已移动",
    "failed": "移动失败",
    "undoing": "待核对撤销",
    "undone": "已撤销",
    "undo_failed": "撤销失败",
}


class OrganizePage(QWidget):
    detail_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.plan: MovePlan | None = None
        self.tags: dict[int, tuple[str, ...]] = {}
        self._tag_file_id: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("OrganizationTabs")
        self.tabs.setDocumentMode(True)
        self.refresh = button("刷新", "RefreshOrganization")
        self.tabs.setCornerWidget(self.refresh)
        layout.addWidget(self.tabs)

        files = self._tab("受管文件")
        self.filter = QLineEdit()
        self.filter.setAccessibleName("筛选受管文件")
        self.filter.setPlaceholderText("按文件名或标签筛选")
        self.filter.setClearButtonEnabled(True)
        self.files = _list("OrganizationFiles", "受管文件及整理勾选")
        self.files.setWordWrap(False)
        self.file_summary = label("正在读取受管文件…", "Hint", wrap=True)
        self.preview = button("预览按类型整理", "PreviewOrganization")
        self.preview.setProperty("primary", True)
        files.addWidget(self.filter)
        files.addWidget(self.file_summary)
        files.addWidget(self.files, 1)
        files.addLayout(_actions(self.preview))

        duplicates = self._tab("重复文件")
        self.duplicates = _list("DuplicateFiles", "完全重复文件的位置")
        self.duplicates.setWordWrap(False)
        self.duplicate_summary = label("按文件内容检查完全重复的副本。", "Hint", wrap=True)
        duplicates.addWidget(self.duplicate_summary)
        duplicates.addWidget(self.duplicates, 1)

        plans = self._tab("整理计划")
        self.history = QComboBox()
        self.history.setAccessibleName("历史整理计划")
        self.history.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.plan_summary = label("勾选受管副本后可生成整理计划。", "Hint", wrap=True)
        self.moves = _list("OrganizationMoves", "当前计划的文件和执行结果")
        self.moves.setWordWrap(False)
        self.apply = button("确认执行", "ExecuteOrganization")
        self.apply.setProperty("primary", True)
        self.undo = button("撤销移动", "UndoOrganization")
        history = QHBoxLayout()
        history.addWidget(label("历史计划", "Hint"))
        history.addWidget(self.history, 1)
        plans.addLayout(history)
        plans.addWidget(self.plan_summary)
        plans.addWidget(self.moves, 1)
        plans.addLayout(_actions(self.apply, self.undo))

        self.details = QWidget()
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.detail_text.setAccessibleName("文件路径或整理计划详情")
        self.detail_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        detail_layout.addWidget(self.detail_text, 1)
        self.tag_panel = QWidget()
        tags = QVBoxLayout(self.tag_panel)
        tags.setContentsMargins(0, 0, 0, 0)
        tags.addWidget(label("标签", "SectionTitle"))
        self.tag_input = QLineEdit()
        self.tag_input.setAccessibleName("文件标签")
        self.tag_input.setPlaceholderText("用逗号分隔")
        self.tag_input.setMaxLength(400)
        self.suggestion = button("采用建议", "SuggestFileTag")
        self.save_tags = button("保存标签", "SaveFileTags")
        tags.addWidget(self.tag_input)
        tags.addLayout(_actions(self.suggestion, self.save_tags))
        detail_layout.addWidget(self.tag_panel)
        self.suggestion.clicked.connect(self._use_suggestion)
        self.filter.textChanged.connect(self._filter_files)
        self.files.itemChanged.connect(self._file_summary)
        self.files.currentItemChanged.connect(self.detail_changed)
        self.duplicates.currentItemChanged.connect(self.detail_changed)
        self.moves.currentItemChanged.connect(self.detail_changed)
        self.tabs.currentChanged.connect(self.detail_changed)
        self.preview.setEnabled(False)
        self.show_plan(None)

    def _tab(self, title: str) -> QVBoxLayout:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(3, 10, 3, 2)
        layout.setSpacing(9)
        self.tabs.addTab(tab, title)
        return layout

    def selected_file(self) -> FileRecord | None:
        item = self.files.currentItem()
        return item.data(ROLE) if item is not None and not item.isHidden() else None

    def checked_files(self) -> set[int]:
        return {
            item.data(ROLE).id
            for item in self._file_items()
            if item.checkState() == Qt.CheckState.Checked
        }

    def _file_items(self) -> Iterator[QListWidgetItem]:
        return (self.files.item(row) for row in range(self.files.count()))

    def history_id(self) -> str | None:
        return self.history.currentData()

    def show_snapshot(self, snapshot: OrganizationSnapshot) -> None:
        if self._tag_file_id is not None and (
            self.tags.get(self._tag_file_id) != snapshot.tags.get(self._tag_file_id)
        ):
            self._tag_file_id = None
        self.tags = snapshot.tags
        checked = self.checked_files()
        current = self.selected_file()
        with QSignalBlocker(self.files):
            self.files.clear()
            for file in snapshot.files:
                tags = "、".join(self.tags.get(file.id, ()))
                if archived(file):
                    status = "已归档"
                elif file.sha256 and file.identity and not file.import_from:
                    status = f"建议归入{suggested_tag(file)}"
                else:
                    status = STATUS[file.status]
                tooltip = f"{file.name}\n{status}\n{file.relative_path}"
                item = _add_item(self.files, f"{file.name}\n{status}", file)
                item.setToolTip(tooltip + (f"\n标签  {tags}" if tags else ""))
                if file.sha256 and file.identity and not archived(file) and not file.import_from:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(
                        Qt.CheckState.Checked if file.id in checked else Qt.CheckState.Unchecked
                    )
                if current and current.id == file.id:
                    self.files.setCurrentItem(item)
        self._filter_files()

        with QSignalBlocker(self.duplicates):
            selected = self.duplicates.currentItem()
            previous = selected.data(ROLE)[0].id if selected else None
            self.duplicates.clear()
            for number, group in enumerate(snapshot.duplicates, 1):
                for file in group:
                    description = f"第 {number} 组  ·  {len(group)} 份  ·  {file_origin(file)}"
                    item = _add_item(
                        self.duplicates, f"{file.name}\n{description}", (file, description)
                    )
                    item.setToolTip(
                        self._full_path(file) if file.source_path else file.relative_path
                    )
                    if file.id == previous:
                        self.duplicates.setCurrentItem(item)
        count = len(snapshot.duplicates)
        self.duplicate_summary.setText(
            f"找到 {count} 组内容完全相同的文件。逐项选择可查看所在位置。"
            if count
            else "当前索引中没有内容完全相同的文件。"
        )

        with QSignalBlocker(self.history):
            self.history.clear()
            for plan in snapshot.history:
                date = plan.created_at[:19].replace("T", " ")
                if plan.interrupted:
                    state = "待核对"
                elif plan.pending:
                    state = "待执行"
                elif plan.undoable:
                    state = "有可撤销项"
                else:
                    state = "已结束"
                self.history.addItem(f"{date}  ·  {plan.total} 项  ·  {state}", plan.id)
            self.history.setEnabled(bool(snapshot.history))
            if snapshot.plan:
                self.history.setCurrentIndex(self.history.findData(snapshot.plan.id))
        self.show_plan(snapshot.plan)

    def _filter_files(self) -> None:
        query = self.filter.text().strip().casefold()
        current = self.files.currentItem()
        first_visible = None
        with QSignalBlocker(self.files):
            for item in self._file_items():
                file = item.data(ROLE)
                searchable = " ".join((file.name, *self.tags.get(file.id, ())))
                visible = query in searchable.casefold()
                item.setHidden(not visible)
                if not visible and item.checkState() == Qt.CheckState.Checked:
                    item.setCheckState(Qt.CheckState.Unchecked)
                if visible and first_visible is None:
                    first_visible = item
            if current is None or current.isHidden():
                self.files.setCurrentItem(first_visible)
        self._file_summary()
        self.detail_changed.emit()

    def _file_summary(self) -> None:
        count = len(self.checked_files())
        if not self.files.count():
            text = "在文件库导入副本后，就可以在这里添加标签或整理。"
        elif self.selected_file() is None:
            text = "没有符合筛选条件的文件。"
        elif count:
            text = f"已勾选 {count} 项。预览可查看每个文件的目标位置。"
        else:
            text = "勾选要移动的副本，或选择一行编辑标签。"
        self.file_summary.setText(text)
        self.preview.setEnabled(count > 0)

    def show_plan(self, plan: MovePlan | None) -> None:
        self.plan = plan
        self.moves.clear()
        if plan is None:
            self.plan_summary.setText(
                "正在读取计划…" if self.history_id() else "勾选受管副本后可生成整理计划。"
            )
            self.apply.setEnabled(False)
            self.undo.setEnabled(False)
        else:
            counts = dict.fromkeys(_MOVE_STATUS, 0)
            for move in plan.items:
                counts[move.state] += 1
                status = _MOVE_STATUS[move.state]
                folder = Path(move.destination).parent
                item = _add_item(
                    self.moves, f"{Path(move.origin).name}\n{status}  ·  目标目录  {folder}", move
                )
                item.setToolTip(f"{move.origin}\n→ {move.destination}\n{move.reason or status}")
            self.plan_summary.setText(
                "  ·  ".join(
                    f"{count} 项{_MOVE_STATUS[state]}" for state, count in counts.items() if count
                )
            )
            unresolved = counts["moving"] or counts["undoing"]
            self.apply.setEnabled(not unresolved and bool(counts["pending"]))
            self.undo.setEnabled(
                not unresolved
                and not counts["pending"]
                and bool(counts["moved"] + counts["undo_failed"])
            )
            self.moves.setCurrentRow(0)
        self.detail_changed.emit()

    def _use_suggestion(self) -> None:
        file = self.selected_file()
        if file is not None:
            tag = suggested_tag(file)
            text = self.tag_input.text().strip()
            if tag.casefold() not in text.lower().replace("，", ",").split(","):
                self.tag_input.setText(f"{text.rstrip(',，')}，{tag}" if text else tag)
            self.tag_input.setFocus()

    @staticmethod
    def _full_path(file: FileRecord, workspace: Path | None = None) -> str:
        root = Path(file.source_path) if file.source_path else workspace
        return str(root / file.relative_path) if root is not None else file.relative_path

    def show_detail(self, workspace: Path) -> str:
        text = ""
        file = self.selected_file() if self.tabs.currentIndex() == 0 else None
        self.tag_panel.setVisible(file is not None)
        if file is not None:
            if self._tag_file_id != file.id:
                self._tag_file_id = file.id
                self.tag_input.setText("，".join(self.tags.get(file.id, ())))
            self.suggestion.setToolTip(f"添加建议标签「{suggested_tag(file)}」")
            text = f"{file.name}\n\n{self._full_path(file, workspace)}\n\n建议标签  {suggested_tag(file)}"
        elif self.tabs.currentIndex() == 1 and (item := self.duplicates.currentItem()):
            file, group = item.data(ROLE)
            text = f"{file.name}\n\n{group}\n{self._full_path(file, workspace)}\n\nSHA-256\n{file.sha256}"
        elif self.tabs.currentIndex() == 2 and (item := self.moves.currentItem()):
            move = item.data(ROLE)
            text = (
                f"{_MOVE_STATUS[move.state]}\n\n"
                f"原位置\n{workspace / move.origin}\n\n目标位置\n{workspace / move.destination}"
            )
            if move.reason:
                text += f"\n\n{move.reason}"
        if text != self.detail_text.toPlainText():
            self.detail_text.setPlainText(text)
        self.details.setVisible(bool(text))
        return (
            "文件与标签"
            if self.tabs.currentIndex() == 0 and text
            else "副本位置"
            if self.tabs.currentIndex() == 1 and text
            else "计划详情"
        )
