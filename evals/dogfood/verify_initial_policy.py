"""Fixed controller-side acceptance for the real V0.2 engineering task.

Run with cwd=the candidate checkout. Keep this file outside the model's writable
workspace. It is public acceptance, not a held-out benchmark or an LLM judge.
"""
import importlib
import json
from pathlib import Path
import sys


def evaluate():
    sys.path.insert(0, str(Path.cwd() / 'src'))
    from atr.core import ATRException, Model
    import tomlkit
    module = Path('src/atr/task_policy.py')
    if not module.is_file():
        return False, 'task_policy.py has not been implemented'
    select = importlib.import_module('atr.task_policy').select_initial
    models = [Model('fixture-cheap', 'fast', ('low',)),
              Model('fixture-regular', 'balanced', ('low', 'medium')),
              Model('fixture-think', 'reasoning', ('medium', 'high')),
              Model('fixture-expert', 'expert', ('high',))]
    for task, level, ident, effort in [('Fix a typo', 'simple', 'fixture-cheap', 'low'),
                                      ('Add a command', 'medium', 'fixture-regular', 'medium'),
                                      ('Design distributed architecture', 'complex', 'fixture-think', 'high')]:
        result = select(task, models)
        assert result['complexity'] == level, result
        assert result['model'] == ident and result['effort'] == effort, result
        assert isinstance(result['reason'], str) and result['reason'], result
    assert select('Design architecture', models, complexity='simple')['model'] == 'fixture-cheap'
    assert select('Review production authentication', models)['complexity'] == 'complex'
    config = tomlkit.parse('[simple]\ntier="balanced"\neffort="medium"\n')
    before = config.as_string()
    assert select('Fix a typo', models, config)['model'] == 'fixture-regular'
    assert config.as_string() == before
    assert select('Add a command', models, {'medium': {'model': 'fixture-think', 'effort': 'high'}})['model'] == 'fixture-think'
    fallback = select('Design architecture', models[:1])
    assert fallback['model'] == 'fixture-cheap' and fallback['effort'] == 'low'
    for action in [lambda: select('task', []), lambda: select('task', models, complexity='invalid'),
                   lambda: select('task', models, {'unknown': {}}), lambda: select('task', models, {'simple': 'bad'}),
                   lambda: select('task', models, {'simple': {'tier': 'invalid'}}),
                   lambda: select('task', models, {'simple': {'effort': 'invalid'}}),
                   lambda: select('task', models, {'complex': {'mispelled': True}}),
                   lambda: select('task', models, {'medium': {'model': 'not-in-catalog'}}),
                   lambda: select('task', models, {'simple': {'effort': 'high'}}, 'simple')]:
        try:
            action()
        except ATRException:
            pass
        else:
            raise AssertionError('Invalid configuration was accepted')
    return True, 'Defaults, actual TOML overrides, explicit choice, fallbacks and failure cases passed'


if __name__ == '__main__':
    try:
        passed, reason = evaluate()
        status = 'passed' if passed else 'failed'
        code = 0 if passed else 1
    except (AssertionError, ImportError, AttributeError, SyntaxError, TypeError, ValueError, KeyError) as exc:
        status, reason, code = 'failed', f'{type(exc).__name__}: {exc}', 1
    except Exception as exc:
        status, reason, code = 'error', f'{type(exc).__name__}: {exc}', 2
    print(json.dumps({'status': status, 'reason': reason, 'failure_id': 'atr-initial-policy-contract'}, ensure_ascii=False))
    raise SystemExit(code)
