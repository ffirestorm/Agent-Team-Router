"""Pure escalation decisions. No model names, commands or platform configuration."""
from dataclasses import dataclass

from atr.core import ATRException


@dataclass(frozen=True)
class Limits:
    same_model_retries: int = 1
    max_attempts: int = 3
    timeout: int = 600
    attempt_timeout: int = 180
    verifier_timeout: int = 60
    max_tokens: int | None = None
    allow_escalation: bool = False
    escalation_tier: str = "reasoning"
    escalation_effort: str = "high"


def limits_from(data: dict | None) -> Limits:
    data = {} if data is None else data
    if not isinstance(data, dict) or set(data) - set(Limits.__dataclass_fields__):
        raise ATRException("execution 配置字段无效")
    limits = Limits(**data)
    for field, minimum, maximum in (("same_model_retries", 0, 1), ("max_attempts", 1, 3),
                                    ("timeout", 1, 3600), ("attempt_timeout", 1, 1800),
                                    ("verifier_timeout", 1, 600)):
        value = getattr(limits, field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ATRException(f"execution.{field} 必须在 {minimum}..{maximum} 内")
    if not isinstance(limits.allow_escalation, bool):
        raise ATRException("allow_escalation 必须是布尔值")
    if limits.max_tokens is not None and (isinstance(limits.max_tokens, bool) or not isinstance(limits.max_tokens, int) or limits.max_tokens < 1):
        raise ATRException("max_tokens 必须是正整数；它只阻止后续调用，不是当前调用硬上限")
    if limits.escalation_tier not in ("fast", "balanced", "reasoning", "expert"):
        raise ATRException("escalation_tier 无效")
    if not isinstance(limits.escalation_effort, str) or not limits.escalation_effort:
        raise ATRException("escalation_effort 无效")
    return limits


def decide(*, failure: str, attempts: int, retries: int, escalations: int,
           limits: Limits, remaining_seconds: float, tokens: int | None,
           cancelled: bool = False) -> tuple[str, str]:
    if cancelled or failure == "cancelled":
        return "stop", "cancelled"
    if remaining_seconds <= 0 or failure == "timeout":
        return "stop", "budget_exceeded"
    if limits.max_tokens is not None and (tokens is None or tokens >= limits.max_tokens):
        return "stop", "token_budget_exceeded_or_unobservable"
    if failure != "quality":
        return "stop", f"not_an_upgrade_candidate:{failure}"
    if attempts >= limits.max_attempts:
        return "stop", "attempt_budget_exceeded"
    if escalations:
        return "stop", "escalation_already_used"
    if retries < limits.same_model_retries:
        return "retry", "reproducible_acceptance_failure:bounded_same_model_repair"
    if limits.allow_escalation:
        return "escalate", "reproducible_acceptance_failure:upgrade_candidate_not_proven_causality"
    return "stop", "automatic_escalation_disabled"
