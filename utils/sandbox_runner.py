"""Child process for generated ETL Python. Started only by utils.sandbox.

ponytail: import and filesystem jail inside one subprocess, plus POSIX rlimits.
Upgrade path is a container with --network=none, --read-only, --memory, --cpus,
and a single mounted workdir.
"""

import os
import runpy
import sys
from pathlib import Path

_BLOCKED = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "subprocess",
    "ctypes",
    "multiprocessing",
    "pty",
    "fcntl",
}


class _NetBlock:
    def find_spec(self, fullname, path, target=None):
        root = fullname.split(".", 1)[0]
        if root in _BLOCKED:
            raise ImportError(f"blocked import: {fullname}")
        return None


def _limits() -> None:
    try:
        import resource
    except ImportError:
        return
    memory_mb = int(os.environ.get("ETL_MEMORY_MB", "512"))
    cpu_s = int(os.environ.get("ETL_TIMEOUT_SECONDS", "20"))
    memory = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))


def _inside(path) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    root = Path.cwd().resolve()
    return resolved == root or root in resolved.parents


def _jail_fs() -> None:
    import io

    real_open = io.open

    def guarded_open(file, *args, **kwargs):
        if not _inside(file):
            raise PermissionError("path is outside the job directory")
        return real_open(file, *args, **kwargs)

    real_os_open = os.open

    def guarded_os_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None or not _inside(path):
            raise PermissionError("path is outside the job directory")
        return real_os_open(path, flags, mode)

    import builtins

    builtins.open = guarded_open
    io.open = guarded_open
    os.open = guarded_os_open

    def deny(*_args, _name, **_kwargs):
        raise PermissionError(_name)

    for name in ("system", "popen", "execl", "execle", "execlp", "execv", "execve", "execvp", "spawnl", "spawnv"):
        if hasattr(os, name):
            setattr(os, name, lambda *args, _name=name, **kwargs: deny(*args, _name=_name, **kwargs))


def main() -> int:
    if len(sys.argv) != 2:
        print("sandbox runner expects a script path", file=sys.stderr)
        return 2
    script = Path(sys.argv[1]).resolve()
    _limits()
    sys.meta_path.insert(0, _NetBlock())
    _jail_fs()
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
