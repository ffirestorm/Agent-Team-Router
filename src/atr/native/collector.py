"""Self-contained, stdlib-only Codex hook collector (also copied into projects).

No inference, transcript scraping, shell execution, model switching or stop loops.
Hook mode fails open. Explicit reports and notes report errors to their caller.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import shlex
import sys
import tempfile

VERSION = "0.4.0"
EVENTS = ("SessionStart", "UserPromptSubmit", "SubagentStart", "SubagentStop",
          "PreToolUse", "PostToolUse", "PreModelRequest", "PostModelRequest",
          "Stop", "Interrupt", "SessionEnd")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
DELEGATION = re.compile(r"(^|[_.])(?:spawn_agent|wait_agent|wait|send_input|close_agent)$|^Agent$")
MAX_INPUT = 1024 * 1024


def comparison_path(path):
    """Normalize equivalent Windows namespaces AFTER filesystem resolution.

    realpath(strict=False) can retain the extended prefix when a missing parent
    is created between its two Win32 lookups. That does not change containment.
    Only drive and UNC filesystem namespaces are folded, never device paths.
    """
    if isinstance(path, PureWindowsPath):
        value = str(path)
        if value.lower().startswith('\\\\?\\unc\\'):
            return PureWindowsPath('\\\\' + value[8:])
        if re.match(r'^\\\\\?\\[A-Za-z]:\\', value):
            return PureWindowsPath(value[4:])
    return path


def safe(root, relative):
    rel = Path(relative)
    win = PureWindowsPath(relative)
    if rel.anchor or win.anchor or '..' in rel.parts or '..' in win.parts:
        raise ValueError('unsafe native observation path')
    target = root
    boundary = comparison_path(root.resolve(strict=True))
    for part in rel.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('native observation path is a symlink')
        # Resolve existing directories strictly, including Windows junctions.
        # Resolving a transient SQLite journal leaf with strict=False races its
        # deletion/Win32 sharing and can return an inconsistent fallback spelling.
        # With no '..' or symlinks, a file/missing suffix stays in the checked dir.
        if target.is_dir() and not comparison_path(target.resolve(strict=True)).is_relative_to(boundary):
            raise ValueError('native observation path escapes project')
    return target


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.atr-native-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def identifier(value, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid session, turn or agent identifier')
    return value


def text(value, limit=200):
    if not isinstance(value, str):
        return None
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)[:limit]
    value = re.sub(r'(?i)(?:bearer\s+|sk-)[A-Za-z0-9_.-]{12,}', '[redacted]', value)
    value = re.sub(r'(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+', r'\1=[redacted]', value)
    return value


def digest(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def location(root, session, turn=None):
    base = f'.atr/native/sessions/{digest(session)[:32]}'
    if turn:
        base += f'/turns/{digest(turn)[:32]}'
    return safe(root, base)


SETTINGS = '.atr/native/settings.json'
MODES = ('off', 'summary', 'detailed')


def settings(root):
    path = safe(root, SETTINGS)
    data = json.loads(path.read_bytes()) if path.exists() else {'mode': 'off'}
    if not isinstance(data, dict) or set(data) - {'mode', 'changed_at'} or data.get('mode') not in MODES:
        raise ValueError('invalid native settings')
    result = dict(data)
    if safe(root, '.atr/native/disabled').exists():
        result['mode'] = 'off'
    return result


def set_mode(root, mode):
    if mode not in MODES:
        raise ValueError('invalid recording mode')
    from atr.storage import locked
    with locked(root):
        value = {'mode': mode, 'changed_at': datetime.now(timezone.utc).isoformat()}
        atomic(safe(root, SETTINGS), json.dumps(value).encode())
        if mode != 'off':
            safe(root, '.atr/native/disabled').unlink(missing_ok=True)
        else:
            # Also stops an older installed V0.3 launcher during upgrade.
            atomic(safe(root, '.atr/native/disabled'), b'')
    return value


def normalize(payload):
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in (*EVENTS, 'ATRPlan'):
        raise ValueError('unsupported hook payload')
    event = {key: identifier(payload.get(key), key == 'session_id') for key in ('session_id', 'turn_id', 'agent_id')}
    event.update({'event': payload['hook_event_name'], 'role': text(payload.get('agent_type'), 100),
                  'model': text(payload.get('model'), 160),
                  'source': 'host-declared' if payload['hook_event_name'] == 'ATRPlan' else 'codex-hook',
                  'received_at': datetime.now(timezone.utc).isoformat()})
    kind = event['event']
    if kind in ('PreToolUse', 'PostToolUse'):
        tool = payload.get('tool_name')
        if not isinstance(tool, str) or not DELEGATION.search(tool):
            return None
        event['tool_name'] = text(tool, 150)
        event['tool_use_id'] = identifier(payload.get('tool_use_id'))
        arguments = payload.get('tool_input')
        arguments = arguments if isinstance(arguments, dict) else {}
        event['requested_role'] = text(arguments.get('agent_type'), 100)
        event['requested_model'] = text(arguments.get('model'), 160)
        event['task'] = text(arguments.get('description') or arguments.get('task_name'))
        if isinstance(arguments.get('message'), str):
            event['task_sha256'] = digest(arguments['message'])  # never retain the prompt
        response = payload.get('tool_response')
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except ValueError:
                response = None
        if isinstance(response, dict) and isinstance(response.get('content'), list):
            for block in response['content']:
                if isinstance(block, dict) and block.get('type') == 'text':
                    try:
                        candidate = json.loads(block.get('text', ''))
                    except ValueError:
                        continue
                    if isinstance(candidate, dict) and ('agent_id' in candidate or 'agentId' in candidate):
                        response = candidate
                        break
        if isinstance(response, dict):
            result = response.get('result', response)
            if isinstance(result, dict):
                returned = result.get('agent_id', result.get('agentId'))
                event['returned_agent_id'] = identifier(returned)
    if kind == 'ATRPlan':
        event['task'] = text(payload.get('task'))
        event['requested_role'] = text(payload.get('role'), 100)
        event['requested_model'] = event.pop('model')
        if not event['task']:
            raise ValueError('a short task label is required')
    if kind == 'PostModelRequest':
        status = payload.get('status')
        event['request_status'] = status if status in ('completed', 'failed', 'cancelled') else 'unknown'
        usage = payload.get('token_usage')
        event['usage'] = None
        if isinstance(usage, dict):
            event['usage'] = {key: value for key, value in usage.items()
                              if key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'total_tokens')
                              and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        if payload.get('error') is not None:
            event['error_sha256'] = digest(json.dumps(payload['error'], sort_keys=True))
            event['error_present'] = True  # no raw error, endpoint, headers or credentials
    return event


def connect(root, create=False):
    folder = safe(root, '.atr/native')
    path = safe(root, '.atr/native/events.sqlite3')
    for suffix in ('-journal', '-wal', '-shm'):
        safe(root, '.atr/native/events.sqlite3' + suffix)
    if not path.exists() and not create:
        return None
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    connection = sqlite3.connect(str(path), timeout=2)
    connection.execute('PRAGMA busy_timeout=2000')
    connection.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, session TEXT NOT NULL, turn TEXT, agent TEXT, kind TEXT NOT NULL, data TEXT NOT NULL)')
    connection.execute('CREATE INDEX IF NOT EXISTS events_session ON events(session)')
    connection.execute('CREATE INDEX IF NOT EXISTS events_agent ON events(agent,kind)')
    return connection


def descendants(connection, session):
    sessions = {session}
    for _ in range(16):
        marks = ','.join('?' * len(sessions))
        rows = connection.execute(f'SELECT agent FROM events WHERE session IN ({marks}) AND kind=? AND agent IS NOT NULL', (*sessions, 'SubagentStart'))
        expanded = sessions | {row[0] for row in rows}
        if expanded == sessions:
            return sessions
        sessions = expanded
        if len(sessions) > 256:
            break
    raise ValueError('native session tree exceeds observation limits')


def ancestors(connection, session):
    roots = {session}
    for _ in range(16):
        marks = ','.join('?' * len(roots))
        parents = {row[0] for row in connection.execute(f'SELECT DISTINCT session FROM events WHERE agent IN ({marks}) AND kind=?', (*roots, 'SubagentStart'))}
        if parents <= roots:
            return roots
        roots |= parents
        if len(roots) > 256:
            break
    raise ValueError('native parent tree exceeds observation limits')


def summarize(connection, session, turn=None):
    identifier(session, True)
    identifier(turn)
    sessions = descendants(connection, session)
    marks = ','.join('?' * len(sessions))
    rows = connection.execute(f'SELECT seq,data FROM events WHERE session IN ({marks}) ORDER BY seq', tuple(sessions)).fetchall()
    events = [dict(json.loads(row[1]), sequence=row[0]) for row in rows]
    if turn:
        # Limit descendants to the parent's observed turn windows. A child session
        # can be resumed in a later parent turn; its entire history is not t1.
        current_turn = None
        windows = {}
        child_turns = {}
        for event in events:
            if (event['session_id'] == session and event.get('turn_id') and
                    (event['event'] in ('UserPromptSubmit', 'SubagentStart', 'ATRPlan') or
                     event.get('agent_id') in (None, session))):
                current_turn = event['turn_id']
            owner = current_turn
            if event['session_id'] != session and event.get('turn_id'):
                # Retries of the same child turn may finish after the parent has
                # accepted another prompt. A resumed child gets a new turn ID.
                owner = child_turns.setdefault((event['session_id'], event['turn_id']), current_turn)
            windows[event['sequence']] = owner
        # Child turns differ from the parent turn; include only children spawned in this turn.
        allowed = {e['agent_id'] for e in events if e['event'] in ('SubagentStart', 'ATRPlan') and e.get('turn_id') == turn and e.get('agent_id')}
        for _ in range(16):
            expanded = allowed | {e['agent_id'] for e in events if e['event'] == 'SubagentStart' and e['session_id'] in allowed}
            if expanded == allowed:
                break
            allowed = expanded
        events = [e for e in events if
                  (e['session_id'] == session and e.get('turn_id') == turn) or
                  (windows[e['sequence']] == turn and
                   (e['session_id'] in allowed or e.get('agent_id') in allowed))]
    agents = {}
    notes = []
    pending = {}
    for event in events:
        kind = event['event']
        if kind == 'ATRPlan':
            notes.append(event)
        if kind == 'PreToolUse' and event.get('tool_use_id'):
            pending[event['tool_use_id']] = event
        if kind == 'PostToolUse' and event.get('returned_agent_id'):
            before = pending.get(event.get('tool_use_id'), {})
            agent = agents.setdefault(event['returned_agent_id'], agent_row(event['returned_agent_id']))
            agent['task'] = before.get('task') or agent['task']
            agent['task_source'] = 'tool-description' if before.get('task') else None
            if before.get('requested_role'):
                agent['role'] = before['requested_role']
            agent['task_sha256'] = before.get('task_sha256')
            agent['requested_model'] = before.get('requested_model')
        if kind in ('Stop', 'Interrupt', 'SessionEnd'):
            ident = event.get('agent_id') or event['session_id']
            if ident == session:
                ident = 'main'
            agent = agents.setdefault(ident, agent_row(ident))
            agent['execution_status'] = 'interrupted' if kind == 'Interrupt' else 'stopped'
        if kind not in ('SubagentStart', 'SubagentStop', 'PreModelRequest', 'PostModelRequest'):
            continue
        ident = event.get('agent_id') or event['session_id']
        if ident == session:
            ident = 'main'
        agent = agents.setdefault(ident, agent_row(ident))
        if event.get('role'):
            agent['role'] = event['role']
        if kind == 'SubagentStart':
            agent['execution_status'] = 'started'
            agent['start_model'] = event.get('model')
        elif kind == 'SubagentStop':
            agent['execution_status'] = 'stopped'  # not proof of verification success
        elif kind == 'PreModelRequest':
            agent['requests_started'] += 1
            agent['execution_status'] = 'running'
        elif kind == 'PostModelRequest':
            agent['requests_finished'] += 1
            state = event.get('request_status', 'unknown')
            agent['request_outcomes'][state] = agent['request_outcomes'].get(state, 0) + 1
            usage = event.get('usage')
            if usage is None:
                agent['usage_missing_requests'] += 1
            else:
                for key in ('input_tokens', 'output_tokens', 'cached_input_tokens'):
                    value = usage.get(key)
                    if value is None:
                        agent['usage_missing_fields'][key] += 1
                    else:
                        agent['known_usage'][key] += value
        if kind in ('PreModelRequest', 'PostModelRequest') and event.get('model') and event['model'] not in agent['models']:
            agent['models'].append(event['model'])
    for note in notes:
        ident = note.get('agent_id')
        if ident and ident in agents:
            agents[ident]['task'] = note.get('task')
            agents[ident]['requested_model'] = note.get('requested_model')
            agents[ident]['task_source'] = 'host-declared'
            if note.get('requested_role') and agents[ident]['role'] == 'unknown':
                agents[ident]['role'] = note['requested_role']
    warnings = []
    hook_count = sum(e['event'] != 'ATRPlan' for e in events)
    if not hook_count:
        warnings.append('No events observed: hooks may be untrusted, unsupported, disabled, or not yet triggered.')
    for agent in agents.values():
        if not agent['models']:
            warnings.append(f"{agent['agent_id']}: no request model observed")
        if agent['usage_missing_requests'] or any(agent['usage_missing_fields'].values()):
            warnings.append(f"{agent['agent_id']}: token usage incomplete; known totals are partial")
        if agent['requests_started'] != agent['requests_finished']:
            warnings.append(f"{agent['agent_id']}: request events incomplete or still running")
        if agent['agent_id'] != 'main' and agent['execution_status'] != 'stopped':
            warnings.append(f"{agent['agent_id']}: no stop event observed")
    return {'session_id': session, 'turn_id': turn, 'event_count': len(events), 'hook_event_count': hook_count,
            'as_of_sequence': max((e['sequence'] for e in events), default=0),
            'agents': list(agents.values()), 'declared_plans': [{k: e.get(k) for k in ('agent_id', 'requested_role', 'requested_model', 'task')} for e in notes],
            'warnings': warnings, 'verification': 'not_observed', 'cost': None,
            'note': 'Host-reported models, not server attestation. Request completion and agent stop are not task acceptance. Cached input is a subset of input; absent usage is unknown.'}


def agent_row(ident):
    return {'agent_id': ident, 'role': 'main' if ident == 'main' else 'unknown', 'task': None,
            'models': [], 'execution_status': 'unknown', 'verification_status': 'not_observed',
            'requests_started': 0, 'requests_finished': 0, 'request_outcomes': {},
            'known_usage': {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0},
            'usage_missing_requests': 0, 'usage_missing_fields': {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0}}


def markdown(report):
    def cell(value):
        return str(value if value is not None else '未知').replace('|', '\\|').replace('\n', ' ').replace('`', "'")[:240]
    lines = ['# ATR 原生执行记录', '', f"Session: {cell(report['session_id'])}; turn: {cell(report['turn_id'])}; 截至事件 {report['as_of_sequence']}。", '',
             '| Agent | 角色 | 宿主报告的请求模型 | 任务标签 | 执行状态 | 验证 |', '|---|---|---|---|---|---|']
    if 'recording_mode' in report:
        lines[3:3] = [f"当前采集模式：{cell(report['recording_mode'])}。关闭时查询的是历史记录。", '']
    for agent in report['agents']:
        lines.append('| ' + ' | '.join(cell(value) for value in [agent['agent_id'], agent['role'], ', '.join(agent['models']) or None,
                                                               agent['task'], agent['execution_status'], agent['verification_status']]) + ' |')
    if not report['agents']:
        lines.append('| 未观测到 Agent/模型事件 | — | 未知 | — | 未知 | 未观测 |')
    for agent in report['agents']:
        usage = agent['known_usage']
        outcomes = ', '.join(f'{key}={value}' for key, value in agent['request_outcomes'].items()) or '未知'
        lines += ['', f"- {cell(agent['agent_id'])}: 请求结果 {cell(outcomes)}；已知 input/output/cache Token "
                  f"{usage['input_tokens']}/{usage['output_tokens']}/{usage['cached_input_tokens']}（仅已观测部分；缺失字段仍未知）。"]
    lines += ['', '任务标签可能由主 Agent 声明；模型列仅来自请求 hooks。停止不等于验收通过。最终回复的用量可能在收尾后补记。',
              '无用量字段不按零用量计费；费用未知。']
    lines += ['- ' + value for value in report['warnings']]
    return '\n'.join(lines) + '\n'


def export(connection, root, session, turn=None):
    report = summarize(connection, session, turn)
    folder = location(root, session, turn)
    atomic(safe(root, str((folder / 'summary.json').relative_to(root))), json.dumps(report, ensure_ascii=False, indent=2).encode())
    atomic(safe(root, str((folder / 'summary.md').relative_to(root))), markdown(report).encode())
    return report


def collect(root, payload, installation='unknown'):
    root = Path(root).resolve(strict=True)
    cwd = payload.get('cwd') if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not comparison_path(Path(cwd).resolve()).is_relative_to(comparison_path(root)):
        raise ValueError('hook cwd is outside the bound project')
    mode = settings(root)['mode']
    if mode == 'off' or safe(root, '.atr/native/maintenance').exists():
        return {}
    if mode == 'summary' and payload.get('hook_event_name') in ('PreToolUse', 'PostToolUse'):
        return {}
    event = normalize(payload)
    if event is None:
        return {}
    if mode == 'summary':
        for key in ('task_sha256', 'error_sha256'):
            event.pop(key, None)
    event['recording_mode'] = mode
    event['installation'] = text(installation, 100)
    event['collector_version'] = VERSION
    connection = connect(root, create=True)
    try:
        connection.execute('BEGIN IMMEDIATE')
        mode = settings(root)['mode']
        if mode == 'off' or safe(root, '.atr/native/maintenance').exists():
            return {}
        if mode == 'summary':
            if event['event'] in ('PreToolUse', 'PostToolUse'):
                return {}
            for key in ('task_sha256', 'error_sha256'):
                event.pop(key, None)
        event['recording_mode'] = mode
        connection.execute('INSERT INTO events(session,turn,agent,kind,data) VALUES (?,?,?,?,?)',
                           (event['session_id'], event.get('turn_id'), event.get('agent_id'), event['event'], json.dumps(event, ensure_ascii=False)))
        # The event database is authoritative. Export failures must not erase facts.
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        affected = ancestors(connection, event['session_id']) if mode == 'detailed' or event['event'] in ('SessionStart', 'UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd', 'SubagentStop') else set()
        for session in affected:
            export(connection, root, session)
            if event.get('turn_id') and session == event['session_id']:
                export(connection, root, session, event['turn_id'])
            # Refresh parent-turn views when the model request belongs to a child session.
            if event['event'] in ('Stop', 'SessionEnd', 'SubagentStop'):
                for row in connection.execute('SELECT DISTINCT turn FROM events WHERE session=? AND kind IN (?,?,?) AND turn IS NOT NULL',
                                              (session, 'SubagentStart', 'UserPromptSubmit', 'ATRPlan')):
                    if mode == 'detailed':
                        export(connection, root, session, row[0])
                if mode == 'summary':
                    latest = connection.execute('SELECT turn FROM events WHERE session=? AND turn IS NOT NULL ORDER BY seq DESC LIMIT 1', (session,)).fetchone()
                    if latest:
                        export(connection, root, session, latest[0])
        connection.commit()
    finally:
        connection.close()
    if event['event'] in ('SessionStart', 'UserPromptSubmit'):
        path = location(root, event['session_id'], event.get('turn_id')) / 'summary.md'
        helper = helper_command(root, installation)
        instructions = (f"ATR native observation is installed for this project. Session {event['session_id']}. "
                        f"Turn {event.get('turn_id') or 'unavailable'}. Current observation report: {path}. Internal helper: {helper}. Before actual delegation, tell the user the intended role, model and task. "
                        "Distinguish plans from confirmed starts. After spawn returns an agent ID, record a short non-secret task label using the helper with --note --session SESSION [--turn TURN] --agent-id ID --role ROLE --task 'short label' (internal tool use, not a user step). "
                        "Before final response, use the helper --report --session SESSION [--turn TURN] to derive a fresh report (summary files may be snapshots), then summarize observed agents/models, outcomes and independent test evidence. "
                        "Do not claim tests passed from a stopped agent or completed model request. If hooks are missing, say observation is incomplete. "
                        "Do not ask the user to type ATR per task. Continue using native tools; atr run is optional and needs explicit execution scope. "
                        "Respect task scope and host delegation restrictions; simple tasks may stay with the main agent.")
        return {'hookSpecificOutput': {'hookEventName': event['event'], 'additionalContext': instructions}}
    return {}  # Never block, continue a stopped turn or change a model.


def report(root, session, turn=None):
    connection = connect(root)
    if connection is None:
        # Build the empty shape without creating any project files.
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE events(seq INTEGER,session TEXT,turn TEXT,agent TEXT,kind TEXT,data TEXT)')
    try:
        return summarize(connection, identifier(session, True), identifier(turn))
    finally:
        connection.close()


def sessions(root):
    connection = connect(root)
    if connection is None:
        return []
    try:
        return [{'session_id': row[0], 'events': row[1], 'last_sequence': row[2]} for row in
                connection.execute('SELECT session,COUNT(*),MAX(seq) FROM events GROUP BY session ORDER BY MAX(seq) DESC')]
    finally:
        connection.close()


def helper_command(root, installation):
    args = [sys.executable, str(root / '.codex/atr_native_hook.py'), '--project', str(root), '--installation', installation]
    if os.name == 'nt':
        return ' '.join(chr(34) + value + chr(34) for value in args)
    return shlex.join(args)


def note(root, session, turn, agent, role, task, model=None):
    identifier(agent, True)
    if not isinstance(role, str) or not role.strip():
        raise ValueError('a role is required')
    return collect(root, {'hook_event_name': 'ATRPlan', 'cwd': str(root),
                         'session_id': session, 'turn_id': turn, 'agent_id': agent,
                         'role': role, 'task': task, 'model': model})


def main(argv=None):
    # Hook JSON and human reports must work with redirected Windows stdio too.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--installation', default='unknown')
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--note', action='store_true')
    parser.add_argument('--agent-id')
    parser.add_argument('--role')
    parser.add_argument('--task')
    parser.add_argument('--model')
    parser.add_argument('--session')
    parser.add_argument('--turn')
    args = parser.parse_args(argv)
    if args.note:
        note(Path(args.project).resolve(strict=True), args.session, args.turn, args.agent_id, args.role, args.task, args.model)
        print('{}')
        return 0
    if args.report:
        print(markdown(report(Path(args.project).resolve(strict=True), args.session, args.turn)))
        return 0
    try:
        if settings(Path(args.project).resolve(strict=True))['mode'] == 'off':
            print('{}')
            return 0
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError('hook input too large')
        response = collect(Path(args.project), json.loads(data), args.installation)
    except Exception as exc:
        # Report code locations, never payloads, raw errors, paths or credentials.
        trace = exc.__traceback__
        frames = []
        while trace:
            frames.append(f'{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}')
            trace = trace.tb_next
        print('ATR observation unavailable: ' + type(exc).__name__ + ' [' + ' > '.join(frames[-3:]) + ']', file=sys.stderr)
        try:
            root = Path(args.project).resolve(strict=True)
            if settings(root)['mode'] != 'off':
                atomic(safe(root, '.atr/native/last-error.json'), json.dumps({
                    'at': datetime.now(timezone.utc).isoformat(), 'type': type(exc).__name__,
                    'location': frames[-3:]}).encode())
        except Exception:
            pass
        response = {}
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
