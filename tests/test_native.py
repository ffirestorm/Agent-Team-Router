"""Protocol fixtures are synthetic; native discovery is separately probed without inference."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from atr.cli import main
from atr.core import ATRException
from atr.native import collector as c
from atr.native.install import HOOKS, LAUNCHER, MANIFEST, command, prepare_install, prepare_uninstall, status
from atr.service import prepare
from atr.storage import apply, read, rollback


def event(root, kind, session='parent', turn='t1', **fields):
    payload = dict(cwd=str(root), hook_event_name=kind, session_id=session, turn_id=turn)
    payload.update(fields)
    return c.collect(root, payload)


def test_native_facts_plans_privacy_and_stop(project):
    response = event(project, 'UserPromptSubmit', prompt='PRIVATE FULL PROMPT')
    assert 'additionalContext' in response['hookSpecificOutput']
    event(project, 'PreToolUse', tool_name='functions.spawn_agent', tool_use_id='call-1',
          tool_input={'agent_type': 'atr_worker', 'model': 'intended-model', 'message': 'PRIVATE TASK', 'description': 'implement cache'})
    event(project, 'SubagentStart', agent_id='child', agent_type='atr_worker', model='start-only-model')
    event(project, 'PostToolUse', tool_name='functions.spawn_agent', tool_use_id='call-1',
          tool_response={'content': [{'type': 'text', 'text': '{"agent_id":"child"}'}]})
    c.note(project, 'parent', 't1', 'child', 'atr_worker', 'cache API', 'another-intended-model')
    event(project, 'PreModelRequest', session='child', turn='child-turn', model='observed-model')
    event(project, 'PostModelRequest', session='child', turn='child-turn', model='observed-model', status='completed',
          token_usage={'input_tokens': 20, 'output_tokens': 4, 'cached_input_tokens': 3, 'secret': 'PRIVATE TOKEN'},
          error='PRIVATE ERROR AND URL')
    event(project, 'SubagentStop', agent_id='child')
    result = c.report(project, 'parent', 't1')
    agent = next(a for a in result['agents'] if a['agent_id'] == 'child')
    assert agent['models'] == ['observed-model']
    assert agent['start_model'] == 'start-only-model'
    assert agent['requested_model'] == 'another-intended-model'
    assert agent['task'] == 'cache API' and agent['task_source'] == 'host-declared'
    assert agent['execution_status'] == 'stopped'
    assert agent['verification_status'] == 'not_observed' and result['cost'] is None
    assert agent['request_outcomes'] == {'completed': 1}
    assert agent['known_usage'] == {'input_tokens': 20, 'output_tokens': 4, 'cached_input_tokens': 3}
    connection = c.connect(project)
    try:
        saved = '\n'.join(row[0] for row in connection.execute('SELECT data FROM events'))
    finally:
        connection.close()
    assert 'PRIVATE' not in saved
    assert 'error_sha256' in saved and 'task_sha256' in saved
    summary = c.location(project, 'parent', 't1') / 'summary.json'
    assert json.loads(summary.read_text()) == result
    assert 'observed-model' in c.markdown(result)


def test_turn_session_isolation_and_retries(project):
    for turn, child in [('t1', 'first'), ('t2', 'second')]:
        event(project, 'SubagentStart', turn=turn, agent_id=child)
        event(project, 'PreModelRequest', session=child, turn='private-turn', model=child)
        event(project, 'PostModelRequest', session=child, turn='private-turn', model=child, status='failed')
    # Identical retries must remain distinct attempts, never deduplicated by payload.
    for _ in range(2):
        event(project, 'PreModelRequest', session='first', turn='private-turn', model='first')
        event(project, 'PostModelRequest', session='first', turn='private-turn', model='first', status='completed')
    event(project, 'PreModelRequest', session='other-project-session', model='unrelated')
    result = c.report(project, 'parent', 't1')
    assert [a['agent_id'] for a in result['agents']] == ['first']
    assert result['agents'][0]['requests_finished'] == 3
    assert result['agents'][0]['usage_missing_requests'] == 3
    assert result['agents'][0]['request_outcomes'] == {'failed': 1, 'completed': 2}
    assert all('second' not in json.dumps(a) for a in result['agents'])
    assert c.report(project, 'parent', 't2')['agents'][0]['models'] == ['second']


def test_no_events_no_false_active_and_fail_open(project):
    empty = c.report(project, 'missing')
    assert empty['warnings'] and empty['event_count'] == 0 and empty['cost'] is None
    assert not (project / '.atr').exists()
    script = Path(c.__file__)
    for payload in [b'not json', b'{}', b'x' * (c.MAX_INPUT + 1)]:
        result = subprocess.run([sys.executable, str(script), '--project', str(project)],
                                input=payload, capture_output=True)
        assert result.returncode == 0 and json.loads(result.stdout) == {}
        assert b'not json' not in result.stderr
    assert status(project)['recording'] == 'not_confirmed'


def test_native_concurrent_subprocesses(project):
    script = Path(c.__file__)
    def write(index):
        payload = {'cwd': str(project), 'session_id': 'concurrent', 'turn_id': 'turn',
                   'hook_event_name': 'PostModelRequest', 'model': 'fixture', 'status': 'completed',
                   'token_usage': {'input_tokens': 1, 'output_tokens': 2}}
        return subprocess.run([sys.executable, str(script), '--project', str(project)],
                              input=json.dumps(payload).encode(), capture_output=True, timeout=15)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(write, range(18)))
    failures = [(r.returncode, r.stderr.decode('utf-8', errors='replace')) for r in results if r.returncode or r.stderr]
    assert not failures, failures
    result = c.report(project, 'concurrent')
    assert result['event_count'] == 18
    assert result['agents'][0]['known_usage']['input_tokens'] == 18
    exported = json.loads((c.location(project, 'concurrent') / 'summary.json').read_text())
    assert exported == result


def test_paths_disabled_ignored_tool_and_interruption(project, tmp_path):
    with pytest.raises(ValueError):
        c.safe(project, '../outside')
    with pytest.raises(ValueError):
        c.safe(project, 'C:\\outside')
    with pytest.raises(ValueError):
        c.collect(project, {'cwd': str(project.parent), 'hook_event_name': 'Stop', 'session_id': 's'})
    assert event(project, 'PreToolUse', tool_name='exec_command', tool_input={'cmd': 'PRIVATE'}) == {}
    assert not (project / '.atr').exists()
    event(project, 'PreModelRequest', model='main-model')
    event(project, 'Interrupt')
    result = c.report(project, 'parent')
    assert result['agents'][0]['execution_status'] == 'interrupted'
    assert result['warnings']
    (project / '.atr/native/disabled').touch()
    assert event(project, 'Stop') == {}
    assert c.report(project, 'parent')['event_count'] == 2


def test_database_symlink_rejected(project):
    folder = project / '.atr/native'
    folder.mkdir(parents=True)
    target = project / 'outside.sqlite3'
    target.write_bytes(b'untouched')
    try:
        (folder / 'events.sqlite3').symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError):
        event(project, 'Stop')
    assert target.read_bytes() == b'untouched'


def setup(project, fake_codex):
    assert main(['--project', str(project), 'init']) == 0


def test_install_lifecycle_rollback_preservation(project, fake_codex, capsys):
    setup(project, fake_codex)
    custom = b'{"description":"user hooks","hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo user"}]}]}}'
    (project / '.codex').mkdir()
    (project / HOOKS).write_bytes(custom)
    changes, before = prepare_install(project)
    assert not (project / LAUNCHER).exists()  # preview is read-only
    apply(project, changes, before)
    installed = read(project, HOOKS)
    assert json.loads(installed)['hooks']['Stop'][0]['hooks'][0]['command'] == 'echo user'
    assert status(project)['recording'] == 'not_confirmed'
    assert 'ATR native observation' in read(project, 'AGENTS.md').decode()
    changes, before = prepare_install(project)
    assert '无需修改' in apply(project, changes, before)
    changes, before = prepare(project)
    assert changes['AGENTS.md'] == read(project, 'AGENTS.md')
    # General reapply never loses the instructions.
    apply(project, changes, before)
    event(project, 'Stop')
    changes, before = prepare_uninstall(project)
    apply(project, changes, before)
    assert read(project, HOOKS) == custom
    assert read(project, LAUNCHER) is None and read(project, MANIFEST) is None
    assert 'ATR native observation' not in read(project, 'AGENTS.md').decode()
    assert c.report(project, 'parent')['event_count'] == 1
    assert (project / '.agents/skills/agent-team-router/SKILL.md').is_file()
    rollback(project)
    assert read(project, HOOKS) == installed and read(project, LAUNCHER)
    assert status(project)['installed']


def test_installed_standalone_without_atr_import(project, fake_codex):
    setup(project, fake_codex)
    changes, before = prepare_install(project)
    apply(project, changes, before)
    launcher = project / LAUNCHER
    result = subprocess.run([sys.executable, '-I', str(launcher), '--project', str(project)],
                            input=json.dumps({'cwd': str(project), 'session_id': 's', 'hook_event_name': 'SessionStart'}).encode(),
                            capture_output=True)
    assert not result.stderr and result.returncode == 0
    assert 'Session s' in json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    result = subprocess.run([sys.executable, '-I', str(launcher), '--project', str(project), '--report', '--session', 's'], capture_output=True)
    assert result.returncode == 0 and 'ATR'.encode() in result.stdout


def test_cli_native_notes_report_and_dry_run(project, fake_codex, capsys):
    setup(project, fake_codex)
    prefix = ['--project', str(project), 'native']
    assert main(prefix + ['install', '--dry-run']) == 0
    capsys.readouterr()
    assert read(project, MANIFEST) is None
    assert main(prefix + ['install']) == 0
    capsys.readouterr()
    assert main(prefix + ['note', '--session', 's', '--agent-id', 'a', '--role', 'r', '--task', 'task', '--model', 'planned']) == 0
    capsys.readouterr()
    assert main(prefix + ['report', '--session', 's', '--format', 'json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report['agents'] and report['declared_plans'][0]['requested_model'] == 'planned'
    assert main(prefix + ['sessions']) == 0
    assert json.loads(capsys.readouterr().out)[0]['session_id'] == 's'
    assert main(prefix + ['uninstall']) == 0
    assert read(project, HOOKS) is None


def test_native_install_conflicts(project, fake_codex):
    setup(project, fake_codex)
    (project / '.codex').mkdir()
    (project / LAUNCHER).write_text('user script')
    with pytest.raises(ATRException, match='未托管'):
        prepare_install(project)
    (project / LAUNCHER).unlink()
    (project / '.codex/config.toml').write_text('[hooks]\n')
    with pytest.raises(ATRException, match='inline'):
        prepare_install(project)
    (project / '.codex/config.toml').unlink()
    changes, before = prepare_install(project)
    apply(project, changes, before)
    (project / HOOKS).write_text('{}')
    with pytest.raises(ATRException, match='冲突'):
        prepare_uninstall(project)


def test_shell_command_quoting():
    assert command(['a b', "c'd", '$(unsafe)'], windows=False) == "'a b' 'c'\"'\"'d' '$(unsafe)'"
    assert command(['C:\\Program Files\\python.exe', 'C:\\ATR\\hook.py'], windows=True).startswith('"C:\\Program Files')
    for name in ['C:\\%PATH%\\hook', 'C:\\a&b', 'C:\\a!b']:
        with pytest.raises(ATRException):
            command([name], windows=True)


def test_event_survives_export_failure(project, monkeypatch):
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(c, 'export', fail)
    with pytest.raises(OSError):
        event(project, 'PostModelRequest', status='failed')
    assert c.report(project, 'parent')['event_count'] == 1


def test_child_resume_never_leaks_later_turn_into_earlier_report(project):
    event(project, 'UserPromptSubmit')
    event(project, 'SubagentStart', agent_id='child')
    event(project, 'PreModelRequest', session='child', turn='ct1', model='first-model')
    event(project, 'PostModelRequest', session='child', turn='ct1', model='first-model', status='completed')
    event(project, 'UserPromptSubmit', turn='t2')
    c.note(project, 'parent', 't2', 'child', 'worker', 'follow-up')
    event(project, 'PreModelRequest', session='child', turn='ct2', model='second-model')
    event(project, 'PostModelRequest', session='child', turn='ct2', model='second-model', status='completed')
    assert c.report(project, 'parent', 't1')['agents'][0]['models'] == ['first-model']
    assert c.report(project, 'parent', 't2')['agents'][0]['models'] == ['second-model']
    assert c.report(project, 'parent')['agents'][0]['models'] == ['first-model', 'second-model']


def test_main_id_usage_validation_and_start_only_model(project):
    event(project, 'PreModelRequest', agent_id='parent', model='main-model')
    event(project, 'PostModelRequest', agent_id='parent', status='completed', model='main-model',
          token_usage={'input_tokens': True, 'output_tokens': -1, 'cached_input_tokens': 0})
    event(project, 'Stop')
    event(project, 'SubagentStart', agent_id='child', model='planned-at-start')
    result = c.report(project, 'parent')
    assert [a['agent_id'] for a in result['agents']] == ['main', 'child']
    assert result['agents'][0]['execution_status'] == 'stopped'
    assert result['agents'][0]['usage_missing_fields']['input_tokens'] == 1
    assert result['agents'][1]['models'] == []
    assert any('no request model observed' in warning for warning in result['warnings'])


def test_installation_transaction_rollback_exact_original(project, fake_codex):
    setup(project, fake_codex)
    changes, before = prepare_install(project)
    apply(project, changes, before)
    rollback(project)
    assert read(project, HOOKS) is None
    assert read(project, LAUNCHER) is None
    assert read(project, MANIFEST) is None
    assert read(project, 'AGENTS.md') is None
    assert read(project, '.atr/config.toml')


def test_normalization_unsupported_and_unsafe_metadata():
    with pytest.raises(ValueError):
        c.normalize({'hook_event_name': 'Unsupported', 'session_id': 's'})
    with pytest.raises(ValueError):
        c.normalize({'hook_event_name': 'Stop', 'session_id': '../../outside'})
    clean = c.normalize({'hook_event_name': 'ATRPlan', 'session_id': 's', 'task': 'token=PRIVATE',
                         'model': 'sk-abcdefghijklmnopqrstuv'})
    assert 'PRIVATE' not in clean['task']
    assert clean['requested_model'] == '[redacted]'


def test_manual_notes_do_not_claim_hook_recording(project):
    c.note(project, 's', 't', 'a', 'worker', 'declared task')
    result = c.report(project, 's')
    assert result['hook_event_count'] == 0 and result['warnings']
    assert status(project)['recording'] == 'not_confirmed'
