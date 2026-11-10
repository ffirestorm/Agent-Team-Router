"""Process-tree cancellation, bounded wall time and local evidence files."""
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time


@dataclass
class ProcessResult:
    code: int
    status: str
    seconds: float


def terminate_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # A child can ignore TERM after its parent has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def run_process(argv: list[str], cwd: Path, stdout: Path, stderr: Path,
                timeout: float, cancel: Path, input_text: str | None = None,
                env: dict | None = None) -> ProcessResult:
    start = time.monotonic()
    if cancel.exists():
        return ProcessResult(-1, "cancelled", 0)
    if timeout <= 0:
        return ProcessResult(-1, "timeout", 0)
    settings = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            # File-backed stdin avoids deadlocking on a large prompt before timeout polling.
            import tempfile
            with tempfile.TemporaryFile() as stdin:
                if input_text is not None:
                    stdin.write(input_text.encode("utf-8"))
                    stdin.seek(0)
                process = subprocess.Popen(argv, cwd=cwd, stdin=stdin, stdout=out, stderr=err,
                                           env=env, **settings)
                stdout.with_suffix(stdout.suffix + ".pid").write_text(str(process.pid), encoding="ascii")
                status = "completed"
                try:
                    while process.poll() is None:
                        if cancel.exists() or time.monotonic() - start >= timeout:
                            status = "cancelled" if cancel.exists() else "timeout"
                            terminate_tree(process)
                            break
                        time.sleep(0.05)
                except BaseException:
                    terminate_tree(process)
                    raise
                return ProcessResult(process.returncode, status, time.monotonic() - start)
    except OSError as exc:
        stderr.write_text(str(exc), encoding="utf-8")
        return ProcessResult(-1, "environment", time.monotonic() - start)
