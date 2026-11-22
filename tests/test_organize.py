import sys
from pathlib import Path

import pytest

from filemind.library import FileAccessError, Library
from filemind.storage import LocalStore


def test_tags_and_exact_duplicates_use_current_index(library: Library, tmp_path: Path) -> None:
    one = tmp_path / "一.doc.txt"
    other = tmp_path / "关联目录"
    other.mkdir()
    two = other / "二.txt"
    one.write_text("完全相同", encoding="utf-8")
    two.write_bytes(one.read_bytes())
    library.import_files([one])
    library.link_directory(other)
    organizer = library.organizer
    snapshot = organizer.snapshot(None, lambda: False)
    managed = snapshot.files[0]
    assert len(snapshot.duplicates) == 1
    assert {file.name for file in snapshot.duplicates[0]} == {one.name, two.name}
    linked = next(file for file in snapshot.duplicates[0] if file.source_id is not None)
    with pytest.raises(FileAccessError, match="受管副本"):
        organizer.edit_tags(linked.id, "专题")
    with pytest.raises(FileAccessError, match="没有可整理项"):
        organizer.preview([linked.id])
    organizer.edit_tags(managed.id, " 作业，PDF, pdf ")
    assert library.store.tags() == {managed.id: ("作业", "PDF")}
    two.write_text("更新后的文档", encoding="utf-8")
    assert not organizer.snapshot(None, lambda: False).duplicates


@pytest.mark.skipif(sys.platform != "win32", reason="Windows managed moves")
def test_move_plan_preserves_id_index_and_existing_files(library: Library, tmp_path: Path) -> None:
    one = tmp_path / "讲义.txt"
    one.write_text("编译原理 FIRST/FOLLOW 讲义", encoding="utf-8")
    library.import_files([one, one])
    organizer = library.organizer
    files = organizer.snapshot(None, lambda: False).files
    organizer.edit_tags(files[0].id, "课程")
    plan = organizer.preview(file.id for file in files)
    assert len({item.destination for item in plan.items}) == 2
    workspace = library.paths.workspace
    assert all((workspace / item.origin).is_file() for item in plan.items)
    assert not (workspace / "整理").exists()
    with pytest.raises(FileAccessError, match="计划已变更"):
        organizer.run(plan.id, "wrong digest", lambda: False)

    result = organizer.run(plan.id, plan.digest, lambda: False)
    assert [item.state for item in result.items] == ["moved", "moved"], result
    for item in result.items:
        moved = library.store.file(item.file_id)
        assert (moved.relative_path, moved.version) == (item.destination, item.version)
        assert library.metadata_current(moved)
        assert library.preview_file(item.file_id).text == one.read_text(encoding="utf-8")
    assert one.is_file()
    assert library.store.tags() == {files[0].id: ("课程",)}

    store = LocalStore(library.paths.database)
    store.initialize()
    reopened = Library(library.paths, store).organizer
    assert reopened.snapshot(None, lambda: False).plan == result
    original = workspace / plan.items[0].origin
    original.write_text("不得覆盖", encoding="utf-8")
    undo = reopened.run(plan.id, plan.digest, lambda: False, undo=True)
    assert [item.state for item in undo.items] == ["undo_failed", "undone"]
    assert original.read_text(encoding="utf-8") == "不得覆盖"
    assert (workspace / plan.items[0].destination).is_file()
    original.unlink()
    undo = reopened.run(plan.id, plan.digest, lambda: False, undo=True)
    assert [item.state for item in undo.items] == ["undone", "undone"]
    assert original.read_bytes() == one.read_bytes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows managed moves")
def test_plan_records_changed_files_and_target_conflicts(library: Library, tmp_path: Path) -> None:
    source = tmp_path / "课程大纲.txt"
    source.write_text("课程大纲", encoding="utf-8")
    library.import_files([source, source, source])
    organizer = library.organizer
    plan = organizer.preview(file.id for file in library.store.source_files(None).values())
    workspace = library.paths.workspace
    first, second, third = plan.items
    (workspace / first.origin).write_text("最新课程大纲", encoding="utf-8")
    collision = workspace / second.destination
    collision.parent.mkdir(parents=True)
    collision.write_text("其他内容", encoding="utf-8")
    result = organizer.run(plan.id, plan.digest, lambda: False)
    assert [item.state for item in result.items] == ["failed", "failed", "moved"], result
    assert "已变更" in result.items[0].reason
    assert "已被占用" in result.items[1].reason
    assert collision.read_text(encoding="utf-8") == "其他内容"
    assert (workspace / third.destination).read_bytes() == source.read_bytes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows managed moves")
@pytest.mark.parametrize("undo", [False, True], ids=["apply", "undo"])
def test_recover_interruption_between_rename_and_database_commit(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, undo: bool
) -> None:
    source = tmp_path / "待整理.txt"
    source.write_text("原文", encoding="utf-8")
    library.import_files([source])
    organizer = library.organizer
    file = organizer.snapshot(None, lambda: False).files[0]
    plan = organizer.preview([file.id])
    if undo:
        assert organizer.run(plan.id, plan.digest, lambda: False).items[0].state == "moved"
    record = library.store.record_move

    def interrupt(*args, **kwargs):
        if kwargs.get("new_path") is not None:
            raise RuntimeError("simulated interruption")
        return record(*args, **kwargs)

    monkeypatch.setattr(library.store, "record_move", interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        organizer.run(plan.id, plan.digest, lambda: False, undo=undo)

    reopened_store = LocalStore(library.paths.database)
    reopened_store.initialize()
    reopened = Library(library.paths, reopened_store)
    assert reopened_store.move_plan(plan.id).items[0].state == ("undoing" if undo else "moving")
    reopened.refresh_managed(lambda: False)
    item = reopened_store.move_plan(plan.id).items[0]
    assert item.state == ("undone" if undo else "moved")
    assert reopened_store.file(file.id).relative_path == (item.origin if undo else item.destination)
    assert reopened.preview_file(file.id).text == "原文"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows managed moves")
def test_plan_rejects_linked_target_directory(library: Library, tmp_path: Path) -> None:
    import _winapi

    source = tmp_path / "受管副本.txt"
    source.write_text("原文", encoding="utf-8")
    library.import_files([source])
    organizer = library.organizer
    file = organizer.snapshot(None, lambda: False).files[0]
    plan = organizer.preview([file.id])
    target = library.paths.workspace / plan.items[0].destination
    outside = tmp_path / "资料库外"
    outside.mkdir()
    target.parent.parent.mkdir()
    _winapi.CreateJunction(str(outside), str(target.parent))
    result = organizer.run(plan.id, plan.digest, lambda: False)
    assert result.items[0].state == "failed"
    assert "链接" in result.items[0].reason
    assert not list(outside.iterdir())
    assert library.path_for(file).read_bytes() == source.read_bytes()
