"""Native-role-aware delegation preview. Does not create agents or call models."""
from atr.adapters.codex import parse_toml
from atr.config import load
from atr.core import ATRException, plan_team
from atr.delegation import select_delegation
from atr.storage import read


def preview(root, task, complexity=None, risk=None, tiny=False):
    doc, profile, rules, models = load(root)
    available = {}
    raw = read(root, '.codex/config.toml')
    native = parse_toml(raw.decode()) if raw else {}
    agents = native.get('agents', {})
    features = native.get('features', {})
    if not isinstance(agents, dict) or not isinstance(features, dict):
        raise ATRException('原生 agents/features 字段必须是表')
    enabled = agents.get('enabled') is not False and features.get('multi_agent') is not False
    plan = plan_team('Static team configuration', profile, models, rules, static=True)
    for assignment in plan.assignments:
        role = 'atr_' + assignment.role
        if not enabled or role not in ('atr_simple_worker', 'atr_worker') or assignment.read_only:
            continue
        entry = agents.get(role)
        expected = f'agents/{role}.toml'
        if not isinstance(entry, dict) or entry.get('config_file') != expected:
            continue
        layer = read(root, '.codex/' + expected)
        if layer is None:
            continue
        layer = parse_toml(layer.decode())
        if layer.get('sandbox_mode') != 'workspace-write':
            continue
        if layer.get('model') != assignment.model or layer.get('model_reasoning_effort') != assignment.effort:
            continue
        available[role] = {'model': assignment.model, 'effort': assignment.effort}
    result = select_delegation(task, models, doc.get('routing', {}), complexity, risk, tiny,
                               available_roles=available if profile.name == 'development' else {})
    result['profile'] = profile.name
    result['main_model'] = 'user-selected in Codex; unchanged'
    result['execution'] = 'not performed; native host decides whether to delegate'
    result['workflow'] = 'no mandatory reviewer, acceptance, retry or upgrade pipeline'
    return result
