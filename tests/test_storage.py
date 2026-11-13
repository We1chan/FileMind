import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from filemind.config import AppPaths
from filemind.library import Library
from filemind.search import Search
from filemind.storage import SCHEMA_VERSION, LocalStore, UnsupportedSchemaError


def test_initialization_and_preferences_survive_reopen(tmp_path: Path) -> None:
    paths = AppPaths.resolve(tmp_path / "资料库")
    paths.prepare()
    first_store = LocalStore(paths.database)

    status = first_store.initialize()
    assert status.schema_version == SCHEMA_VERSION
    assert status.fts5_available
    assert paths.workspace.is_dir()
    assert first_store.get_preference("last_page") is None
    first_store.set_preference("last_page", "library")
    first_store.set_preference("last_page", "search")

    reopened = LocalStore(paths.database)
    assert reopened.initialize() == status
    assert reopened.get_preference("last_page") == "search"


def test_newer_schema_is_rejected_without_changing_data(tmp_path: Path) -> None:
    database = tmp_path / "filemind.sqlite3"
    newer_version = SCHEMA_VERSION + 1
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE saved_data (content TEXT NOT NULL)")
        connection.execute("INSERT INTO saved_data VALUES ('keep')")
        connection.execute(f"PRAGMA user_version = {newer_version}")

    with pytest.raises(UnsupportedSchemaError, match=str(newer_version)):
        LocalStore(database).initialize()

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (newer_version,)
        assert connection.execute("SELECT content FROM saved_data").fetchall() == [("keep",)]


def test_migrates_foundation_database_with_existing_preference(tmp_path: Path) -> None:
    database = tmp_path / "filemind.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO preferences VALUES ('last_page', 'search')")
        connection.execute("PRAGMA user_version = 1")

    store = LocalStore(database)
    assert store.initialize().schema_version == SCHEMA_VERSION
    assert store.get_preference("last_page") == "search"
    assert store.files() == []


def test_rebuilds_filename_index_for_version_two_library(tmp_path: Path) -> None:
    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    store.initialize()
    note = tmp_path / "known_notes.txt"
    note.write_text("PreservedCitationNeedle", encoding="utf-8")
    library = Library(paths, store)
    library.import_files([note])
    original = library.preview_file(store.files()[0].id)
    with closing(sqlite3.connect(paths.database)) as connection, connection:
        connection.execute("DROP TABLE file_catalog")
        connection.execute("DROP INDEX files_display_order")
        connection.execute("DROP INDEX files_by_status")
        connection.execute("DROP INDEX files_by_source")
        connection.execute("ALTER TABLE files DROP COLUMN identity")
        connection.execute("PRAGMA user_version = 2")

    reopened = LocalStore(paths.database)
    assert reopened.initialize().schema_version == SCHEMA_VERSION
    migrated = Library(paths, reopened)
    preserved = migrated.preview(original.chunk_id, original.file.version)
    assert (preserved.file.id, preserved.file.version, preserved.text) == (
        original.file.id,
        original.file.version,
        original.text,
    )
    hits = Search(migrated).find("known notes", "keyword").hits
    assert [hit.file.id for hit in hits] == [original.file.id]
