"""Transactional, project-local native integration; never writes hook trust."""
from importlib.resources import files
import json
import os
from pathlib import Path
import shlex
import sys
import uuid

import tomlkit

from atr import __version__
from atr.adapters.codex import CodexAdapter, END, parse_toml
from atr.core import ATRException
from atr.storage import check_drift, digest, parse_record, read, state
from atr.native.collector import EVENTS, connect, sessions

MANIFEST = '.atr/native-install.json'
LAUNCHER = '.codex/atr_native_hook.py'
HOOKS = '.codex/hooks.json'
SKILL = '.agents/skills/agent-team-router/SKILL.md'


def command(arguments, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        # Restrict cmd expansion characters rather than silently misquoting paths.
        if any(any(c in str(arg) for c in '\r\n"%!^&|<>') for arg in arguments):
            raise ATRException('Windows native hook path contains unsupported shell characters')
        return ' '.join('"' + str(arg) + '"' for arg in arguments)
    return shlex.join([str(arg) for arg in arguments])


def manifest(root):
    raw = read(root, MANIFEST)
    if raw is None:
        return None
    result = parse_record(raw, MANIFEST)
    if result.get('project') != str(root) or not isinstance(result.get('groups'), dict):
        raise ATRException('Native installation belongs to another path or is invalid; uninstall before moving')
    return result


def instructions(data, record):
    text = data.decode()
    content = f'''### ATR native observation (V0.4)
Native recording defaults OFF. Installation is not permission to enable recording.
Check `.atr/native/settings.json`: only explicit mode summary/detailed enables it;
`.atr/native/disabled` overrides it. When off, skip notes/reports and do not ask the
user to enable recording for ordinary tasks. Say recording is off when relevant.
When enabled, native hooks collect facts; instructions provide narration.
Before useful, authorized delegation, announce the intended role, configured model
(or unknown) and task. Respect higher-priority host restrictions on delegation.
Use actual native tools. Do not announce a planned assignment as a completed call.
SessionStart/UserPromptSubmit context supplies the session, turn and report path.
After spawn returns an ID, internally record a SHORT NON-SECRET task label with:
`{record['command']} --note --session SESSION --turn TURN --agent-id ID --role ROLE --task 'short label'`
Omit --turn if unavailable; never guess IDs. Notes declare intent, not model proof.
Before the final response, derive a fresh report internally (files may be snapshots):
`{record['command']} --report --session SESSION --turn TURN`
Summarize observed agent/model/task/status and separate test evidence in the final
reply. Missing hooks/models/usage mean unknown. Stop/completed request is not task
acceptance. The final request usage may arrive after the reply. No per-task user
ATR commands are needed. Never bypass hook trust, force delegation, scrape private
transcripts, or switch models through hooks. If no observation context is present,
state recording is unconfirmed; do not invent a session or an execution record.
'''
    if text.count(END) != 1:
        raise ATRException('Missing ATR managed instructions block')
    return text.replace(END, content + '\n' + END).encode()


def hook_document(root):
    raw = read(root, HOOKS)
    doc = parse_record(raw, HOOKS) if raw is not None else {'hooks': {}}
    if not isinstance(doc.get('hooks'), dict):
        raise ATRException('hooks.json requires a hooks object')
    if any(not isinstance(groups, list) for groups in doc['hooks'].values()):
        raise ATRException('Hook matcher groups must be arrays')
    return doc


def encode(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode()


def prepare_install(root):
    from atr.service import prepare
    changes, before = prepare(root, native=False)
    old = manifest(root)
    config = parse_toml(changes['.codex/config.toml'].decode())
    if 'hooks' in config:
        raise ATRException('已有 inline [hooks]；请先迁移到 .codex/hooks.json，避免同层双配置')
    if config.get('features', {}).get('hooks') is False or config.get('features', {}).get('codex_hooks') is False:
        raise ATRException('Project hooks explicitly disabled; coordinate the setting before installing')
    doc = hook_document(root)
    if old:
        for event, group in old['groups'].items():
            groups = doc['hooks'].get(event, [])
            if groups.count(group) != 1:
                raise ATRException('ATR hook group missing, modified or duplicated')
            groups.remove(group)
    installation = old['installation'] if old else uuid.uuid4().hex
    launcher = files('atr.native').joinpath('collector.py').read_bytes()
    launcher_hash = digest(launcher)
    generation = installation + '-' + launcher_hash[:16]
    executable = str(Path(sys.executable).absolute())
    # Script changes also change the exact hook definition, requiring re-review.
    cmd = command([executable, str(root / LAUNCHER), '--project', str(root), '--installation', generation])
    groups = {}
    for event in EVENTS:
        group = {'hooks': [{'type': 'command', 'command': cmd, 'timeout': 3 if event in ('SessionEnd', 'Interrupt') else 5}]}
        if event in ('PreToolUse', 'PostToolUse'):
            group['matcher'] = r'(^|[_.])(?:spawn_agent|wait_agent|wait|send_input|close_agent)$|^Agent$'
        doc['hooks'].setdefault(event, []).append(group)
        groups[event] = group
    # Validate event names and handler fields against this installed Codex binary.
    CodexAdapter().validate(tomlkit.dumps({'hooks': doc['hooks']}).encode(), {})
    record = {'version': __version__, 'project': str(root), 'installation': installation,
              'generation': generation, 'launcher_sha256': launcher_hash,
              'python': executable, 'command': cmd, 'groups': groups,
              'original_hooks': old['original_hooks'] if old else (read(root, HOOKS).decode() if read(root, HOOKS) is not None else None)}
    for name, data in {LAUNCHER: launcher,
                       SKILL: files('atr').joinpath('skill/SKILL.md').read_bytes(),
                       HOOKS: encode(doc), MANIFEST: encode(record)}.items():
        current = read(root, name)
        if name not in (HOOKS, MANIFEST) and current is not None and name not in state(root)['managed'] and current != data:
            raise ATRException(f'已有未托管文件：{name}')
        before[name] = current
        changes[name] = data
    changes['AGENTS.md'] = instructions(changes['AGENTS.md'], record)
    return changes, before


def prepare_uninstall(root):
    from atr.service import prepare
    old = manifest(root)
    if old is None:
        raise ATRException('Native integration is not installed')
    changes, before = prepare(root, native=False)
    doc = hook_document(root)
    original = old['original_hooks']
    original_doc = json.loads(original) if original is not None else {'hooks': {}}
    for event, group in old['groups'].items():
        groups = doc['hooks'].get(event, [])
        if groups.count(group) != 1:
            raise ATRException('ATR hook group missing, modified or duplicated')
        groups.remove(group)
        if not groups and event not in original_doc['hooks']:
            del doc['hooks'][event]
    # Restore exact user bytes when no other change was made to the hook document.
    data = (original.encode() if original is not None else None) if doc == original_doc else encode(doc)
    for name, value in {HOOKS: data, LAUNCHER: None, MANIFEST: None}.items():
        before[name] = read(root, name)
        changes[name] = value
    # The general ATR Skill/team and historical event data intentionally survive.
    return changes, before


def status(root):
    record = manifest(root)
    conflicts = []
    try:
        check_drift(root, state(root))
    except ATRException as exc:
        conflicts.append(str(exc))
    from atr.native.manage import health
    from atr.native.collector import settings
    diagnostics = health(root)
    observed = sessions(root)
    connection = connect(root)
    try:
        hook_count = connection.execute("SELECT COUNT(*) FROM events WHERE kind != 'ATRPlan'").fetchone()[0] if connection else 0
    finally:
        if connection:
            connection.close()
    return {'installed': record is not None, 'version': record.get('version') if record else None,
            'bound_project': str(root), 'disabled': settings(root)['mode'] == 'off',
            'mode': settings(root)['mode'], 'health': diagnostics,
            'trust': 'not_checked: review exact hooks in Codex /hooks or Settings > Hooks',
            'recording': 'off' if settings(root)['mode'] == 'off' else diagnostics['state'],
            'observed_hook_events': hook_count,
            'sessions': len(observed), 'conflicts': conflicts,
            'python_exists': Path(record['python']).is_file() if record else None,
            'limitations': ['Host coverage and hook trust require checking in the actual Codex client.',
                            'Installed configuration alone does not prove callbacks ran.']}
