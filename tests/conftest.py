import os
from pathlib import Path

import pytest

from filemind import library as library_module
from filemind.config import AppPaths
from filemind.library import Library
from filemind.storage import LocalStore


def pytest_configure() -> None:
    temporary = Path(__file__).resolve().parents[1] / "data" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(temporary))


@pytest.fixture(autouse=True)
def allow_local_test_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_module, "checkout_data_dir", lambda: None)


@pytest.fixture
def library(tmp_path: Path) -> Library:
    paths = AppPaths.resolve(tmp_path / "app")
    paths.prepare()
    store = LocalStore(paths.database)
    store.initialize()
    return Library(paths, store)
