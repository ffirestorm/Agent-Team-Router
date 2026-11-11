import json
from pathlib import Path
import subprocess
import tomllib

import pytest

from atr.adapters.codex import CodexAdapter


@pytest.fixture
def project(tmp_path):
    return tmp_path


@pytest.fixture
def fake_codex(monkeypatch, tmp_path):
    """Emulate only local metadata/config commands, never a model execution."""
    calls = []
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "isolated-global"))
    catalog = {"models": [
        {"slug": "gpt-6-luna", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]},
        {"slug": "gpt-6.1-terra", "efforts": ["low", "medium", "high"]},
        {"slug": "gpt-6.1-sol", "efforts": ["low", "medium", "high"]},
        {"slug": "gpt-6-astra", "efforts": ["low", "medium", "high"]},
    ]}
    def run(self, arguments, home: Path, timeout=20):
        calls.append(arguments)
        if arguments == ["--version"]:
            return subprocess.CompletedProcess(arguments, 0, "codex-cli test-version", "")
        if arguments == ["debug", "models", "--bundled"]:
            return subprocess.CompletedProcess(arguments, 0, json.dumps(catalog), "")
        assert arguments == ["--strict-config", "app-server", "--print-startup-config"]
        config = tomllib.loads((home / "config.toml").read_text())
        valid = {"model", "model_reasoning_effort", "sandbox_mode", "developer_instructions", "features", "agents", "approval_policy", "hooks"}
        hooks = config.get("hooks", {})
        if any(not isinstance(groups, list) for groups in hooks.values()):
            return subprocess.CompletedProcess(arguments, 1, "hook groups must be arrays", "")
        if set(config) - valid:
            return subprocess.CompletedProcess(arguments, 1, "unknown field", "")
        return subprocess.CompletedProcess(arguments, 0, '{"configRequirements":{},"appServerVersion":"test"}', "")
    monkeypatch.setattr(CodexAdapter, "_run", run)
    return {"calls": calls, "catalog": catalog}
