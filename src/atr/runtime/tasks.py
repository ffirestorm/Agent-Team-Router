"""Immutable task contracts and isolated, uncommitted repository snapshots."""
import difflib
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys

from atr.core import ATRException
from atr.storage import safe_path

EXCLUDED = {".git", ".atr", ".venv", "node_modules", ".codex", ".agents", "dist", ".pytest_cache", "__pycache__"}
CACHES = {".pytest_cache", "__pycache__"}


def contract(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_contract(data)


def validate_contract(data: dict) -> dict:
    required = {"id", "description", "allowed_paths", "baseline", "verify"}
    if not isinstance(data, dict) or set(data) != required:
        raise ATRException(f"任务 JSON 必须且仅包含 {sorted(required)}")
    for field in ("id", "description"):
        if not isinstance(data[field], str) or not data[field].strip():
            raise ATRException(f"任务 {field} 必须是非空字符串")
    if not isinstance(data["allowed_paths"], list) or not data["allowed_paths"]:
        raise ATRException("allowed_paths 必须列出允许修改的文件或以 / 结尾的目录")
    for name in data["allowed_paths"]:
        if not isinstance(name, str) or not name or any(c in name for c in "\\\n\r\t*?["):
            raise ATRException("allowed_paths 使用相对 POSIX 路径，不支持通配符")
        rel = Path(name)
        if rel.anchor or PureWindowsPath(name).anchor or ".." in rel.parts or not rel.parts or rel.parts[0] in EXCLUDED:
            raise ATRException(f"任务修改范围无效：{name}")
    for field in ("baseline", "verify"):
        argv = data[field]
        if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
            raise ATRException(f"{field} 必须是非空 argv 数组，不通过 shell 执行")
    return data


def command(argv: list[str], task_dir: Path) -> list[str]:
    return [a.replace("{python}", sys.executable).replace("{task_dir}", str(task_dir)) for a in argv]


def git_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, capture_output=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != root.resolve():
        raise ATRException("执行功能目前要求目标目录是 Git 仓库根目录")
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=root, capture_output=True, check=True)
    return sorted(set(result.stdout.decode("utf-8").strip("\0").split("\0")) - {""})


def source_snapshot(root: Path) -> dict[str, bytes]:
    result = {}
    total = 0
    for name in git_files(root):
        if any(part in EXCLUDED for part in Path(name).parts):
            continue
        if any(c in name for c in "\r\n\t"):
            raise ATRException("执行快照暂不支持含换行或制表符的文件名")
        path = safe_path(root, name)
        if path.is_file():
            total += path.stat().st_size
            if total > 50 * 1024 * 1024:
                raise ATRException("执行快照超过 50 MiB；请缩小项目或忽略大型非代码文件")
            result[name] = path.read_bytes()
    return result


def workspace_snapshot(root: Path) -> dict[str, bytes]:
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = Path(parent) / name
            safe_path(root, path.relative_to(root).as_posix())
        dirs[:] = [name for name in dirs if name not in CACHES]
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            result[relative] = safe_path(root, relative).read_bytes()
    return result


def fingerprint(snapshot: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(snapshot.items()):
        digest.update(json.dumps([name, hashlib.sha256(content).hexdigest()]).encode())
    return digest.hexdigest()


def file_modes(root: Path, names) -> dict[str, int]:
    return {name: safe_path(root, name).stat().st_mode & 0o777 for name in names}


def changes(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def scope_violation(names: list[str], allowed: list[str]) -> list[str]:
    return [name for name in names if not any(name == rule or (rule.endswith("/") and name.startswith(rule)) for rule in allowed)]


def patch(before: dict[str, bytes], after: dict[str, bytes]) -> str:
    output = []
    for name in changes(before, after):
        left = before.get(name, b"").decode("utf-8")
        right = after.get(name, b"").decode("utf-8")
        for line in difflib.unified_diff(left.splitlines(keepends=True), right.splitlines(keepends=True),
                                         fromfile=f"a/{name}" if name in before else "/dev/null",
                                         tofile=f"b/{name}" if name in after else "/dev/null"):
            output.append(line)
            if not line.endswith("\n"):
                output.append("\n\\ No newline at end of file\n")
    return "".join(output)
