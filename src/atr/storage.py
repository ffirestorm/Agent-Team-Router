"""Journaled multi-file writes, drift detection and reversible history.

Atomic replace per file; a prepared journal makes interrupted batches recoverable.
The project must not be concurrently edited while a transaction is committing.
"""
import base64
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import uuid

from atr.core import ATRException


def safe_path(root: Path, relative: str) -> Path:
    rel = Path(relative)
    windows = PureWindowsPath(relative)
    # A Windows rooted or drive-relative path can escape root without is_absolute().
    # Reject these spellings on every OS so transaction records stay portable.
    if rel.anchor or windows.anchor or ".." in rel.parts or ".." in windows.parts or not rel.parts:
        raise ATRException(f"拒绝越界路径：{relative}")
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise ATRException(f"拒绝符号链接：{path}")
    # Windows junctions need not report is_symlink(); canonical containment still applies.
    if not path.resolve().is_relative_to(root.resolve()):
        raise ATRException(f"拒绝越界路径：{relative}")
    return path


def read(root: Path, relative: str) -> bytes | None:
    path = safe_path(root, relative)
    return path.read_bytes() if path.exists() else None


def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def atomic(path: Path, data: bytes | None):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temp = tempfile.mkstemp(prefix=".atr-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def encode(data: bytes | None):
    return base64.b64encode(data).decode() if data is not None else None


def decode(data):
    return base64.b64decode(data, validate=True) if data is not None else None


def parse_record(data: bytes | None, name: str) -> dict:
    try:
        result = json.loads(data) if data is not None else None
    except (ValueError, UnicodeError) as exc:
        raise ATRException(f"ATR 元数据损坏：{name}；请保留备份后检查文件") from exc
    if not isinstance(result, dict):
        raise ATRException(f"ATR 元数据缺失或格式无效：{name}")
    return result


def is_checksum(value, length=64):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def validate_state(root: Path, result: dict) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("managed"), dict) or not isinstance(result.get("history"), list):
        raise ATRException("ATR state.json 格式无效；请保留备份后检查状态文件")
    for name, checksum in result["managed"].items():
        safe_path(root, name)
        if not is_checksum(checksum):
            raise ATRException("ATR 托管文件校验和无效")
    if any(not is_checksum(txid, 32) for txid in result["history"]):
        raise ATRException("ATR 事务历史记录无效")
    return result


def state(root: Path) -> dict:
    data = read(root, ".atr/state.json")
    result = parse_record(data, ".atr/state.json") if data is not None else {"managed": {}, "history": []}
    return validate_state(root, result)


def transaction_snapshots(root: Path, record: dict) -> dict[str, bytes | None]:
    before, after = record.get("before"), record.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict) or before.keys() != after.keys():
        raise ATRException("ATR 事务快照格式无效或文件集合不一致")
    restored = {}
    for name, value in before.items():
        safe_path(root, name)
        if after[name] is not None and not is_checksum(after[name]):
            raise ATRException(f"ATR 事务校验和无效：{name}")
        if value is not None and not isinstance(value, str):
            raise ATRException(f"ATR 事务原始内容格式无效：{name}")
        try:
            restored[name] = decode(value)
        except (ValueError, TypeError) as exc:
            raise ATRException(f"ATR 事务原始内容损坏：{name}") from exc
    return restored


def check_drift(root: Path, current: dict):
    for name, expected in current["managed"].items():
        if digest(read(root, name)) != expected:
            raise ATRException(f"配置冲突：{name} 在 ATR 应用后被修改或删除；请保留人工修改并手动协调")


@contextmanager
def locked(root: Path):
    path = safe_path(root, ".atr/lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ATRException("另一个 ATR 事务正在运行；若已崩溃，请确认无进程后删除 .atr/lock，再运行 atr recover") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def diff(root: Path, changes: dict[str, bytes | None]) -> str:
    output = []
    for name, after in changes.items():
        before = read(root, name)
        if before == after:
            continue
        output.extend(difflib.unified_diff(
            (before or b"").decode().splitlines(keepends=True),
            (after or b"").decode().splitlines(keepends=True),
            fromfile=f"a/{name}", tofile=f"b/{name}"))
    return "".join(output)


def _batch(root: Path, changes: dict[str, bytes | None]):
    before = {name: read(root, name) for name in changes}
    journal = {"before": {n: encode(b) for n, b in before.items()},
               "after": {n: digest(b) for n, b in changes.items()}}
    journal_path = safe_path(root, ".atr/journal.json")
    atomic(journal_path, json.dumps(journal).encode())
    try:
        for name, data in changes.items():
            atomic(safe_path(root, name), data)
    except BaseException:
        # If restoration itself fails, keep journal for explicit recovery.
        for name, data in before.items():
            atomic(safe_path(root, name), data)
        journal_path.unlink()
        raise
    journal_path.unlink()


def require_clean(root: Path):
    if read(root, ".atr/journal.json") is not None:
        raise ATRException("发现未完成事务；先运行 atr recover")


def apply(root: Path, changes: dict[str, bytes | None], expected: dict[str, bytes | None]) -> str:
    with locked(root):
        require_clean(root)
        current = state(root)
        check_drift(root, current)
        for name, before in expected.items():
            if read(root, name) != before:
                raise ATRException(f"预览期间文件发生变更：{name}；请重试")
        changes = {n: b for n, b in changes.items() if read(root, n) != b}
        if not changes:
            return "配置已一致，无需修改（未创建重复备份）"
        txid = uuid.uuid4().hex
        backup = {"before": {n: encode(read(root, n)) for n in changes},
                  "after": {n: digest(b) for n, b in changes.items()}, "state": current}
        backup_name = f".atr/backups/{txid}.json"
        atomic(safe_path(root, backup_name), json.dumps(backup, indent=2).encode())
        updated = {"managed": dict(current["managed"]), "history": current["history"] + [txid]}
        for name, data in changes.items():
            if data is None:
                updated["managed"].pop(name, None)
            else:
                updated["managed"][name] = digest(data)
        changes[".atr/state.json"] = json.dumps(updated, indent=2).encode()
        _batch(root, changes)
        return f"已应用；备份：{backup_name}"


def rollback(root: Path) -> str:
    with locked(root):
        require_clean(root)
        current = state(root)
        check_drift(root, current)
        if not current["history"]:
            raise ATRException("没有可回滚的 ATR 应用记录")
        txid = current["history"][-1]
        if not isinstance(txid, str) or len(txid) != 32 or any(c not in "0123456789abcdef" for c in txid):
            raise ATRException("备份记录无效")
        backup_name = f".atr/backups/{txid}.json"
        backup = parse_record(read(root, backup_name), backup_name)
        changes = transaction_snapshots(root, backup)
        previous_state = validate_state(root, backup.get("state"))
        for name, expected in backup["after"].items():
            if digest(read(root, name)) != expected:
                raise ATRException(f"回滚冲突：{name}")
        changes[".atr/state.json"] = json.dumps(previous_state, indent=2).encode()
        _batch(root, changes)
        return f"已回滚事务 {txid}；保留备份以供核查"


def recover(root: Path) -> str:
    with locked(root):
        raw = read(root, ".atr/journal.json")
        if raw is None:
            return "无未完成事务"
        journal = parse_record(raw, ".atr/journal.json")
        restored = transaction_snapshots(root, journal)
        for name, before in restored.items():
            now = digest(read(root, name))
            if now not in (digest(before), journal["after"][name]):
                raise ATRException(f"恢复冲突：{name} 含事务之外的修改")
        for name, before in restored.items():
            atomic(safe_path(root, name), before)
        safe_path(root, ".atr/journal.json").unlink()
        return "已恢复未完成事务的全部原始文件"
