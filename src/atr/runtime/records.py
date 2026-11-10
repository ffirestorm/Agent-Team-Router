"""Durable local run records. Recovery never silently starts another model call."""
import json
import os
import subprocess
from pathlib import Path

from atr.core import ATRException
from atr.storage import atomic, locked, safe_path

TERMINAL = {"succeeded", "failed", "cancelled", "budget_exceeded", "needs_attention"}


def run_path(root: Path, ident: str) -> Path:
    if len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
        raise ATRException("run ID 必须是 32 位十六进制字符串")
    return safe_path(root, f".atr/runs/{ident}")


def save(folder: Path, record: dict):
    atomic(folder / "record.json", json.dumps(record, ensure_ascii=False, indent=2).encode())


def show(root: Path, ident: str) -> dict:
    path = run_path(root, ident) / "record.json"
    if not path.is_file() or path.is_symlink():
        raise ATRException("执行记录不存在或路径无效")
    record = json.loads(path.read_bytes())
    if not isinstance(record, dict) or record.get("id") != ident:
        raise ATRException("执行记录格式无效")
    return record


def all_records(root: Path) -> list[dict]:
    parent = safe_path(root, ".atr/runs")
    if not parent.exists():
        return []
    return [show(root, path.name) for path in sorted(parent.iterdir()) if path.is_dir() and not path.is_symlink()]


def request_cancel(root: Path, ident: str) -> dict:
    record = show(root, ident)
    if record["status"] not in TERMINAL:
        atomic(safe_path(root, f".atr/runs/{ident}/cancel.request"), b"cancel\n")
    return {"id": ident, "status": record["status"], "cancel_requested": record["status"] not in TERMINAL}


def recover_run(root: Path, ident: str) -> dict:
    with locked(root):
        record = show(root, ident)
        if record["status"] not in TERMINAL:
            for path in run_path(root, ident).rglob("*.pid"):
                pid = int(path.read_text(encoding="ascii"))
                if os.name == "nt":
                    result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                            capture_output=True, text=True, timeout=10)
                    if result.returncode:
                        raise ATRException("无法确认残留进程状态；请先检查执行进程")
                    alive = f'"{pid}"' in result.stdout
                else:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except ProcessLookupError:
                        alive = False
                    except PermissionError:
                        alive = True
                if alive:
                    raise ATRException(f"记录中的进程 {pid} 仍存在；先确认并终止残留执行，再恢复记录")
            record["status"] = "needs_attention"
            record["reason"] = "interrupted_controller:no_automatic_reexecution; inspect worker.pid files for orphan processes"
            for attempt in record["attempts"]:
                if attempt.get("status") == "running":
                    attempt["status"] = "interrupted"
            save(run_path(root, ident), record)
        return record


def report(root: Path) -> dict:
    runs = all_records(root)
    started = [run for run in runs if run.get("attempts")]
    noops = [run for run in runs if run.get("reason") == "already_satisfied:no_model_call"]
    first_denominator = len(runs) - len(noops)
    first_success = sum(run["status"] == "succeeded" and run["attempts"][0].get("status") == "succeeded" for run in started)
    upgraded = [run for run in started if any(a.get("kind") == "escalation" for a in run["attempts"])]
    rescued = sum(run["status"] == "succeeded" for run in upgraded)
    statuses = {}
    for run in runs:
        statuses[run["status"]] = statuses.get(run["status"], 0) + 1
    attempts = [attempt for run in started for attempt in run["attempts"]]
    known_cost = [attempt.get("usage", {}).get("cost") for attempt in attempts]
    token_usage = {}
    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
        values = [attempt.get("usage", {}).get(key) for attempt in attempts]
        token_usage[key] = {"known_subtotal": sum(v for v in values if v is not None),
                            "missing_attempts": sum(v is None for v in values),
                            "total": sum(values) if values and all(v is not None for v in values) else None}
    return {"runs": len(runs), "started_tasks": len(runs), "model_tasks": len(started),
            "already_satisfied_without_inference": len(noops), "statuses": statuses,
            "first_success_rate": first_success / first_denominator if first_denominator else None,
            "end_to_end_success_rate": sum(r["status"] == "succeeded" for r in runs) / len(runs) if runs else None,
            "upgrades_started": len(upgraded), "upgrades_unfinished": sum(r["status"] not in TERMINAL for r in upgraded),
            "upgrade_rescue_rate": rescued / len(upgraded) if upgraded else None,
            "attempts": len(attempts), "cost": sum(known_cost) if known_cost and all(c is not None for c in known_cost) else None,
            "token_usage": token_usage,
            "elapsed_seconds": sum(run.get("elapsed_seconds", 0) for run in runs),
            "cost_missing_attempts": sum(c is None for c in known_cost),
            "note": "基线失败/运行中/取消均保留在任务分母；已满足任务单列且不计为模型首次成功。费用不可观测为 null；不是模型能力因果结论。"}
