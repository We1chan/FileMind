"""SQLite migrations, library records and versioned passages."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from filemind.documents import Section, file_extension
from filemind.text_index import TextChunk, catalog_terms

SCHEMA_VERSION = 3
_LIBRARY_SCHEMA = (
    """CREATE TABLE sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL,
        path_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
        relative_path TEXT NOT NULL,
        path_key TEXT NOT NULL,
        name TEXT NOT NULL,
        extension TEXT NOT NULL,
        import_from TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT NOT NULL DEFAULT '',
        sha256 TEXT,
        version INTEGER NOT NULL DEFAULT 0,
        size INTEGER,
        modified_ns INTEGER,
        changed_ns INTEGER
    )""",
    "CREATE UNIQUE INDEX managed_path ON files(path_key) WHERE source_id IS NULL",
    "CREATE UNIQUE INDEX linked_path ON files(source_id, path_key) WHERE source_id IS NOT NULL",
    """CREATE TABLE sections (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        location TEXT NOT NULL,
        text TEXT NOT NULL
    )""",
    "CREATE INDEX sections_file ON sections(file_id)",
    """CREATE TABLE chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
        start INTEGER NOT NULL,
        end INTEGER NOT NULL,
        location TEXT NOT NULL,
        name_terms TEXT NOT NULL,
        body_terms TEXT NOT NULL,
        model_fingerprint TEXT,
        vector BLOB
    )""",
    "CREATE INDEX chunks_section ON chunks(section_id)",
)
_FILES = "FROM files f LEFT JOIN sources src ON src.id = f.source_id"
_PASSAGES = """
    FROM chunks c JOIN sections s ON s.id = c.section_id
    JOIN files f ON f.id = s.file_id LEFT JOIN sources src ON src.id = f.source_id
"""
_PASSAGE_COLUMNS = """
    f.*, src.path AS source_path, c.id AS chunk_id, c.location AS chunk_location,
    c.start, c.end, s.text AS section_text
"""


class UnsupportedSchemaError(RuntimeError):
    """当前程序无法打开该数据库版本。"""


@dataclass(frozen=True, slots=True)
class StorageStatus:
    schema_version: int
    fts5_available: bool


@dataclass(frozen=True, slots=True)
class Source:
    id: int
    path: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class FileRecord:
    id: int
    source_id: int | None
    source_path: str | None
    relative_path: str
    name: str
    extension: str
    import_from: str | None
    status: str
    reason: str
    sha256: str | None
    version: int
    size: int | None
    modified_ns: int | None
    changed_ns: int | None
    identity: str | None = None


@dataclass(frozen=True, slots=True)
class Passage:
    file: FileRecord
    chunk_id: int
    location: str
    section_text: str
    start: int
    end: int

    @property
    def text(self) -> str:
        return self.section_text[self.start : self.end]


@dataclass(frozen=True, slots=True)
class SearchScope:
    extension: str | None = None
    source: str | None = None
    file_ids: tuple[int, ...] | None = None
    content_only: bool = False
    source_id: int | None = None

    def clause(self, *, catalog: bool = False) -> tuple[str, list[str | int]]:
        if catalog:
            conditions = ["f.status != 'missing'"]
        elif self.content_only:
            conditions = ["f.status = 'ready'"]
        else:
            conditions = ["f.status IN ('ready', 'cataloged')"]
        parameters: list[str | int] = []
        if self.extension is not None:
            conditions.append("f.extension = ?")
            parameters.append(self.extension)
        if self.source == "managed":
            conditions.append("f.source_id IS NULL")
        elif self.source == "linked":
            conditions.append("f.source_id IS NOT NULL")
        if self.source_id is not None:
            conditions.append("f.source_id = ?")
            parameters.append(self.source_id)
        if self.file_ids is not None:
            if self.file_ids:
                conditions.append(f"f.id IN ({','.join('?' for _ in self.file_ids)})")
                parameters.extend(self.file_ids)
            else:
                conditions.append("0")
        return " AND ".join(conditions), parameters


def _file(row: sqlite3.Row) -> FileRecord:
    columns = set(row.keys())
    return FileRecord(
        **{name: row[name] for name in FileRecord.__dataclass_fields__ if name in columns}
    )


def _passage(row: sqlite3.Row) -> Passage:
    return Passage(
        _file(row),
        row["chunk_id"],
        row["chunk_location"],
        row["section_text"],
        row["start"],
        row["end"],
    )


class LocalStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.fts5_available = False

    def initialize(self) -> StorageStatus:
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not 0 <= schema_version <= SCHEMA_VERSION:
                raise UnsupportedSchemaError(
                    f"当前 FileMind 无法打开数据库结构版本 {schema_version}。"
                    "请使用与该数据匹配的应用版本。"
                )
            if schema_version == 0:
                connection.execute(
                    "CREATE TABLE preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
            if schema_version < 2:
                for statement in _LIBRARY_SCHEMA:
                    connection.execute(statement)
            if schema_version < 3:
                connection.execute(
                    "CREATE INDEX files_display_order ON files(name COLLATE NOCASE, id)"
                )
                connection.execute("CREATE INDEX files_by_status ON files(status)")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            if not any(
                row[1] == "identity" for row in connection.execute("PRAGMA table_info(files)")
            ):
                connection.execute("ALTER TABLE files ADD COLUMN identity TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS files_by_source ON files(source_id, id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chunks_pending_embedding ON chunks(id) WHERE vector IS NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chunks_by_embedding_model ON chunks(model_fingerprint, id)"
            )

            try:
                connection.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(content)")
            except sqlite3.OperationalError as error:
                if str(error) != "no such module: fts5":
                    raise
                self.fts5_available = False
                return StorageStatus(SCHEMA_VERSION, fts5_available=False)

            has_fts = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'chunk_fts'"
            ).fetchone()
            dirty = connection.execute(
                "SELECT 1 FROM preferences WHERE name = 'fts5_dirty'"
            ).fetchone()
            if not has_fts or dirty:
                connection.execute("DROP TABLE IF EXISTS chunk_fts")
                connection.execute(
                    "CREATE VIRTUAL TABLE chunk_fts USING fts5(name_terms, body_terms)"
                )
                connection.execute(
                    "INSERT INTO chunk_fts(rowid, name_terms, body_terms) "
                    "SELECT id, name_terms, body_terms FROM chunks"
                )
                connection.execute("DELETE FROM preferences WHERE name = 'fts5_dirty'")
            has_catalog = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'file_catalog'"
            ).fetchone()
            if not has_catalog or dirty:
                connection.execute("DROP TABLE IF EXISTS file_catalog")
                connection.execute(
                    "CREATE VIRTUAL TABLE file_catalog USING fts5(name_terms, path_terms)"
                )
                connection.executemany(
                    "INSERT INTO file_catalog(rowid, name_terms, path_terms) VALUES (?, ?, ?)",
                    (
                        (
                            row[0],
                            catalog_terms(row[1]),
                            catalog_terms(row[2]) if row[3] is not None else "",
                        )
                        for row in connection.execute(
                            "SELECT id, name, relative_path, source_id FROM files"
                        )
                    ),
                )

        self.fts5_available = True
        return StorageStatus(SCHEMA_VERSION, fts5_available=True)

    def get_preference(self, name: str) -> str | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT value FROM preferences WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def set_preference(self, name: str, value: str) -> None:
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO preferences (name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value = excluded.value
                """,
                (name, value),
            )

    def sources(self) -> list[Source]:
        with self._session() as connection:
            return [
                Source(*row)
                for row in connection.execute(
                    "SELECT id, path, status, reason FROM sources ORDER BY path"
                )
            ]

    def register_source(self, path: Path, path_key: str) -> int:
        with self._session() as connection:
            connection.execute(
                "INSERT INTO sources(path, path_key) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (str(path), path_key),
            )
            return connection.execute(
                "SELECT id FROM sources WHERE path_key = ?", (path_key,)
            ).fetchone()[0]

    def update_source(self, source_id: int, status: str, reason: str = "") -> None:
        with self._session() as connection:
            connection.execute(
                "UPDATE sources SET status = ?, reason = ? WHERE id = ?",
                (status, reason, source_id),
            )

    def remove_source(self, source_id: int) -> None:
        with self._session() as connection:
            if self.fts5_available:
                connection.execute(
                    "DELETE FROM file_catalog WHERE rowid IN (SELECT id FROM files WHERE source_id = ?)",
                    (source_id,),
                )
                connection.execute(
                    """DELETE FROM chunk_fts WHERE rowid IN (
                    SELECT c.id FROM chunks c JOIN sections s ON s.id = c.section_id
                    JOIN files f ON f.id = s.file_id WHERE f.source_id = ?
                )""",
                    (source_id,),
                )
            else:
                connection.execute("INSERT OR IGNORE INTO preferences VALUES ('fts5_dirty', '1')")
            connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def files(
        self, *, limit: int = -1, offset: int = 0, name: str = "", status: str | None = None
    ) -> list[FileRecord]:
        filters, values = [], []
        if name:
            filters.append(
                "(instr(lower(f.name), lower(?)) > 0 OR instr(lower(f.relative_path), lower(?)) > 0)"
            )
            values.extend((name, name))
        if status == "tasks":
            filters.append("f.status IN ('failed', 'ocr', 'empty', 'pending', 'missing')")
        elif status == "searchable":
            filters.append("f.status NOT IN ('missing', 'failed')")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._session() as connection:
            rows = connection.execute(
                f"SELECT f.*, src.path AS source_path {_FILES} {where} "
                "ORDER BY f.name COLLATE NOCASE, f.id LIMIT ? OFFSET ?",
                (*values, limit, offset),
            )
            return [_file(row) for row in rows]

    def counts(self) -> tuple[int, int]:
        with self._session() as connection:
            return tuple(
                connection.execute(
                    "SELECT count(*), count(*) FILTER (WHERE status IN ('ready', 'cataloged')) FROM files"
                ).fetchone()
            )

    def source_files(self, source_id: int | None) -> dict[str, FileRecord]:
        with self._session() as connection:
            rows = connection.execute(
                f"SELECT f.*, src.path AS source_path {_FILES} WHERE f.source_id IS ?", (source_id,)
            )
            return {row["path_key"]: _file(row) for row in rows}

    def source_batch(self, source_id: int, after: int) -> list[FileRecord]:
        with self._session() as connection:
            rows = connection.execute(
                f"SELECT f.*, src.path AS source_path {_FILES} "
                "WHERE f.source_id = ? AND f.id > ? AND f.status != 'missing' ORDER BY f.id LIMIT 64",
                (source_id, after),
            )
            return [_file(row) for row in rows]

    def file_at(self, source_id: int, key: str) -> FileRecord | None:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT f.*, src.path AS source_path {_FILES} WHERE f.source_id = ? AND f.path_key = ?",
                (source_id, key),
            ).fetchone()
            return _file(row) if row else None

    def file(self, file_id: int) -> FileRecord | None:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT f.*, src.path AS source_path {_FILES} WHERE f.id = ?", (file_id,)
            ).fetchone()
            return _file(row) if row else None

    def files_by_id(self, file_ids: set[int]) -> dict[int, FileRecord]:
        ids = list(file_ids)
        result = {}
        with self._session() as connection:
            for start in range(0, len(ids), 500):
                batch = ids[start : start + 500]
                rows = connection.execute(
                    f"SELECT f.*, src.path AS source_path {_FILES} "
                    f"WHERE f.id IN ({','.join('?' for _ in batch)})",
                    batch,
                )
                result.update((row["id"], _file(row)) for row in rows)
        return result

    def register_file(
        self, source_id: int | None, relative: Path, key: str, *, import_from: Path | None = None
    ) -> int:
        with self._session() as connection:
            self._register_file(connection, source_id, relative, key, import_from)
            return connection.execute(
                "SELECT id FROM files WHERE source_id IS ? AND path_key = ?", (source_id, key)
            ).fetchone()[0]

    def register_batch(self, source_id: int, batch: dict[str, Path]) -> None:
        with self._session() as connection:
            for key, relative in batch.items():
                self._register_file(connection, source_id, relative, key, None)

    def _register_file(
        self,
        connection: sqlite3.Connection,
        source_id: int | None,
        relative: Path,
        key: str,
        import_from: Path | None,
    ) -> None:
        inserted = connection.execute(
            """INSERT INTO files(source_id, relative_path, path_key, name, extension, import_from)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
            (
                source_id,
                str(relative),
                key,
                relative.name,
                file_extension(relative),
                str(import_from) if import_from is not None else None,
            ),
        )
        if inserted.rowcount and self.fts5_available:
            connection.execute(
                "INSERT INTO file_catalog(rowid, name_terms, path_terms) VALUES (?, ?, ?)",
                (
                    inserted.lastrowid,
                    catalog_terms(relative.name),
                    catalog_terms(str(relative)) if source_id is not None else "",
                ),
            )
        elif inserted.rowcount:
            connection.execute("INSERT OR IGNORE INTO preferences VALUES ('fts5_dirty', '1')")

    def complete_import(self, file_id: int) -> None:
        with self._session() as connection:
            connection.execute(
                "UPDATE files SET import_from = NULL, status = 'pending', reason = '' WHERE id = ?",
                (file_id,),
            )

    def unavailable(self, file_id: int, status: str, reason: str) -> None:
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._delete_sections(connection, file_id)
            connection.execute(
                """UPDATE files SET status = ?, reason = ?, sha256 = NULL, size = NULL,
                modified_ns = NULL, changed_ns = NULL, identity = NULL WHERE id = ?""",
                (status, reason, file_id),
            )

    def update_file_stat(self, file_id: int, fingerprint: tuple[int, int, int, str]) -> None:
        with self._session() as connection:
            connection.execute(
                "UPDATE files SET size = ?, modified_ns = ?, changed_ns = ?, identity = ? WHERE id = ?",
                (*fingerprint, file_id),
            )

    def begin_index(
        self, file_id: int, sha256: str | None, fingerprint: tuple[int, int, int, str]
    ) -> int:
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._delete_sections(connection, file_id)
            return connection.execute(
                """UPDATE files SET version = version + (sha256 IS NOT ? OR ? IS NULL), sha256 = ?,
                size = ?, modified_ns = ?, changed_ns = ?, identity = ?, status = 'pending', reason = ''
                WHERE id = ? RETURNING version""",
                (sha256, sha256, sha256, *fingerprint, file_id),
            ).fetchone()[0]

    def finish_index(
        self,
        file_id: int,
        version: int,
        status: str,
        reason: str,
        name_terms: str,
        sections: list[tuple[Section, list[TextChunk]]],
    ) -> None:
        with self._session() as connection:
            updated = connection.execute(
                "UPDATE files SET status = ?, reason = ? WHERE id = ? AND version = ? AND status = 'pending'",
                (status, reason, file_id, version),
            )
            if not updated.rowcount:
                return
            for section, chunks in sections:
                section_id = connection.execute(
                    "INSERT INTO sections(file_id, location, text) VALUES (?, ?, ?)",
                    (file_id, section.location, section.text),
                ).lastrowid
                for chunk in chunks:
                    chunk_id = connection.execute(
                        """INSERT INTO chunks(section_id, start, end, location, name_terms, body_terms)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            section_id,
                            chunk.start,
                            chunk.end,
                            chunk.location,
                            name_terms,
                            chunk.terms,
                        ),
                    ).lastrowid
                    if self.fts5_available:
                        connection.execute(
                            "INSERT INTO chunk_fts(rowid, name_terms, body_terms) VALUES (?, ?, ?)",
                            (chunk_id, name_terms, chunk.terms),
                        )

    def _delete_sections(self, connection: sqlite3.Connection, file_id: int) -> None:
        if self.fts5_available:
            connection.execute(
                """DELETE FROM chunk_fts WHERE rowid IN (
                    SELECT c.id FROM chunks c JOIN sections s ON s.id = c.section_id WHERE s.file_id = ?
                )""",
                (file_id,),
            )
        else:
            connection.execute("INSERT OR IGNORE INTO preferences VALUES ('fts5_dirty', '1')")
        connection.execute("DELETE FROM sections WHERE file_id = ?", (file_id,))

    def passage(self, chunk_id: int, version: int) -> Passage | None:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT {_PASSAGE_COLUMNS} {_PASSAGES} "
                "WHERE c.id = ? AND f.version = ? AND f.status IN ('ready', 'cataloged')",
                (chunk_id, version),
            ).fetchone()
            return _passage(row) if row else None

    def first_passage(self, file_id: int) -> Passage | None:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT {_PASSAGE_COLUMNS} {_PASSAGES} "
                "WHERE f.id = ? AND f.status IN ('ready', 'cataloged') ORDER BY c.id LIMIT 1",
                (file_id,),
            ).fetchone()
            return _passage(row) if row else None

    def passages(self, chunk_ids: list[int]) -> dict[int, Passage]:
        if not chunk_ids:
            return {}
        with self._session() as connection:
            rows = connection.execute(
                f"SELECT {_PASSAGE_COLUMNS} {_PASSAGES} "
                f"WHERE c.id IN ({','.join('?' for _ in chunk_ids)}) AND f.status IN ('ready', 'cataloged')",
                chunk_ids,
            )
            return {row["chunk_id"]: _passage(row) for row in rows}

    def keyword_ranks(
        self, query: str, scope: SearchScope, budget: int = 6000
    ) -> list[tuple[int, int]]:
        if not self.fts5_available or not query:
            return []
        clause, parameters = scope.clause()
        with self._session() as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    f"""SELECT f.id, c.id {_PASSAGES} JOIN chunk_fts ON chunk_fts.rowid = c.id
                WHERE chunk_fts MATCH ? AND {clause} ORDER BY bm25(chunk_fts, 1.8, 1.0), c.id LIMIT ?""",
                    (query, *parameters, budget),
                )
            ]

    def catalog_ranks(self, query: str, scope: SearchScope, budget: int = 600) -> list[int]:
        if not self.fts5_available or not query:
            return []
        clause, parameters = scope.clause(catalog=True)
        with self._session() as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT f.id FROM file_catalog JOIN files f ON f.id = file_catalog.rowid "
                    f"WHERE file_catalog MATCH ? AND {clause} ORDER BY bm25(file_catalog, 4.0, 1.0), f.id LIMIT ?",
                    (query, *parameters, budget),
                )
            ]

    def filename_ranks(self, query: str, scope: SearchScope, limit: int) -> list[int]:
        clause, parameters = scope.clause(catalog=True)
        with self._session() as connection:
            return [
                row[0]
                for row in connection.execute(
                    f"""SELECT f.id FROM files f WHERE {clause} AND (
                    instr(lower(f.name), lower(?)) > 0 OR instr(lower(f.relative_path), lower(?)) > 0
                ) ORDER BY (f.name = ? COLLATE NOCASE) DESC,
                    (instr(lower(f.name), lower(?)) = 1) DESC, length(f.name), f.id LIMIT ?""",
                    (*parameters, query, query.replace("/", "\\"), query, query, limit),
                )
            ]

    def embedding_inputs(
        self, fingerprint: str, scope: SearchScope, *, limit: int = 16
    ) -> list[tuple[int, str]]:
        clause, parameters = scope.clause()
        with self._session() as connection:
            pending = connection.execute(
                f"""SELECT c.id, s.text, c.start, c.end FROM chunks c INDEXED BY chunks_pending_embedding
                JOIN sections s ON s.id = c.section_id JOIN files f ON f.id = s.file_id
                WHERE c.vector IS NULL AND {clause} ORDER BY c.id LIMIT ?""",
                (*parameters, limit),
            ).fetchall()
            remaining = limit - len(pending)
            if remaining:
                pending.extend(
                    connection.execute(
                        f"""SELECT c.id, s.text, c.start, c.end FROM chunks c INDEXED BY chunks_by_embedding_model
                    JOIN sections s ON s.id = c.section_id JOIN files f ON f.id = s.file_id
                    WHERE c.model_fingerprint IS NOT NULL AND c.model_fingerprint != ?
                      AND c.vector IS NOT NULL AND {clause} ORDER BY c.id LIMIT ?""",
                        (fingerprint, *parameters, remaining),
                    )
                )
            return [(row[0], row[1][row[2] : row[3]]) for row in pending]

    def set_embeddings(self, fingerprint: str, vectors: list[tuple[bytes, int]]) -> None:
        with self._session() as connection:
            connection.executemany(
                "UPDATE chunks SET model_fingerprint = ?, vector = ? WHERE id = ?",
                ((fingerprint, vector, chunk_id) for vector, chunk_id in vectors),
            )

    def embeddings(self, fingerprint: str, scope: SearchScope) -> Iterator[tuple[int, int, bytes]]:
        clause, parameters = scope.clause()
        with self._session() as connection:
            rows = connection.execute(
                f"""SELECT f.id, c.id, c.vector {_PASSAGES}
                WHERE c.model_fingerprint = ? AND {clause} ORDER BY c.id""",
                (fingerprint, *parameters),
            )
            yield from map(tuple, rows)

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path, timeout=3.0)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
