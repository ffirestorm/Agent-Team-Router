"""Read-only diagnostics and opt-in retention for native observations."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import os
import math
from pathlib import Path
import shutil

from atr.core import ATRException
from atr.native import collector as c
from atr.storage import locked


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def inventory(root):
    connection = c.connect(root)
    if connection is None:
        return []
    try:
        events = [dict(json.loads(data), sequence=seq, bytes=len(data.encode())) for seq, data in
                  connection.execute('SELECT seq,data FROM events ORDER BY seq')]
    finally:
        connection.close()
    # Undirected components retain parent/descendant histories together.
    components = {}
    for event in events:
        session = event['session_id']
        components.setdefault(session, {session})
        child = event.get('agent_id') if event['event'] == 'SubagentStart' else None
        if child:
            components.setdefault(child, {child})
            union = components[session] | components[child]
            for ident in union:
                components[ident] = union
    groups = {}
    for event in events:
        key = tuple(sorted(components[event['session_id']]))
        groups.setdefault(key, []).append(event)
    result = []
    for members, rows in groups.items():
        parents = {e.get('agent_id') for e in rows if e['event'] == 'SubagentStart'}
        roots = set(members) - parents
        session = min(roots or set(members))
        times = [timestamp(e.get('received_at')) for e in rows]
        last = max((t for t in times if t is not None), default=None)
        terminal = max((e['sequence'] for e in rows if e['session_id'] == session and e['event'] in ('Stop', 'SessionEnd', 'Interrupt')), default=0)
        work_started = max((e['sequence'] for e in rows if e['event'] in ('UserPromptSubmit', 'PreModelRequest', 'SubagentStart')), default=0)
        child_states = {}
        for event in rows:
            if event['event'] in ('SubagentStart', 'SubagentStop'):
                child_states[event.get('agent_id')] = event['event']
        started = sum(e['event'] == 'PreModelRequest' for e in rows)
        finished = sum(e['event'] == 'PostModelRequest' for e in rows)
        result.append({'session_id': session, 'members': list(members), 'events': len(rows),
                       'last_sequence': rows[-1]['sequence'], 'last_event_at': last.isoformat() if last else None,
                       'event_bytes': sum(e['bytes'] for e in rows),
                       'incomplete': not terminal or terminal < work_started or started != finished or 'SubagentStart' in child_states.values(),
                       'last_turn': next((e['turn_id'] for e in reversed(rows) if e['session_id'] == session and e.get('turn_id')), None)})
    return sorted(result, key=lambda row: row['last_sequence'], reverse=True)


def resolve(root, session=None, latest=False, current=False, turn=None):
    if current:
        session = os.environ.get('CODEX_THREAD_ID')
        if not session:
            raise ATRException('当前会话 ID 不可用；使用 --session 或 --latest，不猜测当前会话')
    elif latest:
        rows = inventory(root)
        session = rows[0]['session_id'] if rows else None
    if not session:
        raise ATRException('没有可查询的会话；指定 --session、--current 或 --latest')
    c.identifier(session, True)
    if turn == 'latest':
        connection = c.connect(root)
        try:
            row = connection.execute('SELECT turn FROM events WHERE session=? AND turn IS NOT NULL ORDER BY seq DESC LIMIT 1', (session,)).fetchone() if connection else None
        finally:
            if connection:
                connection.close()
        if not row:
            raise ATRException('该会话没有可用轮次')
        turn = row[0]
    return session, turn


def query(root, session=None, latest=False, current=False, turn=None, agent=None):
    session, turn = resolve(root, session, latest, current, turn)
    result = c.report(root, session, turn)
    if agent:
        c.identifier(agent, True)
        result['agents'] = [a for a in result['agents'] if a['agent_id'] == agent]
        result['declared_plans'] = [a for a in result['declared_plans'] if a['agent_id'] == agent]
        result['agent_filter'] = agent
        result['warnings'].append('Agent filter applies to rows; session totals and sequence remain unfiltered.')
    result['recording_mode'] = c.settings(root)['mode']
    return result


def health(root, recent_minutes=15):
    if recent_minutes <= 0:
        raise ATRException('recent-minutes 必须大于零')
    mode = c.settings(root)['mode']
    connection = c.connect(root)
    try:
        rows = [json.loads(row[0]) for row in connection.execute("SELECT data FROM events WHERE kind != 'ATRPlan' ORDER BY seq")] if connection else []
    finally:
        if connection:
            connection.close()
    cutoff = now() - timedelta(minutes=recent_minutes)
    enabled_at = timestamp(c.settings(root).get('changed_at'))
    if enabled_at and enabled_at > cutoff:
        cutoff = enabled_at
    recent = [row for row in rows if timestamp(row.get('received_at')) and timestamp(row['received_at']) >= cutoff]
    last = rows[-1] if rows else None
    error_path = c.safe(root, '.atr/native/last-error.json')
    try:
        error = json.loads(error_path.read_bytes()) if error_path.exists() else None
        if error is not None and not isinstance(error, dict):
            raise ValueError('invalid diagnostic')
    except (ValueError, UnicodeError):
        error = {'type': 'InvalidDiagnostic', 'at': now().isoformat()}
    recent_error = error and timestamp(error.get('at')) and timestamp(error['at']) >= cutoff and (not last or timestamp(error['at']) >= timestamp(last.get('received_at')))
    coverage = sorted({row['event'] for row in recent})
    expected = set(c.EVENTS) - ({'PreToolUse', 'PostToolUse'} if mode == 'summary' else set())
    state = 'off' if mode == 'off' else 'error' if recent_error else 'recent_events' if recent else 'stale' if last else 'not_confirmed'
    last_session = last['session_id'] if last else None
    warnings = c.report(root, last_session)['warnings'] if last_session else []
    return {'mode': mode, 'state': state, 'recent_minutes': recent_minutes,
            'recent_hook_events': len(recent), 'last_hook_at': last.get('received_at') if last else None,
            'observed_event_types': coverage, 'not_observed_recently': sorted(expected - set(coverage)),
            'last_error': error, 'warnings': warnings,
            'storage_bytes': disk_bytes(root),
            'note': 'Missing event types may be unused rather than broken. Recent events do not attest current hook trust. Off mode does not write diagnostics.'}


def disk_bytes(root):
    folder = c.safe(root, '.atr/native')
    total = 0
    if folder.exists():
        for parent, dirs, files in os.walk(folder, followlinks=False):
            for name in dirs + files:
                path = c.safe(root, (Path(parent) / name).relative_to(root).as_posix())
                if path.is_file():
                    total += path.stat().st_size
    return total


def cleanup_plan(root, days=None, keep=None, max_mib=None, include_incomplete=False):
    for name, value in [('days', days), ('keep', keep), ('max_mib', max_mib)]:
        if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value < 0):
            raise ATRException(name + ' 必须是非负数')
    if all(value is None for value in (days, keep, max_mib)):
        raise ATRException('至少指定 --older-than-days、--keep-sessions 或 --max-mib')
    rows = inventory(root)
    selected, protected = [], []
    cutoff = now() - timedelta(days=days) if days is not None else None
    total = disk_bytes(root)
    estimated = total
    for index, row in enumerate(rows):
        last = timestamp(row['last_event_at'])
        # Protect active/recent sessions even if include-incomplete was requested.
        if last is None or last > now() - timedelta(minutes=5) or (row['incomplete'] and not include_incomplete):
            protected.append(row['session_id'])
            continue
        if (cutoff and last < cutoff) or (keep is not None and index >= keep):
            selected.append(row)
            estimated -= row['event_bytes'] + report_bytes(root, row['members'])
    if max_mib is not None:
        for row in reversed(rows):
            if estimated <= max_mib * 1024 * 1024:
                break
            if row in selected or row['session_id'] in protected:
                continue
            selected.append(row)
            estimated -= row['event_bytes'] + report_bytes(root, row['members'])
    return {'dry_run': True, 'sessions': [r['session_id'] for r in selected],
            'members': sorted({s for r in selected for s in r['members']}),
            'events_to_delete': sum(r['events'] for r in selected), 'protected_sessions': protected,
            'storage_bytes': total, 'estimated_reclaim_bytes': max(0, total - max(estimated, 0)),
            'note': 'Estimate excludes SQLite page/index overhead. Size target is best effort; active/recent sessions and settings are retained. Execute only while recording is off.'}


def report_bytes(root, sessions):
    total = 0
    for session in sessions:
        folder = c.location(root, session)
        if folder.exists():
            for parent, dirs, files in os.walk(folder, followlinks=False):
                for name in dirs + files:
                    path = c.safe(root, (Path(parent) / name).relative_to(root).as_posix())
                    if path.is_file():
                        total += path.stat().st_size
    return total


@contextmanager
def maintenance(root):
    path = c.safe(root, '.atr/native/maintenance')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ATRException('另一个清理任务正在运行；确认无进程后才可移除 maintenance 标记') from exc
    os.close(fd)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def cleanup(root, *, execute=False, **criteria):
    if not execute:
        return cleanup_plan(root, **criteria)
    with locked(root):
        if c.settings(root)['mode'] != 'off':
            raise ATRException('清理前先 atr native off；避免与采集写入竞争')
        with maintenance(root):
            connection = c.connect(root)
            try:
                if connection:
                    connection.execute('BEGIN IMMEDIATE')
                plan = cleanup_plan(root, **criteria)
                # Validate all derived paths before deleting authoritative events.
                paths = []
                for session in plan['members']:
                    report_bytes(root, [session])
                    folder = c.location(root, session)
                    if folder.exists():
                        paths.append(folder)
                if connection and plan['members']:
                    for session in plan['members']:
                        connection.execute('DELETE FROM events WHERE session=?', (session,))
                    connection.commit()
                elif connection:
                    connection.commit()
                for folder in paths:
                    shutil.rmtree(folder)
                if connection:
                    connection.execute('VACUUM')
                after = disk_bytes(root)
                plan.update({'dry_run': False, 'storage_after_bytes': after,
                             'actual_reclaim_bytes': max(0, plan['storage_bytes'] - after)})
                return plan
            finally:
                if connection:
                    connection.close()
