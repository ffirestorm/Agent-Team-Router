"""Deterministic natural-language contract drafts; never executes proposed argv."""
from importlib.resources import files
import hashlib
import json
from pathlib import Path
import uuid

from atr.core import ATRException
from atr.runtime.tasks import scope_violation
from atr.storage import apply, read, safe_path


def templates():
    return [{'name': 'custom', 'requires': ['allowed_paths', 'baseline argv', 'independent verifier argv'],
             'note': 'Missing inputs produce a non-executable proposal, never guessed acceptance.'},
            {'name': 'pytest', 'requires': ['allowed_paths', 'acceptance test paths'],
             'note': 'Python pytest baseline; fixed JSON verifier runs existing acceptance tests outside writable scope.'}]


def argv(value, field):
    if value is None:
        return None
    try:
        result = json.loads(value)
    except ValueError as exc:
        raise ATRException(field + ' 必须是 JSON argv 数组') from exc
    if not isinstance(result, list) or not result or any(not isinstance(a, str) or not a for a in result):
        raise ATRException(field + ' 必须是非空 JSON argv 数组')
    return result


def draft_contract(root, description, ident=None, allowed=None, template='custom',
                   baseline_json=None, verify_json=None, acceptance_tests=None, output=None):
    if not isinstance(description, str) or not description.strip():
        raise ATRException('需求不能为空')
    if template not in ('custom', 'pytest'):
        raise ATRException('未知验收模板')
    allowed = list(allowed or [])
    tests = list(acceptance_tests or [])
    baseline = argv(baseline_json, 'baseline')
    verify = argv(verify_json, 'verify')
    if template == 'pytest':
        baseline = baseline or ['{python}', '-m', 'pytest', '-q', *[argument for name in tests for argument in ('--ignore', name)]]
        if verify is not None:
            raise ATRException('pytest 模板不能同时指定 --verify-json；自定义命令使用 custom')
        for name in tests:
            path = safe_path(root, name)
            if not path.is_file() or not name.endswith('.py'):
                raise ATRException('验收测试必须是项目内已有 Python 文件：' + name)
            if not scope_violation([name], allowed):
                raise ATRException('独立验收测试不能位于模型允许修改范围：' + name)
        verify = ['{python}', '{task_dir}/verify.py'] if tests else None
    elif tests:
        raise ATRException('--acceptance-test 仅适用于 pytest 模板')
    candidate = {'id': ident or 'task-' + uuid.uuid4().hex[:12], 'description': description.strip(),
                 'allowed_paths': allowed, 'baseline': baseline, 'verify': verify}
    missing = [key for key in ('allowed_paths', 'baseline', 'verify') if not candidate[key]]
    # Validate the same contract schema without creating a temporary file.
    validate_draft(candidate, missing)
    result = {'ready': not missing, 'missing': missing, 'contract': candidate,
              'template': template, 'model_invocation': 'not performed',
              'commands_executed': False,
              'note': 'Review paths and argv. A natural-language description does not establish independent acceptance. Only atr run --execute invokes models.'}
    if not output:
        if template == 'pytest' and not missing:
            result.update({'ready': False, 'missing': ['output'], 'note': 'pytest template requires --output to bind its fixed verifier; no commands executed.'})
        return result
    destination = safe_path(root, output)
    relative = destination.relative_to(root)
    if (destination.suffix != '.json' or relative.parts[0] in ('.git', '.codex', '.agents') or
            (relative.parts[0] == '.atr' and relative.parts[:2] != ('.atr', 'tasks'))):
        raise ATRException('输出须为 JSON 任务文件；ATR 内部仅允许写入 .atr/tasks/ 下的新目录')
    if not scope_violation([output], allowed):
        raise ATRException('任务契约必须位于模型允许修改范围之外')
    if missing:
        proposal = {'proposal': candidate, 'missing': missing, 'executable': False}
        data = (json.dumps(proposal, ensure_ascii=False, indent=2) + '\n').encode()
        result['output_kind'] = 'non_executable_proposal'
        changes = {output: data}
    else:
        changes = {output: (json.dumps(candidate, ensure_ascii=False, indent=2) + '\n').encode()}
        if template == 'pytest':
            helper = (destination.parent / 'verify.py').relative_to(root).as_posix()
            if helper == output:
                raise ATRException('任务输出不能覆盖 verify.py')
            spec = {'tests': tests, 'sha256': {name: hashlib.sha256(safe_path(root, name).read_bytes()).hexdigest() for name in tests}}
            source = files('atr.runtime').joinpath('pytest_verifier.py').read_text(encoding='utf-8')
            source = source.replace('SPEC = None', 'SPEC = ' + repr(spec), 1)
            changes[helper] = source.encode()
            if any(not scope_violation([name], allowed) for name in (output, helper)):
                raise ATRException('任务契约和验证器必须位于模型允许修改范围之外')
        result['output_kind'] = 'executable_contract'
    before = {name: read(root, name) for name in changes}
    if any(data is not None for data in before.values()):
        raise ATRException('输出已存在；请选择新的任务目录，不覆盖已有契约/验证器')
    result['transaction'] = apply(root, changes, before)
    result['output'] = str(destination)
    return result


def validate_draft(candidate, missing):
    # Reuse the strict contract reader; no temporary artifact needed.
    from atr.runtime.tasks import validate_contract
    complete = dict(candidate)
    if 'allowed_paths' in missing:
        complete['allowed_paths'] = ['proposal-placeholder.py']
    for key in ('baseline', 'verify'):
        if key in missing:
            complete[key] = ['not-yet-configured']
    validate_contract(complete)
