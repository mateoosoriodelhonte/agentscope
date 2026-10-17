"""Who owns in-flight work, and whether that owner is still alive.

A suite is stored `running` while a process works on it: this one, another CLI or server on
the same machine, or a process on another host sharing a PostgreSQL database. Recovery must
never mark such a suite interrupted while its process lives, so every running row records
its owner ("host:pid") and a heartbeat the owner refreshes while it works. A row counts as
abandoned only when its owner is provably gone (a process on this host that no longer
exists) or, when that cannot be checked from here, when its heartbeat went silent.
"""

from __future__ import annotations

import os
import socket
import sys
from datetime import timedelta

# How often a running suite refreshes its heartbeat, and how long a heartbeat may be silent
# before an owner that cannot be checked directly (another host) is presumed gone.
HEARTBEAT_INTERVAL = timedelta(seconds=30)
STALE_AFTER = timedelta(minutes=5)

_MAX_HOST = 255


def _host() -> str:
    return socket.gethostname()[:_MAX_HOST]


def process_owner() -> str:
    """'host:pid' of the calling process (computed per call: a forked child is its own)."""
    return f"{_host()}:{os.getpid()}"


def owner_alive(owner: str | None) -> bool | None:
    """False when `owner` is a process on this host that no longer exists, True when it
    still does, None when that cannot be told from here (another host, no owner recorded,
    or a platform without a safe probe): the heartbeat then decides.

    A live pid on this host is trusted even with a silent heartbeat: the process may be
    suspended (a sleeping laptop) and resume its suite, and once it really exits the row is
    recovered as dead.
    """
    if not owner:
        return None
    host, _, pid_text = owner.rpartition(":")
    if host != _host() or not pid_text.isdigit():
        return None
    pid = int(pid_text)
    if pid <= 0:  # signalling pid 0 would reach the whole process group
        return None
    if pid == os.getpid():
        return True
    if sys.platform == "win32":  # pragma: no cover - os.kill(pid, 0) terminates on Windows
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, but belongs to another user
    except OSError:
        return None
    return True
