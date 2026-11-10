"""Unified project configuration: model IDs belong in data, not routing rules."""
import json
import os
from pathlib import Path

import tomlkit

from atr.adapters import get_adapter
from atr.adapters.codex import MODEL_HINTS, normalize_catalog, parse_toml
from atr.core import ATRException, Model, TIERS
from atr.profiles import load_profile
from atr.storage import atomic, locked, read, require_clean, safe_path

CONFIG = ".atr/config.toml"


def project_root(value: str) -> Path:
    candidate = Path(value).expanduser().absolute()
    # Canonicalize user-selected roots (macOS /tmp and /var are system aliases).
    # All managed paths *inside* that selected root still reject symlinks.
    root = candidate.resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    if root == Path.home().resolve() or root == codex_home or codex_home in root.parents:
        raise ATRException("请指定项目目录；ATR 不向用户主目录或全局 CODEX_HOME 写入")
    if not root.is_dir():
        raise ATRException(f"项目目录不存在：{root}")
    return root


def initialize(root: Path, profile: str, platform: str, model_filter: str | None,
               catalog_path: str | None, model_map: list[str] | None = None) -> dict:
    adapter = get_adapter(platform)
    load_profile(profile, {})
    version = adapter.version()
    field = adapter.concurrency_field()
    if catalog_path:
        payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        catalog = normalize_catalog(payload["models"])
        source = "user-supplied (account access unverified)"
    else:
        catalog, source = adapter.catalog()
    requested = [s.strip() for s in model_filter.split(",")] if model_filter else []
    known = {m["id"]: m for m in catalog}
    missing = set(requested) - known.keys()
    if missing:
        raise ATRException(f"模型不在目录中：{', '.join(sorted(missing))}；可选：{', '.join(known)}")
    mappings = {tier: name for tier, name in MODEL_HINTS.items() if name in known and (not requested or name in requested)}
    if model_map:
        mappings = {}
        for entry in model_map:
            tier, separator, ident = entry.partition("=")
            if not separator or tier not in TIERS or ident not in known:
                raise ATRException("--model-map 需要 tier=模型ID，且 ID 必须在目录内")
            if tier in mappings or ident in mappings.values():
                raise ATRException("--model-map 的档位和模型均不能重复")
            mappings[tier] = ident
        if requested and set(mappings.values()) - set(requested):
            raise ATRException("--model-map 不能选择 --models 允许列表以外的模型")
    if not mappings:
        raise ATRException("目录中没有默认候选模型；请用 --model-map fast=模型ID 显式指定能力映射")
    selected = {name for name in mappings.values()}
    # Explicit unfamiliar IDs are recorded without claiming their capability: user must choose tiers.
    for name in requested:
        if name not in selected:
            raise ATRException(f"{name} 的能力档位未知；请用 --model-map 显式指定")
    doc = tomlkit.document()
    doc.add(tomlkit.comment("ATR unified config. Model tiers are heuristic policy labels, not benchmark results."))
    doc["version"] = 1
    doc["platform"] = platform
    doc["profile"] = profile
    doc["discovery"] = {"codex_version": version, "source": source, "concurrency_field": field}
    doc["models"] = mappings
    doc["catalog"] = {name: {"efforts": known[name]["efforts"]} for name in known}
    with locked(root):
        require_clean(root)
        if read(root, CONFIG) is not None:
            raise ATRException("项目已初始化；使用 atr profile use 或编辑 .atr/config.toml")
        atomic(safe_path(root, CONFIG), tomlkit.dumps(doc).encode())
    return {"project": str(root), "config": CONFIG, "profile": profile,
            "codex": version, "models": mappings, "availability": source,
            "note": "未执行模型调用；按账号实际可用模型编辑映射。"}


def load(root: Path):
    require_clean(root)
    raw = read(root, CONFIG)
    if raw is None:
        raise ATRException("项目尚未初始化；先运行 atr init")
    doc = parse_toml(raw.decode())
    if doc.get("version") != 1:
        raise ATRException("不支持的 ATR 配置版本")
    get_adapter(doc.get("platform", ""))
    unknown = set(doc) - {"version", "platform", "profile", "discovery", "models", "catalog", "profiles", "routing", "execution"}
    if unknown:
        raise ATRException(f"未知 ATR 配置字段：{sorted(unknown)}")
    from atr.runtime.policy import limits_from
    limits_from(doc.get("execution", {}))
    profile, rules = load_profile(doc.get("profile", ""), doc.get("profiles", {}))
    mappings = doc.get("models", {})
    catalog = doc.get("catalog", {})
    if not isinstance(mappings, dict) or not isinstance(catalog, dict):
        raise ATRException("models 和 catalog 必须是 TOML 表")
    if set(mappings) - set(TIERS):
        raise ATRException("models 仅允许 fast/balanced/reasoning/expert 档位")
    discovery = doc.get("discovery", {})
    if not isinstance(discovery, dict) or not isinstance(discovery.get("source", ""), str):
        raise ATRException("discovery 必须是表且 source 必须是字符串")
    models = []
    for tier, ident in mappings.items():
        if not isinstance(ident, str) or ident not in catalog:
            raise ATRException(f"模型 {ident} 无目录记录；请选择替代模型或补充显式目录")
        if not isinstance(catalog[ident], dict):
            raise ATRException(f"模型 {ident} 的目录记录必须是表")
        raw_efforts = catalog[ident].get("efforts")
        if not isinstance(raw_efforts, list) or not raw_efforts or any(not isinstance(e, str) or not e for e in raw_efforts):
            raise ATRException(f"模型 {ident} 的 efforts 无效")
        if any(m.id == ident for m in models):
            raise ATRException("同一模型不能映射多个档位；缺失档位由路由器解释性回退")
        models.append(Model(ident, tier, tuple(raw_efforts)))
    if not models:
        raise ATRException("至少配置一个模型映射")
    from atr.task_policy import select_initial
    select_initial("configuration validation", models, doc.get("routing", {}))
    return doc, profile, rules, models


def use_profile(root: Path, name: str):
    with locked(root):
        doc, _, _, _ = load(root)
        load_profile(name, doc.get("profiles", {}))
        doc["profile"] = name
        atomic(safe_path(root, CONFIG), tomlkit.dumps(doc).encode())
    return {"profile": name, "note": "仅更新 ATR 设置；运行 atr apply --dry-run 查看原生配置差异"}
