import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from filemind.config import AppPaths
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
