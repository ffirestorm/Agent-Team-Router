import json
from pathlib import Path

import pytest

from atr.core import Model, plan_team
from atr.profiles import PROFILES

CASES = json.loads((Path(__file__).parents[1] / "evals/routing_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["task"] for c in CASES])
def test_public_rule_regression(case):
    models = [Model("fixture-fast", "fast", ("low", "medium", "high")),
              Model("fixture-expert", "expert", ("low", "medium", "high"))]
    plan = plan_team(case["task"], PROFILES[case["profile"]], models)
    assert (len(plan.assignments) == 1) == case["expect_single_agent"]
    assert plan.stronger_review == case["expect_review"]
