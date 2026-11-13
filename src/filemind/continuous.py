"""Keep approved directory catalogs current while the desktop window is closed."""

import logging
import os
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from filemind.documents import EXTENSIONS, file_extension
from filemind.embeddings import ModelError, SemanticIndex
from filemind.library import Library
from filemind.storage import Source

RECONCILE_SECONDS = 600
EVENT_SECONDS = 2
WORK_SECONDS = 0.2
INDEX_BATCH = 16
MAX_CHANGED_PATHS = 10_000


class _Changes(FileSystemEventHandler):
    def __init__(self, service: "ContinuousIndex", source: Source) -> None:
        self.service = service
        self.source = source

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "deleted", "moved", "closed"}:
            return
        root = Path(self.source.path)
        for value in (event.src_path, event.dest_path):
            if not value:
                continue
            path = Path(os.fsdecode(value))
            if not path.is_relative_to(root) or self.service.library.excluded(path, root):
                continue
            if self.service.owner(path) != self.source.id:
                continue
            if event.is_directory:
                if event.event_type != "modified":
                    self.service.mark_tree(self.source.id)
            elif file_extension(path) in EXTENSIONS:
                self.service.mark_path(self.source.id, path)
            elif event.event_type == "deleted":
                # ReadDirectoryChangesW does not identify removed directories as such.
                self.service.mark_tree(self.source.id)


class ContinuousIndex:
    def __init__(self, library: Library, semantic: SemanticIndex | None = None) -> None:
        self.library = library
        self.store = library.store
        self.semantic = semantic or SemanticIndex(library.paths.model, self.store)
        self._closing = Event()
        self._paused = Event()
        if self.store.get_preference("pause_monitoring") == "1":
            self._paused.set()
        self._foreground = Event()
        self._wake = Event()
        self._refresh = Event()
        self._refresh.set()
        self._writer = Lock()
        self._events = Lock()
        self._changed: dict[int, set[Path]] = defaultdict(set)
        self._trees: dict[int, float] = {}
        self._jobs: dict[int, Iterator[None]] = {}
        self._source_roots: tuple[tuple[Path, int], ...] = ()
        self._observer = Observer(timeout=0.5)
        self._watches = {}
        self._thread = Thread(target=self._run, name="FileMind index", daemon=True)
        self.error = ""

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def start(self) -> None:
        self._observer.start()
        self._thread.start()

    def request_stop(self) -> None:
        self._closing.set()
        self._wake.set()

    def stop(self) -> None:
        self.request_stop()
        self._thread.join()
        self._observer.stop()
        self._observer.join()

    def pause(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
            self._refresh.set()
        self.store.set_preference("pause_monitoring", "1" if paused else "0")
        self._wake.set()

    def refresh(self) -> None:
        self._refresh.set()
        self._wake.set()

    @contextmanager
    def foreground(self) -> Iterator[None]:
        self._foreground.set()
        try:
            with self._writer:
                yield
        finally:
            self._foreground.clear()
            self._wake.set()

    def owner(self, path: Path) -> int | None:
        return next(
            (source_id for root, source_id in self._source_roots if path.is_relative_to(root)), None
        )

    def mark_tree(self, source_id: int) -> None:
        with self._events:
            self._trees.setdefault(source_id, monotonic() + EVENT_SECONDS)

    def mark_path(self, source_id: int, path: Path) -> None:
        with self._events:
            paths = self._changed[source_id]
            if len(paths) >= MAX_CHANGED_PATHS:
                self._trees.setdefault(source_id, monotonic() + EVENT_SECONDS)
            else:
                paths.add(path)

    def _sync_watches(self, sources: list[Source]) -> None:
        self._source_roots = tuple(
            sorted(
                ((Path(source.path), source.id) for source in sources),
                key=lambda item: -len(item[0].parts),
            )
        )
        wanted = {source.id for source in sources}
        alive = {emitter.watch for emitter in self._observer.emitters if emitter.is_alive()}
        for source_id, watch in list(self._watches.items()):
            if source_id not in wanted or watch not in alive or self.paused:
                self._observer.unschedule(watch)
                del self._watches[source_id]
        if self.paused:
            return
        for source in sources:
            if source.id in self._watches:
                continue
            try:
                self._watches[source.id] = self._observer.schedule(
                    _Changes(self, source), source.path, recursive=True
                )
            except OSError:
                continue
            self.mark_tree(source.id)

    def _cancelled(self) -> bool:
        return self._closing.is_set() or self.paused or self._foreground.is_set()

    def _drain(self) -> tuple[set[int], dict[int, set[Path]]]:
        with self._events:
            trees = {source_id for source_id, due in self._trees.items() if due <= monotonic()}
            for source_id in trees:
                del self._trees[source_id]
            changes, self._changed = self._changed, defaultdict(set)
            return trees, changes

    def _run(self) -> None:
        semantic = self.semantic
        next_full = 0.0
        retry_model_at = 0.0
        while not self._closing.is_set():
            embedding_progress = False
            try:
                sources = self.store.sources()
                self._sync_watches(sources)
                if not self._cancelled() and self._writer.acquire(blocking=False):
                    try:
                        full = self._refresh.is_set() or (
                            not self._jobs and monotonic() >= next_full
                        )
                        if full:
                            self._refresh.clear()
                        self._update(sources, full)
                        if full:
                            next_full = monotonic() + RECONCILE_SECONDS
                        if full and self._cancelled() and not self._closing.is_set():
                            self._refresh.set()
                        # Use a small share of the first scan for vectors so content does not stall.
                        if (
                            not self._cancelled()
                            and monotonic() >= retry_model_at
                            and semantic.path.is_file()
                        ):
                            try:
                                for _ in range(1 if self._jobs else 4):
                                    if self._cancelled() or not semantic.populate_batch():
                                        break
                                    embedding_progress = True
                                retry_model_at = monotonic() + (
                                    8 if self._jobs and not semantic.uses_gpu else 0
                                )
                            except ModelError:
                                retry_model_at = monotonic() + 60
                        self.error = ""
                    finally:
                        self._writer.release()
            except Exception:
                logging.getLogger(__name__).exception("Continuous indexing failed")
                self.error = "后台索引暂时遇到错误，将自动重试。"
                next_full = monotonic() + 15
            busy = self._jobs or (embedding_progress and semantic.uses_gpu)
            wait = WORK_SECONDS if busy and not self._cancelled() else EVENT_SECONDS
            self._wake.wait(wait)
            self._wake.clear()

    def _update(self, sources: list[Source], full: bool) -> None:
        trees, changes = self._drain()
        ids = {source.id for source in sources}
        for source_id in list(self._jobs):
            if source_id not in ids:
                del self._jobs[source_id]
        for source in sources:
            if self._cancelled():
                self.refresh()
                return
            if paths := changes.get(source.id):
                batch = set(islice(paths, INDEX_BATCH))
                paths.difference_update(batch)
                self.library.update_paths(source, batch, self._cancelled)
                if self._cancelled():
                    self.refresh()
                if paths:
                    with self._events:
                        self._changed[source.id].update(paths)
        scans = [
            source
            for source in sources
            if full or (source.id in trees and source.id not in self._jobs)
        ]
        for source in scans:
            if self._cancelled():
                break
            self.library.scan(source, self._cancelled, sources=sources, catalog_only=True)
        if scans:
            scanned = {source.id for source in scans}
            for source in self.store.sources():
                if self._cancelled():
                    break
                if source.id in scanned and source.status in {"indexing", "failed"}:
                    self._jobs[source.id] = self.library.index_registered(source)
        for source_id in trees - {source.id for source in scans}:
            if source_id in ids:
                self.mark_tree(source_id)
        if self._cancelled() and scans:
            self.refresh()
        if full and not self._cancelled():
            self.library.refresh_managed(self._cancelled)
        deadline = monotonic() + 1.0
        for _ in range(INDEX_BATCH):
            if self._cancelled() or monotonic() >= deadline:
                return
            source_id = next(iter(self._jobs), None)
            if source_id is None:
                return
            job = self._jobs[source_id]
            if next(job, False) is False:
                del self._jobs[source_id]
