from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess
import sys

import pytest

from atr.cli import main
from atr.core import ATRException
from atr.native import collector as c, manage as m
from atr.native.install import prepare_install, status
from atr.storage import apply


def add(root, kind, session='s', **values):
    return c.collect(root, dict(cwd=str(root), session_id=session, hook_event_name=kind, **values))


def age(root, session, days):
    conn = c.connect(root)
    try:
        for seq, data in conn.execute('SELECT seq,data FROM events WHERE session=?', (session,)).fetchall():
            event = json.loads(data)
            event['received_at'] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conn.execute('UPDATE events SET data=? WHERE seq=?', (json.dumps(event), seq))
        conn.commit()
    finally:
        conn.close()


def test_default_off_creates_nothing_even_on_invalid_input(project):
    assert c.settings(project)['mode'] == 'off'
    assert add(project, 'PostModelRequest', model='fixture', status='completed') == {}
    script = Path(c.__file__)
    result = subprocess.run([sys.executable, str(script), '--project', str(project)], input=b'INVALID', capture_output=True)
    assert result.returncode == 0 and result.stdout.strip() == b'{}' and not result.stderr
    assert not (project / '.atr').exists()
    assert status(project)['recording'] == 'off'
    assert not (project / '.atr').exists()


def test_install_defaults_off_reinstall_preserves_explicit_choice(project, fake_codex):
    assert main(['--project', str(project), 'init']) == 0
    changes, before = prepare_install(project)
    apply(project, changes, before)
    assert status(project)['mode'] == 'off'
    assert add(project, 'SessionStart') == {}
    assert not (project / '.atr/native/events.sqlite3').exists()
    assert main(['--project', str(project), 'native', 'on']) == 0
    assert c.settings(project)['mode'] == 'summary'
    changes, before = prepare_install(project)
    apply(project, changes, before)
    assert c.settings(project)['mode'] == 'summary'
    assert main(['--project', str(project), 'native', 'off']) == 0
    assert status(project)['recording'] == 'off'


def test_modes_summary_excludes_tools_and_digests_and_off_preserves_db(project):
    c.set_mode(project, 'summary')
    add(project, 'PreToolUse', tool_name='spawn_agent', tool_input={'message': 'private'})
    assert not (project / '.atr/native/events.sqlite3').exists()
    add(project, 'PostModelRequest', model='observed', status='failed', error='private')
    conn = c.connect(project)
    try:
        event = json.loads(conn.execute('SELECT data FROM events').fetchone()[0])
    finally:
        conn.close()
    assert event['error_present'] and 'error_sha256' not in event
    assert c.report(project, 's')['agents'][0]['models'] == ['observed']
    db = project / '.atr/native/events.sqlite3'
    before = db.read_bytes()
    c.set_mode(project, 'off')
    add(project, 'SessionEnd')
    assert db.read_bytes() == before
    c.set_mode(project, 'detailed')
    add(project, 'PreToolUse', tool_name='spawn_agent', tool_use_id='id', tool_input={'message': 'private'})
    assert c.report(project, 's')['event_count'] == 2


def test_health_distinguishes_stale_errors_and_missing_unused_events(project):
    c.set_mode(project, 'summary')
    assert m.health(project)['state'] == 'not_confirmed'
    add(project, 'PostModelRequest', model='observed', status='completed')
    assert m.health(project)['state'] == 'recent_events'
    assert 'SubagentStart' in m.health(project)['not_observed_recently']
    age(project, 's', 2)
    assert m.health(project)['state'] == 'stale'
    error = project / '.atr/native/last-error.json'
    error.write_text(json.dumps({'type': 'OSError', 'at': datetime.now(timezone.utc).isoformat()}))
    assert m.health(project)['state'] == 'error'
    add(project, 'Stop')
    assert m.health(project)['state'] == 'recent_events'
    c.set_mode(project, 'off')
    assert m.health(project)['state'] == 'off'


def test_queries_latest_current_turn_and_agent(project, monkeypatch):
    c.set_mode(project, 'detailed')
    add(project, 'UserPromptSubmit', session='old', turn_id='t1')
    add(project, 'SubagentStart', session='old', turn_id='t1', agent_id='child')
    add(project, 'PreModelRequest', session='child', turn_id='ct', model='child-model')
    add(project, 'PostModelRequest', session='child', turn_id='ct', model='child-model', status='completed')
    assert m.resolve(project, latest=True) == ('old', None)
    add(project, 'PreModelRequest', session='new', turn_id='t2', model='main-model')
    assert m.resolve(project, latest=True, turn='latest') == ('new', 't2')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    with pytest.raises(ATRException, match='ID'):
        m.resolve(project, current=True)
    monkeypatch.setenv('CODEX_THREAD_ID', 'old')
    result = m.query(project, current=True, agent='child')
    assert [a['agent_id'] for a in result['agents']] == ['child']
    assert result['agents'][0]['models'] == ['child-model']
    assert result['agent_filter'] == 'child'


def test_cleanup_preview_family_isolation_and_reclaimed_space(project):
    c.set_mode(project, 'detailed')
    add(project, 'SubagentStart', session='old', agent_id='child')
    add(project, 'PreModelRequest', session='child', model='fixture')
    add(project, 'PostModelRequest', session='child', model='fixture', status='completed')
    add(project, 'SubagentStop', session='old', agent_id='child')
    add(project, 'Stop', session='old')
    add(project, 'Stop', session='keep')
    age(project, 'old', 10)
    age(project, 'child', 10)
    age(project, 'keep', 1)
    c.set_mode(project, 'off')
    before = c.report(project, 'old')['event_count']
    preview = m.cleanup(project, days=5)
    assert preview['dry_run'] and set(preview['members']) == {'old', 'child'}
    assert c.report(project, 'old')['event_count'] == before
    result = m.cleanup(project, execute=True, days=5)
    assert not result['dry_run'] and result['events_to_delete'] == before
    assert c.report(project, 'old')['event_count'] == 0
    assert c.report(project, 'keep')['event_count'] == 1
    assert not c.location(project, 'old').exists() and not c.location(project, 'child').exists()
    assert c.settings(project)['mode'] == 'off'
    assert m.cleanup(project, execute=True, days=5)['events_to_delete'] == 0


def test_cleanup_capacity_keep_and_protection(project):
    c.set_mode(project, 'summary')
    add(project, 'Stop', session='old')
    age(project, 'old', 5)
    add(project, 'PreModelRequest', session='unfinished', model='fixture')
    age(project, 'unfinished', 5)
    add(project, 'Stop', session='recent')
    with pytest.raises(ATRException, match='off'):
        m.cleanup(project, execute=True, keep=0)
    c.set_mode(project, 'off')
    plan = m.cleanup(project, keep=0)
    assert plan['sessions'] == ['old']
    assert set(plan['protected_sessions']) == {'unfinished', 'recent'}
    assert set(m.cleanup(project, max_mib=0, include_incomplete=True)['sessions']) == {'old', 'unfinished'}
    for criteria in ({}, {'days': -1}, {'max_mib': float('nan')}, {'max_mib': float('inf')}):
        with pytest.raises(ATRException):
            m.cleanup(project, **criteria)


def test_cleanup_symlink_rejected_before_deleting_facts(project):
    c.set_mode(project, 'detailed')
    add(project, 'Stop', session='old')
    age(project, 'old', 10)
    folder = c.location(project, 'old')
    target = project / 'keep.txt'
    target.write_text('keep')
    try:
        (folder / 'trap').symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    c.set_mode(project, 'off')
    with pytest.raises(ValueError):
        m.cleanup(project, execute=True, days=1)
    assert c.report(project, 'old')['event_count'] == 1
    assert target.read_text() == 'keep'


def test_invalid_settings_never_enable_and_cli_queries(project, fake_codex, capsys):
    assert main(['--project', str(project), 'native', 'on']) == 2
    capsys.readouterr()
    c.set_mode(project, 'summary')
    add(project, 'Stop')
    args = ['--project', str(project), 'native']
    assert main(args + ['report', '--latest', '--turn', 'latest']) == 2  # no turn observed
    capsys.readouterr()
    assert main(args + ['report', '--latest', '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)['session_id'] == 's'
    assert main(args + ['health']) == 0
    capsys.readouterr()
    c.safe(project, c.SETTINGS).write_text('{"mode":"invalid"}')
    with pytest.raises(ValueError):
        add(project, 'Stop')


def test_disable_race_is_rechecked_inside_database_transaction(project, monkeypatch):
    c.set_mode(project, 'detailed')
    original = c.connect
    def toggle(root, create=False):
        connection = original(root, create)
        c.set_mode(root, 'off')
        return connection
    monkeypatch.setattr(c, 'connect', toggle)
    assert add(project, 'PostModelRequest', model='fixture', status='completed') == {}
    monkeypatch.setattr(c, 'connect', original)
    assert c.report(project, 's')['event_count'] == 0


def test_resumed_incomplete_family_not_deleted_and_maintenance_blocks_callbacks(project):
    c.set_mode(project, 'detailed')
    add(project, 'Stop')
    add(project, 'UserPromptSubmit', turn_id='next')
    add(project, 'SubagentStart', agent_id='child')
    age(project, 's', 5)
    c.set_mode(project, 'off')
    assert m.cleanup(project, keep=0)['protected_sessions'] == ['s']
    c.set_mode(project, 'detailed')
    with m.maintenance(project):
        assert add(project, 'Stop') == {}
    assert c.report(project, 's')['event_count'] == 3


def test_enabled_error_diagnostics_are_private_and_off_does_not_update_them(project):
    c.set_mode(project, 'summary')
    script = Path(c.__file__)
    result = subprocess.run([sys.executable, str(script), '--project', str(project)],
                            input=b'{"secret":"PRIVATE_PASSWORD"}', capture_output=True)
    error = project / '.atr/native/last-error.json'
    assert result.returncode == 0 and error.exists()
    original = error.read_bytes()
    assert b'PRIVATE' not in original and b'PRIVATE' not in result.stderr
    c.set_mode(project, 'off')
    subprocess.run([sys.executable, str(script), '--project', str(project)], input=b'bad', capture_output=True)
    assert error.read_bytes() == original


def test_capacity_cleanup_reclaims_authoritative_and_derived_storage(project):
    c.set_mode(project, 'detailed')
    for ident in ('one', 'two'):
        add(project, 'Stop', session=ident)
        age(project, ident, 5)
    c.set_mode(project, 'off')
    result = m.cleanup(project, execute=True, max_mib=0)
    assert set(result['sessions']) == {'one', 'two'}
    assert result['actual_reclaim_bytes'] > 0
    assert not c.sessions(project)
    assert c.safe(project, c.SETTINGS).exists()


def test_retention_preview_keeps_facts_on_export_path_failure(project, monkeypatch):
    c.set_mode(project, 'detailed')
    add(project, 'Stop')
    age(project, 's', 5)
    c.set_mode(project, 'off')
    original = m.report_bytes
    def fail(*args):
        raise OSError('simulated report validation failure')
    monkeypatch.setattr(m, 'report_bytes', fail)
    with pytest.raises(OSError):
        m.cleanup(project, execute=True, keep=0)
    monkeypatch.setattr(m, 'report_bytes', original)
    assert c.report(project, 's')['event_count'] == 1
    assert not (project / '.atr/native/maintenance').exists()


def test_downgrade_to_summary_is_rechecked_before_storing_tool_data(project, monkeypatch):
    c.set_mode(project, 'detailed')
    original = c.connect
    def downgrade(root, create=False):
        connection = original(root, create)
        c.set_mode(root, 'summary')
        return connection
    monkeypatch.setattr(c, 'connect', downgrade)
    add(project, 'PreToolUse', tool_name='spawn_agent', tool_input={'message': 'private'})
    monkeypatch.setattr(c, 'connect', original)
    assert c.report(project, 's')['event_count'] == 0


def test_transient_database_sidecar_does_not_require_leaf_realpath(project, monkeypatch):
    folder = project / '.atr/native'
    folder.mkdir(parents=True)
    journal = folder / 'events.sqlite3-journal'
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == journal:
            raise OSError('transient Win32 file sharing')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    assert c.safe(project, '.atr/native/events.sqlite3-journal') == journal


def test_native_directory_junction_cannot_escape(project, tmp_path_factory):
    import os
    if os.name != 'nt':
        pytest.skip('Windows directory junction behavior')
    outside = tmp_path_factory.mktemp('native-junction-outside')
    (project / '.atr').mkdir()
    junction = project / '.atr/native'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError):
            c.safe(project, '.atr/native/events.sqlite3')
        assert not list(outside.iterdir())
    finally:
        junction.rmdir()
