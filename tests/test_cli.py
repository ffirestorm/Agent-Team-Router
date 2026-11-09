import json
import subprocess
import sys

from atr.cli import main
from atr.storage import read


def test_full_cli_lifecycle(project, fake_codex, capsys):
    prefix = ["--project", str(project)]
    assert main(prefix + ["init", "--profile", "code-review"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "code-review"
    assert main(prefix + ["profile", "list"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 5
    assert main(["plan", "分析 RAG 调用链", "--project", str(project)]) == 0
    assert json.loads(capsys.readouterr().out)["assignments"][0]["read_only"]
    assert main(prefix + ["apply", "--dry-run"]) == 0
    assert "Dry-run" in capsys.readouterr().out
    assert not (project / ".codex").exists()
    assert main(prefix + ["apply"]) == 0
    capsys.readouterr()
    before = read(project, ".codex/config.toml")
    assert main(prefix + ["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["model_invocation"] == "not performed"
    assert "atr_explorer" in report["existing_agents"]["project"]
    assert main(prefix + ["skill", "install"]) == 0
    skill = project / ".agents/skills/agent-team-router/SKILL.md"
    assert skill.exists()
    assert main(prefix + ["rollback"]) == 0
    assert not skill.exists()
    assert read(project, ".codex/config.toml") == before
    assert main(["profile", "use", "economical", "--project", str(project)]) == 0
    assert main(prefix + ["apply"]) == 0
    assert main(prefix + ["rollback"]) == 0
    assert read(project, ".codex/config.toml") == before
    assert all("exec" not in c for c in fake_codex["calls"])


def test_doctor_uninitialized_and_cli_errors(project, fake_codex, capsys):
    assert main(["doctor", "--project", str(project)]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert main(["plan", "task", "--project", str(project)]) == 2
    assert "尚未初始化" in capsys.readouterr().err
    assert main(["init", "--platform", "kimi", "--project", str(project)]) == 2
    assert "尚未支持" in capsys.readouterr().err


def test_python_module_entrypoint():
    result = subprocess.run([sys.executable, "-m", "atr", "--version"], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "0.1.0"
