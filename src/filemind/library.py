"""Managed copies, read-only source scans and repeatable indexing."""

import hashlib
import os
import re
import stat
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from filemind.config import AppPaths
from filemind.documents import (
    EXTENSIONS,
    MAX_FILE_BYTES,
    TEXT_EXTENSIONS,
    DocumentError,
    file_extension,
    parse_document,
)
from filemind.file_metadata import file_metadata
from filemind.storage import FileRecord, LocalStore, Passage, Source
from filemind.text_index import keyword_terms, split_section

DISCOVERY_BATCH = 256
_SKIPPED_DIRECTORIES = {
    "node_modules",
    "__pycache__",
    "$recycle.bin",
    "system volume information",
    "windowsapps",
}
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
    if not stat.S_ISREG(info.st_mode):
        raise FileAccessError("仅接入普通文件。")


def _signature(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, str]:
    return (*_signature(info), f"{info.st_dev}:{info.st_ino}")


def _stored_fingerprint(file: FileRecord) -> tuple[int | None, int | None, int | None, str | None]:
    return file.size, file.modified_ns, file.changed_ns, file.identity


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
        self.excluded_roots = (paths.root, AppPaths.resolve().root)
        project = Path(__file__).resolve().parents[2]
        if (project / "pyproject.toml").is_file() and (project / "src" / "filemind").is_dir():
            self.excluded_roots += (project / "data",)
        if os.name == "nt":
            self.excluded_roots += (Path(os.environ.get("SystemRoot", r"C:\Windows")),)

    def excluded(self, path: Path, root: Path | None = None) -> bool:
        parts = path.relative_to(root).parts if root is not None else path.parts[1:]
        return any(path.is_relative_to(item) for item in self.excluded_roots) or any(
            _excluded_name(part) for part in parts
        )

    def _external(self, path: Path) -> tuple[Path, os.stat_result]:
        info = _inspect(path)
        actual = path.resolve(strict=True)
        if (_is_hidden(info) and actual.parent != actual) or self.excluded(actual):
            raise FileAccessError("跳过隐藏文件、敏感配置或系统和应用数据目录。")
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
                if file_extension(source) not in EXTENSIONS:
                    raise FileAccessError("不支持此文件格式。")
                if info.st_size > MAX_FILE_BYTES:
                    raise FileAccessError("导入副本限 32 MiB。可关联其目录后按文件名检索。")
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

    def register_directory(self, path: Path) -> int:
        root, info = self._external(Path(os.path.abspath(path)))
        if not stat.S_ISDIR(info.st_mode):
            raise FileAccessError("请选择要关联的资料目录。")
        for existing in self.store.sources():
            existing_root = Path(existing.path)
            if root.is_relative_to(existing_root):
                return existing.id
        return self.store.register_source(root, _key(root))

    def link_directory(self, path: Path, cancelled: Callable[[], bool] = _continue) -> int:
        source_id = self.register_directory(path)
        source = next(source for source in self.store.sources() if source.id == source_id)
        self.scan(source, cancelled)
        return source_id

    def refresh(self, cancelled: Callable[[], bool] = _continue, *, verify: bool = True) -> None:
        sources = self.store.sources()
        for source in sources:
            if cancelled():
                return
            self.scan(source, cancelled, verify=verify, sources=sources)
        self.refresh_managed(cancelled, verify=verify)

    def refresh_managed(self, cancelled: Callable[[], bool], *, verify: bool = False) -> None:
        _require_directory(self.paths.workspace)
        for file in self.store.source_files(None).values():
            if cancelled():
                return
            self._index(file, missing_allowed=True, verify=verify)

    def _discover(
        self,
        root: Path,
        cancelled: Callable[[], bool],
        register: Callable[[dict[str, Path]], None],
        skip: set[Path],
    ) -> tuple[dict[str, Path], str]:
        pending = [root]
        found = {}
        batch = {}
        warning = ""
        while pending:
            directory = pending.pop()
            try:
                _require_directory(directory)
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if cancelled():
                            return found, "扫描已中止。"
                        path = Path(entry.path)
                        if (
                            _excluded_name(entry.name)
                            or path in skip
                            or any(path == item for item in self.excluded_roots)
                        ):
                            continue
                        extension = file_extension(path)
                        if extension not in EXTENSIONS and not entry.is_dir(follow_symlinks=False):
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
                        elif stat.S_ISREG(info.st_mode) and extension in EXTENSIONS:
                            relative = path.relative_to(root)
                            key = _key(relative)
                            found[key] = batch[key] = relative
                            if len(batch) == DISCOVERY_BATCH:
                                register(batch)
                                batch.clear()
            except (OSError, FileAccessError):
                warning = "部分文件或子目录无法访问。"
        if batch:
            register(batch)
        return found, warning

    def scan(
        self,
        source: Source,
        cancelled: Callable[[], bool],
        *,
        verify: bool = False,
        sources: list[Source] | None = None,
        catalog_only: bool = False,
    ) -> None:
        self.store.update_source(source.id, "scanning")
        root = Path(source.path)
        try:
            actual, info = self._external(root)
            if actual != root or not stat.S_ISDIR(info.st_mode):
                raise FileAccessError("关联目录已变更，请重新关联。")
        except (OSError, FileAccessError) as error:
            self.store.update_source(source.id, "failed", _reason(error))
            return

        linked = sources if sources is not None else self.store.sources()
        skip = {
            Path(item.path)
            for item in linked
            if item.id != source.id and Path(item.path).is_relative_to(root)
        }
        found, warning = self._discover(
            root, cancelled, lambda batch: self.store.register_batch(source.id, batch), skip
        )
        if cancelled():
            self.store.update_source(source.id, "pending", "扫描已中止，可在下次启动继续。")
            return
        existing = self.store.source_files(source.id)
        if not catalog_only:
            self.store.update_source(
                source.id, "indexing", f"发现 {len(found)} 个文件，正在补建内容索引。"
            )
            for key in found:
                if cancelled():
                    self.store.update_source(source.id, "pending", "索引已中止，可在下次启动继续。")
                    return
                file = existing.get(key)
                if file is not None and not self._index(
                    file, missing_allowed=not warning, verify=verify
                ):
                    warning = "部分文件在扫描时无法访问。"
        if warning:
            self.store.update_source(source.id, "failed", warning + " 已保留未确认缺失的记录。")
            return
        if cancelled():
            return
        for key, file in existing.items():
            if key not in found and file.status != "missing":
                self.store.unavailable(file.id, "missing", "完整扫描时未找到该文件。")
        self.store.update_source(
            source.id, "indexing" if catalog_only else "ready", f"已登记 {len(found)} 个文件。"
        )

    def update_paths(
        self, source: Source, changed: set[Path], cancelled: Callable[[], bool]
    ) -> None:
        root = Path(source.path)
        try:
            _require_directory(root)
        except (OSError, FileAccessError):
            self.store.update_source(source.id, "failed", "关联目录暂不可访问。")
            return
        for path in changed:
            if cancelled():
                return
            if (
                not path.is_relative_to(root)
                or self.excluded(path, root)
                or file_extension(path) not in EXTENSIONS
            ):
                continue
            relative = path.relative_to(root)
            key = _key(relative)
            existing = self.store.file_at(source.id, key)
            try:
                info = _inspect(path)
            except FileNotFoundError:
                if existing and existing.status != "missing":
                    self.store.unavailable(existing.id, "missing", "文件已移除。")
                continue
            except (OSError, FileAccessError):
                continue
            if _is_hidden(info) or not stat.S_ISREG(info.st_mode):
                continue
            if existing is None:
                self.store.register_batch(source.id, {key: relative})
                existing = self.store.file_at(source.id, key)
            if existing is not None:
                self._index(existing, missing_allowed=True, verify=True)

    def index_registered(self, source: Source) -> Iterator[None]:
        warning = False
        after = 0
        while batch := self.store.source_batch(source.id, after):
            for file in batch:
                warning = not self._index(file, missing_allowed=False) or warning
                yield
            after = batch[-1].id
        if source.status == "indexing":
            reason = (
                source.reason + " 部分文件在索引时移除，等待复查。" if warning else source.reason
            )
            self.store.update_source(source.id, "failed" if warning else "ready", reason)

    def retry(self, file_id: int) -> None:
        file = self.store.file(file_id)
        if file is not None:
            self._index(file, force=True, missing_allowed=file.source_id is None)

    def _index(
        self,
        file: FileRecord,
        *,
        force: bool = False,
        missing_allowed: bool = False,
        verify: bool = False,
    ) -> bool:
        try:
            path = self.path_for(file)
            self._complete_copy(file, path)
            info = self._file_info(file, path)
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

        signature = _fingerprint(info)
        metadata = file.extension not in TEXT_EXTENSIONS or info.st_size > MAX_FILE_BYTES
        if (
            not force
            and (not verify or metadata)
            and file.status not in {"pending", "missing", "failed"}
            and signature == _stored_fingerprint(file)
        ):
            return True
        sha = None
        if not metadata:
            try:
                data = _read(path, info)
            except (OSError, FileAccessError) as error:
                self.store.unavailable(file.id, "failed", _reason(error))
                return True
            sha = hashlib.sha256(data).hexdigest()
        if sha is not None and sha == file.sha256 and file.status != "pending" and not force:
            if signature != _stored_fingerprint(file):
                self.store.update_file_stat(file.id, signature)
            return True

        version = self.store.begin_index(file.id, sha, signature)
        name_terms = " ".join(keyword_terms(file.name))
        status, reason = "ready", ""
        try:
            if metadata:
                raw = file_metadata(path, file.extension)
                status = "cataloged"
                if file.extension in TEXT_EXTENSIONS:
                    reason = "超过 32 MiB，已登记文件名和路径。"
                elif not raw or not any(section.text.strip() for section in raw):
                    reason = "已登记文件名和路径。"
            else:
                raw = parse_document(data, file.extension)
            sections = [(section, split_section(section)) for section in raw]
            sections = [(section, chunks) for section, chunks in sections if chunks]
            current = self._file_info(file, path)
            if signature != _fingerprint(current):
                raise FileAccessError("文件在索引时发生变化，请刷新索引。")
        except DocumentError as error:
            self.store.finish_index(
                file.id, version, "cataloged" if metadata else "failed", str(error), "", []
            )
            return True
        except (OSError, FileAccessError) as error:
            self.store.unavailable(file.id, "failed", _reason(error))
            return True

        if not sections and not metadata:
            status, reason = (
                ("ocr", "PDF 无可提取文本，需要 OCR。")
                if file.extension == ".pdf"
                else ("empty", "未提取到可索引的文本。")
            )
        self.store.finish_index(file.id, version, status, reason, name_terms, sections)
        return True

    def metadata_current(self, file: FileRecord) -> bool:
        try:
            fingerprint = _fingerprint(self._file_info(file, self.path_for(file)))
            if file.identity is None:
                return file.status == "ready" and fingerprint[:3] == _stored_fingerprint(file)[:3]
            return fingerprint == _stored_fingerprint(file)
        except (OSError, FileAccessError):
            return False

    def catalog_current(self, file: FileRecord) -> bool:
        if file.import_from is not None:
            return False
        try:
            self._file_info(file, self.path_for(file))
            return True
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
        if passage is None or not self._preview_current(passage.file):
            raise FileAccessError("原文已变化或无法读取。请刷新索引并重新检索。")
        return passage

    def preview_file(self, file_id: int) -> Passage:
        passage = self.store.first_passage(file_id)
        if passage is None or not self._preview_current(passage.file):
            raise FileAccessError("没有可预览的原文。请检查文件状态并刷新索引。")
        return passage

    def _preview_current(self, file: FileRecord) -> bool:
        return (
            self.metadata_current(file)
            if file.status == "cataloged"
            else self.content_current(file)
        )
