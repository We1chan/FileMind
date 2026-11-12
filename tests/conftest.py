from pathlib import Path

import pytest

from filemind.config import AppPaths
from filemind.library import Library
from filemind.storage import LocalStore


@pytest.fixture
def library(tmp_path: Path) -> Library:
    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    store.initialize()
    return Library(paths, store)
