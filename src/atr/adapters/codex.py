"""Codex-specific discovery, strict native validation and comment-preserving rendering."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import tomlkit

from atr.core import ATRException, Plan

BEGIN = "<!-- ATR:BEGIN -->"
END = "<!-- ATR:END -->"
# Adapter defaults are suggestions, not capabilities or pricing claims.
MODEL_HINTS = {
    "fast": "gpt-6-luna", "balanced": "gpt-6.1-terra",
    "reasoning": "gpt-6.1-sol", "expert": "gpt-6-astra",
}


def parse_toml(text: str):
    try:
        return tomlkit.parse(text)
    except Exception as exc:
        raise ATRException(f"TOML 无效：{exc}") from exc


def managed_block(text: str, content: str) -> str:
    if BEGIN in content or END in content:
        raise ATRException("角色指令不能包含 ATR 托管边界标记")
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise ATRException("AGENTS.md 的 ATR 区块边界损坏或重复")
    block = BEGIN + "\n" + content.rstrip() + "\n" + END
    if BEGIN in text:
        start, end = text.index(BEGIN), text.index(END)
        if end < start:
            raise ATRException("AGENTS.md 的 ATR 区块顺序无效")
        return text[:start] + block + text[end + len(END):]
    separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return text + separator + block + "\n"


class CodexAdapter:
    def __init__(self, executable: str | None = None):
        self.executable = executable or shutil.which("codex")

    def _run(self, arguments: list[str], home: Path, timeout=20):
        if not self.executable:
            raise ATRException("未检测到 Codex；请先安装 Codex CLI 并加入 PATH")
        try:
            return subprocess.run([self.executable, *arguments], cwd=home,
                                  env={**os.environ, "CODEX_HOME": str(home)},
                                  text=True, encoding="utf-8", errors="replace",
                                  capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ATRException(f"Codex 本地检查失败：{exc}") from exc

    def version(self) -> str:
        with tempfile.TemporaryDirectory(prefix="atr-discovery-") as temp:
            result = self._run(["--version"], Path(temp))
            if result.returncode:
                raise ATRException("无法获取 Codex 版本")
            return result.stdout.strip()

    def catalog(self) -> tuple[list[dict], str]:
        # Bundled catalog only. Never refresh credentials or issue inference requests.
        with tempfile.TemporaryDirectory(prefix="atr-models-") as temp:
            result = self._run(["debug", "models", "--bundled"], Path(temp))
            if result.returncode:
                raise ATRException("此 Codex 无 bundled 模型目录；使用 --catalog 提供显式模型目录 JSON")
            try:
                models = json.loads(result.stdout)["models"]
                return normalize_catalog(models), "codex-bundled (account access unverified)"
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ATRException("Codex 模型目录格式不兼容；使用 --catalog") from exc

    def validate(self, config: bytes, roles: dict[str, bytes]) -> None:
        with tempfile.TemporaryDirectory(prefix="atr-validate-") as temp:
            home = Path(temp)
            for name, data in roles.items():
                path = home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                # Role layers also need independent field validation: Codex can load them lazily.
                (home / "config.toml").write_bytes(data)
                self._strict(home)
            (home / "config.toml").write_bytes(config)
            self._strict(home)

    def _strict(self, home: Path):
        result = self._run(["--strict-config", "app-server", "--print-startup-config"], home)
        if result.returncode:
            raise ATRException("本机 Codex 严格配置验证失败（可能版本或字段不兼容）：" +
                               (result.stdout + result.stderr)[:1400])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ATRException("Codex 严格配置检查未返回可识别 JSON") from exc
        if "error" in payload or "configRequirements" not in payload:
            raise ATRException("Codex 严格配置检查返回错误或不兼容响应")

    def concurrency_field(self) -> str:
        # Probe actual binary rather than guessing support from a version number.
        for field in ("max_concurrent_threads_per_session", "max_threads"):
            doc = tomlkit.dumps({"agents": {field: 2}}).encode()
            try:
                self.validate(doc, {})
                return field
            except ATRException:
                pass
        raise ATRException("此 Codex 不支持 ATR 所需的本地严格配置检查或并发字段；请升级 CLI")

    def render(self, plan: Plan, current: dict[str, bytes | None],
               owned: set[str], concurrency_field: str) -> dict[str, bytes | None]:
        config_name = ".codex/config.toml"
        doc = parse_toml((current.get(config_name) or b"").decode())
        if "agents" not in doc:
            doc["agents"] = tomlkit.table()
        agents = doc["agents"]
        if not isinstance(agents, dict):
            raise ATRException("原生 agents 字段必须为 TOML 表")
        # max_threads is an older alias; do not emit both aliases.
        other = "max_threads" if concurrency_field != "max_threads" else "max_concurrent_threads_per_session"
        for field in (concurrency_field, other):
            if field in agents and config_name not in owned and agents[field] != plan.concurrency:
                raise ATRException(f"已有 agents.{field} 与 Profile 并发数冲突")
        if other in agents:
            del agents[other]
        agents[concurrency_field] = plan.concurrency
        if "features" not in doc:
            doc["features"] = tomlkit.table()
        if not isinstance(doc["features"], dict):
            raise ATRException("原生 features 字段必须为 TOML 表")
        if doc["features"].get("multi_agent") is False and config_name not in owned:
            raise ATRException("已有 features.multi_agent=false；请先明确协调该设置")
        doc["features"]["multi_agent"] = True
        if agents.get("enabled") is False:
            raise ATRException("已有 agents.enabled=false；请先明确协调该设置")
        changes: dict[str, bytes | None] = {}
        desired_names = {"atr_" + item.role for item in plan.assignments}
        for key in list(agents):
            if key.startswith("atr_") and key not in desired_names:
                path = f".codex/agents/{key}.toml"
                if path in owned:
                    del agents[key]
                    changes[path] = None
        lines = ["## Agent Team Router", "", "Static team configuration; ATR does not execute tasks or intercept model calls.",
                 f"Profile: {plan.profile}. Maximum concurrent subagents: {plan.concurrency}.",
                 "Only delegate when useful. Honor task scope, sandbox restrictions and host instructions."]
        if plan.profile == "code-review":
            lines.append("This is a READ-ONLY team. Do not modify project files, including from the main agent.")
        for item in plan.assignments:
            key = "atr_" + item.role
            path = f".codex/agents/{key}.toml"
            if key in agents and path not in owned:
                raise ATRException(f"已有同名 Agent {key}；ATR 不会覆盖用户角色")
            if current.get(path) is not None and path not in owned:
                raise ATRException(f"已有未托管文件 {path}")
            agents[key] = {"description": item.instructions, "config_file": f"agents/{key}.toml"}
            layer = {"model": item.model, "model_reasoning_effort": item.effort,
                     "sandbox_mode": "read-only" if item.read_only else "workspace-write",
                     "developer_instructions": item.instructions}
            changes[path] = tomlkit.dumps(layer).encode()
            lines.append(f"- `{key}`: `{item.model}` / `{item.effort}`; {'read-only' if item.read_only else 'workspace-write'}. {item.instructions}")
        changes[config_name] = tomlkit.dumps(doc).encode()
        changes["AGENTS.md"] = managed_block((current.get("AGENTS.md") or b"").decode(), "\n".join(lines)).encode()
        return changes


def normalize_catalog(models: list[dict]) -> list[dict]:
    if not isinstance(models, list):
        raise ATRException("模型目录 models 必须是数组")
    result = []
    for model in models:
        if not isinstance(model, dict):
            raise ATRException("模型目录条目必须是对象")
        ident = model.get("slug", model.get("id"))
        raw = model.get("supported_reasoning_levels", model.get("efforts", []))
        if not isinstance(raw, list):
            raise ATRException("模型目录 efforts 必须是数组")
        efforts = [e.get("effort") if isinstance(e, dict) else e for e in raw]
        if not isinstance(ident, str) or not ident.strip() or not efforts or any(not isinstance(e, str) or not e for e in efforts):
            raise ATRException("模型目录必须包含非空 slug/id 和 supported_reasoning_levels/efforts")
        result.append({"id": ident, "efforts": efforts})
    if len({m["id"] for m in result}) != len(result):
        raise ATRException("模型目录含重复模型 ID")
    return result
