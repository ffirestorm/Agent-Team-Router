"""Codex exec adapter. Explicit native sandbox, JSONL evidence, no bypass flags."""
from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
from urllib.parse import urlsplit

from atr.core import ATRException
from atr.runtime.process import run_process


@dataclass
class Execution:
    status: str
    failure: str
    usage: dict
    seconds: float = 0
    detail: str = ""


def classify_error(text: str) -> str:
    text = text.lower()
    categories = {
        "auth": ("unauthorized", "authentication", "invalid_api_key", "not logged in", "401", "403", "model_not_found", "access denied"),
        "quota": ("insufficient_quota", "rate limit", "429", "usage limit", "quota exceeded"),
        "network": ("connection refused", "connection reset", "network", "dns", "502", "503", "504", "stream disconnected", "request timed out"),
        "environment": ("sandbox", "permission denied", "no such file", "not found in path", "model provider"),
    }
    found = [key for key, values in categories.items() if any(value in text for value in values)]
    return found[0] if len(found) == 1 else "unknown"


def parse_events(stdout: str, stderr: str, code: int, process_status: str = "completed") -> Execution:
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    completions, errors, failed_turn = 0, [], False
    usage_known = True
    malformed = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("not an event object")
        except ValueError:
            malformed = True
            continue
        if event.get("type") == "turn.completed":
            completions += 1
            usage = event.get("usage") or {}
            for key in totals:
                value = usage.get(key, 0 if key == "cached_input_tokens" else None)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    usage_known = False
                else:
                    totals[key] += value
        if event.get("type") in ("turn.failed", "error"):
            errors.append(json.dumps(event.get("error", event.get("message", ""))))
            failed_turn = failed_turn or event.get("type") == "turn.failed"
    usage = {**({key: None for key in totals} if not usage_known or not completions else totals),
             "cost": None, "currency": None, "cost_source": None}
    if process_status != "completed":
        return Execution(process_status, process_status, usage)
    if code == 0 and completions == 1 and not failed_turn and not malformed:
        return Execution("completed", "", usage)
    detail = "\n".join(errors) or stderr
    # Only terminal protocol errors / process stderr are evidence, not model prose.
    return Execution("failed", classify_error(detail), usage, detail=detail[-2000:])


class CodexExecutor:
    def __init__(self, executable: str | None = None):
        self.executable = executable or shutil.which("codex")

    def preflight(self):
        if not self.executable:
            raise ATRException("未检测到 Codex")
        with tempfile.TemporaryDirectory(prefix="atr-exec-check-") as temp:
            result = subprocess.run([self.executable, "exec", "--help"], cwd=temp,
                                    env={**os.environ, "CODEX_HOME": temp},
                                    capture_output=True, text=True, timeout=15)
        required = ("--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check")
        if result.returncode or any(flag not in result.stdout for flag in required):
            raise ATRException("本机 Codex exec 缺少所需隔离/事件选项；请升级 CLI")
        self.transport = transport_overrides()

    def execute(self, workspace: Path, model: str, effort: str, prompt: str,
                artifacts: Path, timeout: float, cancel: Path) -> Execution:
        argv = [self.executable, "--no-daemon", "--ask-for-approval", "never", "exec", "--json", "--ephemeral",
                "--ignore-user-config", "--skip-git-repo-check", "--color", "never",
                "--sandbox", "workspace-write", "--model", model,
                "-c", "model_reasoning_effort=" + json.dumps(effort),
                "-c", "agents.enabled=false", "-c", "features.multi_agent=false",
                "-c", "features.multi_agent_v2=false", "-c", "features.apps=false",
                "-c", "features.plugins=false", "-"]
        argv[-1:-1] = self.transport if hasattr(self, "transport") else transport_overrides()
        process = run_process(argv, workspace, artifacts / "events.jsonl", artifacts / "stderr.log",
                              timeout, cancel, input_text=prompt)
        result = parse_events((artifacts / "events.jsonl").read_text(encoding="utf-8", errors="replace") if (artifacts / "events.jsonl").exists() else "",
                              (artifacts / "stderr.log").read_text(encoding="utf-8", errors="replace") if (artifacts / "stderr.log").exists() else "",
                              process.code, process.status)
        result.seconds = process.seconds
        return result


def transport_overrides() -> list[str]:
    """Preserve an explicitly configured provider without loading unrelated hooks.

    Forward only transport settings and environment variable *names*, never headers,
    tokens or credentials. Authentication remains in the native Codex credential store.
    """
    path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    if not path.is_file():
        return []
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    name = doc.get("model_provider")
    provider = doc.get("model_providers", {}).get(name) if isinstance(name, str) else None
    if not isinstance(provider, dict):
        return []
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
        raise ATRException("提供者名称含当前 CLI dotted-key 转发不支持的字符")
    if set(provider) & {"http_headers", "env_http_headers", "experimental_bearer_token"}:
        raise ATRException("隔离执行暂不支持转发自定义认证 header；不会复制其中的凭证")
    base = provider.get("base_url", "")
    parts = urlsplit(base)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ATRException("提供者 URL 含认证信息或查询参数，拒绝转发到进程参数")
    allowed = {"name", "base_url", "wire_api", "requires_openai_auth", "supports_websockets", "env_key",
               "request_max_retries", "stream_max_retries", "stream_idle_timeout_ms"}
    arguments = ["-c", "model_provider=" + json.dumps(name)]
    for key in sorted(allowed & set(provider)):
        value = provider[key]
        if not isinstance(value, (str, int, bool)):
            raise ATRException(f"提供者传输字段格式无效：{key}")
        arguments += ["-c", f"model_providers.{name}.{key}=" + json.dumps(value)]
    return arguments
