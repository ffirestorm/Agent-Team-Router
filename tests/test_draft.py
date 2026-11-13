import json
from pathlib import Path
import subprocess
import sys

import pytest

from atr.core import ATRException
from atr.runtime.draft import draft_contract
from atr.runtime.tasks import contract
from atr.storage import rollback


def test_natural_language_missing_inputs_is_non_executable_proposal(project):
    result = draft_contract(project, '实现缓存过期功能', output='.atr/tasks/cache/proposal.json')
    assert not result['ready'] and set(result['missing']) == {'allowed_paths', 'baseline', 'verify'}
    assert not result['commands_executed']
    with pytest.raises(ATRException):
        contract(Path(result['output']))
    rollback(project)
    assert not Path(result['output']).exists()


def test_custom_draft_preserves_argv_never_executes_and_reuses_contract_validation(project):
    result = draft_contract(project, 'Add a cache', 'cache', ['src/cache.py'], baseline_json='["python", "baseline.py"]',
                            verify_json='["python", "verify.py", "$(never run)"]', output='.atr/tasks/cache/task.json')
    assert result['ready'] and not result['commands_executed']
    assert contract(Path(result['output']))['verify'][-1] == '$(never run)'
    with pytest.raises(ATRException, match='输出已存在'):
        draft_contract(project, 'Other', output='.atr/tasks/cache/task.json')
    with pytest.raises(ATRException):
        draft_contract(project, 'Scope', allowed=['../escape'])


def test_pytest_template_has_fixed_acceptance_outside_edit_scope(project):
    test = project / 'test_acceptance.py'
    test.write_text('def test_feature():\n    assert True\n')
    result = draft_contract(project, 'Implement feature', allowed=['src/feature.py'], template='pytest',
                            acceptance_tests=['test_acceptance.py'], output='.atr/tasks/feature/task.json')
    assert result['ready']
    data = contract(Path(result['output']))
    helper = Path(result['output']).parent / 'verify.py'
    completed = subprocess.run([sys.executable, str(helper)], cwd=project, capture_output=True)
    assert completed.returncode == 0 and json.loads(completed.stdout)['status'] == 'passed'
    test.write_text('def test_feature():\n    assert False\n')
    altered = subprocess.run([sys.executable, str(helper)], cwd=project, capture_output=True)
    assert altered.returncode == 2 and json.loads(altered.stdout)['status'] == 'error'
    assert data['verify'] == ['{python}', '{task_dir}/verify.py']
    with pytest.raises(ATRException, match='独立验收'):
        draft_contract(project, 'Unsafe', allowed=['test_acceptance.py'], template='pytest', acceptance_tests=['test_acceptance.py'])
    with pytest.raises(ATRException):
        draft_contract(project, 'Unsafe', allowed=['src/'], template='pytest', acceptance_tests=['missing.py'])


def test_json_verifier_reports_failure_and_collection_errors(project):
    test = project / 'test_acceptance.py'
    test.write_text('def test_feature():\n    assert False\n')
    result = draft_contract(project, 'feature', allowed=['src/'], template='pytest', acceptance_tests=['test_acceptance.py'],
                            output='.atr/tasks/failing/task.json')
    helper = Path(result['output']).parent / 'verify.py'
    completed = subprocess.run([sys.executable, str(helper)], cwd=project, capture_output=True)
    assert completed.returncode == 1 and json.loads(completed.stdout)['failure_id']
    test.write_text('INVALID SYNTAX')
    result = draft_contract(project, 'feature', allowed=['src/'], template='pytest', acceptance_tests=['test_acceptance.py'],
                            output='.atr/tasks/broken/task.json')
    broken = subprocess.run([sys.executable, str(Path(result['output']).parent / 'verify.py')], cwd=project, capture_output=True)
    assert broken.returncode == 2 and json.loads(broken.stdout)['status'] == 'error'


def test_generated_verifier_and_contract_transaction_roll_back_together(project):
    acceptance = project / 'test_acceptance.py'
    acceptance.write_text('def test_acceptance():\n    assert True\n')
    result = draft_contract(project, 'feature', allowed=['src/'], template='pytest', acceptance_tests=['test_acceptance.py'],
                            output='.atr/tasks/transaction/task.json')
    task = Path(result['output'])
    helper = task.parent / 'verify.py'
    assert '--ignore' in contract(task)['baseline']
    rollback(project)
    assert not task.exists() and not helper.exists()


def test_task_cli_creates_proposal_and_lists_templates(project, capsys):
    from atr.cli import main
    args = ['--project', str(project), 'task']
    assert main(args + ['templates']) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert main(args + ['draft', 'Add feature', '--allow', 'src/feature.py']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['missing'] == ['baseline', 'verify']
    assert not (project / '.atr').exists()
    assert main(args + ['draft', 'Add feature', '--baseline-json', '["python"', '--allow', 'src/']) == 2


def test_pytest_draft_without_output_is_explicitly_not_materialized(project):
    test = project / 'test_acceptance.py'
    test.write_text('def test_acceptance():\n    assert True\n')
    result = draft_contract(project, 'feature', allowed=['src/'], template='pytest', acceptance_tests=['test_acceptance.py'])
    assert not result['ready'] and 'output' in result['missing']
    assert not (project / '.atr').exists()


@pytest.mark.parametrize('output', ['.atr/state.json', '.atr/config.toml', '.git/config', '.codex/hooks.json', '.agents/task.json'])
def test_task_output_cannot_overwrite_control_metadata(project, output):
    with pytest.raises(ATRException):
        draft_contract(project, 'feature', output=output)
    assert not (project / '.atr/state.json').exists()
