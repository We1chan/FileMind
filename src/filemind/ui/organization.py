"""Tag editing and review of complete duplicate groups."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from filemind.library import DuplicateGroup, DuplicateReport
from filemind.storage import FileRecord
from filemind.ui.pages import ROLE, _actions, button, file_origin, label


class TagDialog(QDialog):
    def __init__(
        self, parent: QWidget, file: FileRecord, tags: tuple[str, ...], suggestions: tuple[str, ...]
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑标签")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(label(file.name, "SectionTitle", wrap=True))
        layout.addWidget(label("多个标签用逗号分隔。", "Hint"))
        self.editor = QLineEdit("，".join(tags))
        self.editor.setAccessibleName("文件标签")
        layout.addWidget(self.editor)

        choices = []
        for tag in suggestions:
            action = button(f"+ {tag}", "SuggestedTag")
            action.setAccessibleName(f"添加建议标签 {tag}")
            action.setAutoDefault(False)
            action.clicked.connect(lambda _checked=False, tag=tag: self._add_suggestion(tag))
            choices.append(action)
        if choices:
            layout.addWidget(label("根据格式和文件名建议", "Hint"))
            layout.addLayout(_actions(*choices))

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def tags(self) -> list[str]:
        return [part.strip() for part in self.editor.text().replace("，", ",").split(",")]

    def _add_suggestion(self, tag: str) -> None:
        tags = [value for value in self.tags() if value]
        if tag.casefold() not in {value.casefold() for value in tags}:
            self.editor.setText("，".join([*tags, tag]))
        self.editor.setFocus()


class OrganizePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.scan_button = button("扫描重复文件", "ScanDuplicates")
        self.scan_button.setProperty("primary", True)
        self.cancel_button = button("取消扫描", "CancelDuplicateScan")
        self.cancel_button.hide()
        layout.addLayout(_actions(self.scan_button, self.cancel_button))
        self.note = label("扫描范围包括已登记的受管副本和关联文件。", "Hint", wrap=True)
        layout.addWidget(self.note)

        self.results = QTreeWidget()
        self.results.setProperty("results", True)
        self.results.setAccessibleName("完全重复文件分组")
        self.results.setHeaderHidden(True)
        self.results.setUniformRowHeights(True)
        self.results.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        layout.addWidget(self.results, 1)
        self.locate_button = button("定位文件", "LocateDuplicate")
        self.locate_button.setEnabled(False)
        self.results.currentItemChanged.connect(self._update_locate)
        layout.addLayout(_actions(self.locate_button))

    def selected(self) -> DuplicateGroup | FileRecord | None:
        item = self.results.currentItem()
        return item.data(0, ROLE) if item is not None else None

    def enable_actions(self, enabled: bool) -> None:
        self.scan_button.setEnabled(enabled)
        self.results.setEnabled(enabled)
        self._update_locate()
        if enabled:
            self.cancel_button.hide()

    def _update_locate(self) -> None:
        self.locate_button.setEnabled(
            self.results.isEnabled() and isinstance(self.selected(), FileRecord)
        )

    def show_report(self, report: DuplicateReport, path_for: Callable[[FileRecord], Path]) -> None:
        self.results.clear()
        self.results.setUpdatesEnabled(False)
        try:
            for group in report.groups:
                title = f"{group.files[0].name} · {len(group.files):,} 份"
                parent = QTreeWidgetItem(self.results, [title])
                parent.setData(0, ROLE, group)
                for file in group.files:
                    path = str(path_for(file))
                    item = QTreeWidgetItem(parent, [f"{file_origin(file)} · {path}"])
                    item.setData(0, ROLE, file)
                    item.setToolTip(0, path)
            if first := self.results.topLevelItem(0):
                first.setExpanded(True)
                self.results.setCurrentItem(first)
        finally:
            self.results.setUpdatesEnabled(True)

        summary = f"最近一次扫描找到 {len(report.groups):,} 组完全重复文件。"
        if report.skipped:
            summary += f" 跳过 {report.skipped:,} 个无法访问或扫描时发生变化的文件。"
        self.note.setText(summary)
