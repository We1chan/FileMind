"""应用数据路径。"""

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Self


def checkout_data_dir() -> Path | None:
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file() and (project / "src" / "filemind").is_dir():
        return project / "data"
    return None


def installed_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(local_app_data) / "FileMind"


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @classmethod
    def resolve(cls, data_dir: Path | None = None) -> Self:
        if data_dir is None:
            checkout = checkout_data_dir()
            data_dir = checkout / "library" if checkout is not None else installed_data_dir()
        return cls(data_dir.expanduser().resolve())

    @property
    def database(self) -> Path:
        return self.root / "filemind.sqlite3"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def model(self) -> Path:
        return self.root / "models" / "bge-small-zh-v1.5.onnx"

    @property
    def temporary(self) -> Path:
        return self.root / "tmp"

    @property
    def bytecode(self) -> Path:
        return (checkout_data_dir() or self.root) / "cache" / "python"

    def prepare(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

    def use_local_temp(self) -> None:
        self.temporary.mkdir(parents=True, exist_ok=True)
        directory = str(self.temporary)
        os.environ.update(TEMP=directory, TMP=directory, TMPDIR=directory)
        tempfile.tempdir = directory
        cache = str(self.bytecode)
        os.environ["PYTHONPYCACHEPREFIX"] = cache
        sys.pycache_prefix = cache
