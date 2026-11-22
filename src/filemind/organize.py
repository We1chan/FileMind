"""Local tags, exact duplicate groups and journaled organization of managed copies."""

import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import groupby
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from filemind.documents import (
    ARCHIVE_EXTENSIONS,
    SHEET_EXTENSIONS,
    SLIDE_EXTENSIONS,
    WORD_EXTENSIONS,
)
from filemind.library import (
    FileAccessError,
    Library,
    _fingerprint,
    _key,
    _reason,
    _stored_fingerprint,
)
from filemind.managed_move import locked_rename
from filemind.storage import FileRecord, MoveItem, MovePlan, PlanSummary

ARCHIVE_ROOT = "整理"
_GROUPS = (
    ("文档", (".pdf", ".doc", ".txt", ".md", *WORD_EXTENSIONS)),
    ("表格", SHEET_EXTENSIONS),
    ("演示文稿", (".ppt", *SLIDE_EXTENSIONS)),
    ("压缩包", ARCHIVE_EXTENSIONS),
    ("应用程序", (".exe",)),
)
_INVALID_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')


def suggested_tag(file: FileRecord) -> str:
    return next((label for label, formats in _GROUPS if file.extension in formats), "其他")


def archived(file: FileRecord) -> bool:
    return Path(file.relative_path).parts[:2] == (ARCHIVE_ROOT, suggested_tag(file))


def _digest(items: tuple[MoveItem, ...]) -> str:
    payload = [
        (i.position, i.file_id, i.origin, i.destination, i.version, i.sha256, i.identity)
        for i in items
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class OrganizationSnapshot:
    files: list[FileRecord]
    tags: dict[int, tuple[str, ...]]
    duplicates: list[tuple[FileRecord, ...]]
    history: list[PlanSummary]
    plan: MovePlan | None


class Organizer:
    def __init__(self, library: Library) -> None:
        self.library = library
        self.store = library.store
        self.workspace = library.paths.workspace

    def snapshot(self, plan_id: str | None, cancelled: Callable[[], bool]) -> OrganizationSnapshot:
        files = self.store.source_files(None).values()
        managed = sorted(files, key=lambda file: (file.name.casefold(), file.id))
        duplicate_groups = []
        for _, candidates in groupby(
            self.store.duplicate_candidates(), key=lambda file: file.sha256
        ):
            if cancelled():
                break
            copies = tuple(file for file in candidates if self.library.metadata_current(file))
            # Multiple overlapping catalog sources can describe the same physical path.
            locations = {_key(self.library.path_for(file)): file for file in copies}
            if len(locations) > 1:
                duplicate_groups.append(tuple(locations.values()))
        history = self.store.move_plans()
        plan = self.store.move_plan(plan_id or history[0].id) if history else None
        return OrganizationSnapshot(managed, self.store.tags(), duplicate_groups, history, plan)

    def edit_tags(self, file_id: int, text: str) -> None:
        tags = {}
        for part in re.split("[,，\\n]", text):
            if tag := part.strip():
                if len(tag) > 32 or any(ord(char) < 32 for char in tag):
                    raise FileAccessError("每个标签最多 32 个字符，请用逗号分隔。")
                tags.setdefault(tag.casefold(), tag)
        if len(tags) > 12:
            raise FileAccessError("每个文件最多保存 12 个标签。")
        with self.store.organization_lock:
            file = self.store.file(file_id)
            if file is None or file.source_id is not None:
                raise FileAccessError("请选择受管副本。")
            self.store.save_tags(file_id, tuple(tags.values()))

    def preview(self, file_ids: Iterable[int]) -> MovePlan:
        with self.store.organization_lock:
            paths = self.store.source_files(None)
            candidates = {file.id: file for file in paths.values()}
            reserved = set(paths)
            unresolved = {item.file_id for _, item in self.store.inflight_moves()}
            items = []
            for file_id in sorted(set(file_ids) - unresolved):
                file = candidates.get(file_id)
                if (
                    file is None
                    or archived(file)
                    or not file.identity
                    or not file.sha256
                    or file.import_from is not None
                    or not self.library.metadata_current(file)
                ):
                    continue
                folder = Path(ARCHIVE_ROOT) / suggested_tag(file)
                relative = folder / file.name
                if _key(relative) in reserved or os.path.lexists(self.workspace / relative):
                    relative = folder / f"副本 {file.id}" / file.name
                    number = 2
                    while _key(relative) in reserved or os.path.lexists(self.workspace / relative):
                        relative = folder / f"副本 {file.id}-{number}" / file.name
                        number += 1
                reserved.add(_key(relative))
                items.append(
                    MoveItem(
                        len(items),
                        file.id,
                        file.relative_path,
                        str(relative),
                        file.version,
                        file.sha256,
                        file.identity,
                    )
                )
            if not items:
                raise FileAccessError(
                    "选中的文件没有可整理项。可刷新索引或选择尚未归档的受管副本。"
                )
            ordered = tuple(items)
            plan = MovePlan(uuid4().hex, _digest(ordered), ordered)
            self.store.save_move_plan(
                plan, datetime.now().astimezone().isoformat(timespec="seconds")
            )
            return plan

    def run(
        self, plan_id: str, digest: str, cancelled: Callable[[], bool], *, undo: bool = False
    ) -> MovePlan:
        with self.store.organization_lock:
            plan = self.store.move_plan(plan_id)
            if (
                plan is None
                or not hmac.compare_digest(plan.digest, digest)
                or not hmac.compare_digest(plan.digest, _digest(plan.items))
            ):
                raise FileAccessError("整理计划已变更，请重新加载后核对。")
            if any(item.state in {"moving", "undoing"} for item in plan.items):
                raise FileAccessError("此计划有待核对的中断项，请先刷新计划。")
            eligible = {"moved", "undo_failed"} if undo else {"pending"}
            if not any(item.state in eligible for item in plan.items):
                raise FileAccessError("此计划没有可撤销项。" if undo else "此计划没有待执行项。")
            if undo and any(item.state == "pending" for item in plan.items):
                raise FileAccessError("请先完成计划中待执行的项目。")
            blocked = {item.file_id for _, item in self.store.inflight_moves()}
            for item in reversed(plan.items) if undo else plan.items:
                if cancelled():
                    break
                if item.state in eligible:
                    self._move(plan_id, item, undo, blocked)
            return self.store.move_plan(plan_id) or plan

    def _paths(self, item: MoveItem, undo: bool) -> tuple[Path, Path]:
        origin, destination = Path(item.origin), Path(item.destination)
        for path in (origin, destination):
            if (
                path.anchor
                or not path.parts
                or any(
                    part == ".."
                    or part.endswith((" ", "."))
                    or _INVALID_NAME.search(part)
                    or PureWindowsPath(part).is_reserved()
                    for part in path.parts
                )
            ):
                raise FileAccessError("计划路径超出受管工作区或包含无效文件名。")
        if (
            origin.name != destination.name
            or _key(origin) == _key(destination)
            or destination.parts[0] != ARCHIVE_ROOT
            or len(destination.parts) < 3
        ):
            raise FileAccessError("计划的目标不符合归档规则。")
        return (destination, origin) if undo else (origin, destination)

    def _record(self, item: MoveItem, source: Path) -> FileRecord:
        file = self.store.file(item.file_id)
        if (
            file is None
            or file.source_id is not None
            or file.import_from is not None
            or file.relative_path != str(source)
            or file.version != item.version
            or file.sha256 != item.sha256
            or file.identity != item.identity
        ):
            raise FileAccessError("文件路径、内容或索引版本已变更，请核对资料库。")
        return file

    def _move(self, plan_id: str, item: MoveItem, undo: bool, blocked: set[int]) -> None:
        active, done, failed = (
            ("undoing", "undone", "undo_failed") if undo else ("moving", "moved", "failed")
        )
        renamed = False
        try:
            if item.file_id in blocked:
                raise FileAccessError("该文件在另一个中断计划中，请先核对其路径。")
            source, destination = self._paths(item, undo)
            file = self._record(item, source)
            target = self.workspace / destination
            if self.store.file_at(None, _key(destination)) or os.path.lexists(target):
                raise FileAccessError("目标位置已被占用，已保留现有文件。")
            info = self.library._file_info(file, self.workspace / source)
            fingerprint = _fingerprint(info)
            if fingerprint != _stored_fingerprint(file):
                raise FileAccessError("文件状态已变更，请刷新索引后重新预览。")
            with locked_rename(
                self.workspace / source, target, self.workspace, fingerprint, item.sha256
            ) as rename:
                self.store.record_move(plan_id, item, active)
                item = replace(item, state=active)
                rename()
                renamed = True
                fingerprint = _fingerprint(self.library._file_info(file, target))
                if fingerprint[3] != item.identity:
                    raise FileAccessError("移动后的文件身份需要核对。")
                self.store.record_move(
                    plan_id,
                    item,
                    done,
                    new_path=(destination, _key(destination)),
                    fingerprint=fingerprint,
                )
        except (OSError, FileAccessError) as error:
            code = getattr(error, "winerror", None)
            reason = {
                32: "文件或目录正在使用，请关闭后重试。",
                33: "文件或目录正在使用，请关闭后重试。",
                80: "目标位置已被占用，已保留现有文件。",
                183: "目标位置已被占用，已保留现有文件。",
            }.get(code, _reason(error))
            if renamed:
                blocked.add(item.file_id)
                self.store.record_move(plan_id, item, active, reason="文件已移动，等待核对索引。")
            else:
                self.store.record_move(plan_id, item, failed, reason=reason)

    def recover(self) -> set[int]:
        """Reconcile a committed intent after interruption. Never move files during recovery."""
        with self.store.organization_lock:
            unresolved = set()
            for plan_id, item in self.store.inflight_moves():
                if not self._recover_item(plan_id, item):
                    unresolved.add(item.file_id)
            return unresolved

    def _recover_item(self, plan_id: str, item: MoveItem) -> bool:
        undo = item.state == "undoing"
        try:
            plan = self.store.move_plan(plan_id)
            if plan is None or plan.digest != _digest(plan.items):
                raise FileAccessError("计划摘要无法校验。")
            source, destination = self._paths(item, undo)
            file = self._record(item, source)
            original = self._matching_path(file, source)
            result = self._matching_path(file, destination)
            if result and not original:
                self.store.record_move(
                    plan_id,
                    item,
                    "undone" if undo else "moved",
                    reason="已核对中断前完成的移动。",
                    new_path=(destination, _key(destination)),
                    fingerprint=result,
                )
                return True
            if original and not result:
                self.store.record_move(
                    plan_id,
                    item,
                    "undo_failed" if undo else "failed",
                    reason="操作中断，文件仍在原位置。",
                )
                return True
        except (OSError, FileAccessError):
            pass
        self.store.record_move(
            plan_id,
            item,
            item.state,
            reason="无法确认文件位置，请核对原路径和目标路径后刷新。",
        )
        return False

    def _matching_path(self, file: FileRecord, relative: Path) -> tuple[int, int, int, str] | None:
        path = self.workspace / relative
        try:
            fingerprint = _fingerprint(self.library._file_info(file, path))
        except FileNotFoundError:
            return None
        candidate = replace(file, relative_path=str(relative))
        if fingerprint[3] == file.identity and self.library.content_current(candidate):
            return fingerprint
        return None
