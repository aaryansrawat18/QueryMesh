"""Run generated Python in a child process. The API must not call this."""

import os
import subprocess
import sys
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent / "sandbox_runner.py"
_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "COMSPEC", "HOME")
_MAX_CODE = 50_000


def _child_env() -> dict:
    env = {key: os.environ[key] for key in _KEEP if os.environ.get(key)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ETL_MEMORY_MB"] = os.environ.get("ETL_MEMORY_MB", "512")
    env["ETL_TIMEOUT_SECONDS"] = os.environ.get("ETL_TIMEOUT_SECONDS", "20")
    return env


def run_isolated(code: str, workdir: Path, timeout: int | None = None) -> str:
    if not isinstance(code, str) or not code.strip():
        return "Failed to execute code: empty script"
    if len(code) > _MAX_CODE:
        return "Failed to execute code: script is too large"
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "_user.py"
    script.write_text(code, encoding="utf-8")
    limit = timeout if timeout is not None else int(os.environ.get("ETL_TIMEOUT_SECONDS", "20"))
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(_RUNNER), str(script)],
            cwd=workdir,
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=limit,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return "Failed to execute code: timeout"
    out = (proc.stdout or "")[:4000]
    err = (proc.stderr or "")[:4000]
    if proc.returncode != 0:
        detail = err.strip() or out.strip() or f"exit {proc.returncode}"
        return f"Failed to execute code: {detail}"
    return out.strip() or "Code executed successfully."
