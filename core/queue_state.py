"""Shared helpers for reading/writing the queue-index state file.

Both app.py (the Streamlit app) and host_gui.py (the desktop launcher) need
to read "which queued file is currently being viewed" and sometimes update
it. Previously each file had its own copy of this logic with no locking,
which meant:
  - the two implementations could silently drift apart, and
  - concurrent reads/writes (e.g. clicking "Next" in the app at the same
    moment the launcher's Move button reads the index) had no protection
    against interleaving.

This module fixes both: one implementation, and every read/write goes
through a file lock plus an atomic write (write-to-temp, then rename) so a
reader never sees a half-written file.

Kept free of Streamlit/Tkinter imports so it stays usable from either side.
"""

import json
from pathlib import Path
from typing import Optional

from filelock import FileLock, Timeout

_LOCK_TIMEOUT_SECONDS = 2


def _lock_for(state_file: Path) -> FileLock:
    return FileLock(str(state_file.with_suffix(state_file.suffix + ".lock")), timeout=_LOCK_TIMEOUT_SECONDS)


def read_index(state_file: Optional[Path], n: int) -> int:
    """Read the current queue index, clamped to a valid range for a queue
    of length `n`. Returns 0 if there's no state file, it can't be parsed,
    or the lock can't be acquired in time (never blocks the caller)."""
    if not state_file or not state_file.exists():
        return 0
    try:
        with _lock_for(state_file):
            idx = json.loads(state_file.read_text()).get("index", 0)
    except Exception:
        idx = 0
    return max(0, min(idx, max(n - 1, 0)))


def write_index(state_file: Optional[Path], idx: int) -> None:
    """Write the queue index atomically: write to a temp file, then rename
    it over the real one, so a concurrent reader always sees either the old
    complete file or the new complete file -- never a partial write."""
    if not state_file:
        return
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    try:
        with _lock_for(state_file):
            tmp.write_text(json.dumps({"index": idx}))
            tmp.replace(state_file)
    except Timeout:
        pass
