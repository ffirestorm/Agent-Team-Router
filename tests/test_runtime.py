import json
from pathlib import Path
import subprocess
import sys

import pytest

from atr.cli import main
from atr.config import initialize
from atr.core import ATRException
from atr.runtime.codex import Execution, classify_error, parse_events
from atr.runtime.policy import Limits, decide, limits_from
from atr.runtime.process import run_process
from atr.runtime.records import all_records, recover_run, report, request_cancel, show
from atr.runtime.runner import execute, preview
from atr.runtime.tasks import command, contract, patch, source_snapshot


@pytest.fixture
def task(project, fake_codex):
    subprocess.run(['git', 'init', str(project)], check=True, capture_output=True)
    (project / '.gitignore').write_text('.atr/\n__pycache__/\n', encoding='utf-8')
    (project / 'existing.txt').write_text('user changes', encoding='utf-8')
    verifier = project / 'verify.py'
    verifier.write_text('''import json
from pathlib import Path
import sys
ok = Path("feature.py").exists() and Path("feature.py").read_text() == "good\\n"
print(json.dumps({"status": "passed" if ok else "failed", "reason": "feature contract", "failure_id": "feature-behavior"}))
sys.exit(0 if ok else 1)
''', encoding='utf-8')
    data = {'id': 'feature', 'description': 'Implement a feature', 'allowed_paths': ['feature.py'],
            'baseline': ['{python}', '-c', 'print("baseline ok")'],
            'verify': ['{python}', '{task_dir}/verify.py']}
    path = project / 'task.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    initialize(project, 'development', 'codex', None, None)
    return path


class FakeExecutor:
    def __init__(self, success_at=1, failure='', unexpected=False, cancel=False, usage=True):
        self.calls = []
        self.success_at, self.failure, self.unexpected = success_at, failure, unexpected
        self.cancel, self.usage = cancel, usage

    def preflight(self):
        pass

    def execute(self, workspace, model, effort, prompt, artifacts, timeout, cancel):
        self.calls.append((model, effort, prompt))
        if self.cancel:
            cancel.write_bytes(b'cancel')
            return Execution('cancelled', 'cancelled', {})
        if self.failure:
            return Execution('failed', self.failure, {})
        (workspace / 'feature.py').write_text('good\n' if len(self.calls) >= self.success_at else 'bad\n', encoding='utf-8')
        if self.unexpected:
            (workspace / 'existing.txt').write_text('outside scope', encoding='utf-8')
        usage = {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 0, 'cost': None} if self.usage else {}
        return Execution('completed', '', usage, seconds=0.01)


def planned(project, task, **overrides):
    result = preview(project, task, 'gpt-6.1-terra', 'medium', allow_escalation=True)
    result['limits'].update(overrides)
    return result


def test_real_task_snapshot_success_without_touching_source(project, task):
    executor = FakeExecutor()
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'succeeded'
    assert record['tokens'] == 15
    assert not (project / 'feature.py').exists()
    assert (project / 'existing.txt').read_text() == 'user changes'
    folder = project / '.atr/runs' / record['id']
    assert '+good' in (folder / 'candidate.patch').read_text()
    assert record['attempts'][0]['regression']['code'] == 0
    assert report(project)['first_success_rate'] == 1
    assert report(project)['cost'] is None
    assert show(project, record['id']) == record


def test_repair_then_escalate_then_success(project, task):
    executor = FakeExecutor(success_at=3)
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'succeeded'
    assert [call[0] for call in executor.calls] == ['gpt-6.1-terra', 'gpt-6.1-terra', 'gpt-6.1-sol']
    assert [a['kind'] for a in record['attempts']] == ['initial', 'repair', 'escalation']
    assert 'Previous attempt failed' in executor.calls[-1][2]
    assert report(project)['first_success_rate'] == 0
    assert report(project)['upgrade_rescue_rate'] == 1
    assert record['tokens'] == 45


def test_same_model_repair_success_does_not_escalate(project, task):
    executor = FakeExecutor(success_at=2)
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'succeeded'
    assert len(executor.calls) == 2
    assert len(set(call[0] for call in executor.calls)) == 1
    assert report(project)['upgrades_started'] == 0


def test_failed_upgrade_stops_after_one_escalation(project, task):
    executor = FakeExecutor(success_at=99)
    record = execute(project, planned(project, task), executor)
    assert record['status'] in ('failed', 'budget_exceeded')
    assert len(executor.calls) == 3
    assert report(project)['upgrade_rescue_rate'] == 0


@pytest.mark.parametrize('failure', ['auth', 'quota', 'network', 'environment', 'unknown', 'timeout'])
def test_non_quality_failures_never_upgrade(project, task, failure):
    executor = FakeExecutor(failure=failure)
    record = execute(project, planned(project, task), executor)
    assert len(executor.calls) == 1
    assert record['status'] != 'succeeded'
    assert record['attempts'][0]['decision']['action'] == 'stop'


def test_disabled_escalation(project, task):
    executor = FakeExecutor(success_at=99)
    record = execute(project, planned(project, task, allow_escalation=False, same_model_retries=0), executor)
    assert len(executor.calls) == 1
    assert record['reason'] == 'automatic_escalation_disabled'


@pytest.mark.parametrize('settings', [{'max_attempts': 1}, {'max_tokens': 10}, {'max_tokens': 100, 'usage': False}])
def test_attempt_and_observable_token_limits(project, task, settings):
    settings = dict(settings)
    executor = FakeExecutor(success_at=99, usage=settings.pop('usage', True))
    record = execute(project, planned(project, task, **settings), executor)
    assert len(executor.calls) == 1
    assert record['status'] == 'budget_exceeded'


def test_cancel_stops_before_upgrade(project, task):
    executor = FakeExecutor(cancel=True)
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'cancelled'
    assert len(executor.calls) == 1
    assert not request_cancel(project, record['id'])['cancel_requested']
    assert report(project)['end_to_end_success_rate'] == 0


def test_scope_violation_never_merges(project, task):
    executor = FakeExecutor(unexpected=True)
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'needs_attention'
    assert len(executor.calls) == 1
    assert (project / 'existing.txt').read_text() == 'user changes'


def test_unhealthy_baseline_prevents_inference(project, task):
    plan = planned(project, task)
    plan['task']['baseline'] = [sys.executable, '-c', 'raise SystemExit(1)']
    executor = FakeExecutor()
    record = execute(project, plan, executor)
    assert record['status'] == 'needs_attention'
    assert not executor.calls


def test_verifier_protocol_error_prevents_inference(project, task):
    (project / 'verify.py').write_text('print("not json")', encoding='utf-8')
    executor = FakeExecutor()
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'needs_attention'
    assert not executor.calls


def test_flaky_verifier_cannot_trigger_upgrade(project, task, monkeypatch):
    checks = iter([{'status': 'failed', 'reason': 'missing', 'failure_id': 'one'},
                   {'status': 'failed', 'reason': 'wrong', 'failure_id': 'one'},
                   {'status': 'passed', 'reason': 'flaky'}])
    monkeypatch.setattr('atr.runtime.runner.verification', lambda *a, **k: next(checks))
    executor = FakeExecutor(success_at=99)
    record = execute(project, planned(project, task), executor)
    assert len(executor.calls) == 1
    assert record['attempts'][0]['failure'] == 'unknown'


def test_source_changes_are_not_discarded(project, task):
    class Concurrent(FakeExecutor):
        def execute(self, *args):
            result = super().execute(*args)
            (project / 'existing.txt').write_text('new user edit', encoding='utf-8')
            return result
    record = execute(project, planned(project, task), Concurrent())
    assert record['status'] == 'needs_attention'
    assert record['reason'] == 'source_project_changed_during_execution'
    assert (project / 'existing.txt').read_text() == 'new user edit'


def test_cli_preview_is_nonexecuting_and_records_queryable(project, task, capsys):
    assert main(['--project', str(project), 'run', str(task), '--model', 'gpt-6.1-terra']) == 0
    assert json.loads(capsys.readouterr().out)['dry_run']
    assert all_records(project) == []
    assert main(['--project', str(project), 'runs', 'report']) == 0
    assert json.loads(capsys.readouterr().out)['started_tasks'] == 0


def test_recovery_never_reexecutes(project, task):
    record = execute(project, planned(project, task), FakeExecutor())
    folder = project / '.atr/runs' / record['id']
    record['status'] = 'running'
    record['attempts'][0]['status'] = 'running'
    (folder / 'record.json').write_text(json.dumps(record), encoding='utf-8')
    restored = recover_run(project, record['id'])
    assert restored['status'] == 'needs_attention'
    assert restored['attempts'][0]['status'] == 'interrupted'
    assert len(restored['attempts']) == 1


@pytest.mark.parametrize('data', [{'same_model_retries': True}, {'max_attempts': 4}, {'max_cost': 1}, {'max_tokens': -1}, {'allow_escalation': 'yes'}])
def test_invalid_or_unenforceable_limits_rejected(data):
    with pytest.raises(ATRException):
        limits_from(data)


@pytest.mark.parametrize('error,category', [('401 Unauthorized', 'auth'), ('insufficient_quota', 'quota'),
                                          ('connection reset', 'network'), ('sandbox permission denied', 'environment'),
                                          ('unexplained error', 'unknown')])
def test_terminal_error_categories(error, category):
    stream = json.dumps({'type': 'turn.failed', 'error': {'message': error}})
    assert parse_events(stream, '', 1).failure == category


def test_json_events_account_usage_without_double_counting_cache():
    stream = '\n'.join([json.dumps({'type': 'item.completed', 'item': {'text': 'auth quota network'}}),
                        json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20}})])
    result = parse_events(stream, '', 0)
    assert result.status == 'completed'
    assert result.usage['input_tokens'] == 100
    assert result.usage['cost'] is None
    assert parse_events(stream + '\nbad', '', 0).status == 'failed'
    assert parse_events('', 'just a nonzero exit', 1).failure == 'unknown'


def test_process_timeout_and_preemptive_cancel(tmp_path):
    cancel = tmp_path / 'cancel'
    result = run_process([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path,
                         tmp_path / 'stdout', tmp_path / 'stderr', 0.1, cancel)
    assert result.status == 'timeout' and result.seconds < 5
    cancel.write_bytes(b'cancel')
    result = run_process([sys.executable, '-c', 'raise RuntimeError()'], tmp_path,
                         tmp_path / 'out2', tmp_path / 'err2', 5, cancel)
    assert result.status == 'cancelled'
    assert not (tmp_path / 'out2').exists()


def test_patch_applies_new_and_unterminated_files(tmp_path):
    diff = patch({'old.txt': b'old'}, {'old.txt': b'new', 'feature.py': b'good\n'})
    (tmp_path / 'old.txt').write_bytes(b'old')
    # Match the binary patch artifact: text-mode pipes translate LF to CRLF on Windows.
    result = subprocess.run(['git', 'apply', '--check', '-'], cwd=tmp_path, input=diff.encode('utf-8'), capture_output=True)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')


def test_transient_reconnect_does_not_invalidate_completed_turn():
    events = '\n'.join([json.dumps({'type': 'error', 'message': 'Reconnecting: request timed out'}),
                        json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 3, 'output_tokens': 2}})])
    assert parse_events(events, '', 0).status == 'completed'
    assert classify_error('request timed out') == 'network'


def test_native_transport_preserves_http_selection_without_other_config(tmp_path, monkeypatch):
    from atr.runtime.codex import transport_overrides
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path / 'config.toml').write_text('''model_provider = "custom_http"
+[model_providers.custom_http]
+name = "HTTP transport"
+base_url = "https://example.invalid/responses"
+wire_api = "responses"
+requires_openai_auth = true
+supports_websockets = false
+[hooks]
+'''.replace('\n+', '\n'), encoding='utf-8')
    result = transport_overrides()
    assert 'model_provider="custom_http"' in result
    assert any('supports_websockets=false' in value for value in result)
    assert any(value.startswith('model_providers.custom_http.base_url=') for value in result)
    assert not any('hooks' in value for value in result)


@pytest.mark.parametrize('provider', ['base_url="https://example.invalid?token=private"', 'http_headers={Authorization="private"}'])
def test_transport_refuses_credential_forwarding(tmp_path, monkeypatch, provider):
    from atr.runtime.codex import transport_overrides
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path / 'config.toml').write_text('model_provider="custom"\n[model_providers.custom]\n'+provider, encoding='utf-8')
    with pytest.raises(ATRException):
        transport_overrides()


def test_initial_verifier_must_not_mutate_candidate(project, task):
    (project / 'verify.py').write_text('''from pathlib import Path
import json
Path("feature.py").write_text("good\\n")
print(json.dumps({"status":"passed","reason":"mutating verifier"}))
''', encoding='utf-8')
    executor = FakeExecutor()
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'needs_attention'
    assert record['reason'] == 'initial_verifier_modified_workspace'
    assert not executor.calls


def test_regression_failure_is_not_counted_as_first_success(project, task):
    plan = planned(project, task)
    plan['task']['baseline'] = [sys.executable, '-c', 'from pathlib import Path; raise SystemExit(1 if Path("feature.py").exists() else 0)']
    record = execute(project, plan, FakeExecutor())
    assert record['status'] == 'needs_attention'
    assert report(project)['first_success_rate'] == 0
    assert report(project)['end_to_end_success_rate'] == 0


def test_recovery_refuses_live_recorded_process(project, task):
    import os
    record = execute(project, planned(project, task), FakeExecutor())
    folder = project / '.atr/runs' / record['id']
    record['status'] = 'running'
    (folder / 'record.json').write_text(json.dumps(record), encoding='utf-8')
    (folder / 'live.pid').write_text(str(os.getpid()), encoding='ascii')
    with pytest.raises(ATRException, match='仍存在'):
        recover_run(project, record['id'])
    assert show(project, record['id'])['status'] == 'running'


def test_three_level_cli_plan_and_default_run_preview(project, task, capsys):
    assert main(['--project', str(project), 'plan', 'fix typo', '--complexity', 'complex']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['complexity'] == 'high'
    assert result['initial_execution']['model'] == 'gpt-6.1-sol'
    assert main(['--project', str(project), 'run', str(task)]) == 0
    assert json.loads(capsys.readouterr().out)['initial']['model'] == 'gpt-6.1-terra'
    assert all_records(project) == []


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process group termination')
def test_timeout_kills_term_ignoring_descendant(tmp_path):
    import time
    child = 'import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(0.8); Path("orphan-marker").write_text("bad")'
    parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(child)+']); time.sleep(10)'
    result = run_process([sys.executable, '-c', parent], tmp_path, tmp_path/'out', tmp_path/'err', 0.3, tmp_path/'cancel')
    assert result.status == 'timeout'
    time.sleep(0.9)
    assert not (tmp_path/'orphan-marker').exists()


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX executable permissions')
def test_permission_changes_cannot_be_accepted_as_text_patch(project, task):
    class Chmod(FakeExecutor):
        def execute(self, *args):
            result = super().execute(*args)
            (args[0] / 'feature.py').chmod(0o755)
            return result
    record = execute(project, planned(project, task), Chmod())
    assert record['status'] == 'needs_attention'
    assert record['reason'] == 'candidate_permission_changes_not_supported'
    assert report(project)['first_success_rate'] == 0


def test_task_or_verifier_change_after_preview_requires_new_plan(project, task):
    plan = planned(project, task)
    (project / 'verify.py').write_text('print("changed verifier")', encoding='utf-8')
    executor = FakeExecutor()
    with pytest.raises(ATRException, match='固定验证文件已改变'):
        execute(project, plan, executor)
    assert not executor.calls


def test_unhealthy_baseline_remains_in_success_denominator(project, task):
    healthy = planned(project, task)
    bad = planned(project, task)
    bad['task']['baseline'] = [sys.executable, '-c', 'raise SystemExit(1)']
    execute(project, bad, FakeExecutor())
    execute(project, healthy, FakeExecutor())
    summary = report(project)
    assert summary['started_tasks'] == 2
    assert summary['model_tasks'] == 1
    assert summary['first_success_rate'] == 0.5
    assert summary['end_to_end_success_rate'] == 0.5


def test_already_satisfied_is_not_a_model_success(project, task):
    (project/'feature.py').write_text('good\n', encoding='utf-8')
    executor = FakeExecutor()
    record = execute(project, planned(project, task), executor)
    assert record['status'] == 'succeeded' and not executor.calls
    summary = report(project)
    assert summary['already_satisfied_without_inference'] == 1
    assert summary['model_tasks'] == 0
    assert summary['first_success_rate'] is None


def test_disabled_upgrade_does_not_require_target_effort(project, task):
    from dataclasses import replace
    from atr.config import load
    doc, profile, rules, models = load(project)
    doc['catalog']['gpt-6.1-sol']['efforts'] = ['low']
    import tomlkit
    (project/'.atr/config.toml').write_text(tomlkit.dumps(doc), encoding='utf-8')
    result = preview(project, task, 'gpt-6.1-terra', 'medium')
    assert result['upgrade'] is None
    with pytest.raises(ATRException, match='升级模型不支持'):
        preview(project, task, 'gpt-6.1-terra', 'medium', allow_escalation=True)
