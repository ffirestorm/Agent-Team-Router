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
        if version.startswith(("0.2.", "0.3.", "0.4.", "0.5.")):
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
        native_collector = None
        if version.startswith(("0.3.", "0.4.", "0.5.")):
            native = json.loads(run("-m", "atr", "--project", str(project), "native", "status"))
            if native["installed"] or native["recording"] != ("off" if version.startswith(("0.4.", "0.5.")) else "not_confirmed"):
                raise RuntimeError("Fresh wheel incorrectly claims native recording")
            standalone = project / "native_collector.py"
            run("-c", "from importlib.resources import files; from pathlib import Path; "
                "Path(" + repr(str(standalone)) + ").write_bytes(files('atr.native').joinpath('collector.py').read_bytes())")
            if version.startswith(("0.4.", "0.5.")):
                disabled = subprocess.run([str(python), "-I", str(standalone), "--project", str(project)],
                                          input=b'invalid input', cwd=base, env=env,
                                          check=True, capture_output=True, timeout=15)
                if disabled.stdout.strip() != b'{}' or disabled.stderr or (project / '.atr/native/events.sqlite3').exists():
                    raise RuntimeError("Default-off collector wrote events or errors")
                run("-c", "from pathlib import Path; from atr.native.collector import set_mode; set_mode(Path(" + repr(str(project)) + "), 'summary')")
            payload = {"cwd": str(project), "session_id": "distribution-fixture", "turn_id": "turn", "hook_event_name": "SessionStart"}
            result = subprocess.run([str(python), "-I", str(standalone), "--project", str(project)],
                                    input=json.dumps(payload).encode(), cwd=base, env=env,
                                    check=True, capture_output=True, timeout=15)
            if not json.loads(result.stdout).get("hookSpecificOutput") or result.stderr:
                raise RuntimeError("Packaged standalone collector failed protocol fixture")
            observed = json.loads(run("-m", "atr", "--project", str(project), "native", "report",
                                      "--session", "distribution-fixture", "--format", "json"))
            if observed["event_count"] != 1 or observed["agents"]:
                raise RuntimeError("Packaged collector fabricated a model invocation")
            if version.startswith(("0.4.", "0.5.")):
                run("-m", "atr", "--project", str(project), "native", "off")
                cleanup = json.loads(run("-m", "atr", "--project", str(project), "native", "cleanup", "--keep-sessions", "0"))
                if not cleanup["dry_run"] or not cleanup["protected_sessions"]:
                    raise RuntimeError("Retention preview failed to protect recent fixture")
                draft = json.loads(run("-m", "atr", "--project", str(project), "task", "draft", "Add a feature"))
                if draft["ready"] or not draft["missing"]:
                    raise RuntimeError("Task draft fabricated independent acceptance")
            if version.startswith("0.5."):
                delegation = json.loads(run("-m", "atr", "--project", str(project), "delegate", "fix typo"))
                if delegation["action"] != "main" or delegation["model"] is not None:
                    raise RuntimeError("Delegation preview fabricated an unapplied native worker")
            native_collector = True
        return {"ok": True, "wheel": wheel.name, "version": version,
                "isolated_import": True, "profiles": len(profiles),
                "console_entrypoint": True,
                "offline_runtime_preview": runtime_preview,
                "offline_native_collector": native_collector,
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
