"""Initial model selection for the three task complexity levels."""
from collections.abc import Mapping

from atr.core import ATRException, EFFORTS, TIERS, Model, analyze, choose_model


_DEFAULTS = {
    "simple": ("fast", "low"),
    "medium": ("balanced", "medium"),
    "complex": ("reasoning", "high"),
}


def select_initial(task, models, routing=None, complexity=None):
    """Select an initial model and effort, validating the complete routing table."""
    if not isinstance(task, str):
        raise ATRException("Task must be a string")
    if complexity is not None and (not isinstance(complexity, str) or complexity not in _DEFAULTS):
        raise ATRException("Complexity must be simple, medium, or complex")
    if not isinstance(models, (list, tuple)) or not models:
        raise ATRException("Model catalog must not be empty")
    for model in models:
        if (not isinstance(model, Model) or not isinstance(model.id, str) or not model.id.strip()
                or model.tier not in TIERS or not isinstance(model.efforts, (list, tuple))
                or not model.efforts or any(not isinstance(e, str) or not e.strip() for e in model.efforts)):
            raise ATRException("Invalid model catalog entry")
    if routing is None:
        routing = {}
    if not isinstance(routing, Mapping):
        raise ATRException("Routing must be a table")

    rules = {}
    for level, rule in routing.items():
        if level not in _DEFAULTS:
            raise ATRException(f"Unknown routing complexity: {level}")
        if not isinstance(rule, Mapping):
            raise ATRException(f"Routing rule for {level} must be a table")
        if set(rule) - {"tier", "model", "effort"}:
            raise ATRException(f"Unknown routing field for {level}")
        tier = rule.get("tier", _DEFAULTS[level][0])
        pinned = rule.get("model")
        effort = rule.get("effort")
        if not isinstance(tier, str) or tier not in TIERS:
            raise ATRException(f"Invalid tier for {level}: {tier}")
        if "model" in rule and (not isinstance(pinned, str) or not pinned.strip()):
            raise ATRException(f"Invalid pinned model for {level}")
        if "effort" in rule and (not isinstance(effort, str) or effort not in EFFORTS):
            raise ATRException(f"Invalid effort for {level}: {effort}")
        selected = choose_model(models, tier, pinned)
        if "effort" in rule and effort not in selected.efforts:
            raise ATRException(f"Model {selected.id} does not support effort {effort}")
        rules[level] = rule

    level = complexity or {"low": "simple", "normal": "medium", "high": "complex"}[analyze(task)[1]]
    rule = rules.get(level, {})
    tier = rule.get("tier", _DEFAULTS[level][0])
    selected = choose_model(models, tier, rule.get("model"))
    effort = rule.get("effort", _DEFAULTS[level][1])
    reason = f"{level} complexity selects the {tier} tier"
    if rule.get("model"):
        reason += f"; model {selected.id} is pinned"
    elif selected.tier != tier:
        reason += f"; no model in {tier}, using {selected.tier} tier"
    if effort not in selected.efforts:
        available = [value for value in EFFORTS if value in selected.efforts]
        original = effort
        effort = min(available, key=lambda value: abs(EFFORTS.index(value) - EFFORTS.index(original))) if available else selected.efforts[0]
        reason += f"; effort {original} unavailable, using {effort}"
    return {"complexity": level, "tier": selected.tier, "model": selected.id,
            "effort": effort, "reason": reason}
