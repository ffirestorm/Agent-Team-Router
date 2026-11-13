"""Real native configuration discovery for V0.5; no inference or hook trust."""
import json
from pathlib import Path
import shutil
import tempfile
import tomllib

import tomlkit

from native_probe import MetadataClient, cli
from atr.native.collector import settings


def probe():
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('Codex CLI is required')
    with tempfile.TemporaryDirectory(prefix='atr-delegation-probe-') as temp:
        base = Path(temp).resolve()
        project, home = base / 'project', base / 'home'
        project.mkdir()
        home.mkdir()
        (project / '.git').mkdir()
        version = json.loads(cli(project, home, 'init', '--profile', 'development'))['codex']
        cli(project, home, 'apply')
        main_model = 'gpt-6-astra'  # disposable user preference; never an ATR default
        (home / 'config.toml').write_text(tomlkit.dumps({
            'model': main_model, 'projects': {str(project): {'trust_level': 'trusted'}},
            'analytics': {'enabled': False},
        }), encoding='utf-8')
        expected = {'atr_simple_worker': 'gpt-6-luna', 'atr_worker': 'gpt-6.1-terra',
                    'atr_explorer': 'gpt-6-luna'}
        with MetadataClient(executable, project, home) as client:
            client.initialize()
            result = client.request('config/read', {'cwd': str(project), 'includeLayers': True})
            native = result['config']
            roles = native.get('agents') or {}
            if not set(expected) <= set(roles) or 'atr_reviewer' in roles:
                raise RuntimeError('Expected two workers and optional explorer without a reviewer')
            if native.get('model') != main_model:
                raise RuntimeError('ATR changed the user-selected main model')
            for role, model in expected.items():
                path = Path(roles[role]['config_file']).resolve()
                if path != project / '.codex/agents' / (role + '.toml'):
                    raise RuntimeError('Codex did not resolve the native role path')
                layer = tomllib.loads(path.read_text(encoding='utf-8'))
                if layer['model'] != model:
                    raise RuntimeError('Role model does not match generated mapping')
                if role in ('atr_simple_worker', 'atr_worker') and layer['sandbox_mode'] != 'workspace-write':
                    raise RuntimeError('Worker role is not configured for implementation')
            methods = list(client.methods)
        small = json.loads(cli(project, home, 'delegate', 'fix typo'))
        regular = json.loads(cli(project, home, 'delegate', 'implement a cache'))
        if small['agent_type'] != 'atr_simple_worker' or regular['agent_type'] != 'atr_worker':
            raise RuntimeError('Native-role-aware delegation preview failed')
        return {'ok': True, 'codex': version, 'loaded_roles': expected,
                'user_selected_main_preserved': True, 'writable_workers': ['atr_simple_worker', 'atr_worker'],
                'reviewer_added': False, 'hooks_installed': (project / '.codex/hooks.json').exists(),
                'recording_mode': settings(project)['mode'], 'rpc_methods': methods,
                'inference_requested': False, 'account_access_verified': False,
                'limitations': 'Configuration discovery and previews only. No worker was spawned; generated instructions guide the host rather than enforce its decisions.'}


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
