import json
import os
import subprocess

import pytest

from atr.core import ATRException
from atr import storage


def test_backup_and_rollback_restore_exact_original_bytes(project):
    path = project / "AGENTS.md"
    original = b"existing\r\nwith bytes\n"
    path.write_bytes(original)
    storage.apply(project, {"AGENTS.md": b"new", ".codex/config.toml": b"x"},
                  {"AGENTS.md": original, ".codex/config.toml": None})
    assert len(list((project / ".atr/backups").glob("*.json"))) == 1
    storage.rollback(project)
    assert path.read_bytes() == original
    assert not (project / ".codex/config.toml").exists()
    assert storage.state(project)["history"] == []


def test_multiple_rollback(project):
    for before, after in [(None, b"one"), (b"one", b"two")]:
        storage.apply(project, {"file": after}, {"file": before})
    storage.rollback(project)
    assert (project / "file").read_bytes() == b"one"
    storage.rollback(project)
    assert not (project / "file").exists()


@pytest.mark.parametrize("action", [storage.rollback, lambda root: storage.apply(root, {"file": b"x"}, {"file": b"edited"})])
def test_user_edits_never_overwritten(project, action):
    storage.apply(project, {"file": b"managed"}, {"file": None})
    (project / "file").write_bytes(b"edited")
    with pytest.raises(ATRException, match="冲突"):
        action(project)
    assert (project / "file").read_bytes() == b"edited"


def test_preview_race_detected(project):
    (project / "file").write_bytes(b"concurrent edit")
    with pytest.raises(ATRException, match="预览期间"):
        storage.apply(project, {"file": b"new"}, {"file": None})


def test_mid_write_failure_restores_batch(project, monkeypatch):
    (project / "one").write_bytes(b"original")
    actual = storage.atomic
    def fail(path, data):
        if path.name == "two" and data == b"new":
            raise OSError("simulated disk failure")
        actual(path, data)
    monkeypatch.setattr(storage, "atomic", fail)
    with pytest.raises(OSError):
        storage.apply(project, {"one": b"new", "two": b"new"}, {"one": b"original", "two": None})
    assert (project / "one").read_bytes() == b"original"
    assert not (project / "two").exists()
    assert not (project / ".atr/journal.json").exists()
    assert not (project / ".atr/state.json").exists()


def test_interrupted_transaction_recovery_and_drift_guard(project):
    (project / ".atr").mkdir()
    journal = {"before": {"one": storage.encode(b"original"), "two": None},
               "after": {"one": storage.digest(b"new"), "two": storage.digest(b"new")}}
    (project / ".atr/journal.json").write_text(json.dumps(journal))
    (project / "one").write_bytes(b"external change")
    with pytest.raises(ATRException, match="恢复冲突"):
        storage.recover(project)
    (project / "one").write_bytes(b"new")
    storage.recover(project)
    assert (project / "one").read_bytes() == b"original"
    assert not (project / "two").exists()
    assert storage.recover(project) == "无未完成事务"


def test_lock_prevents_second_writer(project):
    with storage.locked(project):
        with pytest.raises(ATRException, match="另一个 ATR"):
            storage.apply(project, {"file": b"new"}, {"file": None})


def test_no_rollback_without_history(project):
    with pytest.raises(ATRException, match="没有可回滚"):
        storage.rollback(project)


@pytest.mark.parametrize("name", ["../escape", "/absolute", ".atr/../../escape", r"D:escape", r"\escape", r"C:\escape", r"\\server\share\file", r".atr\..\..\escape"])
def test_path_traversal_rejected(project, name):
    with pytest.raises(ATRException, match="越界"):
        storage.safe_path(project, name)


def test_symlink_rejected(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    try:
        (project / ".codex").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ATRException, match="符号链接"):
        storage.read(project, ".codex/config.toml")
    assert not list(outside.iterdir())


def test_explicit_project_alias_is_canonicalized(project, tmp_path_factory):
    from atr.config import project_root
    alias = tmp_path_factory.mktemp("alias") / "repo"
    try:
        alias.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert project_root(str(alias)) == project.resolve()


def test_empty_journal_blocks_new_transaction(project):
    (project / ".atr").mkdir()
    (project / ".atr/journal.json").write_bytes(b"")
    with pytest.raises(ATRException, match="未完成事务"):
        storage.apply(project, {"file": b"new"}, {"file": None})
    assert not (project / "file").exists()
    with pytest.raises(ATRException, match="元数据损坏"):
        storage.recover(project)


@pytest.mark.parametrize("corrupt", [
    {"before": {"one": storage.encode(b"old")}, "after": {}},
    {"before": {"one": "not-base64!"}, "after": {"one": storage.digest(b"new")}},
    {"before": {"one": None}, "after": {"one": "not-a-checksum"}},
])
def test_corrupt_journal_cannot_partially_restore(project, corrupt):
    (project / ".atr").mkdir()
    (project / ".atr/journal.json").write_text(json.dumps(corrupt), encoding="utf-8")
    (project / "one").write_bytes(b"new")
    with pytest.raises(ATRException):
        storage.recover(project)
    assert (project / "one").read_bytes() == b"new"
    assert (project / ".atr/journal.json").exists()


def test_corrupt_backup_state_cannot_damage_current_state(project):
    storage.apply(project, {"one": b"new"}, {"one": None})
    before = storage.read(project, ".atr/state.json")
    backup = next((project / ".atr/backups").glob("*.json"))
    record = json.loads(backup.read_bytes())
    record["state"] = []
    backup.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ATRException):
        storage.rollback(project)
    assert (project / "one").read_bytes() == b"new"
    assert storage.read(project, ".atr/state.json") == before


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction behavior")
def test_windows_junction_cannot_escape_project(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("junction-outside")
    junction = project / ".codex"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with pytest.raises(ATRException, match="越界|符号链接"):
            storage.read(project, ".codex/config.toml")
        assert not list(outside.iterdir())
    finally:
        junction.rmdir()
