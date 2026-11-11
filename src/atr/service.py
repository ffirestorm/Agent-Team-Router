import os
from pathlib import Path

from atr.adapters import get_adapter
from atr.adapters.codex import parse_toml
from atr.config import CONFIG, load
from atr.core import ATRException, plan_team
from atr.storage import check_drift, read, state


def prepare(root: Path, *, native=True):
    config_snapshot = read(root, CONFIG)
    doc, profile, rules, models = load(root)
    if read(root, CONFIG) != config_snapshot:
        raise ATRException("ATR 设置在读取期间被修改；请重试")
    adapter = get_adapter(doc["platform"])
    adapter.version()
    field = adapter.concurrency_field()
    if str(doc.get("discovery", {}).get("source", "")).startswith("codex-bundled"):
        live, _ = adapter.catalog()
        known = {m["id"]: m["efforts"] for m in live}
        for model in models:
            if model.id not in known or not set(model.efforts) <= set(known[model.id]):
                raise ATRException(f"模型 {model.id} 或推理强度已不在本机目录；请更新映射并选择替代模型")
    current_state = state(root)
    check_drift(root, current_state)
    plan = plan_team("Static team configuration", profile, models, rules, static=True)
    paths = {".codex/config.toml", "AGENTS.md"} | set(current_state["managed"])
    paths |= {f".codex/agents/atr_{item.role}.toml" for item in plan.assignments}
    current = {name: read(root, name) for name in paths}
    current[CONFIG] = config_snapshot
    changes = adapter.render(plan, current, set(current_state["managed"]), field)
    role_layers = {name.removeprefix(".codex/"): data for name, data in changes.items()
                   if name.startswith(".codex/agents/") and data is not None}
    adapter.validate(changes[".codex/config.toml"], role_layers)
    if native:
        from atr.native.install import instructions, manifest
        installed = manifest(root)
        if installed:
            changes["AGENTS.md"] = instructions(changes["AGENTS.md"], installed)
    return changes, current


def doctor(root: Path) -> dict:
    checks = []
    adapter = get_adapter("codex")
    try:
        checks.append({"check": "codex", "ok": True, "detail": adapter.version()})
        changes, _ = prepare(root)
        checks.append({"check": "native_fields", "ok": True,
                       "detail": "isolated Codex strict-config validation passed"})
        pending = [n for n, b in changes.items() if read(root, n) != b]
        checks.append({"check": "applied", "ok": not pending, "pending": pending})
    except (ATRException, OSError, ValueError, TypeError) as exc:
        checks.append({"check": "configuration", "ok": False, "detail": str(exc)})
    existing = {}
    global_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    for name, path in (("global", global_path), ("project", root / ".codex/config.toml")):
        try:
            if path.exists():
                agents = parse_toml(path.read_text(encoding="utf-8")).get("agents", {})
                if not isinstance(agents, dict):
                    raise ATRException("agents 字段必须是 TOML 表")
                existing[name] = sorted(k for k, v in agents.items() if isinstance(v, dict))
        except (ATRException, OSError) as exc:
            checks.append({"check": f"{name}_config", "ok": False, "detail": str(exc)})
    return {"ok": all(c["ok"] for c in checks), "project": str(root), "checks": checks,
            "existing_agents": existing, "model_invocation": "not performed",
            "limitations": ["模型账号权限和真实调用未验证", "隔离检查不包含全局或管理员配置约束",
                            "项目 .codex/config.toml 需 Codex 信任该项目后才会加载"]}
