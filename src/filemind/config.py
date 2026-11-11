"""应用数据路径。"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @classmethod
    def resolve(cls, data_dir: Path | None = None) -> Self:
        if data_dir is None:
            local_app_data = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
            data_dir = Path(local_app_data) / "FileMind"
        return cls(data_dir.expanduser().resolve())

    @property
    def database(self) -> Path:
        return self.root / "filemind.sqlite3"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    def prepare(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
