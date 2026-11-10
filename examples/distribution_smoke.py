"""Install a wheel in a fresh venv, outside the checkout, on any supported OS."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def smoke(wheel: Path, wheelhouse: Path | None = None):
    wheel = wheel.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="atr-dist-") as temp:
        base = Path(temp).resolve()
        environment, project = base / "venv", base / "project"
        project.mkdir()
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["PYTHONUTF8"] = "1"
        def run(*args):
            return subprocess.run([str(python), *args], cwd=base, env=env,
                                  check=True, capture_output=True, text=True, encoding="utf-8", timeout=60).stdout
        flags = ["--no-index", "--find-links", str(wheelhouse.resolve())] if wheelhouse else []
        run("-m", "pip", "--isolated", "install", *flags, str(wheel))
        origin = run("-c", "import atr; print(atr.__file__)").strip()
        if not Path(origin).resolve().is_relative_to(environment):
            raise RuntimeError("Smoke test imported ATR from outside the fresh environment")
        version = run("-m", "atr", "--version").strip()
        executable = environment / ("Scripts/atr.exe" if os.name == "nt" else "bin/atr")
        console_version = subprocess.run([str(executable), "--version"], cwd=base, env=env,
                                         check=True, capture_output=True, text=True, timeout=15).stdout.strip()
        if version != console_version:
            raise RuntimeError("Installed console entry point and module versions differ")
        profiles = json.loads(run("-m", "atr", "profile", "list"))
        if len(profiles) != 5:
            raise RuntimeError("Installed wheel does not expose the five profiles")
        run("-m", "atr", "--project", str(project), "skill", "install")
        skill = project / ".agents/skills/agent-team-router/SKILL.md"
        if not skill.is_file() or not skill.read_text(encoding="utf-8").startswith("---\n"):
            raise RuntimeError("Installed wheel did not install a packaged Skill")
        run("-m", "atr", "--project", str(project), "rollback")
        if skill.exists():
            raise RuntimeError("Skill rollback failed")
        runtime_preview = None
        if version.startswith("0.2."):
            (project / ".atr/config.toml").write_text('''version=1
platform="codex"
profile="development"
[models]
balanced="fixture-local"
[catalog.fixture-local]
efforts=["medium"]
''', encoding="utf-8")
            task = project / "task.json"
            task.write_text(json.dumps({"id": "offline-preview", "description": "Add a feature",
                "allowed_paths": ["feature.py"], "baseline": ["{python}", "-c", "pass"],
                "verify": ["{python}", "-c", "pass"]}), encoding="utf-8")
            preview = json.loads(run("-m", "atr", "--project", str(project), "run", str(task), "--dry-run"))
            if not preview["dry_run"] or preview["initial"]["model"] != "fixture-local":
                raise RuntimeError("Installed runtime preview did not load its packaged policy")
            if json.loads(run("-m", "atr", "--project", str(project), "runs", "list")):
                raise RuntimeError("Dry-run unexpectedly created an execution record")
            runtime_preview = True
        return {"ok": True, "wheel": wheel.name, "version": version,
                "isolated_import": True, "profiles": len(profiles),
                "console_entrypoint": True,
                "offline_runtime_preview": runtime_preview,
                "packaged_skill_installed_and_rolled_back": True, "codex_required": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="default: most recently built ATR wheel in dist/")
    parser.add_argument("--wheelhouse", type=Path, help="optional offline directory containing dependency wheels")
    args = parser.parse_args()
    if args.wheel is None:
        wheels = list(Path("dist").glob("agent_team_router-*.whl"))
        if not wheels:
            parser.error("Build a wheel first with python -m build")
        args.wheel = max(wheels, key=lambda path: path.stat().st_mtime)
    print(json.dumps(smoke(args.wheel, args.wheelhouse), indent=2))
