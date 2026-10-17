"""Who owns in-flight work, and whether that owner is still alive.

A suite is stored `running` while a process works on it: this one, another CLI or server on
the same machine, or a process on another host sharing a PostgreSQL database. Recovery must
never mark such a suite interrupted while its process lives, so every running row records
its owner ("host:pid:incarnation") and a heartbeat the owner refreshes while it works. A row
counts as abandoned only when its owner is provably gone (a process on this host that no
longer exists, or an earlier process that had this process's pid) or, when that cannot be
checked from here, when its heartbeat went silent.
"""

from __future__ import annotations

import os
import socket
import sys
import uuid
from datetime import timedelta

# How often a running suite refreshes its heartbeat, and how long a heartbeat may be silent
# before an owner that cannot be checked directly (another host) is presumed gone.
HEARTBEAT_INTERVAL = timedelta(seconds=30)
STALE_AFTER = timedelta(minutes=5)

_MAX_HOST = 255


def _new_incarnation() -> str:
    return uuid.uuid4().hex[:12]


# Random per process. A pid only names a process while it lives: a server restarted in its
# container is pid 1 again, so without this it would take the suites its predecessor left
# `running` for its own and never recover them.
_incarnation = _new_incarnation()


def _reincarnate() -> None:
    global _incarnation
    _incarnation = _new_incarnation()


if hasattr(os, "register_at_fork"):  # a forked child is a process of its own
    os.register_at_fork(after_in_child=_reincarnate)


def this_host() -> str:
    """The host part of an owner: pids are only comparable on the same host."""
    return socket.gethostname()[:_MAX_HOST]


def process_owner() -> str:
    """'host:pid:incarnation' of the calling process."""
    return f"{this_host()}:{os.getpid()}:{_incarnation}"


def _parse(owner: str) -> tuple[str, str, str | None]:
    """(host, pid, incarnation); the incarnation is None for a bare 'host:pid'."""
    parts = owner.rsplit(":", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return parts[0], parts[1], parts[2]
    host, _, pid_text = owner.rpartition(":")
    return host, pid_text, None


def owner_alive(owner: str | None) -> bool | None:
    """False when `owner` is a process on this host that no longer exists, True when it
    still does, None when that cannot be told from here (another host, no owner recorded,
    or a platform without a safe probe): the heartbeat then decides.

    A live pid on this host is trusted even with a silent heartbeat: the process may be
    suspended (a sleeping laptop) and resume its suite, and once it really exits the row is
    recovered as dead. This process's own pid with another incarnation is an earlier process
    that is gone. (A pid that an unrelated process reused, e.g. after a reboot, still reads
    as alive: such a row stays `running` rather than risk marking live work.)
    """
    if not owner:
        return None
    host, pid_text, incarnation = _parse(owner)
    if host != this_host() or not pid_text.isdigit():
        return None
    pid = int(pid_text)
    if pid <= 0:  # signalling pid 0 would reach the whole process group
        return None
    if pid == os.getpid():
        return incarnation is None or incarnation == _incarnation
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
