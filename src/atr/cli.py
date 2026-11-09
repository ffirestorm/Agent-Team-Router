import argparse
from importlib.resources import files
import json
import sys

from atr import __version__
from atr.config import initialize, load, project_root, use_profile
from atr.core import ATRException, plan_team
from atr.profiles import PROFILES
from atr.service import doctor, prepare
from atr.storage import apply, diff, read, recover, rollback, state


def parser():
    root = argparse.ArgumentParser(prog="atr", description="Explainable agent planning and safe Codex configuration; no task execution.")
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
    app = command("apply", help="validate, diff and safely apply native configuration")
    app.add_argument("--dry-run", action="store_true")
    command("doctor")
    command("rollback")
    command("recover", help="restore a prepared transaction after interruption")
    skill = command("skill")
    skill.add_argument("action", choices=["install"])
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
        _, profile, rules, models = load(root)
        emit(plan_team(args.task, profile, models, rules).to_dict())
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
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (ATRException, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"atr: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
