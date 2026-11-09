import pytest

from atr.core import ATRException, Model, analyze, plan_team
from atr.profiles import PROFILES, load_profile


@pytest.fixture
def models():
    return [Model("cheap", "fast", ("low",)), Model("everyday", "balanced", ("low", "medium")),
            Model("think", "reasoning", ("medium", "high")), Model("best", "expert", ("high",))]


def test_economical_simple_avoids_overhead(models):
    plan = plan_team("fix a typo", PROFILES["economical"], models)
    assert len(plan.assignments) == 1
    assert plan.assignments[0].model == "cheap"
    assert not plan.stronger_review


@pytest.mark.parametrize("task", ["修复支付认证的拼写", "review production migration", "设计分布式架构"])
def test_risky_or_complex_tasks_get_expert_review(models, task):
    plan = plan_team(task, PROFILES["economical"], models)
    assert plan.stronger_review
    assert next(a for a in plan.assignments if a.role == "reviewer").model == "best"
    assert plan.concurrency == 1


def test_missing_tier_explains_fallback(models):
    plan = plan_team("review security", PROFILES["quality"], models[:1])
    assert all(a.model == "cheap" and a.effort == "low" for a in plan.assignments)
    assert "降级" in plan.assignments[0].reason


def test_no_models_is_error():
    with pytest.raises(ATRException):
        plan_team("task", PROFILES["balanced"], [])


def test_read_only_profile_and_alternatives(models):
    plan = plan_team("分析 RAG 调用链", PROFILES["code-review"], models)
    assert plan.category == "review"
    assert all(a.read_only and len(a.alternatives) == 3 for a in plan.assignments)
    assert plan.concurrency == 2


def test_profile_and_role_override(models):
    profile, rules = load_profile("development", {"development": {
        "concurrency": 1, "roles": {"explorer": {"enabled": False}, "worker": {"model": "think", "effort": "high"}}}})
    plan = plan_team("implement feature", profile, models, rules)
    assert plan.concurrency == 1
    assert "explorer" not in [a.role for a in plan.assignments]
    assert next(a for a in plan.assignments if a.role == "worker").model == "think"


@pytest.mark.parametrize("override", [
    {"concurrency": 0}, {"concurrency": True}, {"mispelled": 1},
    {"roles": {"bad": {}}}, {"roles": {"worker": {"effort": "nonsense"}}},
    {"roles": {"worker": {"sandbox_mode": "danger-full-access"}}},
])
def test_bad_override(override):
    with pytest.raises(ATRException):
        load_profile("balanced", {"balanced": override})


def test_bad_pinned_model_and_effort(models):
    for rule in ({"model": "missing"}, {"model": "cheap", "effort": "high"}):
        with pytest.raises(ATRException):
            plan_team("task", PROFILES["economical"], models, {"worker": rule})


@pytest.mark.parametrize("name", PROFILES)
def test_all_profiles_static_usable(models, name):
    plan = plan_team("static", PROFILES[name], models, static=True)
    assert plan.assignments
    assert plan.concurrency <= len(plan.assignments)


def test_static_config_does_not_fake_dynamic_routing(models):
    plan = plan_team("static", PROFILES["economical"], models, static=True)
    assert len(plan.assignments) == 1
    assert analyze("debug bug")[0] == "debugging"


def test_unknown_profile_override_is_not_silently_ignored():
    with pytest.raises(ATRException, match="未知 Profile 覆写"):
        load_profile("balanced", {"balancd": {"concurrency": 1}})
