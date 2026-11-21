"""FileMind Windows 桌面应用。"""

from functools import cache


@cache
def __getattr__(name: str) -> str:
    if name == "__version__":
        from importlib.metadata import version

        return version("filemind")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
