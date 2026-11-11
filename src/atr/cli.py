import argparse
from importlib.resources import files
import json
import sys
import sqlite3

from atr import __version__
from atr.config import initialize, load, project_root, use_profile
from atr.core import ATRException, plan_team
from atr.profiles import PROFILES
from atr.service import doctor, prepare
from atr.storage import apply, diff, read, recover, rollback, state


def parser():
    root = argparse.ArgumentParser(prog="atr", description="Safe Codex configuration, explainable routing and bounded single-task execution.")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--project", default=".", help="target project (default: current directory)")
    commands = root.add_subparsers(dest="command", required=True)
    def command(name, **kwargs):
        p = commands.add_parser(name, **kwargs)
        p.add_argument("--project", default=argparse.SUPPRESS)
        return p
    init = command("init", help="detect Codex and create project-local ATR configuration")
    init.add_argument("--platform", default="codex")
    init.add_argument("--profile", choices=PROFILES, default="balanced")
    init.add_argument("--models", help="comma-separated subset of models known to be available to you")
    init.add_argument("--catalog", help="explicit model catalog JSON when bundled discovery is unavailable")
    init.add_argument("--model-map", action="append", help="explicit tier=model-ID mapping (repeatable)")
    profile = command("profile")
    sub = profile.add_subparsers(dest="action", required=True)
    sub.add_parser("list").add_argument("--project", default=argparse.SUPPRESS)
    use = sub.add_parser("use")
    use.add_argument("name", choices=PROFILES)
    use.add_argument("--project", default=argparse.SUPPRESS)
    plan = command("plan", help="explain task allocation without executing it")
    plan.add_argument("task")
    plan.add_argument("--complexity", choices=["simple", "medium", "complex"])
    app = command("apply", help="validate, diff and safely apply native configuration")
    app.add_argument("--dry-run", action="store_true")
    command("doctor")
    command("rollback")
    command("recover", help="restore a prepared transaction after interruption")
    skill = command("skill")
    skill.add_argument("action", choices=["install"])
    execution = command("run", help="preview or explicitly execute a task contract in an isolated snapshot")
    execution.add_argument("task_file")
    mode = execution.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    execution.add_argument("--model")
    execution.add_argument("--effort")
    execution.add_argument("--complexity", choices=["simple", "medium", "complex"])
    execution.add_argument("--allow-escalation", action="store_true")
    runs = command("runs", help="inspect, cancel or recover local execution records")
    actions = runs.add_subparsers(dest="action", required=True)
    for action in ("list", "report", "show", "cancel", "recover"):
        entry = actions.add_parser(action)
        entry.add_argument("--project", default=argparse.SUPPRESS)
        if action in ("show", "cancel", "recover"):
            entry.add_argument("id")
    native = command("native", help="install passive native Codex hooks and inspect their factual records")
    actions = native.add_subparsers(dest="action", required=True)
    for action in ("install", "uninstall", "status", "sessions", "report", "note"):
        entry = actions.add_parser(action)
        entry.add_argument("--project", default=argparse.SUPPRESS)
        if action in ("install", "uninstall"):
            entry.add_argument("--dry-run", action="store_true")
        if action in ("report", "note"):
            entry.add_argument("--session", required=True)
            entry.add_argument("--turn")
        if action == "report":
            entry.add_argument("--format", choices=["json", "markdown"], default="markdown")
        if action == "note":
            entry.add_argument("--agent-id", required=True)
            entry.add_argument("--role", required=True)
            entry.add_argument("--task", required=True)
            entry.add_argument("--model")
    return root


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def run(args):
    if args.command == "profile" and args.action == "list":
        emit([{"name": p.name, "description": p.description, "concurrency": p.concurrency,
               "roles": [r.name for r in p.roles]} for p in PROFILES.values()])
        return 0
    root = project_root(args.project)
    if args.command == "init":
        emit(initialize(root, args.profile, args.platform, args.models, args.catalog, args.model_map))
    elif args.command == "profile":
        emit(use_profile(root, args.name))
    elif args.command == "plan":
        if not args.task.strip():
            raise ATRException("任务描述不能为空")
        from atr.task_policy import select_initial
        doc, profile, rules, models = load(root)
        override = {"simple": "low", "medium": "normal", "complex": "high"}.get(args.complexity)
        result = plan_team(args.task, profile, models, rules, complexity_override=override).to_dict()
        result["initial_execution"] = select_initial(args.task, models, doc.get("routing", {}), args.complexity)
        emit(result)
    elif args.command == "apply":
        changes, before = prepare(root)
        preview = diff(root, changes)
        print(preview or "无配置差异")
        if args.dry_run:
            print("Dry-run：静态验证通过；未修改项目文件；未调用模型。")
        else:
            print(apply(root, changes, before))
    elif args.command == "doctor":
        result = doctor(root)
        emit(result)
        return 0 if result["ok"] else 1
    elif args.command == "rollback":
        print(rollback(root))
    elif args.command == "recover":
        print(recover(root))
    elif args.command == "skill":
        name = ".agents/skills/agent-team-router/SKILL.md"
        data = files("atr").joinpath("skill/SKILL.md").read_bytes()
        existing = read(root, name)
        if existing is not None and name not in state(root)["managed"] and existing != data:
            raise ATRException("已有同名 Skill；不会覆盖用户文件")
        print(apply(root, {name: data}, {name: existing}))
        print("Skill 已安装到项目 .agents/skills；在 Codex 新会话中使用 $agent-team-router。")
    elif args.command == "run":
        from pathlib import Path
        from atr.runtime.runner import execute, preview
        plan = preview(root, Path(args.task_file).resolve(), args.model, args.effort,
                       args.complexity, args.allow_escalation)
        if not args.execute:
            emit(plan)
        else:
            record = execute(root, plan)
            emit(record)
            return 0 if record["status"] == "succeeded" else 1
    elif args.command == "native":
        from atr.native import collector
        from atr.native.install import prepare_install, prepare_uninstall, status
        if args.action in ("install", "uninstall"):
            changes, before = (prepare_install if args.action == "install" else prepare_uninstall)(root)
            print(diff(root, changes) or "无配置差异")
            if args.dry_run:
                print("Dry-run：未修改文件，未调用模型，未写入 hook 信任。")
            else:
                print(apply(root, changes, before))
                if args.action == "install":
                    print("首次启用：在 Codex 信任项目并通过 /hooks 或 Settings > Hooks 审阅并信任 hooks；新会话生效。")
        elif args.action == "status":
            emit(status(root))
        elif args.action == "sessions":
            emit(collector.sessions(root))
        elif args.action == "report":
            result = collector.report(root, args.session, args.turn)
            emit(result) if args.format == "json" else print(collector.markdown(result))
        elif args.action == "note":
            collector.note(root, args.session, args.turn, args.agent_id, args.role, args.task, args.model)
            emit({"source": "host-declared", "model_invocation": "not performed"})
    elif args.command == "runs":
        from atr.runtime.records import all_records, recover_run, report, request_cancel, show
        if args.action == "list":
            emit([{key: run[key] for key in ("id", "task_id", "status", "created_at")} for run in all_records(root)])
        elif args.action == "report":
            emit(report(root))
        elif args.action == "show":
            emit(show(root, args.id))
        elif args.action == "cancel":
            emit(request_cancel(root, args.id))
        elif args.action == "recover":
            emit(recover_run(root, args.id))
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (ATRException, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f"atr: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
