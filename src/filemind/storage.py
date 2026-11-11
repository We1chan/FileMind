"""本地数据库初始化、版本检查和界面偏好。"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1


class UnsupportedSchemaError(RuntimeError):
    """当前程序无法打开该数据库版本。"""


@dataclass(frozen=True, slots=True)
class StorageStatus:
    schema_version: int
    fts5_available: bool


class LocalStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> StorageStatus:
        with closing(self._connect()) as connection:
            # 将版本检查与迁移放在同一事务，避免两个进程同时初始化。
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                if schema_version == 0:
                    connection.execute(
                        "CREATE TABLE preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    schema_version = SCHEMA_VERSION
                elif schema_version != SCHEMA_VERSION:
                    raise UnsupportedSchemaError(
                        f"当前 FileMind 无法打开数据库结构版本 {schema_version}。"
                        "请使用与该数据匹配的应用版本。"
                    )

            # 实际创建临时表验证 FTS5，探测结果不会进入资料库。
            try:
                connection.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(content)")
            except sqlite3.OperationalError as error:
                if str(error) != "no such module: fts5":
                    raise
                return StorageStatus(schema_version, fts5_available=False)

        return StorageStatus(schema_version, fts5_available=True)

    def get_preference(self, name: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM preferences WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def set_preference(self, name: str, value: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO preferences (name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value = excluded.value
                """,
                (name, value),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=0.5)
