"""Single-task execution, independent checks and one bounded escalation."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sys
import time
import uuid

from atr.config import load
from atr.core import ATRException, TIERS, choose_model
from atr.runtime.codex import CodexExecutor
from atr.runtime.policy import decide, limits_from
from atr.runtime.process import run_process
from atr.runtime.records import run_path, save
from atr.runtime.tasks import (changes, command, contract, fingerprint, patch,
                               file_modes, scope_violation, source_snapshot, workspace_snapshot)
from atr.storage import atomic, locked, safe_path


def preview(root: Path, task_path: Path, model: str | None = None, effort: str | None = None,
            complexity: str | None = None, allow_escalation: bool = False) -> dict:
    task = contract(task_path)
    task_path = task_path.resolve()
    verification_inputs = {}
    for argument in command(task["baseline"], task_path.parent)[1:] + command(task["verify"], task_path.parent)[1:]:
        path = Path(argument)
        if path.is_absolute() and path.is_file():
            verification_inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    doc, profile, _, models = load(root)
    if profile.name == "code-review":
        raise ATRException("当前 code-review Profile 只读；切换到 development 后才能执行代码修改")
    limits = limits_from(doc.get("execution", {}))
    if allow_escalation:
        limits = replace(limits, allow_escalation=True)
    if model:
        selected = choose_model(models, "balanced", model)
        reasoning = effort or ("medium" if "medium" in selected.efforts else selected.efforts[0])
        initial = {"model": selected.id, "effort": reasoning, "tier": selected.tier, "complexity": complexity or "manual", "reason": "explicit model selection"}
    else:
        try:
            from atr.task_policy import select_initial
        except ImportError as exc:
            raise ATRException("三档初始策略模块尚未就绪；暂用 --model 和 --effort 显式选择") from exc
        initial = select_initial(task["description"], models, doc.get("routing", {}), complexity)
        if effort:
            initial["effort"] = effort
        selected = next(m for m in models if m.id == initial["model"])
    if initial["effort"] not in selected.efforts:
        raise ATRException(f"{selected.id} 不支持推理强度 {initial['effort']}")
    target = choose_model(models, limits.escalation_tier)
    upgrade = None
    if target.id != selected.id and TIERS.index(target.tier) > TIERS.index(selected.tier):
        if limits.escalation_effort not in target.efforts:
            if limits.allow_escalation:
                raise ATRException("升级模型不支持配置的推理强度")
        else:
            upgrade = {"model": target.id, "effort": limits.escalation_effort, "tier": target.tier}
    return {"task": task, "task_dir": str(task_path.parent), "initial": initial,
            "task_path": str(task_path), "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
            "verification_inputs": verification_inputs,
            "upgrade": upgrade, "limits": asdict(limits), "dry_run": True,
            "notes": ["仅 --execute 才会调用模型；候选修改留在隔离目录，不自动合入。",
                      "Token 上限仅阻止下一次调用；当前调用只有墙钟超时限制。",
                      "费用不可观测时为 null；配置模型档位不等于账号授权或效果保证。"]}


def verification(argv, workspace, artifacts, name, timeout, cancel, task_dir):
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(workspace / "src")}
    out, err = artifacts / f"{name}.json", artifacts / f"{name}.stderr"
    process = run_process(command(argv, task_dir), workspace, out, err, timeout, cancel, env=env)
    if process.status != "completed":
        return {"status": "error", "reason": process.status, "failure_id": ""}
    try:
        data = json.loads(out.read_bytes())
        if not isinstance(data, dict) or data.get("status") not in ("passed", "failed", "error") or not isinstance(data.get("reason"), str) or not data["reason"].strip():
            raise ValueError("invalid verifier response")
        expected = {"passed": 0, "failed": 1}
        if data["status"] in expected and process.code != expected[data["status"]]:
            raise ValueError("verifier exit/status disagreement")
        if data["status"] == "failed" and (not isinstance(data.get("failure_id"), str) or not data["failure_id"]):
            raise ValueError("failed verifier requires stable failure_id")
        return data
    except (ValueError, TypeError) as exc:
        return {"status": "error", "reason": "invalid_verifier_protocol:" + str(exc), "failure_id": ""}


def execute(root: Path, plan: dict, executor=None) -> dict:
    executor = executor or CodexExecutor()
    executor.preflight()
    limits = limits_from(plan["limits"])
    task, task_dir = plan["task"], Path(plan["task_dir"])
    def check_contract_inputs():
        paths = {**plan.get("verification_inputs", {}), plan["task_path"]: plan["task_sha256"]}
        for name, expected in paths.items():
            path = Path(name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ATRException("任务或固定验证文件已改变，请重新预览：" + name)
    check_contract_inputs()
    with locked(root):
        snapshot = source_snapshot(root)
        original_modes = file_modes(root, snapshot)
        ident = uuid.uuid4().hex
        folder = run_path(root, ident)
        folder.mkdir(parents=True, mode=0o700)
        workspace = folder / "workspace"
        workspace.mkdir()
        for name, content in snapshot.items():
            path = safe_path(workspace, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(safe_path(root, name).stat().st_mode & 0o777)
        cancel = folder / "cancel.request"
        record = {"id": ident, "task_id": task["id"], "task": task, "policy_version": "v0.2-rules-1",
                  "created_at": datetime.now(timezone.utc).isoformat(), "status": "planned",
                  "source_sha256": fingerprint(snapshot), "plan": plan, "attempts": [],
                  "workspace": str(workspace), "runner_pid": os.getpid(), "cost": None}
        save(folder, record)
        start = time.monotonic()
        def remaining():
            return limits.timeout - (time.monotonic() - start)
        def finish(status, reason):
            if remaining() <= 0:
                status, reason = "budget_exceeded", "wall_clock_limit"
            if cancel.exists():
                status, reason = "cancelled", "cancel_requested"
            record["status"], record["reason"] = status, reason
            record["elapsed_seconds"] = time.monotonic() - start
            try:
                after = workspace_snapshot(workspace)
                names = changes(snapshot, after)
                record["changed_paths"] = names
                candidate_modes = file_modes(workspace, after)
                if source_snapshot(root) != snapshot or file_modes(root, snapshot) != original_modes:
                    record["status"], record["reason"] = "needs_attention", "source_project_changed_during_execution"
                elif scope_violation(names, task["allowed_paths"]):
                    record["status"], record["reason"] = "needs_attention", "candidate_changed_paths_outside_task_scope"
                elif any(candidate_modes[name] != original_modes[name] for name in snapshot.keys() & after.keys()) or (
                    os.name != "nt" and any(candidate_modes[name] & 0o111 for name in after.keys() - snapshot.keys())):
                    record["status"], record["reason"] = "needs_attention", "candidate_permission_changes_not_supported"
                atomic(folder / "candidate.patch", patch(snapshot, after).encode())
            except (ATRException, OSError, UnicodeError) as exc:
                record["status"], record["reason"] = "needs_attention", str(exc)
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                check_contract_inputs()
            except ATRException as exc:
                record["status"], record["reason"] = "needs_attention", str(exc)
            save(folder, record)
            return record
        try:
            record["status"] = "baseline"
            save(folder, record)
            baseline = run_process(command(task["baseline"], task_dir), workspace,
                                   folder / "baseline.stdout", folder / "baseline.stderr",
                                   min(limits.verifier_timeout, remaining()), cancel,
                                   env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(workspace / "src")})
            record["baseline"] = asdict(baseline)
            if baseline.status != "completed" or baseline.code:
                return finish("cancelled" if baseline.status == "cancelled" else "needs_attention", "baseline_not_healthy:" + baseline.status)
            if workspace_snapshot(workspace) != snapshot or file_modes(workspace, snapshot) != original_modes:
                return finish("needs_attention", "baseline_modified_project_files")
            initial = verification(task["verify"], workspace, folder, "initial-verification",
                                   min(limits.verifier_timeout, remaining()), cancel, task_dir)
            record["initial_verification"] = initial
            if workspace_snapshot(workspace) != snapshot or file_modes(workspace, snapshot) != original_modes:
                return finish("needs_attention", "initial_verifier_modified_workspace")
            if initial["status"] == "error":
                return finish("needs_attention", "initial_verifier_error")
            if initial["status"] == "passed":
                return finish("succeeded", "already_satisfied:no_model_call")
            current, retries, escalations, tokens, usage_known = dict(plan["initial"]), 0, 0, 0, True
            kind, feedback = "initial", ""
            while True:
                check_contract_inputs()
                if cancel.exists():
                    return finish("cancelled", "cancel_requested")
                if remaining() <= 0:
                    return finish("budget_exceeded", "wall_clock_limit")
                attempts = record["attempts"]
                if len(attempts) >= limits.max_attempts:
                    return finish("budget_exceeded", "attempt_limit")
                attempt_dir = folder / f"attempt-{len(attempts) + 1}"
                attempt_dir.mkdir()
                prompt = (f"Task: {task['description']}\nAllowed paths: {json.dumps(task['allowed_paths'])}\n"
                          "Implement the scoped task in this isolated workspace. Preserve all other files. "
                          "Do not commit, push, install skills, spawn agents, modify permissions or access credentials. "
                          "Existing files include the user's starting changes. Do not discard them. "
                          "Do not edit verification scripts or attempt to change the acceptance conditions.\n"
                          f"For Python checks use {sys.executable} -m pytest with PYTHONPATH=src; a local venv is not copied.\n"
                          + feedback)
                attempt = {"id": len(attempts) + 1, "parent_attempt_id": len(attempts) or None,
                           "kind": kind, "model": current["model"], "effort": current["effort"],
                           "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                           "artifacts": attempt_dir.name, "workspace_start_sha256": fingerprint(workspace_snapshot(workspace))}
                attempts.append(attempt)
                record["status"] = "running"
                save(folder, record)  # persist before starting any inference
                result = executor.execute(workspace, current["model"], current["effort"], prompt,
                                          attempt_dir, min(limits.attempt_timeout, remaining()), cancel)
                attempt.update({"status": result.status, "failure": result.failure, "usage": result.usage,
                                "seconds": result.seconds, "detail": result.detail})
                usage = result.usage
                if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
                    usage_known = False
                else:
                    tokens += usage["input_tokens"] + usage["output_tokens"]
                record["tokens"] = tokens if usage_known else None
                failure = result.failure
                if result.status == "completed":
                    check_contract_inputs()
                    now = workspace_snapshot(workspace)
                    if scope_violation(changes(snapshot, now), task["allowed_paths"]):
                        return finish("needs_attention", "candidate_changed_paths_outside_task_scope")
                    checked_snapshot = fingerprint(now)
                    checked = verification(task["verify"], workspace, attempt_dir, "verification",
                                           min(limits.verifier_timeout, remaining()), cancel, task_dir)
                    attempt["verification"] = checked
                    if checked["status"] == "passed":
                        # Existing tests must still pass, not only the new feature contract.
                        regression = run_process(command(task["baseline"], task_dir), workspace,
                                                 attempt_dir / "regression.stdout", attempt_dir / "regression.stderr",
                                                 min(limits.verifier_timeout, remaining()), cancel,
                                                 env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(workspace / "src")})
                        attempt["regression"] = asdict(regression)
                        if regression.status == "completed" and regression.code == 0 and fingerprint(workspace_snapshot(workspace)) == checked_snapshot:
                            attempt["failure"] = ""
                            attempt["status"] = "succeeded"
                            return finish("succeeded", "acceptance_and_regression_passed")
                        return finish("cancelled" if cancel.exists() else "needs_attention", "regression_failed_or_modified_workspace")
                    if checked["status"] == "failed":
                        repeated = verification(task["verify"], workspace, attempt_dir, "verification-repeat",
                                                min(limits.verifier_timeout, remaining()), cancel, task_dir)
                        attempt["verification_repeat"] = repeated
                        failure = "quality" if repeated["status"] == "failed" and repeated.get("failure_id") == checked.get("failure_id") else "unknown"
                    else:
                        failure = "verifier"
                    if fingerprint(workspace_snapshot(workspace)) != checked_snapshot:
                        return finish("needs_attention", "verifier_modified_workspace")
                attempt["failure"] = failure
                attempt["status"] = "failed"
                action, reason = decide(failure=failure, attempts=len(attempts), retries=retries,
                                        escalations=escalations, limits=limits, remaining_seconds=remaining(),
                                        tokens=tokens if usage_known else None, cancelled=cancel.exists())
                attempt["decision"] = {"action": action, "reason": reason}
                record["tokens"] = tokens if usage_known else None
                save(folder, record)
                if action == "stop":
                    status = "cancelled" if cancel.exists() or failure == "cancelled" else (
                        "budget_exceeded" if "budget" in reason else ("failed" if failure == "quality" else "needs_attention"))
                    return finish(status, reason)
                if action == "retry":
                    retries += 1
                    kind = "repair"
                elif plan["upgrade"]:
                    current = dict(plan["upgrade"])
                    escalations += 1
                    kind = "escalation"
                else:
                    return finish("failed", "no_stronger_configured_upgrade_target")
                feedback = "Previous attempt failed independent acceptance checks. Repair the candidate changes.\n" + json.dumps(attempt.get("verification", {}), ensure_ascii=False)
        except KeyboardInterrupt:
            return finish("cancelled", "keyboard_interrupt")
        except BaseException as exc:
            finish("needs_attention", f"controller_error:{type(exc).__name__}:{exc}")
            raise
