import pytest

from atr.core import ATRException, Assignment, Model, Plan
from atr.delegation import select_delegation, workflow_instructions


MODELS = [Model("quick", "fast", ("low",)), Model("steady", "balanced", ("medium",)),
          Model("deep", "reasoning", ("high",))]


def test_simple_and_medium_select_matching_workers():
    simple = select_delegation("fix typo", MODELS)
    assert (simple["action"], simple["agent_type"], simple["model"], simple["effort"]) == (
        "delegate", "atr_simple_worker", "quick", "low")
    medium = select_delegation("implement feature", MODELS)
    assert (medium["agent_type"], medium["model"]) == ("atr_worker", "steady")
    assert "not evidence" in simple["note"]


@pytest.mark.parametrize("kwargs", [{"tiny": True}, {"risk": "high"}, {"complexity": "complex"}])
def test_main_retains_its_model(kwargs):
    result = select_delegation("implement feature", MODELS, **kwargs)
    assert (result["action"], result["agent_type"], result["model"], result["effort"]) == (
        "main", None, None, None)


def test_analyzed_risk_and_explicit_override():
    assert select_delegation("payment typo", MODELS, complexity="simple")["action"] == "main"
    assert select_delegation("payment typo", MODELS, complexity="simple", risk="low")["action"] == "delegate"
    with pytest.raises(ATRException):
        select_delegation("test", MODELS, risk="maybe")
    with pytest.raises(ATRException):
        select_delegation("test", MODELS, complexity="unknown")


def test_incompatible_routing_and_missing_tier_stay_main():
    assert select_delegation("typo", MODELS, routing={"simple": {"model": "steady"}})["action"] == "main"
    assert select_delegation("typo", MODELS[1:])["action"] == "main"
    assert select_delegation("feature", [MODELS[0]])["action"] == "main"


def test_available_role_must_match_both_configuration_fields():
    matching = {"atr_simple_worker": {"model": "quick", "effort": "low"}}
    assert select_delegation("typo", MODELS, available_roles=matching)["action"] == "delegate"
    for roles in ({}, {"atr_simple_worker": {"model": "steady", "effort": "low"}},
                  {"atr_simple_worker": {"model": "quick", "effort": "medium"}}):
        assert select_delegation("typo", MODELS, available_roles=roles)["action"] == "main"


def test_workflow_uses_actual_eligible_assignments():
    def assignment(role, model, effort):
        return Assignment(role, model, effort, False, "", "", [])
    plan = Plan("task", "development", "normal", "normal", "development", 3, False,
                [assignment("simple_worker", "actual-fast", "minimal"),
                 assignment("worker", "actual-balanced", "high"),
                 assignment("explorer", "ignored", "low")], [])
    text = workflow_instructions(plan)
    assert "atr_simple_worker: configured model actual-fast, effort minimal" in text
    assert "atr_worker: configured model actual-balanced, effort high" in text
    assert "ignored" not in text
    assert "higher-priority host restrictions" in text
    assert "not proof" in text
