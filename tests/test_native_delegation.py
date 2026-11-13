import json
import tomllib

import pytest

from atr.cli import main
from atr.config import initialize, use_profile
from atr.core import Model, plan_team
from atr.native.collector import settings
from atr.native.delegation import preview
from atr.profiles import PROFILES
from atr.service import prepare
from atr.storage import apply, read, rollback


def setup(root):
    initialize(root, 'development', 'codex', None, None)
    changes, before = prepare(root)
    apply(root, changes, before)


def test_development_has_two_writable_models_no_automatic_reviewer(project, fake_codex):
    setup(project)
    config = tomllib.loads(read(project, '.codex/config.toml').decode())
    assert 'model' not in config and 'model_reasoning_effort' not in config
    assert {'atr_simple_worker', 'atr_worker', 'atr_explorer'} <= config['agents'].keys()
    assert 'atr_reviewer' not in config['agents']
    simple = tomllib.loads(read(project, '.codex/agents/atr_simple_worker.toml').decode())
    regular = tomllib.loads(read(project, '.codex/agents/atr_worker.toml').decode())
    assert simple['model'] == 'gpt-6-luna' and regular['model'] == 'gpt-6.1-terra'
    assert simple['sandbox_mode'] == regular['sandbox_mode'] == 'workspace-write'
    assert 'Lightweight native delegation' in read(project, 'AGENTS.md').decode()
    assert not (project / '.codex/hooks.json').exists()
    assert settings(project)['mode'] == 'off'
    assert not (project / '.atr/native/events.sqlite3').exists()


def test_preview_selects_applied_roles_and_leaves_highrisk_and_tiny_on_main(project, fake_codex):
    setup(project)
    assert preview(project, 'fix typo')['agent_type'] == 'atr_simple_worker'
    regular = preview(project, 'implement a cache')
    assert regular['agent_type'] == 'atr_worker' and regular['model'] == 'gpt-6.1-terra'
    assert 'not performed' in regular['execution']
    for task, values in [('payment change', {}), ('design distributed system', {}), ('fix typo', {'tiny': True})]:
        result = preview(project, task, **values)
        assert result['action'] == 'main' and result['model'] is None


def test_preview_never_claims_unapplied_or_changed_roles_are_available(project, fake_codex):
    initialize(project, 'development', 'codex', None, None)
    assert preview(project, 'fix typo')['action'] == 'main'
    changes, before = prepare(project)
    apply(project, changes, before)
    path = project / '.codex/agents/atr_simple_worker.toml'
    original = path.read_bytes()
    path.write_bytes(original.replace(b'gpt-6-luna', b'gpt-6.1-terra'))
    assert preview(project, 'fix typo')['action'] == 'main'
    path.write_bytes(original.replace(b'workspace-write', b'read-only'))
    assert preview(project, 'fix typo')['action'] == 'main'
    path.write_bytes(original)
    native = project / '.codex/config.toml'
    native.write_bytes(native.read_bytes().replace(b'multi_agent = true', b'multi_agent = false'))
    assert preview(project, 'fix typo')['action'] == 'main'


def test_main_model_and_custom_roles_preserved_migration_removes_owned_reviewer(project, fake_codex):
    initialize(project, 'balanced', 'codex', None, None)
    (project / '.codex').mkdir()
    (project / '.codex/config.toml').write_text('model="user-selected-main"\nmodel_reasoning_effort="high"\n[agents.personal]\ndescription="user role"\n')
    changes, before = prepare(project)
    apply(project, changes, before)
    assert (project / '.codex/agents/atr_reviewer.toml').exists()
    use_profile(project, 'development')
    changes, before = prepare(project)
    apply(project, changes, before)
    native = tomllib.loads(read(project, '.codex/config.toml').decode())
    assert native['model'] == 'user-selected-main' and native['model_reasoning_effort'] == 'high'
    assert 'personal' in native['agents'] and 'atr_reviewer' not in native['agents']
    assert not (project / '.codex/agents/atr_reviewer.toml').exists()
    changes, before = prepare(project)
    assert '无需修改' in apply(project, changes, before)
    rollback(project)
    restored = tomllib.loads(read(project, '.codex/config.toml').decode())
    assert 'atr_reviewer' in restored['agents'] and 'atr_simple_worker' not in restored['agents']


def test_disabled_simple_role_and_wrong_routing_do_not_select_other_model(project, fake_codex):
    setup(project)
    config = project / '.atr/config.toml'
    with config.open('a', encoding='utf-8') as stream:
        stream.write('\n[routing.simple]\nmodel="gpt-6.1-terra"\n')
    assert preview(project, 'fix typo')['action'] == 'main'
    config.write_text(config.read_text().split('\n[routing.simple]')[0] + '\n[profiles.development.roles.simple_worker]\nenabled=false\n', encoding='utf-8')
    assert preview(project, 'fix typo')['action'] == 'main'


def test_native_delegate_cli_is_optional_and_has_no_record_side_effect(project, fake_codex, capsys):
    setup(project)
    args = ['--project', str(project)]
    assert main(args + ['delegate', 'fix typo']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['agent_type'] == 'atr_simple_worker'
    assert main(args + ['plan', 'fix typo']) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned['native_delegation']['agent_type'] == 'atr_simple_worker'
    assert planned['assignments'][0]['role'] == 'simple_worker'
    assert not planned['stronger_review']
    assert main(args + ['delegate', '']) == 2
    assert not (project / '.atr/native/events.sqlite3').exists()
    assert all('exec' not in call for call in fake_codex['calls'])


def test_development_complex_team_suggests_no_extra_calls():
    models = [Model('small', 'fast', ('low',)), Model('regular', 'balanced', ('medium',))]
    plan = plan_team('security migration', PROFILES['development'], models)
    assert not plan.assignments and not plan.stronger_review
    assert plan.concurrency == 0
