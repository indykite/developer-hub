"""Shared .env persistence for the music app.

Every route that saves IDs, tokens, or flags writes through here. A thread
lock plus an advisory file lock serialize the read-modify-write both within
one process (two capture streams can finish at the same moment) and across
worker processes (gunicorn/uwsgi), and the file is swapped in atomically with
os.replace so a concurrent reader (dotenv_values on a page render) never sees
a truncated .env.
"""

import contextlib
import logging
import os
import re
import threading
from pathlib import Path

try:
    import fcntl
except ImportError:  # non-POSIX platform: in-process locking only
    fcntl = None

logger = logging.getLogger(__name__)

ENV_FILE = Path(__file__).parent.parent / ".env"

_ENV_LOCK = threading.Lock()


@contextlib.contextmanager
def _env_write_lock():
    """Serialize the .env read-modify-write in-process AND across processes.

    The threading lock covers concurrent request threads in one process; the
    advisory flock covers multi-worker deployments (gunicorn/uwsgi), where
    separate processes would otherwise interleave read-modify-write cycles and
    silently lose keys (last writer wins). Readers need no lock: the atomic
    os.replace in _write_atomic means they always see a complete file.
    """
    with _ENV_LOCK:
        if fcntl is None:
            yield
            return
        # A separate lock file: locking .env itself would race with os.replace
        # swapping the inode out from under the lock.
        lock_path = ENV_FILE.with_name(ENV_FILE.name + ".lock")
        with lock_path.open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read_lines():
    if not ENV_FILE.exists():
        return []
    with ENV_FILE.open() as f:
        return f.readlines()


def _write_atomic(lines):
    tmp = ENV_FILE.with_name(ENV_FILE.name + ".tmp")
    with tmp.open("w") as f:
        f.writelines(lines)
    tmp.replace(ENV_FILE)


def update_env_variable(key, value):
    """Update or add an environment variable in the .env file (and this process's env)."""
    with _env_write_lock():
        lines = _read_lines()
        key_found = False
        updated_lines = []
        for line in lines:
            # Match lines like KEY=value or KEY="value"
            if re.match(f"^{re.escape(key)}=", line):
                updated_lines.append(f"{key}={value}\n")
                key_found = True
            else:
                updated_lines.append(line)
        # If key wasn't found, add it (ensuring previous last line ends with a newline)
        if not key_found:
            if updated_lines and not updated_lines[-1].endswith("\n"):
                updated_lines[-1] += "\n"
            updated_lines.append(f"{key}={value}\n")
        _write_atomic(updated_lines)
    os.environ[key] = value
    logger.info("Updated %s in .env file", key)


def remove_env_variables(keys):
    """Delete the given keys from .env AND this process's environment.

    Removing from os.environ matters as much as the file: load_dotenv only adds
    keys, so a value dropped from the file would otherwise linger in the
    process until restart and keep being read by os.getenv.
    """
    keys = set(keys)
    patterns = [re.compile(f"^{re.escape(key)}=") for key in keys]
    with _env_write_lock():
        lines = _read_lines()
        kept = [line for line in lines if not any(p.match(line) for p in patterns)]
        if len(kept) != len(lines):
            _write_atomic(kept)
    for key in keys:
        os.environ.pop(key, None)


def retain_env_variables(keep_vars):
    """Rewrite .env keeping ONLY the given keys (project-delete cleanup).

    Dropped keys are also removed from os.environ so os.getenv callers cannot
    keep seeing project-scoped values after the delete.
    """
    keep = set(keep_vars)
    dropped = []
    with _env_write_lock():
        lines = _read_lines()
        kept = []
        for line in lines:
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
            if match and match.group(1) not in keep:
                dropped.append(match.group(1))
            else:
                kept.append(line)
        _write_atomic(kept)
    for key in dropped:
        os.environ.pop(key, None)
