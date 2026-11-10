"""Platform-independent, deterministic policy and team planning."""
from dataclasses import asdict, dataclass
from typing import Protocol

TIERS = ("fast", "balanced", "reasoning", "expert")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


class ATRException(ValueError):
    """An actionable configuration or compatibility failure."""


@dataclass(frozen=True)
class Model:
    id: str
    tier: str
    efforts: tuple[str, ...]


@dataclass(frozen=True)
class Role:
    name: str
    tier: str
    effort: str
    instructions: str
    read_only: bool = True


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    concurrency: int
    roles: tuple[Role, ...]


@dataclass
class Assignment:
    role: str
    model: str
    effort: str
    read_only: bool
    instructions: str
    reason: str
    alternatives: list[str]


@dataclass
class Plan:
    task: str
    category: str
    complexity: str
    risk: str
    profile: str
    concurrency: int
    stronger_review: bool
    assignments: list[Assignment]
    notes: list[str]

    def to_dict(self):
        return asdict(self)


class Verifier(Protocol):
    def verify(self, result: dict) -> list[str]: ...


class Executor(Protocol):
    """Future whole-team executor; the V0.2 runtime currently executes one task."""
    def execute(self, plan: Plan) -> dict: ...


def analyze(task: str) -> tuple[str, str, str]:
    text = task.lower()
    risk = "high" if any(w in text for w in (
        "安全", "认证", "支付", "数据迁移", "security", "auth", "payment", "migration",
        "删除数据", "production", "生产")) else "normal"
    complex_task = any(w in text for w in (
        "架构", "分布式", "并发", "算法", "architecture", "distributed", "concurrency", "algorithm"))
    category = "review" if any(w in text for w in (
        "分析", "调用链", "理解", "复盘", "review", "trace", "explain")) else "development"
    if any(w in text for w in ("修复", "bug", "debug")):
        category = "debugging"
    complexity = "high" if complex_task or risk == "high" or len(task) > 350 else "normal"
    if complexity != "high" and any(w in text for w in ("拼写", "typo", "format", "格式", "注释")):
        complexity = "low"
    return category, complexity, risk


def choose_model(models: list[Model], tier: str, pinned: str | None = None) -> Model:
    if not models:
        raise ATRException("没有可用模型；请运行 atr init 或检查模型映射")
    if pinned:
        for model in models:
            if model.id == pinned:
                return model
        raise ATRException(f"模型 {pinned} 不在本机目录中；请选择替代模型")
    rank = TIERS.index(tier)
    # Prefer reaching capability; downgrade explicitly only when no stronger tier exists.
    return min(models, key=lambda m: (
        TIERS.index(m.tier) < rank, abs(TIERS.index(m.tier) - rank), m.id))


def plan_team(task: str, profile: Profile, models: list[Model],
              overrides: dict | None = None, static: bool = False,
              complexity_override: str | None = None) -> Plan:
    category, complexity, risk = analyze(task)
    if complexity_override is not None:
        if complexity_override not in ("low", "normal", "high"):
            raise ATRException("复杂度必须是 low/normal/high")
        complexity = complexity_override
    overrides = overrides or {}
    roles = list(profile.roles)
    notes = ["仅生成建议，不执行任务。模型目录验证不代表账号调用权限或额度验证。"]
    if not static and complexity == "low":
        roles = [r for r in roles if r.name in ("worker", "explorer")][:1] or roles[:1]
        notes.append("简单任务仅安排一个角色，减少子 Agent 调度成本。")
    stronger_review = complexity == "high" or risk == "high"
    if stronger_review and not any(r.name == "reviewer" for r in roles):
        roles.append(Role("reviewer", "expert", "high", "Review correctness, risks and evidence. Do not edit files."))
    assignments = []
    for role in roles:
        custom = overrides.get(role.name, {})
        tier = custom.get("tier", role.tier)
        if not static and stronger_review and role.name in ("reviewer", "architect", "worker"):
            tier = TIERS[max(TIERS.index(tier), 2 if role.name == "worker" else 3)]
        model = choose_model(models, tier, custom.get("model"))
        effort = custom.get("effort", role.effort)
        if custom.get("effort") and effort not in model.efforts:
            raise ATRException(f"{model.id} 不支持 {effort}；支持：{', '.join(model.efforts)}")
        if effort not in model.efforts:
            usable = [e for e in EFFORTS if e in model.efforts]
            effort = min(usable, key=lambda e: abs(EFFORTS.index(e) - EFFORTS.index(effort))) if usable else model.efforts[0]
        reason = f"{profile.name} / {category} / {complexity}复杂度 / {risk}风险；目标 {tier} 档位"
        if TIERS.index(model.tier) < TIERS.index(tier):
            reason += "；可用模型能力不足，已降级，建议补充更强模型"
        read_only = role.read_only or profile.name == "code-review"
        assignments.append(Assignment(role.name, model.id, effort, read_only,
                                      custom.get("instructions", role.instructions), reason,
                                      [m.id for m in models if m.id != model.id]))
    return Plan(task, category, complexity, risk, profile.name,
                min(profile.concurrency, len(assignments)), stronger_review, assignments, notes)
