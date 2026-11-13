"""Fixed independent acceptance for the V0.4 real engineering task."""
import importlib.util
import json
from pathlib import Path
import sys


def main():
    path = Path('src/atr/runtime/metrics.py')
    if not path.exists():
        print(json.dumps({'status': 'failed', 'reason': 'model_metrics_module_missing', 'failure_id': 'model-metrics-v1'}))
        return 1
    try:
        spec = importlib.util.spec_from_file_location('candidate_metrics', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        runs = [{'id': 'one', 'attempts': [
            {'model': 'A', 'status': 'failed', 'kind': 'initial', 'failure': 'quality', 'seconds': 1.5,
             'usage': {'input_tokens': 10, 'output_tokens': 2, 'cached_input_tokens': 4, 'cost': None}},
            {'model': 'B', 'status': 'succeeded', 'kind': 'escalation', 'failure': '', 'seconds': 2,
             'usage': {'input_tokens': 20, 'output_tokens': 3, 'cached_input_tokens': 5, 'cost': None}}]},
            {'id': 'two', 'attempts': [{'model': 'A', 'status': 'succeeded', 'kind': 'initial',
              'seconds': 3, 'usage': {'input_tokens': None, 'output_tokens': None, 'cached_input_tokens': None, 'cost': None}}]},
            {'id': 'noop', 'attempts': []}]
        before = json.dumps(runs, sort_keys=True)
        result = module.summarize_models(runs)
        assert json.dumps(runs, sort_keys=True) == before
        rows = {row['model']: row for row in result['models']}
        a, b = rows['A'], rows['B']
        assert a['attempts'] == 2 and a['tasks'] == 2 and a['succeeded_attempts'] == 1
        assert a['attempt_success_rate'] == .5 and a['failures'] == {'quality': 1}
        assert a['token_usage']['input_tokens'] == {'known_subtotal': 10, 'missing_attempts': 1, 'total': None}
        assert b['token_usage']['input_tokens']['total'] == 20
        assert a['cost'] is None and a['cost_missing_attempts'] == 2
        assert a['elapsed_seconds'] == 4.5 and b['upgraded_attempts'] == 1
        assert b['succeeded_upgraded_attempts'] == 1
        assert 'note' in result and module.summarize_models([])['models'] == []
        repeated = module.summarize_models([{'attempts': [{'model': 'A'}, {'model': 'A'}]}])['models'][0]
        assert repeated['tasks'] == 1 and repeated['attempts'] == 2
        assert repeated['token_usage']['input_tokens']['missing_attempts'] == 2
        assert repeated['attempt_success_rate'] == 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'reason': 'model_metrics_acceptance:' + type(exc).__name__, 'failure_id': 'model-metrics-v1'}))
        return 1
    print(json.dumps({'status': 'passed', 'reason': 'independent_model_metrics_acceptance'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
