from dataclasses import replace

from atr.core import ATRException, EFFORTS, TIERS, Profile, Role

EXPLORER = Role("explorer", "fast", "low", "Explore source and cite file paths. Do not edit files.")
TRACER = Role("logic_tracer", "reasoning", "high", "Trace call chains and explain invariants with source evidence. Do not edit files.")
REVIEWER = Role("reviewer", "reasoning", "high", "Review correctness, tests and risks with concrete evidence. Do not edit files.")
WORKER = Role("worker", "balanced", "medium", "Implement the scoped change and run relevant tests. Report evidence.", False)
ARCHITECT = Role("architect", "expert", "high", "Analyze architecture and tradeoffs. Propose a bounded design. Do not edit files.")

PROFILES = {
    "economical": Profile("economical", "低调度成本；默认一个开发角色，高风险才增加审查", 1, (replace(WORKER, tier="fast", effort="low"),)),
    "balanced": Profile("balanced", "常规开发与独立审查", 2, (WORKER, REVIEWER)),
    "quality": Profile("quality", "复杂设计、实现与专家审查", 3, (ARCHITECT, replace(WORKER, tier="reasoning", effort="high"), replace(REVIEWER, tier="expert"))),
    "code-review": Profile("code-review", "只读源码理解、调用链追踪和复盘", 2, (EXPLORER, TRACER, REVIEWER)),
    "development": Profile("development", "探索、实现、测试与审查", 3, (EXPLORER, WORKER, REVIEWER)),
}


def load_profile(name: str, overrides: dict) -> tuple[Profile, dict]:
    if not isinstance(name, str) or not isinstance(overrides, dict):
        raise ATRException("profile 必须是名称字符串，profiles 必须是 TOML 表")
    if name not in PROFILES:
        raise ATRException(f"未知 Profile {name}；可选：{', '.join(PROFILES)}")
    if set(overrides) - set(PROFILES):
        raise ATRException(f"未知 Profile 覆写：{sorted(set(overrides) - set(PROFILES))}")
    data = overrides.get(name, {})
    if not isinstance(data, dict):
        raise ATRException(f"Profile {name} 的覆写必须是 TOML 表")
    unknown = set(data) - {"concurrency", "roles"}
    if unknown:
        raise ATRException(f"Profile 不支持字段：{sorted(unknown)}")
    profile = PROFILES[name]
    concurrency = data.get("concurrency", profile.concurrency)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 16:
        raise ATRException("concurrency 必须是 1 到 16 的整数")
    rules = data.get("roles", {})
    valid_roles = {r.name for r in profile.roles} | {"reviewer"}
    if not isinstance(rules, dict):
        raise ATRException("roles 必须是表")
    if set(rules) - valid_roles:
        raise ATRException(f"未知角色：{sorted(set(rules) - valid_roles)}")
    for name_, rule in rules.items():
        if not isinstance(rule, dict) or set(rule) - {"tier", "model", "effort", "instructions", "enabled"}:
            raise ATRException(f"角色 {name_} 配置字段无效")
        if rule.get("tier", "fast") not in TIERS or rule.get("effort", "low") not in EFFORTS:
            raise ATRException(f"角色 {name_} 的 tier 或 effort 无效")
        if "enabled" in rule and type(rule["enabled"]) is not bool:
            raise ATRException("enabled 必须是布尔值")
        for field in ("model", "instructions"):
            if field in rule and (not isinstance(rule[field], str) or not rule[field].strip()):
                raise ATRException(f"{field} 必须是非空字符串")
    roles = tuple(r for r in profile.roles if rules.get(r.name, {}).get("enabled", True))
    if not roles:
        raise ATRException("至少启用一个角色")
    return replace(profile, concurrency=int(concurrency), roles=roles), rules
