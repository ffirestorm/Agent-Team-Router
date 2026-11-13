"""Standalone JSON verifier template; installed with a fixed acceptance manifest."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SPEC = None


def main():
    if not isinstance(SPEC, dict):
        print(json.dumps({'status': 'error', 'reason': 'verifier_template_not_bound'}))
        return 2
    for name, checksum in SPEC['sha256'].items():
        path = Path(name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            print(json.dumps({'status': 'error', 'reason': 'acceptance_input_changed'}))
            return 2
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--', *SPEC['tests']], capture_output=True)
    if result.returncode == 0:
        data, code = {'status': 'passed', 'reason': 'fixed_pytest_acceptance_passed'}, 0
    elif result.returncode == 1:
        # Fixed criterion signature; repeated failure alone is not model causality.
        signature = hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()
        data, code = {'status': 'failed', 'reason': 'fixed_pytest_acceptance_failed', 'failure_id': signature}, 1
    else:
        data, code = {'status': 'error', 'reason': 'pytest_environment_or_collection_error'}, 2
    print(json.dumps(data))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
