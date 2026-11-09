"""A real metadata/config demonstration, with no inference or global writes.

Run after installing ATR: python examples/demo.py
"""
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="atr-demo-") as temp:
        project = Path(temp).resolve()
        (project / "AGENTS.md").write_text("# Existing project instructions\nKeep this text.\n", encoding="utf-8")
        commands = [
            ["init"], ["profile", "use", "code-review"],
            ["plan", "分析项目中的 RAG 调用链"], ["apply", "--dry-run"],
            ["apply"], ["doctor"], ["apply"], ["skill", "install"],
            ["rollback"], ["rollback"],
        ]
        for command in commands:
            print("\n$ atr", " ".join(command), "--project", project, flush=True)
            subprocess.run([sys.executable, "-m", "atr", "--project", str(project), *command], check=True)
        assert (project / "AGENTS.md").read_text(encoding="utf-8") == "# Existing project instructions\nKeep this text.\n"
        assert not (project / ".codex/config.toml").exists()
        print("\nPASS: native validation, idempotence, Skill installation, and byte-preserving rollback; zero model calls.")


if __name__ == "__main__":
    main()
