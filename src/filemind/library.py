"""Managed copies, read-only source scans and repeatable indexing."""

import hashlib
import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from filemind.config import AppPaths
from filemind.documents import EXTENSIONS, MAX_FILE_BYTES, DocumentError, parse_document
from filemind.storage import FileRecord, LocalStore, Passage, Source
from filemind.text_index import keyword_terms, split_section

MAX_SCAN_ENTRIES = 20_000
_SKIPPED_DIRECTORIES = {"node_modules", "__pycache__", "$recycle.bin", "system volume information"}
_SENSITIVE = re.compile(
    r"(?:^|[ ._-])(?:secrets?|credentials?|passwords?|passwd|private[ _-]?keys?|"
    r"(?:api[ _-]?keys?|access[ _-]?tokens?)|id_rsa|id_ed25519|kubeconfig)(?:$|[ ._-])",
    re.IGNORECASE,
)


def _continue() -> bool:
    return False


class FileAccessError(ValueError):
    """The file or directory does not meet the library's access rules."""


@dataclass(frozen=True, slots=True)
class ImportReport:
    count: int
    skipped: tuple[str, ...]


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _excluded_name(name: str) -> bool:
    return (
        name.startswith(".")
        or name.lower() in _SKIPPED_DIRECTORIES
        or Path(name).stem.lower() in {"config", "settings", "configuration"}
        or bool(_SENSITIVE.search(name))
    )


def _is_link(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _is_hidden(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def _inspect(path: Path) -> os.stat_result:
    for parent in reversed(path.parents):
        if _is_link(parent.lstat()):
            raise FileAccessError("路径包含符号链接或重解析点。")
    info = path.lstat()
    if _is_link(info):
        raise FileAccessError("跳过符号链接或重解析点。")
    return info


def _require_directory(path: Path) -> None:
    if not stat.S_ISDIR(_inspect(path).st_mode):
        raise FileAccessError("路径不是目录。")


def _require_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise FileAccessError("仅接入独立的普通文件。")
    if info.st_size > MAX_FILE_BYTES:
        raise FileAccessError("文件超过 32 MiB。")


def _signature(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(path: Path, info: os.stat_result) -> bytes:
    def matches(candidate: os.stat_result) -> bool:
        return os.path.samestat(info, candidate) and _signature(info) == _signature(candidate)

    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        # On Windows, fstat and lstat can report different ctime values after a rename.
        if not os.path.samestat(info, opened) or _signature(info)[:2] != _signature(opened)[:2]:
            raise FileAccessError("文件在读取前发生变化，请刷新索引。")
        data = stream.read(MAX_FILE_BYTES + 1)
        if _signature(opened) != _signature(os.fstat(stream.fileno())) or not matches(
            _inspect(path)
        ):
            raise FileAccessError("文件在读取时发生变化，请刷新索引。")
    if len(data) > MAX_FILE_BYTES:
        raise FileAccessError("文件超过 32 MiB。")
    return data


def _reason(error: OSError | ValueError) -> str:
    if isinstance(error, (DocumentError, FileAccessError)):
        return str(error)
    if isinstance(error, FileNotFoundError):
        return "找不到文件或目录。"
    if isinstance(error, PermissionError):
        return "没有权限读取或写入此路径。"
    return "无法读取或写入文件，请检查磁盘和文件状态。"


class Library:
    def __init__(self, paths: AppPaths, store: LocalStore) -> None:
        self.paths = paths
        self.store = store

    def _external(self, path: Path) -> tuple[Path, os.stat_result]:
        info = _inspect(path)
        actual = path.resolve(strict=True)
        if actual.is_relative_to(self.paths.root):
            raise FileAccessError("应用数据目录不能作为文件来源。")
        if _is_hidden(info) or any(_excluded_name(part) for part in actual.parts[1:]):
            raise FileAccessError("跳过隐藏文件、敏感配置或排除目录。")
        return actual, info

    def path_for(self, file: FileRecord) -> Path:
        root = Path(file.source_path) if file.source_path is not None else self.paths.workspace
        relative = Path(file.relative_path)
        if relative.anchor or ".." in relative.parts or not relative.parts:
            raise FileAccessError("文件路径超出资料库。")
        return root / relative

    def _file_info(self, file: FileRecord, path: Path) -> os.stat_result:
        if file.source_path is None:
            info = _inspect(path)
            if not path.resolve(strict=True).is_relative_to(self.paths.workspace):
                raise FileAccessError("文件路径超出受管工作区。")
        else:
            actual, info = self._external(path)
            if not actual.is_relative_to(Path(file.source_path)):
                raise FileAccessError("文件路径超出已关联目录。")
        _require_file(info)
        return info

    def import_files(
        self, paths: Iterable[Path], cancelled: Callable[[], bool] = _continue
    ) -> ImportReport:
        count = 0
        skipped = []
        _require_directory(self.paths.workspace)
        for path in paths:
            if cancelled():
                break
            try:
                source, info = self._external(Path(os.path.abspath(path)))
                _require_file(info)
                if source.suffix.lower() not in EXTENSIONS:
                    raise FileAccessError("不支持此文件格式。")
            except (OSError, FileAccessError) as error:
                skipped.append(f"{path.name} · {_reason(error)}")
                continue

            relative = Path(uuid4().hex) / source.name
            file_id = self.store.register_file(None, relative, _key(relative), import_from=source)
            self.retry(file_id)
            count += 1
        return ImportReport(count, tuple(skipped))

    def _complete_copy(self, file: FileRecord, destination: Path) -> None:
        # The intent is committed first, so a completed copy can be resumed after a crash.
        if file.import_from is None:
            return
        _require_directory(self.paths.workspace)
        destination.parent.mkdir(exist_ok=True)
        _require_directory(destination.parent)
        try:
            self._file_info(file, destination)
        except FileNotFoundError:
            source, info = self._external(Path(file.import_from))
            _require_file(info)
            data = _read(source, info)
            staging = destination.with_name(".importing")
            staging.unlink(missing_ok=True)
            try:
                with staging.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                _require_directory(destination.parent)
                if os.path.lexists(destination):
                    raise FileAccessError("目标文件已存在，请刷新索引。")
                staging.rename(destination)
            finally:
                staging.unlink(missing_ok=True)
        self.store.complete_import(file.id)

    def link_directory(self, path: Path, cancelled: Callable[[], bool] = _continue) -> int:
        root, info = self._external(Path(os.path.abspath(path)))
        if not stat.S_ISDIR(info.st_mode) or root.parent == root:
            raise FileAccessError("请选择要关联的资料目录。")
        for existing in self.store.sources():
            existing_root = Path(existing.path)
            if root != existing_root and (
                root.is_relative_to(existing_root) or existing_root.is_relative_to(root)
            ):
                raise FileAccessError("此路径与已关联目录重叠。")
        source_id = self.store.register_source(root, _key(root))
        self._scan(Source(source_id, str(root), "pending", ""), cancelled)
        return source_id

    def refresh(self, cancelled: Callable[[], bool] = _continue) -> None:
        for source in self.store.sources():
            if cancelled():
                return
            self._scan(source, cancelled)
        _require_directory(self.paths.workspace)
        for file in self.store.files():
            if cancelled():
                return
            if file.source_id is None:
                self._index(file, missing_allowed=True)

    def _discover(self, root: Path, cancelled: Callable[[], bool]) -> tuple[dict[str, Path], str]:
        pending = [root]
        found = {}
        count = 0
        warning = ""
        while pending:
            directory = pending.pop()
            try:
                _require_directory(directory)
                with os.scandir(directory) as entries:
                    for entry in entries:
                        count += 1
                        if cancelled() or count > MAX_SCAN_ENTRIES:
                            return found, "扫描已中止或达到 2 万项目上限。"
                        path = Path(entry.path)
                        if _excluded_name(entry.name) or path.is_relative_to(self.paths.root):
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            warning = "部分文件或子目录无法访问。"
                            continue
                        if _is_link(info) or _is_hidden(info):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(path)
                        elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in EXTENSIONS:
                            relative = path.relative_to(root)
                            found[_key(relative)] = relative
            except (OSError, FileAccessError):
                warning = "部分文件或子目录无法访问。"
        return found, warning

    def _scan(self, source: Source, cancelled: Callable[[], bool]) -> None:
        self.store.update_source(source.id, "pending")
        root = Path(source.path)
        try:
            actual, info = self._external(root)
            if actual != root or not stat.S_ISDIR(info.st_mode):
                raise FileAccessError("关联目录已变更，请重新关联。")
        except (OSError, FileAccessError) as error:
            self.store.update_source(source.id, "failed", _reason(error))
            return

        found, warning = self._discover(root, cancelled)
        existing = {
            _key(Path(file.relative_path)): file
            for file in self.store.files()
            if file.source_id == source.id
        }
        for key, relative in sorted(found.items()):
            if cancelled():
                return
            file = existing.get(key)
            if file is None:
                file_id = self.store.register_file(source.id, relative, key)
                file = self.store.file(file_id)
            if file is not None and not self._index(file, missing_allowed=not warning):
                warning = "部分文件在扫描时无法访问。"
        if warning:
            self.store.update_source(source.id, "failed", warning + " 已保留未确认缺失的记录。")
            return
        if cancelled():
            return
        for key, file in existing.items():
            if key not in found and file.status != "missing":
                self.store.unavailable(file.id, "missing", "完整扫描时未找到该文件。")
        self.store.update_source(source.id, "ready")

    def retry(self, file_id: int) -> None:
        file = self.store.file(file_id)
        if file is not None:
            self._index(file, force=True, missing_allowed=file.source_id is None)

    def _index(
        self, file: FileRecord, *, force: bool = False, missing_allowed: bool = False
    ) -> bool:
        try:
            path = self.path_for(file)
            self._complete_copy(file, path)
            info = self._file_info(file, path)
            data = _read(path, info)
        except (OSError, FileAccessError) as error:
            missing = (
                missing_allowed
                and file.import_from is None
                and isinstance(error, FileNotFoundError)
            )
            if (
                not missing_allowed
                and file.source_id is not None
                and isinstance(error, FileNotFoundError)
            ):
                return False
            self.store.unavailable(file.id, "missing" if missing else "failed", _reason(error))
            return True

        signature = _signature(info)
        sha = hashlib.sha256(data).hexdigest()
        if sha == file.sha256 and file.status != "pending" and not force:
            if signature != (file.size, file.modified_ns, file.changed_ns):
                self.store.update_file_stat(file.id, signature)
            return True

        version = self.store.begin_index(file.id, sha, signature)
        name_terms = " ".join(keyword_terms(file.name))
        try:
            sections = [
                (section, split_section(section))
                for section in parse_document(data, file.extension)
            ]
            sections = [(section, chunks) for section, chunks in sections if chunks]
            current = self._file_info(file, path)
            if not os.path.samestat(info, current) or signature != _signature(current):
                raise FileAccessError("文件在索引时发生变化，请刷新索引。")
        except DocumentError as error:
            self.store.finish_index(file.id, version, "failed", str(error), "", [])
            return True
        except (OSError, FileAccessError) as error:
            self.store.unavailable(file.id, "failed", _reason(error))
            return True

        status, reason = "ready", ""
        if not sections:
            status, reason = (
                ("ocr", "PDF 无可提取文本，需要 OCR。")
                if file.extension == ".pdf"
                else ("empty", "未提取到可索引的文本。")
            )
        self.store.finish_index(file.id, version, status, reason, name_terms, sections)
        return True

    def metadata_current(self, file: FileRecord) -> bool:
        try:
            return _signature(self._file_info(file, self.path_for(file))) == (
                file.size,
                file.modified_ns,
                file.changed_ns,
            )
        except (OSError, FileAccessError):
            return False

    def content_current(self, file: FileRecord) -> bool:
        try:
            path = self.path_for(file)
            data = _read(path, self._file_info(file, path))
            return hashlib.sha256(data).hexdigest() == file.sha256
        except (OSError, FileAccessError):
            return False

    def preview(self, chunk_id: int, version: int) -> Passage:
        passage = self.store.passage(chunk_id, version)
        if passage is None or not self.content_current(passage.file):
            raise FileAccessError("原文已变化或无法读取。请刷新索引并重新检索。")
        return passage

    def preview_file(self, file_id: int) -> Passage:
        passage = self.store.first_passage(file_id)
        if passage is None or not self.content_current(passage.file):
            raise FileAccessError("没有可预览的原文。请检查文件状态并刷新索引。")
        return passage
