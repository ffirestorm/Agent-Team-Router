"""Discover V0.3 hook registrations in real Codex, without inference or hook trust.

All writes are confined to disposable project/CODEX_HOME. Project-layer trust in
that disposable home permits discovery, but hook definitions remain untrusted.
This does NOT prove callbacks fired in the user's desktop conversation.
"""
import json
from pathlib import Path
import shutil
import tempfile

import tomlkit

from native_probe import MetadataClient, cli
from atr.native.collector import EVENTS


class HookMetadataClient(MetadataClient):
    METHODS = MetadataClient.METHODS | {'hooks/list'}


def probe():
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('Codex CLI is required')
    with tempfile.TemporaryDirectory(prefix='atr-hooks-probe-') as temp:
        base = Path(temp).resolve()
        project, home = base / 'project', base / 'home'
        project.mkdir()
        home.mkdir()
        (project / '.git').mkdir()
        version = json.loads(cli(project, home, 'init'))['codex']
        cli(project, home, 'native', 'install')
        (home / 'config.toml').write_text(tomlkit.dumps({
            'projects': {str(project): {'trust_level': 'trusted'}},
            'analytics': {'enabled': False},
        }), encoding='utf-8')
        with HookMetadataClient(executable, project, home) as client:
            client.initialize()
            result = client.request('hooks/list', {'cwds': [str(project)]})
            rows = [row for row in result['data'] if Path(row['cwd']).resolve() == project]
            if len(rows) != 1 or rows[0].get('errors'):
                raise RuntimeError('Hook discovery failed: ' + json.dumps(result))
            hooks = rows[0]['hooks']
            if len(hooks) != len(EVENTS):
                raise RuntimeError('Expected eleven ATR hook handlers: ' + json.dumps(result))
            if any(hook.get('trustStatus') != 'untrusted' for hook in hooks):
                raise RuntimeError('Fresh hooks unexpectedly trusted: ' + json.dumps(result))
            events = sorted(hook['eventName'] for hook in hooks)
            if set(events) != {event[0].lower() + event[1:] for event in EVENTS}:
                raise RuntimeError('Codex discovered an unexpected event set')
            methods = list(client.methods)
        return {'ok': True, 'codex': version, 'discovered_events': events,
                'registered_handlers': len(hooks), 'trust_status': 'untrusted',
                'warnings': rows[0].get('warnings', []), 'rpc_methods': methods,
                'inference_requested': False, 'hook_trust_written': False,
                'callbacks_executed': False,
                'limitations': 'Real metadata discovery only. Collector callback tests use synthetic protocol fixtures; actual desktop activation requires user hook review.'}


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
