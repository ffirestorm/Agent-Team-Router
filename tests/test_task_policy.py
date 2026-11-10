import pytest
import tomlkit

from atr.core import ATRException, Model
from atr.task_policy import select_initial


@pytest.fixture
def models():
    return [Model("quick", "fast", ("low", "medium")),
            Model("standard", "balanced", ("medium", "high")),
            Model("deep", "reasoning", ("medium", "high"))]


@pytest.mark.parametrize("task,level,tier,model,effort", [
    ("fix typo", "simple", "fast", "quick", "low"),
    ("implement feature", "medium", "balanced", "standard", "medium"),
    ("design architecture", "complex", "reasoning", "deep", "high"),
])
def test_defaults(models, task, level, tier, model, effort):
    result = select_initial(task, models)
    assert (result["complexity"], result["tier"], result["model"], result["effort"]) == (level, tier, model, effort)
    assert result["reason"]


def test_toml_overrides_and_input_unchanged(models):
    doc = tomlkit.parse('[routing.simple]\ntier = "reasoning"\nmodel = "standard"\neffort = "high"\n')
    before = tomlkit.dumps(doc)
    result = select_initial("fix typo", models, doc["routing"])
    assert (result["tier"], result["model"], result["effort"]) == ("balanced", "standard", "high")
    assert tomlkit.dumps(doc) == before


def test_manual_complexity(models):
    assert select_initial("fix typo", models, complexity="complex")["model"] == "deep"
    with pytest.raises(ATRException):
        select_initial("task", models, complexity="high")


@pytest.mark.parametrize("routing", [
    [], {"unknown": {}}, {"complex": 1}, {"complex": {"extra": 1}},
    {"complex": {"tier": "missing"}}, {"complex": {"effort": "huge"}},
    {"complex": {"model": "missing"}}, {"complex": {"model": ""}},
    {"complex": {"effort": "low"}},
])
def test_invalid_even_inactive_rules(models, routing):
    with pytest.raises(ATRException):
        select_initial("fix typo", models, routing)


def test_toml_integer_is_rejected(models):
    routing = tomlkit.parse('[routing.complex]\neffort = 2\n')["routing"]
    with pytest.raises(ATRException):
        select_initial("fix typo", models, routing)


def test_missing_tier_and_nearest_effort():
    result = select_initial("fix typo", [Model("one", "balanced", ("high", "medium"))])
    assert (result["tier"], result["effort"]) == ("balanced", "medium")
    assert "fast" in result["reason"] and "balanced" in result["reason"]


def test_unknown_catalog_effort_fallback():
    result = select_initial("task", [Model("one", "balanced", ("custom",))])
    assert result["effort"] == "custom"


def test_explicit_unsupported_effort_and_empty_catalog(models):
    with pytest.raises(ATRException):
        select_initial("task", models, {"medium": {"effort": "low"}})
    with pytest.raises(ATRException):
        select_initial("task", [])
