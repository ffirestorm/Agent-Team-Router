"""Prove native project-layer and Skill discovery without starting an inference turn.

Requires an installed ATR and Codex CLI. All configuration writes go to temporary
projects and CODEX_HOME directories. No user credentials are copied or changed.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import tomlkit


class MetadataClient:
    """Bounded, example-only stdio client. No thread/turn or write RPC is allowed."""

    METHODS = {"initialize", "config/read", "skills/list"}

    def __init__(self, executable: str, project: Path, home: Path):
        self.executable, self.project, self.home = executable, project, home
        self.messages = queue.Queue()
        self.sequence = 0
        self.methods = []

    def __enter__(self):
        self.process = subprocess.Popen(
            [self.executable, "app-server", "--listen", "stdio://"],
            cwd=self.project, env={**os.environ, "CODEX_HOME": str(self.home)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        return self

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self.messages.put(None)

    def _send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def initialize(self):
        self.request("initialize", {
            "clientInfo": {"name": "atr_native_probe", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True, "explicitGatewayOauth": True},
        })
        self._send({"method": "initialized"})

    def request(self, method: str, params: dict, timeout: float = 15):
        if method not in self.METHODS:
            raise ValueError(f"Metadata-only probe refuses RPC: {method}")
        self.sequence += 1
        self.methods.append(method)
        self._send({"id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Codex {method} timed out")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError(f"Codex {method} timed out") from exc
            if message is None:
                raise RuntimeError(f"Codex exited before answering {method}")
            if message.get("id") == self.sequence and "method" not in message:
                if "error" in message:
                    raise RuntimeError(f"Codex {method} failed: {message['error']}")
                return message["result"]
            if "id" in message and "method" in message:
                # Decline unexpected server actions instead of authorizing them.
                self._send({"id": message["id"], "error": {
                    "code": -32601, "message": "Metadata probe does not authorize server actions"}})

    def __exit__(self, *args):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()


def cli(project: Path, home: Path, *arguments):
    result = subprocess.run(
        [sys.executable, "-m", "atr", "--project", str(project), *arguments],
        env={**os.environ, "CODEX_HOME": str(home)}, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=45,
    )
    if result.returncode:
        raise RuntimeError(f"ATR {' '.join(arguments)} failed: {result.stderr}")
    return result.stdout


def probe() -> dict:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Install Codex CLI and add it to PATH before running this probe")
    with tempfile.TemporaryDirectory(prefix="atr-native-") as temp:
        base = Path(temp).resolve()
        project, home = base / "project", base / "home"
        project.mkdir()
        home.mkdir()
        (project / ".git").mkdir()  # repository boundary; no commit or remote
        version = json.loads(cli(project, home, "init", "--profile", "code-review"))["codex"]
        settings = project / ".atr/config.toml"
        with settings.open("a", encoding="utf-8") as stream:
            stream.write("\n[profiles.code-review]\nconcurrency = 1\n")
        cli(project, home, "apply")
        cli(project, home, "skill", "install")
        expected = {"atr_explorer", "atr_logic_tracer", "atr_reviewer"}

        with MetadataClient(executable, project, home) as client:
            client.initialize()
            untrusted = client.request("config/read", {"cwd": str(project), "includeLayers": True})
            if expected & set(untrusted["config"].get("agents") or {}):
                raise RuntimeError("Fresh untrusted project unexpectedly enabled ATR roles")
            disabled = any(layer.get("disabledReason") for layer in untrusted.get("layers") or []
                           if layer.get("name", {}).get("type") == "project")
            if not disabled:
                raise RuntimeError("Codex did not report an untrusted project-layer restriction")
            methods = list(client.methods)

        # Trust only this disposable project, in this disposable CODEX_HOME.
        (home / "config.toml").write_text(tomlkit.dumps({
            "projects": {str(project): {"trust_level": "trusted"}},
            "analytics": {"enabled": False},
        }), encoding="utf-8")
        with MetadataClient(executable, project, home) as client:
            client.initialize()
            trusted = client.request("config/read", {"cwd": str(project), "includeLayers": True})
            agents = trusted["config"].get("agents") or {}
            if not expected <= set(agents):
                raise RuntimeError("Trusted project did not load the ATR roles")
            for role in expected:
                expected_path = project / ".codex/agents" / f"{role}.toml"
                if Path(agents[role]["config_file"]).resolve() != expected_path:
                    raise RuntimeError(f"Codex resolved the wrong role config: {role}")
            concurrency = agents.get("max_concurrent_threads_per_session", agents.get("max_threads"))
            if concurrency != 1:
                raise RuntimeError("Codex did not load the concurrency override")
            skills = client.request("skills/list", {"cwds": [str(project)], "forceReload": True})
            skill_path = project / ".agents/skills/agent-team-router/SKILL.md"
            enabled = any(skill.get("name") == "agent-team-router" and skill.get("enabled")
                          and Path(skill["path"]).resolve() == skill_path
                          for row in skills.get("data", []) for skill in row.get("skills", []))
            if not enabled:
                raise RuntimeError("Codex did not discover the enabled project Skill")
            methods += client.methods
        return {"ok": True, "codex": version, "untrusted_project_layer_disabled": True,
                "trusted_roles_loaded": sorted(expected), "role_paths_resolved": True,
                "concurrency_override_loaded": concurrency, "project_skill_enabled": True,
                "rpc_methods": methods, "inference_requested": False,
                "limitations": "Metadata discovery only; no child Agent was spawned and no model entitlement was tested."}


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
