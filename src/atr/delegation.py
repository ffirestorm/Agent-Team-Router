"""Deterministic guidance for native delegation; this module executes nothing."""
from collections.abc import Mapping

from atr.core import ATRException, analyze
from atr.task_policy import select_initial


_NOTE = "The model and effort are configuration, not evidence of an observed invocation."


def select_delegation(task, models, routing=None, complexity=None, risk=None,
                      tiny=False, available_roles=None):
    """Recommend the main agent or a compatible configured native worker."""
    if not isinstance(task, str) or not task.strip():
        raise ATRException("Task must be a nonempty string")
    if risk is not None and (not isinstance(risk, str) or risk not in ("low", "normal", "high")):
        raise ATRException("Risk must be low, normal, or high")
    if not isinstance(tiny, bool):
        raise ATRException("Tiny must be a boolean")
    if available_roles is not None and not isinstance(available_roles, Mapping):
        raise ATRException("Available roles must be a table")
    selected = select_initial(task, models, routing, complexity)
    task_risk = risk if risk is not None else analyze(task)[2]
    level = selected["complexity"]
    result = {"action": "main", "agent_type": None, "complexity": level,
              "risk": task_risk, "model": None, "effort": None, "reason": "",
              "note": _NOTE}
    if tiny or level == "complex" or task_risk == "high":
        result["reason"] = "Tiny, complex, or high-risk work stays with the main agent."
        return result
    role, tier = (("atr_simple_worker", "fast") if level == "simple"
                  else ("atr_worker", "balanced"))
    if selected["tier"] != tier:
        result["reason"] = f"The selected model is outside the {tier} tier required for {role}."
        return result
    if available_roles is not None:
        config = available_roles.get(role)
        if (not isinstance(config, Mapping) or config.get("model") != selected["model"]
                or config.get("effort") != selected["effort"]):
            result["reason"] = f"{role} is unavailable or its configured model and effort do not match."
            return result
    result.update(action="delegate", agent_type=role, model=selected["model"],
                  effort=selected["effort"], reason=selected["reason"])
    return result


def workflow_instructions(plan):
    """Generate native workflow guidance from the actual planned assignments."""
    eligible = []
    for assignment in plan.assignments:
        role = assignment.role if assignment.role.startswith("atr_") else "atr_" + assignment.role
        if role in ("atr_simple_worker", "atr_worker"):
            eligible.append(f"- {role}: configured model {assignment.model}, effort {assignment.effort}")
    roles = "\n".join(eligible) if eligible else "- No eligible worker assignments are configured."
    return f"""Request useful delegation for independent work within the project scope, subject to higher-priority host restrictions. The main agent retains the user's selected model.

Eligible configured workers:
{roles}
These are configured models and efforts, not proof of observed invocations.

Keep tiny, ambiguous, risky, and complex tasks with the main agent. Delegate clear, low-risk simple chunks to atr_simple_worker and normal implementation chunks to atr_worker when that role is configured and compatible. Prefer one worker per substantive chunk and avoid simultaneous edits to the same files. Pass a short independent task brief and necessary references; prefer no full parent-history fork if the native host supports it.

Announce the planned role, configured model, and short task before spawning. Once the native tool returns an ID, state that it started and give the ID, selected role and configured model. During work, relay meaningful actual progress if available; never invent progress or a model request. When it returns, report what it says it changed or completed and any unresolved issues; distinguish a returned result from independently verified success. Mention tests only when actually run. Use the native subagent activity/thread view when available; these instructions do not create a custom UI or attest backend models.

Recording being off does not disable this narration; do not enable recording or write notes or reports when it is off. Do not add a Reviewer, independent acceptance gate, automatic test pipeline, repeated main-agent implementation, retries or model escalation unless the user requests that workflow. Ordinary checks may still be appropriate when requested, required by the repository, or necessary for the change. On failure or uncertainty, report it to the main agent and decide the next step within scope instead of launching an automatic repair chain. Do not impose a fixed subagent count or claim guaranteed correctness, lower total tokens, or subscription savings.
"""
